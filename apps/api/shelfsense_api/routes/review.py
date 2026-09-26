"""The human review queue: approve or reject actions, judge extractions, promote labels."""

import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import exists, select
from sqlalchemy.orm import selectinload

from shelfsense_api.agents.vision import (
    ExtractionValidationError,
    ShelfExtraction,
    summarise,
    validate_extraction,
)
from shelfsense_api.deps import CurrentPrincipal, SettingsDep, TenantSession, require_role
from shelfsense_api.guardrails import ActionKind
from shelfsense_api.models import (
    Action,
    ActionStatus,
    Extraction,
    ExtractionReview,
    GoldenCase,
    Photo,
    ReviewVerdict,
    Role,
)
from shelfsense_api.planogram_context import load_shelf_bundle, planogram_snapshot
from shelfsense_api.routes._common import READ_ONE_RESPONSES, READ_RESPONSES, WRITE_RESPONSES
from shelfsense_api.routes.photos import StoreDep
from shelfsense_api.schemas import (
    ActionDecisionIn,
    ActionOut,
    ExtractionCandidateOut,
    ExtractionReviewIn,
    ExtractionReviewOut,
    GoldenCaseOut,
    PromoteIn,
    ReviewQueueOut,
)

router = APIRouter(prefix="/v1", tags=["review"])
reviewers = Depends(require_role(Role.owner, Role.manager, Role.reviewer))


# --- queue ------------------------------------------------------------------------------


@router.get(
    "/review/queue",
    response_model=ReviewQueueOut,
    responses=READ_RESPONSES,
    dependencies=[reviewers],
)
async def review_queue(
    session: TenantSession, settings: SettingsDep, store: StoreDep
) -> ReviewQueueOut:
    """Pending actions and low-confidence extractions nobody has judged yet."""
    actions = await session.scalars(
        select(Action)
        .where(Action.status == ActionStatus.pending_review)
        .order_by(Action.created_at.desc())
    )
    reviewed = select(ExtractionReview.id).where(ExtractionReview.extraction_id == Extraction.id)
    candidates = await session.scalars(
        select(Extraction)
        .where(
            Extraction.overall_confidence < settings.review_confidence_threshold,
            ~exists(reviewed),
        )
        .options(selectinload(Extraction.photo))
        .order_by(Extraction.created_at.desc())
        .limit(50)
    )
    out: list[ExtractionCandidateOut] = []
    for extraction in candidates:
        bundle = await load_shelf_bundle(session, extraction.photo.shelf_id)
        if bundle is None:
            continue
        out.append(
            ExtractionCandidateOut(
                extraction_id=extraction.id,
                photo_id=extraction.photo_id,
                shelf_id=bundle.shelf.id,
                shelf_label=bundle.shelf.label,
                store_name=bundle.shelf.store.name,
                confidence=extraction.overall_confidence,
                reason=(
                    f"confidence {extraction.overall_confidence:.2f} below "
                    f"{settings.review_confidence_threshold:.2f}"
                ),
                created_at=extraction.created_at,
                download_url=await store.presigned_get_url(extraction.photo.object_key),
                extraction=ShelfExtraction.model_validate(extraction.result["extraction"]),
                summary=extraction.result["summary"],
                planogram=planogram_snapshot(bundle.context),
            )
        )
    return ReviewQueueOut(actions=[ActionOut.model_validate(a) for a in actions], extractions=out)


# --- actions ----------------------------------------------------------------------------


async def _reviewable_action(session: TenantSession, action_id: UUID) -> Action:
    action = await session.get(Action, action_id)
    if action is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "action not found")
    if action.status not in (ActionStatus.pending_review, ActionStatus.proposed):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"action is already {action.status.value}; cannot review"
        )
    return action


def _decide(
    action: Action, principal: CurrentPrincipal, new_status: ActionStatus, note: str | None
) -> None:
    action.status = new_status
    action.reviewed_by = principal.user_id
    action.reviewed_at = datetime.now(UTC)
    action.review_note = note


@router.post(
    "/actions/{action_id}/approve",
    response_model=ActionOut,
    responses={**WRITE_RESPONSES, **READ_ONE_RESPONSES},
    dependencies=[reviewers],
)
async def approve_action(
    action_id: UUID, body: ActionDecisionIn, principal: CurrentPrincipal, session: TenantSession
) -> ActionOut:
    """Approve, optionally with an edited reorder quantity (cost is re-estimated)."""
    action = await _reviewable_action(session, action_id)
    if body.quantity is not None:
        if action.kind != ActionKind.reorder:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "quantity applies to reorders only"
            )
        unit = int(action.payload["unit_price_kobo"])
        action.payload = {
            **action.payload,
            "quantity": body.quantity,
            "original_quantity": action.payload.get(
                "original_quantity", action.payload["quantity"]
            ),
        }
        action.estimated_cost_kobo = body.quantity * unit
    _decide(action, principal, ActionStatus.approved, body.note)
    await session.flush()
    await session.refresh(action)
    return ActionOut.model_validate(action)


@router.post(
    "/actions/{action_id}/reject",
    response_model=ActionOut,
    responses={**WRITE_RESPONSES, **READ_ONE_RESPONSES},
    dependencies=[reviewers],
)
async def reject_action(
    action_id: UUID, body: ActionDecisionIn, principal: CurrentPrincipal, session: TenantSession
) -> ActionOut:
    """Reject with a note."""
    action = await _reviewable_action(session, action_id)
    _decide(action, principal, ActionStatus.rejected, body.note)
    await session.flush()
    await session.refresh(action)
    return ActionOut.model_validate(action)


# --- extraction reviews (labels) --------------------------------------------------------


@router.post(
    "/extractions/{extraction_id}/review",
    response_model=ExtractionReviewOut,
    status_code=status.HTTP_201_CREATED,
    responses={**WRITE_RESPONSES, **READ_ONE_RESPONSES},
    dependencies=[reviewers],
)
async def review_extraction(
    extraction_id: UUID,
    body: ExtractionReviewIn,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> ExtractionReviewOut:
    """Record a verdict. Corrections are validated against the planogram like model output."""
    extraction = await session.scalar(
        select(Extraction)
        .where(Extraction.id == extraction_id)
        .options(selectinload(Extraction.photo))
    )
    if extraction is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "extraction not found")

    corrected: dict[str, object] | None = None
    if body.verdict == ReviewVerdict.corrected:
        if body.corrected is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "verdict 'corrected' needs a corrected extraction",
            )
        bundle = await load_shelf_bundle(session, extraction.photo.shelf_id)
        if bundle is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "shelf no longer has a planogram")
        try:
            validated = validate_extraction(body.corrected.model_dump_json(), bundle.context)
        except ExtractionValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        corrected = {
            "extraction": json.loads(validated.model_dump_json()),
            "summary": json.loads(summarise(validated, bundle.context).model_dump_json()),
        }
    elif body.corrected is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "only verdict 'corrected' takes a corrected extraction",
        )

    review = ExtractionReview(
        tenant_id=principal.tenant_id,
        extraction_id=extraction.id,
        photo_id=extraction.photo_id,
        reviewer=principal.user_id,
        verdict=body.verdict,
        corrected=corrected,
        note=body.note,
    )
    session.add(review)
    await session.flush()
    await session.refresh(review)
    return ExtractionReviewOut.model_validate(review)


@router.get("/labels", response_model=list[ExtractionReviewOut], responses=READ_RESPONSES)
async def list_labels(session: TenantSession) -> list[ExtractionReviewOut]:
    """Every stored verdict, newest first, with its golden case id when promoted."""
    rows = await session.scalars(
        select(ExtractionReview)
        .options(selectinload(ExtractionReview.golden_case))
        .order_by(ExtractionReview.created_at.desc())
        .limit(200)
    )
    out: list[ExtractionReviewOut] = []
    for review in rows:
        item = ExtractionReviewOut.model_validate(review)
        item.golden_case_id = review.golden_case.id if review.golden_case else None
        out.append(item)
    return out


# --- golden set -------------------------------------------------------------------------


@router.post(
    "/labels/{review_id}/promote",
    response_model=GoldenCaseOut,
    status_code=status.HTTP_201_CREATED,
    responses={**WRITE_RESPONSES, **READ_ONE_RESPONSES},
    dependencies=[reviewers],
)
async def promote_label(
    review_id: UUID, body: PromoteIn, principal: CurrentPrincipal, session: TenantSession
) -> GoldenCaseOut:
    """Turn a reviewed photo into an eval example: photo + planogram snapshot + expected."""
    review = await session.scalar(
        select(ExtractionReview)
        .where(ExtractionReview.id == review_id)
        .options(selectinload(ExtractionReview.golden_case))
    )
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "review not found")
    if review.golden_case is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "already promoted")
    if review.verdict == ReviewVerdict.unusable:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "an unusable photo cannot be a golden case"
        )
    extraction = await session.get(Extraction, review.extraction_id)
    photo = await session.get(Photo, review.photo_id)
    if extraction is None or photo is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "extraction or photo no longer exists")
    bundle = await load_shelf_bundle(session, photo.shelf_id)
    if bundle is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "shelf no longer has a planogram")

    expected = review.corrected if review.corrected is not None else extraction.result
    case = GoldenCase(
        tenant_id=principal.tenant_id,
        review_id=review.id,
        photo_id=photo.id,
        object_key=photo.object_key,
        source="review",
        planogram=planogram_snapshot(bundle.context),
        expected=expected,
        tags=sorted({*body.tags, review.verdict.value}),
        promoted_by=principal.user_id,
    )
    session.add(case)
    await session.flush()
    await session.refresh(case)
    return GoldenCaseOut.model_validate(case)


@router.get("/golden", response_model=list[GoldenCaseOut], responses=READ_RESPONSES)
async def list_golden(session: TenantSession) -> list[GoldenCaseOut]:
    """The tenant's golden cases, newest first."""
    rows = await session.scalars(
        select(GoldenCase).order_by(GoldenCase.created_at.desc()).limit(500)
    )
    return [GoldenCaseOut.model_validate(c) for c in rows]

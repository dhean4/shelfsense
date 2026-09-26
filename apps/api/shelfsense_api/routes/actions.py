"""Planner actions. Listing here; approve/edit/reject arrive with the review queue (P4)."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from shelfsense_api.deps import TenantSession
from shelfsense_api.guardrails import ActionKind
from shelfsense_api.models import Action, ActionStatus
from shelfsense_api.routes._common import READ_ONE_RESPONSES, READ_RESPONSES
from shelfsense_api.schemas import ActionOut

router = APIRouter(prefix="/v1/actions", tags=["actions"])


@router.get("", response_model=list[ActionOut], responses=READ_RESPONSES)
async def list_actions(
    session: TenantSession,
    status_filter: ActionStatus | None = Query(default=None, alias="status"),
    kind: ActionKind | None = Query(default=None),
    store_id: UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ActionOut]:
    """Actions, newest first, filterable by status, kind and store."""
    stmt = select(Action).order_by(Action.created_at.desc()).limit(limit)
    if status_filter is not None:
        stmt = stmt.where(Action.status == status_filter)
    if kind is not None:
        stmt = stmt.where(Action.kind == kind)
    if store_id is not None:
        stmt = stmt.where(Action.store_id == store_id)
    rows = await session.scalars(stmt)
    return [ActionOut.model_validate(a) for a in rows]


@router.get("/{action_id}", response_model=ActionOut, responses=READ_ONE_RESPONSES)
async def read_action(action_id: UUID, session: TenantSession) -> ActionOut:
    """One action."""
    action = await session.get(Action, action_id)
    if action is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "action not found")
    return ActionOut.model_validate(action)

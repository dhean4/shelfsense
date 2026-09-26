"""Shelf photo upload and status."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import Select, select
from sqlalchemy.orm import selectinload

from shelfsense_api.config import Settings
from shelfsense_api.deps import CurrentPrincipal, SettingsDep, TenantSession, require_role
from shelfsense_api.images import InvalidImageError, inspect
from shelfsense_api.jobs import PROCESS_PHOTO, get_photo_store, get_queue
from shelfsense_api.models import Extraction, Photo, PhotoStatus, Role
from shelfsense_api.queue import JobQueue
from shelfsense_api.routes._common import READ_ONE_RESPONSES, WRITE_RESPONSES
from shelfsense_api.routes.shelves import shelf_or_404
from shelfsense_api.schemas import ErrorResponse, ExtractionOut, PhotoOut
from shelfsense_api.storage import PhotoStore

router = APIRouter(prefix="/v1", tags=["photos"])
uploaders = Depends(require_role(Role.owner, Role.manager, Role.field_agent))

EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def queue_dep(settings: SettingsDep) -> JobQueue:
    """Queue dependency (overridden in tests)."""
    return get_queue(settings)


def store_dep(settings: SettingsDep) -> PhotoStore:
    """Storage dependency (overridden in tests)."""
    return get_photo_store(settings)


QueueDep = Annotated[JobQueue, Depends(queue_dep)]
StoreDep = Annotated[PhotoStore, Depends(store_dep)]


async def _to_out(photo: Photo, store: PhotoStore, settings: Settings) -> PhotoOut:
    extraction = None
    if photo.extraction is not None:
        run = photo.extraction.run
        extraction = ExtractionOut(
            id=photo.extraction.id,
            run_id=run.id,
            model=run.model,
            provider=run.provider,
            planogram_version=photo.extraction.planogram_version,
            overall_confidence=photo.extraction.overall_confidence,
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            cost_usd=float(run.cost_usd) if run.cost_usd is not None else None,
            latency_ms=run.latency_ms,
            attempts=run.attempts,
            created_at=photo.extraction.created_at,
            **photo.extraction.result,
        )
    return PhotoOut(
        id=photo.id,
        shelf_id=photo.shelf_id,
        status=photo.status,
        content_type=photo.content_type,
        size_bytes=photo.size_bytes,
        width=photo.width,
        height=photo.height,
        uploaded_by=photo.uploaded_by,
        error=photo.error,
        created_at=photo.created_at,
        updated_at=photo.updated_at,
        download_url=await store.presigned_get_url(photo.object_key),
        extraction=extraction,
    )


def _photo_query() -> Select[Photo]:
    return select(Photo).options(selectinload(Photo.extraction).selectinload(Extraction.run))


@router.post(
    "/shelves/{shelf_id}/photos",
    response_model=PhotoOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **WRITE_RESPONSES,
        **READ_ONE_RESPONSES,
        status.HTTP_413_CONTENT_TOO_LARGE: {"model": ErrorResponse, "description": "Too large"},
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {
            "model": ErrorResponse,
            "description": "Not an image",
        },
    },
    dependencies=[uploaders],
)
async def upload_photo(
    shelf_id: UUID,
    principal: CurrentPrincipal,
    session: TenantSession,
    settings: SettingsDep,
    queue: QueueDep,
    store: StoreDep,
    file: UploadFile = File(description="JPEG, PNG or WebP shelf photo"),
) -> PhotoOut:
    """Store the photo and queue the vision extraction. Returns 202 with status ``queued``."""
    await shelf_or_404(session, shelf_id)
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"photo exceeds {settings.max_upload_bytes} bytes",
        )
    try:
        info = inspect(data)
    except InvalidImageError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc

    photo_id = uuid4()
    key = f"{principal.tenant_id}/{shelf_id}/{photo_id}.{EXTENSIONS[info.media_type]}"
    await store.put(key, data, info.media_type)
    photo = Photo(
        id=photo_id,
        tenant_id=principal.tenant_id,
        shelf_id=shelf_id,
        uploaded_by=principal.user_id,
        object_key=key,
        content_type=info.media_type,
        size_bytes=len(data),
        width=info.width,
        height=info.height,
        status=PhotoStatus.queued,
    )
    session.add(photo)
    await session.flush()
    await session.refresh(photo, attribute_names=["created_at", "updated_at"])
    photo.extraction = None
    await queue.enqueue(
        PROCESS_PHOTO, {"photo_id": str(photo.id), "tenant_id": str(principal.tenant_id)}
    )
    return await _to_out(photo, store, settings)


@router.get(
    "/shelves/{shelf_id}/photos", response_model=list[PhotoOut], responses=READ_ONE_RESPONSES
)
async def list_photos(
    shelf_id: UUID, session: TenantSession, settings: SettingsDep, store: StoreDep
) -> list[PhotoOut]:
    """Photos of a shelf, newest first."""
    await shelf_or_404(session, shelf_id)
    rows = await session.scalars(
        _photo_query().where(Photo.shelf_id == shelf_id).order_by(Photo.created_at.desc())
    )
    return [await _to_out(photo, store, settings) for photo in rows]


@router.get("/photos/{photo_id}", response_model=PhotoOut, responses=READ_ONE_RESPONSES)
async def read_photo(
    photo_id: UUID, session: TenantSession, settings: SettingsDep, store: StoreDep
) -> PhotoOut:
    """One photo with its extraction once processing is done."""
    photo = await session.scalar(_photo_query().where(Photo.id == photo_id))
    if photo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "photo not found")
    return await _to_out(photo, store, settings)

from __future__ import annotations

import asyncio
from collections.abc import Generator

from typing import Annotated

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Path
from fastapi import Query
from fastapi import Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from backend.memory.repository_v2 import MemoryRepositoryV2

SessionIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Session identifier (alphanumeric, hyphens, underscores)",
    ),
]

from backend.bootstrap.memory_export import cleanup_export_file, write_memory_export_zip
from backend.core.auth import require_http_bearer_auth
from backend.core.rate_limit import enforce_http_rate_limit
from backend.core.runtime import get_app_runtime
from backend.core.storage import SessionNotFoundError, now_ms
from backend.memory.retrieval_v2 import (
    LiveMemoryBundleRequest,
    MemoryRetrievalServiceV2,
    summarize_recent_maintenance,
)

router = APIRouter()


def _iter_file_chunks(
    path: str, *, chunk_size: int = 64 * 1024
) -> Generator[bytes, None, None]:
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            yield chunk


class SessionMemoryResetResponse(BaseModel):
    status: str
    session_id: str
    deleted_artifact_rows: int
    deleted_vision_frame_rows: int
    deleted_session_rows: int
    removed_session_dir: bool
    removed_vision_frames_dir: bool


class MemoryItemListResponse(BaseModel):
    count: int
    items: list[dict[str, object]]


class MemoryItemLookupResponse(BaseModel):
    found: bool
    item: dict[str, object] | None = None


class MemoryItemEvidenceResponse(BaseModel):
    item_id: str
    count: int
    evidence: list[dict[str, object]]


class MemoryItemUpdatePayload(BaseModel):
    summary: str | None = None
    structured_value: dict[str, object] | None = None
    confidence: float | None = None
    relevance: float | None = None
    maturity: float | None = None
    tags: list[str] | None = None
    correction_note: str | None = None
    session_id: str | None = None
    status: str | None = None


class MemoryItemSuppressPayload(BaseModel):
    note: str | None = None


class MemoryMaintenanceStateResponse(BaseModel):
    maintenance: dict[str, object]


class MemoryLiveBundleResponse(BaseModel):
    bundle: dict[str, object]


class MemoryConflictGroupListResponse(BaseModel):
    count: int
    groups: list[dict[str, object]]


class MemoryConflictGroupLookupResponse(BaseModel):
    found: bool
    group: dict[str, object] | None = None


class MemoryConflictMergePayload(BaseModel):
    target_item_id: str
    source_item_id: str
    actor: str | None = None
    reason: str | None = None
    suppress_source: bool = True
    merged_at_ms: int | None = None


class MemoryConflictMergeResponse(BaseModel):
    merged: bool
    target_item: dict[str, object] | None = None
    source_item: dict[str, object] | None = None
    merge_event: dict[str, object] | None = None


class MemoryConflictSuppressPayload(BaseModel):
    actor: str | None = None
    reason: str | None = None
    updated_at_ms: int | None = None


class MemoryItemAuditTrailResponse(BaseModel):
    found: bool
    audit_trail: dict[str, object] | None = None


def _serialize_item(item) -> dict[str, object]:
    return {
        "item_id": item.item_id,
        "memory_class": item.memory_class,
        "scope": item.scope,
        "session_id": item.session_id,
        "status": item.status,
        "summary": item.summary,
        "structured_value": dict(item.structured_value),
        "confidence": item.confidence,
        "relevance": item.relevance,
        "maturity": item.maturity,
        "fingerprint": item.fingerprint,
        "subject_key": item.subject_key,
        "value_key": item.value_key,
        "first_seen_at_ms": item.first_seen_at_ms,
        "last_seen_at_ms": item.last_seen_at_ms,
        "last_promoted_at_ms": item.last_promoted_at_ms,
        "source_kinds": list(item.source_kinds),
        "evidence_ids": list(item.evidence_ids),
        "relation_ids": list(item.relation_ids),
        "tags": list(item.tags),
        "correction_notes": list(item.correction_notes),
        "metadata": dict(item.metadata),
    }


def _serialize_evidence(evidence) -> dict[str, object]:
    return {
        "evidence_id": evidence.evidence_id,
        "evidence_kind": evidence.evidence_kind,
        "session_id": evidence.session_id,
        "source_ref": evidence.source_ref,
        "excerpt": evidence.excerpt,
        "captured_at_ms": evidence.captured_at_ms,
        "confidence": evidence.confidence,
        "item_id": evidence.item_id,
        "observation_id": evidence.observation_id,
        "candidate_id": evidence.candidate_id,
        "tags": list(evidence.tags),
        "metadata": dict(evidence.metadata),
    }


ItemIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Memory v2 item identifier",
    ),
]

ConflictGroupKeyPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Memory v2 conflict group key",
    ),
]


@router.get("/memory/export")
async def export_memory(request: Request) -> StreamingResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_export")

    artifacts = await asyncio.to_thread(runtime.storage.list_memory_export_artifacts)
    export_path = await asyncio.to_thread(
        write_memory_export_zip,
        artifacts=artifacts,
        session_retention_days=runtime.settings.backend_session_memory_retention_days,
    )
    filename = f"portworld-memory-export-{now_ms()}.zip"
    return StreamingResponse(
        content=_iter_file_chunks(str(export_path)),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
        background=BackgroundTask(cleanup_export_file, export_path),
    )


@router.post(
    "/memory/sessions/{session_id}/reset",
    response_model=SessionMemoryResetResponse,
)
async def reset_session_memory(
    request: Request,
    session_id: SessionIdPath,
) -> SessionMemoryResetResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_session_reset")

    eligibility = await asyncio.to_thread(
        runtime.storage.get_session_memory_reset_eligibility,
        session_id=session_id,
    )
    if eligibility.is_active:
        raise HTTPException(
            status_code=409,
            detail="Cannot reset memory for an active session.",
        )
    if not eligibility.has_persisted_memory:
        raise HTTPException(
            status_code=404,
            detail="Session memory not found.",
        )

    try:
        result = await asyncio.to_thread(
            runtime.storage.reset_session_memory,
            session_id=session_id,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail="Cannot reset memory for an active session.",
        ) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Session memory not found.",
        ) from exc
    return SessionMemoryResetResponse(
        status="ok",
        session_id=result.session_id,
        deleted_artifact_rows=result.deleted_artifact_rows,
        deleted_vision_frame_rows=result.deleted_vision_frame_rows,
        deleted_session_rows=result.deleted_session_rows,
        removed_session_dir=result.removed_session_dir,
        removed_vision_frames_dir=result.removed_vision_frames_dir,
    )


@router.get("/memory/items", response_model=MemoryItemListResponse)
async def list_memory_items(
    request: Request,
    scope: str | None = Query(default=None),
    memory_class: str | None = Query(default=None),
    status: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=0),
) -> MemoryItemListResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_items_list")
    repository = MemoryRepositoryV2(storage=runtime.storage)
    items = await asyncio.to_thread(
        repository.list_items,
        scope=scope,
        memory_class=memory_class,
        status=status,
        tag=tag,
        session_id=session_id,
        limit=limit,
    )
    return MemoryItemListResponse(
        count=len(items),
        items=[_serialize_item(item) for item in items],
    )


@router.get("/memory/items/{item_id}", response_model=MemoryItemLookupResponse)
async def get_memory_item(
    request: Request,
    item_id: ItemIdPath,
) -> MemoryItemLookupResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_items_get")
    repository = MemoryRepositoryV2(storage=runtime.storage)
    item = await asyncio.to_thread(repository.get_item, item_id=item_id)
    if item is None:
        return MemoryItemLookupResponse(found=False, item=None)
    return MemoryItemLookupResponse(found=True, item=_serialize_item(item))


@router.get("/memory/items/{item_id}/evidence", response_model=MemoryItemEvidenceResponse)
async def get_memory_item_evidence(
    request: Request,
    item_id: ItemIdPath,
) -> MemoryItemEvidenceResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_items_evidence")
    repository = MemoryRepositoryV2(storage=runtime.storage)
    evidence = await asyncio.to_thread(repository.list_item_evidence, item_id=item_id)
    return MemoryItemEvidenceResponse(
        item_id=item_id,
        count=len(evidence),
        evidence=[_serialize_evidence(record) for record in evidence],
    )


@router.patch("/memory/items/{item_id}", response_model=MemoryItemLookupResponse)
async def patch_memory_item(
    request: Request,
    item_id: ItemIdPath,
    payload: MemoryItemUpdatePayload,
) -> MemoryItemLookupResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_items_patch")
    repository = MemoryRepositoryV2(storage=runtime.storage)
    item = await asyncio.to_thread(
        repository.correct_item,
        item_id=item_id,
        summary=payload.summary,
        structured_value=payload.structured_value,
        confidence=payload.confidence,
        relevance=payload.relevance,
        maturity=payload.maturity,
        tags=payload.tags,
        correction_note=payload.correction_note,
        session_id=payload.session_id,
        status=payload.status,
    )
    if item is None:
        return MemoryItemLookupResponse(found=False, item=None)
    return MemoryItemLookupResponse(found=True, item=_serialize_item(item))


@router.post("/memory/items/{item_id}/suppress", response_model=MemoryItemLookupResponse)
async def suppress_memory_item(
    request: Request,
    item_id: ItemIdPath,
    payload: MemoryItemSuppressPayload,
) -> MemoryItemLookupResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_items_suppress")
    repository = MemoryRepositoryV2(storage=runtime.storage)
    item = await asyncio.to_thread(repository.suppress_item, item_id=item_id, note=payload.note)
    if item is None:
        return MemoryItemLookupResponse(found=False, item=None)
    return MemoryItemLookupResponse(found=True, item=_serialize_item(item))


@router.delete("/memory/items/{item_id}")
async def delete_memory_item(
    request: Request,
    item_id: ItemIdPath,
) -> dict[str, object]:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_items_delete")
    repository = MemoryRepositoryV2(storage=runtime.storage)
    deleted = await asyncio.to_thread(repository.delete_item, item_id=item_id)
    return {"item_id": item_id, "deleted": bool(deleted)}


@router.get("/memory/conflicts", response_model=MemoryConflictGroupListResponse)
async def list_memory_conflicts(request: Request) -> MemoryConflictGroupListResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_conflicts_list")
    repository = MemoryRepositoryV2(storage=runtime.storage)
    groups = await asyncio.to_thread(repository.list_conflict_groups)
    return MemoryConflictGroupListResponse(
        count=len(groups),
        groups=[group.to_dict() for group in groups],
    )


@router.get("/memory/conflicts/{group_key}", response_model=MemoryConflictGroupLookupResponse)
async def get_memory_conflict_group(
    request: Request,
    group_key: ConflictGroupKeyPath,
) -> MemoryConflictGroupLookupResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_conflicts_get")
    repository = MemoryRepositoryV2(storage=runtime.storage)
    group = await asyncio.to_thread(repository.get_conflict_group, group_key=group_key)
    if group is None:
        return MemoryConflictGroupLookupResponse(found=False, group=None)
    return MemoryConflictGroupLookupResponse(found=True, group=group.to_dict())


@router.post("/memory/conflicts/merge", response_model=MemoryConflictMergeResponse)
async def merge_memory_conflict_items(
    request: Request,
    payload: MemoryConflictMergePayload,
) -> MemoryConflictMergeResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_conflicts_merge")
    repository = MemoryRepositoryV2(storage=runtime.storage)
    actor = (payload.actor or "operator").strip() or "operator"
    reason = (payload.reason or "manual_merge").strip() or "manual_merge"
    try:
        result = await asyncio.to_thread(
            repository.merge_items,
            target_item_id=payload.target_item_id,
            source_item_id=payload.source_item_id,
            actor=actor,
            reason=reason,
            merged_at_ms=payload.merged_at_ms,
            suppress_source=payload.suppress_source,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return MemoryConflictMergeResponse(
        merged=True,
        target_item=_serialize_item(result["target_item"]),
        source_item=_serialize_item(result["source_item"]),
        merge_event=dict(result["merge_event"]),
    )


@router.post("/memory/conflicts/{item_id}/suppress", response_model=MemoryItemLookupResponse)
async def suppress_memory_conflict_side(
    request: Request,
    item_id: ItemIdPath,
    payload: MemoryConflictSuppressPayload,
) -> MemoryItemLookupResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_conflicts_suppress")
    repository = MemoryRepositoryV2(storage=runtime.storage)
    actor = (payload.actor or "operator").strip() or "operator"
    reason = (payload.reason or "manual_conflict_suppress").strip() or "manual_conflict_suppress"
    item = await asyncio.to_thread(
        repository.suppress_conflict_side,
        item_id=item_id,
        actor=actor,
        reason=reason,
        updated_at_ms=payload.updated_at_ms,
    )
    if item is None:
        return MemoryItemLookupResponse(found=False, item=None)
    return MemoryItemLookupResponse(found=True, item=_serialize_item(item))


@router.get("/memory/items/{item_id}/audit-trail", response_model=MemoryItemAuditTrailResponse)
async def get_memory_item_audit_trail(
    request: Request,
    item_id: ItemIdPath,
) -> MemoryItemAuditTrailResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_items_audit_trail")
    repository = MemoryRepositoryV2(storage=runtime.storage)
    audit_trail = await asyncio.to_thread(repository.build_item_audit_trail, item_id=item_id)
    if audit_trail is None:
        return MemoryItemAuditTrailResponse(found=False, audit_trail=None)
    return MemoryItemAuditTrailResponse(found=True, audit_trail=dict(audit_trail))


@router.get("/memory/sessions/{session_id}/status")
async def session_memory_status(request: Request, session_id: SessionIdPath) -> dict[str, object]:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_session_status")
    try:
        return await asyncio.to_thread(
            runtime.storage.read_session_memory_status,
            session_id=session_id,
        )
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Session memory not found.",
        ) from exc


@router.get("/memory/maintenance/state", response_model=MemoryMaintenanceStateResponse)
async def get_memory_maintenance_state(request: Request) -> MemoryMaintenanceStateResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_maintenance_state")
    repository = MemoryRepositoryV2(storage=runtime.storage)
    maintenance_state = await asyncio.to_thread(repository.read_maintenance_state)
    return MemoryMaintenanceStateResponse(
        maintenance=summarize_recent_maintenance(maintenance_state),
    )


@router.get("/memory/live-bundle", response_model=MemoryLiveBundleResponse)
async def get_memory_live_bundle(
    request: Request,
    session_id: str | None = Query(default=None),
    query_text: str | None = Query(default=None),
    intention_text: str | None = Query(default=None),
    memory_classes: list[str] | None = Query(default=None),
    statuses: list[str] | None = Query(default=None),
    limit: int | None = Query(default=None, ge=0),
    evidence_limit_per_item: int | None = Query(default=None, ge=0),
) -> MemoryLiveBundleResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_live_bundle")
    repository = MemoryRepositoryV2(storage=runtime.storage)
    service = MemoryRetrievalServiceV2(repository=repository)
    bundle = await asyncio.to_thread(
        service.build_live_bundle,
        request=LiveMemoryBundleRequest(
            session_id=session_id,
            query_text=query_text,
            intention_text=intention_text,
            memory_classes=tuple(memory_classes or ()),
            statuses=tuple(statuses or ()),
            limit=limit,
            evidence_limit_per_item=evidence_limit_per_item,
        ),
    )
    return MemoryLiveBundleResponse(bundle=bundle.to_dict())

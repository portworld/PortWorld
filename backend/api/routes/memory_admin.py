from __future__ import annotations

import asyncio
from collections.abc import Generator

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from backend.bootstrap.memory_export import cleanup_export_file, write_memory_export_zip
from backend.core.auth import require_http_bearer_auth
from backend.core.runtime import get_app_runtime
from backend.core.storage import SessionNotFoundError, now_ms

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


@router.get("/memory/export")
async def export_memory(request: Request) -> StreamingResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)

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
    "/memory/session/{session_id}/reset",
    response_model=SessionMemoryResetResponse,
)
async def reset_session_memory(
    request: Request,
    session_id: str,
) -> SessionMemoryResetResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)

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


@router.get("/memory/session/{session_id}/status")
async def session_memory_status(request: Request, session_id: str) -> dict[str, object]:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
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

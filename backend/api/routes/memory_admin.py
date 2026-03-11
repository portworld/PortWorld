from __future__ import annotations

import json
import os
import tempfile
import zipfile
from dataclasses import asdict

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from backend.core.auth import require_http_bearer_auth
from backend.core.runtime import get_app_runtime
from backend.core.storage import MemoryExportArtifact
from backend.core.storage import now_ms
from backend.memory.lifecycle import MemoryExportManifest

router = APIRouter()


def _build_export_manifest(
    *,
    artifacts: list[MemoryExportArtifact],
    session_retention_days: int,
) -> MemoryExportManifest:
    session_ids = tuple(
        sorted(
            {
                artifact.session_id
                for artifact in artifacts
                if artifact.session_id is not None
            }
        )
    )
    included_artifact_kinds = tuple(
        sorted({artifact.artifact_kind for artifact in artifacts})
    )
    return MemoryExportManifest(
        exported_at_ms=now_ms(),
        session_retention_days=session_retention_days,
        session_ids=session_ids,
        included_artifact_kinds=included_artifact_kinds,
    )


def _write_export_zip(
    *,
    artifacts: list[MemoryExportArtifact],
    session_retention_days: int,
) -> str:
    manifest = _build_export_manifest(
        artifacts=artifacts,
        session_retention_days=session_retention_days,
    )
    with tempfile.NamedTemporaryFile(
        prefix="portworld-memory-export-",
        suffix=".zip",
        delete=False,
    ) as handle:
        export_path = handle.name
    try:
        with zipfile.ZipFile(export_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for artifact in artifacts:
                archive.write(artifact.absolute_path, arcname=artifact.relative_path)
            archive.writestr(
                "manifest.json",
                json.dumps(asdict(manifest), ensure_ascii=True, indent=2) + "\n",
            )
    except Exception:
        _cleanup_export_file(export_path)
        raise
    return export_path


def _iter_file_chunks(path: str, *, chunk_size: int = 64 * 1024):
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            yield chunk


def _cleanup_export_file(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        return


@router.get("/memory/export")
async def export_memory(request: Request) -> StreamingResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)

    artifacts = runtime.storage.list_memory_export_artifacts()
    export_path = _write_export_zip(
        artifacts=artifacts,
        session_retention_days=runtime.settings.backend_session_memory_retention_days,
    )
    filename = f"portworld-memory-export-{now_ms()}.zip"
    return StreamingResponse(
        content=_iter_file_chunks(export_path),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
        background=BackgroundTask(_cleanup_export_file, export_path),
    )


@router.post("/memory/session/{session_id}/reset")
async def reset_session_memory(request: Request, session_id: str) -> dict[str, object]:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)

    eligibility = runtime.storage.get_session_memory_reset_eligibility(session_id=session_id)
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
        result = runtime.storage.reset_session_memory(session_id=session_id)
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
    return {
        "status": "ok",
        "session_id": result.session_id,
        "deleted_artifact_rows": result.deleted_artifact_rows,
        "deleted_vision_frame_rows": result.deleted_vision_frame_rows,
        "deleted_session_rows": result.deleted_session_rows,
        "removed_session_dir": result.removed_session_dir,
        "removed_vision_frames_dir": result.removed_vision_frames_dir,
    }


@router.get("/memory/session/{session_id}/status")
async def session_memory_status(request: Request, session_id: str) -> dict[str, object]:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    return runtime.storage.read_session_memory_status(session_id=session_id)

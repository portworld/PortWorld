from __future__ import annotations

import base64
import binascii
import json
import logging
import re

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from pydantic import BaseModel, Field, field_validator

from backend.core.runtime import get_app_runtime
from backend.vision.contracts import VisionFrameContext

router = APIRouter()
logger = logging.getLogger(__name__)


def _sanitize_path_component(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return sanitized or "unknown"


class VisionFramePayload(BaseModel):
    session_id: str = Field(min_length=1)
    ts_ms: int
    frame_id: str = Field(min_length=1)
    capture_ts_ms: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frame_b64: str = Field(min_length=1)

    @field_validator("frame_b64")
    @classmethod
    def validate_frame_b64(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("frame_b64 must not be empty")
        return value


def _decode_frame_bytes(frame_b64: str) -> bytes:
    try:
        frame_bytes = base64.b64decode(frame_b64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid frame_b64 payload: {exc}") from exc

    if not frame_bytes:
        raise HTTPException(status_code=400, detail="Decoded frame payload is empty.")

    return frame_bytes


@router.post("/vision/frame")
async def vision_frame(request: Request, payload: VisionFramePayload) -> dict[str, str]:
    runtime = get_app_runtime(request.app)
    frame_bytes = _decode_frame_bytes(payload.frame_b64)
    runtime.storage.ensure_session_storage(session_id=payload.session_id)

    session_dir = (
        runtime.storage_paths.vision_frames_root
        / _sanitize_path_component(payload.session_id)
    )
    session_dir.mkdir(parents=True, exist_ok=True)

    file_stem = _sanitize_path_component(payload.frame_id)
    frame_path = session_dir / f"{file_stem}.jpg"
    metadata_path = session_dir / f"{file_stem}.json"
    artifact_metadata = {
        "session_id": payload.session_id,
        "frame_id": payload.frame_id,
        "ts_ms": payload.ts_ms,
        "capture_ts_ms": payload.capture_ts_ms,
        "width": payload.width,
        "height": payload.height,
        "stored_bytes": len(frame_bytes),
    }

    frame_path.write_bytes(frame_bytes)
    metadata_path.write_text(
        json.dumps(
            {
                **artifact_metadata,
                "stored_path": str(frame_path),
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    runtime.storage.register_artifact(
        artifact_id=f"{payload.session_id}:vision_frame_jpeg:{payload.frame_id}",
        session_id=payload.session_id,
        artifact_kind="vision_frame_jpeg",
        artifact_path=frame_path,
        content_type="image/jpeg",
        metadata=artifact_metadata,
    )
    runtime.storage.register_artifact(
        artifact_id=f"{payload.session_id}:vision_frame_metadata:{payload.frame_id}",
        session_id=payload.session_id,
        artifact_kind="vision_frame_metadata",
        artifact_path=metadata_path,
        content_type="application/json",
        metadata=artifact_metadata,
    )
    runtime.storage.record_vision_frame_ingest(
        session_id=payload.session_id,
        frame_id=payload.frame_id,
        capture_ts_ms=payload.capture_ts_ms,
        width=payload.width,
        height=payload.height,
    )

    logger.info(
        "Vision frame stored session=%s frame=%s bytes=%s size=%sx%s",
        payload.session_id,
        payload.frame_id,
        len(frame_bytes),
        payload.width,
        payload.height,
    )
    if runtime.vision_memory_runtime is not None:
        await runtime.vision_memory_runtime.submit_frame(
            image_bytes=frame_bytes,
            frame_context=VisionFrameContext(
                frame_id=payload.frame_id,
                session_id=payload.session_id,
                capture_ts_ms=payload.capture_ts_ms,
                width=payload.width,
                height=payload.height,
            ),
            image_media_type="image/jpeg",
        )

    return {"status": "ok", "frame_id": payload.frame_id}

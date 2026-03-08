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

    session_dir = (
        runtime.storage_paths.vision_frames_root
        / _sanitize_path_component(payload.session_id)
    )
    session_dir.mkdir(parents=True, exist_ok=True)

    file_stem = _sanitize_path_component(payload.frame_id)
    frame_path = session_dir / f"{file_stem}.jpg"
    metadata_path = session_dir / f"{file_stem}.json"

    frame_path.write_bytes(frame_bytes)
    metadata_path.write_text(
        json.dumps(
            {
                "session_id": payload.session_id,
                "frame_id": payload.frame_id,
                "ts_ms": payload.ts_ms,
                "capture_ts_ms": payload.capture_ts_ms,
                "width": payload.width,
                "height": payload.height,
                "stored_path": str(frame_path),
                "stored_bytes": len(frame_bytes),
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    logger.info(
        "Vision frame stored session=%s frame=%s bytes=%s size=%sx%s",
        payload.session_id,
        payload.frame_id,
        len(frame_bytes),
        payload.width,
        payload.height,
    )

    return {"status": "ok", "frame_id": payload.frame_id}

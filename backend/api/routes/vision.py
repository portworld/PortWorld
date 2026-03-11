from __future__ import annotations

import base64
import binascii
import json
import logging
from math import ceil

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from pydantic import BaseModel, Field, field_validator

from backend.core.auth import require_http_bearer_auth
from backend.core.runtime import get_app_runtime
from backend.vision.contracts import VisionFrameContext

router = APIRouter()
logger = logging.getLogger(__name__)


def _client_ip_from_request(request: Request) -> str:
    client = request.client
    if client is None:
        return "unknown"
    host = (client.host or "").strip()
    return host or "unknown"


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


def _estimate_decoded_frame_bytes(frame_b64: str) -> int:
    normalized = frame_b64.strip()
    if not normalized:
        return 0
    padding_chars = min(2, len(normalized) - len(normalized.rstrip("=")))
    return max(0, (ceil(len(normalized) / 4) * 3) - padding_chars)


@router.post("/vision/frame")
async def vision_frame(request: Request, payload: VisionFramePayload) -> dict[str, str]:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    client_ip = _client_ip_from_request(request)
    ingest_rate_decision = await runtime.limit_vision_frame_ingest(
        client_ip=client_ip,
        session_id=payload.session_id,
    )
    if not ingest_rate_decision.allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                "Vision ingest rate limit exceeded "
                f"for {ingest_rate_decision.scope}."
            ),
            headers={"Retry-After": str(ingest_rate_decision.retry_after_seconds)},
        )
    estimated_frame_bytes = _estimate_decoded_frame_bytes(payload.frame_b64)
    if estimated_frame_bytes > runtime.settings.backend_max_vision_frame_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                "Decoded vision frame exceeds "
                f"BACKEND_MAX_VISION_FRAME_BYTES={runtime.settings.backend_max_vision_frame_bytes}"
            ),
        )
    frame_bytes = _decode_frame_bytes(payload.frame_b64)
    if len(frame_bytes) > runtime.settings.backend_max_vision_frame_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                "Decoded vision frame exceeds "
                f"BACKEND_MAX_VISION_FRAME_BYTES={runtime.settings.backend_max_vision_frame_bytes}"
            ),
        )
    runtime.storage.ensure_session_storage(session_id=payload.session_id)
    frame_path, metadata_path = runtime.storage.vision_frame_artifact_paths(
        session_id=payload.session_id,
        frame_id=payload.frame_id,
    )
    frame_path.parent.mkdir(parents=True, exist_ok=True)
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

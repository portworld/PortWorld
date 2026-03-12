from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from math import ceil

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from pydantic import BaseModel, Field, field_validator

from backend.core.auth import require_http_bearer_auth
from backend.core.http import client_ip_from_connection
from backend.core.runtime import get_app_runtime
from backend.vision.contracts import VisionFrameContext

router = APIRouter()
logger = logging.getLogger(__name__)


class VisionFrameResponse(BaseModel):
    status: str
    frame_id: str


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


def _reject_frame_if_oversized(frame_size_bytes: int, max_bytes: int) -> None:
    if frame_size_bytes > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                "Decoded vision frame exceeds "
                f"BACKEND_MAX_VISION_FRAME_BYTES={max_bytes}"
            ),
        )


@router.post("/vision/frame", response_model=VisionFrameResponse)
async def vision_frame(request: Request, payload: VisionFramePayload) -> VisionFrameResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    client_ip = client_ip_from_connection(request)
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
    _reject_frame_if_oversized(
        frame_size_bytes=estimated_frame_bytes,
        max_bytes=runtime.settings.backend_max_vision_frame_bytes,
    )
    frame_bytes = _decode_frame_bytes(payload.frame_b64)
    _reject_frame_if_oversized(
        frame_size_bytes=len(frame_bytes),
        max_bytes=runtime.settings.backend_max_vision_frame_bytes,
    )
    ingest_result = await asyncio.to_thread(
        runtime.storage.store_vision_frame_ingest,
        session_id=payload.session_id,
        frame_id=payload.frame_id,
        ts_ms=payload.ts_ms,
        capture_ts_ms=payload.capture_ts_ms,
        width=payload.width,
        height=payload.height,
        frame_bytes=frame_bytes,
    )

    logger.debug(
        "Vision frame stored session=%s frame=%s bytes=%s size=%sx%s",
        payload.session_id,
        payload.frame_id,
        ingest_result.stored_bytes,
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

    return VisionFrameResponse(status="ok", frame_id=payload.frame_id)

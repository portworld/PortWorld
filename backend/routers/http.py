from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.config import settings

router = APIRouter()


class VisionFramePayload(BaseModel):
    frame_id: str | None = None


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "loopa-mock-backend",
        "model": settings.openai_realtime_model,
        "ws_path": "/ws/session",
    }


@router.post("/vision/frame")
async def vision_frame(payload: VisionFramePayload) -> dict[str, str | None]:
    return {"status": "ok", "frame_id": payload.frame_id}

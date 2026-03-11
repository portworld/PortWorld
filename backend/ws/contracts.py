from __future__ import annotations

from time import time_ns
from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


PayloadDict: TypeAlias = dict[str, Any]


class IOSEnvelope(BaseModel):
    """Canonical JSON envelope exchanged with the iOS client."""

    type: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    seq: int = Field(ge=0)
    ts_ms: int
    payload: PayloadDict = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


def now_ms() -> int:
    """Return Unix time in milliseconds."""

    return time_ns() // 1_000_000


def make_envelope(
    message_type: str,
    session_id: str,
    seq: int,
    payload: PayloadDict | None,
) -> IOSEnvelope:
    """Construct and validate an iOS envelope with a fresh timestamp."""

    return IOSEnvelope(
        type=message_type,
        session_id=session_id,
        seq=seq,
        ts_ms=now_ms(),
        payload=payload or {},
    )

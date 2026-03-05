from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass(slots=True)
class PayloadBuilder:
    """Small mutable helper for assembling payload dictionaries in handlers/tests."""

    data: PayloadDict = field(default_factory=dict)

    def put(self, key: str, value: Any) -> "PayloadBuilder":
        self.data[key] = value
        return self

    def extend(self, values: PayloadDict) -> "PayloadBuilder":
        self.data.update(values)
        return self

    def to_dict(self) -> PayloadDict:
        return dict(self.data)


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

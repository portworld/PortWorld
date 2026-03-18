from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

EnvelopeSender = Callable[[str, dict[str, Any]], Awaitable[None]]
BinarySender = Callable[[int, int, bytes], Awaitable[None]]

__all__ = ["BinarySender", "EnvelopeSender"]

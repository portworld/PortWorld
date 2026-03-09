from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProviderError(Exception):
    """Base error for realtime web-search providers."""


class SearchProviderTimeoutError(SearchProviderError):
    """Raised when the provider times out."""


class SearchProvider(Protocol):
    async def startup(self) -> None: ...

    async def shutdown(self) -> None: ...

    async def search(self, *, query: str, max_results: int) -> list[SearchResult]: ...

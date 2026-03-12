from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from backend.tools.search import SearchProviderError, SearchProviderTimeoutError, SearchResult

logger = logging.getLogger(__name__)


DEFAULT_TAVILY_BASE_URL = "https://api.tavily.com"


@dataclass(slots=True)
class TavilySearchProvider:
    api_key: str
    timeout_ms: int
    base_url: str | None = None
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    async def startup(self) -> None:
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(
            base_url=(self.base_url or DEFAULT_TAVILY_BASE_URL).rstrip("/"),
            timeout=max(0.1, self.timeout_ms / 1000.0),
            headers={"Content-Type": "application/json"},
        )

    async def shutdown(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

    async def search(self, *, query: str, max_results: int) -> list[SearchResult]:
        client = self._client
        if client is None:
            raise SearchProviderError("Tavily client is not started")

        try:
            response = await client.post(
                "/search",
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": max_results,
                    "include_answer": False,
                    "include_raw_content": False,
                },
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            logger.warning("Tavily search timed out", exc_info=exc)
            raise SearchProviderTimeoutError("Tavily search timed out") from exc
        except httpx.HTTPError as exc:
            logger.warning("Tavily search HTTP request failed", exc_info=exc)
            raise SearchProviderError("Tavily search request failed") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning("Tavily returned invalid JSON payload", exc_info=exc)
            raise SearchProviderError("Tavily response was not valid JSON") from exc

        raw_results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(raw_results, list):
            raise SearchProviderError("Tavily response missing results list")

        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            url = item.get("url")
            snippet = item.get("content")
            if not isinstance(title, str) or not isinstance(url, str):
                continue
            if not isinstance(snippet, str):
                snippet = ""
            results.append(SearchResult(title=title, url=url, snippet=snippet))
        return results

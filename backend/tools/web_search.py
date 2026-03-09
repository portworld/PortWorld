from __future__ import annotations

from dataclasses import dataclass

from backend.tools.contracts import ToolCall, ToolResult
from backend.tools.search import SearchProvider, SearchProviderError, SearchProviderTimeoutError


@dataclass(frozen=True, slots=True)
class WebSearchToolExecutor:
    provider: SearchProvider
    provider_name: str
    max_results: int

    async def __call__(self, call: ToolCall) -> ToolResult:
        query = call.arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(
                ok=False,
                name=call.name,
                call_id=call.call_id,
                payload={
                    "query": "",
                    "provider": self.provider_name,
                    "results": [],
                },
                error_code="INVALID_TOOL_ARGUMENTS",
                error_message="web_search requires a non-empty string query",
            )

        normalized_query = query.strip()
        try:
            results = await self.provider.search(
                query=normalized_query,
                max_results=self.max_results,
            )
        except SearchProviderTimeoutError as exc:
            return ToolResult(
                ok=False,
                name=call.name,
                call_id=call.call_id,
                payload={
                    "query": normalized_query,
                    "provider": self.provider_name,
                    "results": [],
                },
                error_code="WEB_SEARCH_TIMEOUT",
                error_message=str(exc),
            )
        except SearchProviderError as exc:
            return ToolResult(
                ok=False,
                name=call.name,
                call_id=call.call_id,
                payload={
                    "query": normalized_query,
                    "provider": self.provider_name,
                    "results": [],
                },
                error_code="WEB_SEARCH_FAILED",
                error_message=str(exc),
            )

        return ToolResult(
            ok=True,
            name=call.name,
            call_id=call.call_id,
            payload={
                "query": normalized_query,
                "provider": self.provider_name,
                "results": [
                    {
                        "title": result.title,
                        "url": result.url,
                        "snippet": result.snippet,
                    }
                    for result in results
                ],
            },
        )

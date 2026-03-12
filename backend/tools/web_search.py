from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.tools.contracts import ToolCall, ToolResult
from backend.tools.search import SearchProvider, SearchProviderError, SearchProviderTimeoutError

logger = logging.getLogger(__name__)
_MAX_WEB_SEARCH_RESULTS = 5


@dataclass(frozen=True, slots=True)
class WebSearchToolExecutor:
    provider: SearchProvider
    provider_name: str
    max_results: int

    async def __call__(self, call: ToolCall) -> ToolResult:
        query = call.arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return self._error_result(
                call=call,
                query="",
                error_code="INVALID_TOOL_ARGUMENTS",
                error_message="web_search requires a non-empty string query",
            )

        normalized_query = query.strip()
        max_results = max(1, min(self.max_results, _MAX_WEB_SEARCH_RESULTS))
        try:
            results = await self.provider.search(
                query=normalized_query,
                max_results=max_results,
            )
        except SearchProviderTimeoutError as exc:
            logger.warning(
                "Web search provider timed out call_id=%s provider=%s",
                call.call_id,
                self.provider_name,
                exc_info=exc,
            )
            return self._error_result(
                call=call,
                query=normalized_query,
                error_code="WEB_SEARCH_TIMEOUT",
                error_message="Web search provider timed out",
            )
        except SearchProviderError as exc:
            logger.warning(
                "Web search provider failed call_id=%s provider=%s",
                call.call_id,
                self.provider_name,
                exc_info=exc,
            )
            return self._error_result(
                call=call,
                query=normalized_query,
                error_code="WEB_SEARCH_FAILED",
                error_message="Web search provider request failed",
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

    def _error_result(
        self,
        *,
        call: ToolCall,
        query: str,
        error_code: str,
        error_message: str,
    ) -> ToolResult:
        return ToolResult(
            ok=False,
            name=call.name,
            call_id=call.call_id,
            payload={
                "query": query,
                "provider": self.provider_name,
                "results": [],
            },
            error_code=error_code,
            error_message=error_message,
        )

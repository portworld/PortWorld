from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from backend.memory.normalization_v2 import normalize_semantic_key, normalize_tags

_DEFAULT_QUERY_TOKEN_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "any",
        "are",
        "around",
        "at",
        "do",
        "for",
        "from",
        "have",
        "i",
        "in",
        "is",
        "it",
        "me",
        "most",
        "my",
        "now",
        "of",
        "on",
        "remember",
        "show",
        "tell",
        "that",
        "the",
        "there",
        "to",
        "useful",
        "what",
        "with",
    }
)


@dataclass(frozen=True, slots=True)
class RetrievalPolicyV2:
    default_bundle_limit: int = 8
    default_evidence_limit_per_item: int = 3
    max_bundle_limit: int = 12
    max_evidence_limit_per_item: int = 5
    query_token_stopwords: frozenset[str] = _DEFAULT_QUERY_TOKEN_STOPWORDS
    live_bundle_prefers_durable_queries: bool = True
    conflict_penalty: float = 0.18
    query_match_bonus: float = 0.22
    class_match_bonus: float = 0.12
    tag_match_bonus: float = 0.1
    session_affinity_bonus: float = 0.12
    recency_bonus_fresh: float = 0.08
    recency_bonus_recent: float = 0.04

    def clamp_limit(self, value: int | None) -> int:
        if value is None:
            return self.default_bundle_limit
        return max(0, min(int(value), self.max_bundle_limit))

    def clamp_evidence_limit(self, value: int | None) -> int:
        if value is None:
            return self.default_evidence_limit_per_item
        return max(0, min(int(value), self.max_evidence_limit_per_item))

    def normalize_query_tokens(self, query: str | None) -> tuple[str, ...]:
        if not query:
            return ()
        tokens: list[str] = []
        seen: set[str] = set()
        for raw in query.split():
            token = normalize_semantic_key(raw)
            if not token or token in self.query_token_stopwords or token in seen:
                continue
            seen.add(token)
            tokens.append(token)
        return tuple(tokens)

    def normalize_query_tags(self, query: str | None) -> tuple[str, ...]:
        return tuple(normalize_tags(self.normalize_query_tokens(query)))

    def should_prefer_live_bundle(
        self,
        *,
        user_intent: str | None = None,
        mentions_visual_recency: bool = False,
        requests_raw_markdown: bool = False,
    ) -> bool:
        if requests_raw_markdown or mentions_visual_recency:
            return False
        if not self.live_bundle_prefers_durable_queries:
            return False
        if not user_intent:
            return True
        tokens = set(self.normalize_query_tokens(user_intent))
        if not tokens:
            return True
        markdown_bias_terms = {"markdown", "raw", "export", "dump"}
        return tokens.isdisjoint(markdown_bias_terms)


def build_default_retrieval_policy() -> RetrievalPolicyV2:
    return RetrievalPolicyV2()


__all__ = [
    "RetrievalPolicyV2",
    "build_default_retrieval_policy",
]

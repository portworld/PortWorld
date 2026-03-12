from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from backend.memory.lifecycle import DEFAULT_SESSION_MEMORY_RETENTION_DAYS

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_ENV_PATH = _BACKEND_ROOT / ".env"


DEFAULT_INSTRUCTIONS = "You are a concise assistant. Keep answers short, clear, and practical."


class MissingOpenAIAPIKeyError(RuntimeError):
    pass


def load_environment_files() -> None:
    load_dotenv(dotenv_path=_BACKEND_ENV_PATH)
    load_dotenv()


def _get_env(*names: str) -> str | None:
    for name in names:
        raw = os.getenv(name)
        if raw is not None:
            return raw
    return None


def _parse_bool_env(*names: str, default: bool) -> bool:
    raw = _get_env(*names)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_int_env(*names: str, default: int, minimum: int | None = None) -> int:
    raw = _get_env(*names)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw.strip())
        except ValueError:
            value = default

    if minimum is not None and value < minimum:
        return minimum
    return value


def _parse_csv_env(*names: str, default: str) -> list[str]:
    raw = _get_env(*names) or default
    values = [value.strip() for value in raw.split(",") if value.strip()]
    if values:
        return values
    return [default]


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    mistral_api_key: str | None
    mistral_base_url: str | None
    vision_provider_api_key: str | None
    vision_provider_base_url: str | None
    tavily_api_key: str | None
    tavily_base_url: str | None
    backend_bearer_token: str | None
    realtime_provider: str
    openai_realtime_model: str
    openai_realtime_voice: str
    openai_realtime_instructions: str
    openai_realtime_include_turn_detection: bool
    openai_realtime_enable_manual_turn_fallback: bool
    openai_realtime_manual_turn_fallback_delay_ms: int
    backend_uplink_ack_every_n_frames: int
    backend_data_dir: Path
    backend_sqlite_path: Path
    backend_debug_trace_ws_messages: bool
    backend_max_vision_request_bytes: int
    backend_max_vision_frame_bytes: int
    backend_session_memory_retention_days: int
    vision_memory_enabled: bool
    vision_memory_provider: str
    vision_memory_model: str
    vision_short_term_window_seconds: int
    vision_min_analysis_gap_seconds: int
    vision_scene_change_hamming_threshold: int
    vision_provider_max_rps: int
    vision_analysis_heartbeat_seconds: int
    vision_provider_backoff_initial_seconds: int
    vision_provider_backoff_max_seconds: int
    vision_deferred_candidate_ttl_seconds: int
    vision_session_rollup_interval_seconds: int
    vision_session_rollup_min_accepted_events: int
    vision_debug_retain_raw_frames: bool
    realtime_tooling_enabled: bool
    realtime_tool_timeout_ms: int
    realtime_web_search_provider: str
    realtime_web_search_max_results: int
    backend_profile: str
    backend_allowed_hosts: list[str]
    backend_forwarded_allow_ips: list[str]
    backend_enable_ip_rate_limits: bool
    backend_rate_limit_ws_ip_max_attempts: int
    backend_rate_limit_ws_session_max_attempts: int
    backend_rate_limit_ws_window_seconds: int
    backend_rate_limit_vision_ip_max_requests: int
    backend_rate_limit_vision_session_max_requests: int
    backend_rate_limit_vision_window_seconds: int
    host: str
    port: int
    log_level: str
    cors_origins: list[str]

    @classmethod
    def from_env(cls) -> "Settings":
        backend_profile = (_get_env("BACKEND_PROFILE") or "development").strip().lower()
        return cls(
            **_load_credentials_settings(),
            **_load_realtime_settings(),
            **_load_storage_settings(),
            **_load_vision_settings(),
            **_load_tooling_settings(),
            **_load_server_settings(backend_profile=backend_profile),
            **_load_rate_limit_settings(backend_profile=backend_profile),
        )

    @property
    def is_production_profile(self) -> bool:
        return self.backend_profile in {"prod", "production"}

    def validate_production_posture(self) -> None:
        if not self.is_production_profile:
            return
        if not self.backend_bearer_token:
            raise RuntimeError(
                "BACKEND_BEARER_TOKEN must be set when BACKEND_PROFILE=production."
            )
        if self.cors_origins == ["*"]:
            raise RuntimeError(
                "CORS_ORIGINS must be explicit (not '*') when BACKEND_PROFILE=production."
            )
        if self.backend_allowed_hosts == ["*"]:
            raise RuntimeError(
                "BACKEND_ALLOWED_HOSTS must be explicit (not '*') when "
                "BACKEND_PROFILE=production."
            )
        if self.backend_debug_trace_ws_messages:
            raise RuntimeError(
                "BACKEND_DEBUG_TRACE_WS_MESSAGES must be false when "
                "BACKEND_PROFILE=production."
            )

    def require_openai_api_key(self) -> str:
        key = (self.openai_api_key or "").strip()
        if not key:
            raise MissingOpenAIAPIKeyError("OPENAI_API_KEY is required at runtime")
        return key

    def require_mistral_api_key(self) -> str:
        key = (self.mistral_api_key or "").strip()
        if not key:
            raise RuntimeError("MISTRAL_API_KEY is required when VISION_MEMORY_ENABLED=true")
        return key

    def require_vision_provider_api_key(self) -> str:
        key = (self.vision_provider_api_key or "").strip()
        if key:
            return key
        key = (self.mistral_api_key or "").strip()
        if key:
            return key
        raise RuntimeError(
            "VISION_PROVIDER_API_KEY or MISTRAL_API_KEY is required when "
            "VISION_MEMORY_ENABLED=true"
        )

    def has_tavily_api_key(self) -> bool:
        return bool((self.tavily_api_key or "").strip())


def _load_credentials_settings() -> dict[str, str | None]:
    return {
        "openai_api_key": os.getenv("OPENAI_API_KEY"),
        "mistral_api_key": os.getenv("MISTRAL_API_KEY"),
        "mistral_base_url": _get_env("MISTRAL_BASE_URL"),
        "vision_provider_api_key": os.getenv("VISION_PROVIDER_API_KEY"),
        "vision_provider_base_url": _get_env("VISION_PROVIDER_BASE_URL"),
        "tavily_api_key": os.getenv("TAVILY_API_KEY"),
        "tavily_base_url": _get_env("TAVILY_BASE_URL"),
    }


def _load_realtime_settings() -> dict[str, str | int | bool | None]:
    return {
        "backend_bearer_token": (_get_env("BACKEND_BEARER_TOKEN") or "").strip() or None,
        "realtime_provider": (_get_env("REALTIME_PROVIDER") or "openai").strip().lower(),
        "openai_realtime_model": os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime"),
        "openai_realtime_voice": os.getenv("OPENAI_REALTIME_VOICE", "ash"),
        "openai_realtime_instructions": os.getenv(
            "OPENAI_REALTIME_INSTRUCTIONS",
            DEFAULT_INSTRUCTIONS,
        ),
        "openai_realtime_include_turn_detection": _parse_bool_env(
            "OPENAI_REALTIME_INCLUDE_TURN_DETECTION",
            default=True,
        ),
        "openai_realtime_enable_manual_turn_fallback": _parse_bool_env(
            "OPENAI_REALTIME_ENABLE_MANUAL_TURN_FALLBACK",
            default=True,
        ),
        "openai_realtime_manual_turn_fallback_delay_ms": _parse_int_env(
            "OPENAI_REALTIME_MANUAL_TURN_FALLBACK_DELAY_MS",
            default=900,
            minimum=100,
        ),
        "backend_uplink_ack_every_n_frames": _parse_int_env(
            "BACKEND_UPLINK_ACK_EVERY_N_FRAMES",
            default=20,
            minimum=1,
        ),
    }


def _load_storage_settings() -> dict[str, str | int | bool | Path]:
    backend_data_dir = Path(_get_env("BACKEND_DATA_DIR") or "backend/var")
    backend_sqlite_path = Path(
        _get_env("BACKEND_SQLITE_PATH") or str(backend_data_dir / "portworld.db")
    )
    return {
        "backend_data_dir": backend_data_dir,
        "backend_sqlite_path": backend_sqlite_path,
        "backend_debug_trace_ws_messages": _parse_bool_env(
            "BACKEND_DEBUG_TRACE_WS_MESSAGES",
            default=False,
        ),
        "backend_max_vision_request_bytes": _parse_int_env(
            "BACKEND_MAX_VISION_REQUEST_BYTES",
            default=4_000_000,
            minimum=1,
        ),
        "backend_max_vision_frame_bytes": _parse_int_env(
            "BACKEND_MAX_VISION_FRAME_BYTES",
            default=2_500_000,
            minimum=1,
        ),
        "backend_session_memory_retention_days": _parse_int_env(
            "BACKEND_SESSION_MEMORY_RETENTION_DAYS",
            default=DEFAULT_SESSION_MEMORY_RETENTION_DAYS,
            minimum=1,
        ),
    }


def _load_vision_settings() -> dict[str, str | int | bool]:
    return {
        "vision_memory_enabled": _parse_bool_env(
            "VISION_MEMORY_ENABLED",
            default=False,
        ),
        "vision_memory_provider": (_get_env("VISION_MEMORY_PROVIDER") or "mistral").strip().lower(),
        "vision_memory_model": (_get_env("VISION_MEMORY_MODEL") or "ministral-3b-2512").strip(),
        "vision_short_term_window_seconds": _parse_int_env(
            "VISION_SHORT_TERM_WINDOW_SECONDS",
            default=30,
            minimum=1,
        ),
        "vision_min_analysis_gap_seconds": _parse_int_env(
            "VISION_MIN_ANALYSIS_GAP_SECONDS",
            default=3,
            minimum=1,
        ),
        "vision_scene_change_hamming_threshold": _parse_int_env(
            "VISION_SCENE_CHANGE_HAMMING_THRESHOLD",
            default=12,
            minimum=1,
        ),
        "vision_provider_max_rps": _parse_int_env(
            "VISION_PROVIDER_MAX_RPS",
            default=1,
            minimum=1,
        ),
        "vision_analysis_heartbeat_seconds": _parse_int_env(
            "VISION_ANALYSIS_HEARTBEAT_SECONDS",
            default=15,
            minimum=1,
        ),
        "vision_provider_backoff_initial_seconds": _parse_int_env(
            "VISION_PROVIDER_BACKOFF_INITIAL_SECONDS",
            default=5,
            minimum=1,
        ),
        "vision_provider_backoff_max_seconds": _parse_int_env(
            "VISION_PROVIDER_BACKOFF_MAX_SECONDS",
            default=60,
            minimum=1,
        ),
        "vision_deferred_candidate_ttl_seconds": _parse_int_env(
            "VISION_DEFERRED_CANDIDATE_TTL_SECONDS",
            default=10,
            minimum=1,
        ),
        "vision_session_rollup_interval_seconds": _parse_int_env(
            "VISION_SESSION_ROLLUP_INTERVAL_SECONDS",
            default=10,
            minimum=1,
        ),
        "vision_session_rollup_min_accepted_events": _parse_int_env(
            "VISION_SESSION_ROLLUP_MIN_ACCEPTED_EVENTS",
            default=5,
            minimum=1,
        ),
        "vision_debug_retain_raw_frames": _parse_bool_env(
            "VISION_DEBUG_RETAIN_RAW_FRAMES",
            default=False,
        ),
    }


def _load_tooling_settings() -> dict[str, str | int | bool]:
    return {
        "realtime_tooling_enabled": _parse_bool_env(
            "REALTIME_TOOLING_ENABLED",
            default=False,
        ),
        "realtime_tool_timeout_ms": _parse_int_env(
            "REALTIME_TOOL_TIMEOUT_MS",
            default=4000,
            minimum=100,
        ),
        "realtime_web_search_provider": (
            _get_env("REALTIME_WEB_SEARCH_PROVIDER") or "tavily"
        ).strip().lower(),
        "realtime_web_search_max_results": _parse_int_env(
            "REALTIME_WEB_SEARCH_MAX_RESULTS",
            default=3,
            minimum=1,
        ),
    }


def _load_server_settings(*, backend_profile: str) -> dict[str, str | int | list[str]]:
    return {
        "backend_profile": backend_profile,
        "backend_allowed_hosts": _parse_csv_env("BACKEND_ALLOWED_HOSTS", default="*"),
        "backend_forwarded_allow_ips": _parse_csv_env(
            "BACKEND_FORWARDED_ALLOW_IPS",
            default="127.0.0.1,::1",
        ),
        "host": _get_env("HOST") or "0.0.0.0",
        "port": _parse_int_env("PORT", default=8080),
        "log_level": _get_env("LOG_LEVEL") or "INFO",
        "cors_origins": _parse_csv_env("CORS_ORIGINS", default="*"),
    }


def _load_rate_limit_settings(*, backend_profile: str) -> dict[str, int | bool]:
    enable_ip_rate_limits_default = backend_profile in {"prod", "production"}
    return {
        "backend_enable_ip_rate_limits": _parse_bool_env(
            "BACKEND_ENABLE_IP_RATE_LIMITS",
            default=enable_ip_rate_limits_default,
        ),
        "backend_rate_limit_ws_ip_max_attempts": _parse_int_env(
            "BACKEND_RATE_LIMIT_WS_IP_MAX_ATTEMPTS",
            default=30,
            minimum=1,
        ),
        "backend_rate_limit_ws_session_max_attempts": _parse_int_env(
            "BACKEND_RATE_LIMIT_WS_SESSION_MAX_ATTEMPTS",
            default=6,
            minimum=1,
        ),
        "backend_rate_limit_ws_window_seconds": _parse_int_env(
            "BACKEND_RATE_LIMIT_WS_WINDOW_SECONDS",
            default=60,
            minimum=1,
        ),
        "backend_rate_limit_vision_ip_max_requests": _parse_int_env(
            "BACKEND_RATE_LIMIT_VISION_IP_MAX_REQUESTS",
            default=120,
            minimum=1,
        ),
        "backend_rate_limit_vision_session_max_requests": _parse_int_env(
            "BACKEND_RATE_LIMIT_VISION_SESSION_MAX_REQUESTS",
            default=60,
            minimum=1,
        ),
        "backend_rate_limit_vision_window_seconds": _parse_int_env(
            "BACKEND_RATE_LIMIT_VISION_WINDOW_SECONDS",
            default=60,
            minimum=1,
        ),
    }

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from backend.memory.lifecycle import DEFAULT_SESSION_MEMORY_RETENTION_DAYS

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_ENV_PATH = _BACKEND_ROOT / ".env"


DEFAULT_INSTRUCTIONS = "You are a concise assistant. Keep answers short, clear, and practical."
SUPPORTED_STORAGE_BACKENDS = {"local", "postgres_gcs"}
SUPPORTED_OBJECT_STORE_PROVIDERS = {"filesystem", "gcs"}


class MissingRealtimeProviderAPIKeyError(RuntimeError):
    def __init__(self, *, provider: str, env_var: str) -> None:
        provider_name = provider.strip().lower()
        self.provider = provider_name
        self.env_var = env_var
        self.code = f"MISSING_{provider_name.upper()}_API_KEY"
        self.user_message = f"Server missing {env_var}"
        super().__init__(
            f"{env_var} is required when REALTIME_PROVIDER={provider_name}"
        )


class MissingOpenAIAPIKeyError(MissingRealtimeProviderAPIKeyError):
    def __init__(self, message: str = "OPENAI_API_KEY is required at runtime") -> None:
        super().__init__(provider="openai", env_var="OPENAI_API_KEY")
        self.user_message = "Server missing OPENAI_API_KEY"
        self.code = "MISSING_OPENAI_API_KEY"
        RuntimeError.__init__(self, message)


def load_environment_files(backend_env_path: Path | None = None) -> None:
    load_dotenv(dotenv_path=backend_env_path or _BACKEND_ENV_PATH)
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


def _parse_int_env(
    *names: str,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
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
    if maximum is not None and value > maximum:
        return maximum
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
    vision_mistral_api_key: str | None
    vision_mistral_base_url: str | None
    vision_openai_api_key: str | None
    vision_openai_base_url: str | None
    vision_azure_openai_api_key: str | None
    vision_azure_openai_endpoint: str | None
    vision_azure_openai_api_version: str | None
    vision_azure_openai_deployment: str | None
    vision_gemini_api_key: str | None
    vision_gemini_base_url: str | None
    vision_claude_api_key: str | None
    vision_claude_base_url: str | None
    vision_bedrock_region: str | None
    vision_bedrock_aws_access_key_id: str | None
    vision_bedrock_aws_secret_access_key: str | None
    vision_bedrock_aws_session_token: str | None
    vision_groq_api_key: str | None
    vision_groq_base_url: str | None
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
    gemini_live_api_key: str | None
    gemini_live_model: str
    gemini_live_base_url: str | None
    gemini_live_endpoint: str | None
    backend_uplink_ack_every_n_frames: int
    backend_data_dir: Path
    backend_sqlite_path: Path
    backend_storage_backend: str
    backend_database_url: str | None
    backend_object_store_provider: str
    backend_object_store_bucket: str | None
    backend_object_store_prefix: str | None
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
    backend_rate_limit_http_ip_max_requests: int
    backend_rate_limit_http_window_seconds: int
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

    def validate_storage_contract(self) -> None:
        if self.backend_storage_backend not in SUPPORTED_STORAGE_BACKENDS:
            supported = ", ".join(sorted(SUPPORTED_STORAGE_BACKENDS))
            raise RuntimeError(
                "BACKEND_STORAGE_BACKEND must be one of "
                f"{supported}. Got {self.backend_storage_backend!r}."
            )

        if self.backend_object_store_provider not in SUPPORTED_OBJECT_STORE_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_OBJECT_STORE_PROVIDERS))
            raise RuntimeError(
                "BACKEND_OBJECT_STORE_PROVIDER must be one of "
                f"{supported}. Got {self.backend_object_store_provider!r}."
            )

        if self.backend_storage_backend == "local":
            if self.backend_object_store_provider != "filesystem":
                raise RuntimeError(
                    "BACKEND_OBJECT_STORE_PROVIDER must be 'filesystem' when "
                    "BACKEND_STORAGE_BACKEND=local."
                )
            return

        if self.backend_database_url is None:
            raise RuntimeError(
                "BACKEND_DATABASE_URL must be set when "
                "BACKEND_STORAGE_BACKEND=postgres_gcs."
            )
        if self.backend_object_store_provider != "gcs":
            raise RuntimeError(
                "BACKEND_OBJECT_STORE_PROVIDER must be 'gcs' when "
                "BACKEND_STORAGE_BACKEND=postgres_gcs."
            )
        if self.backend_object_store_bucket is None:
            raise RuntimeError(
                "BACKEND_OBJECT_STORE_BUCKET must be set when "
                "BACKEND_STORAGE_BACKEND=postgres_gcs."
            )
        if self.backend_object_store_prefix is None:
            raise RuntimeError(
                "BACKEND_OBJECT_STORE_PREFIX must be set when "
                "BACKEND_STORAGE_BACKEND=postgres_gcs."
            )

    def require_openai_api_key(self) -> str:
        key = self.resolve_realtime_api_key(provider="openai")
        if not key:
            raise MissingOpenAIAPIKeyError("OPENAI_API_KEY is required at runtime")
        return key

    def resolve_realtime_api_key(self, *, provider: str | None = None) -> str | None:
        provider_name = (provider or self.realtime_provider).strip().lower()
        if provider_name == "openai":
            key = (self.openai_api_key or "").strip()
            return key or None
        if provider_name == "gemini_live":
            key = (self.gemini_live_api_key or "").strip()
            return key or None
        return None

    def require_realtime_api_key(self, *, provider: str | None = None) -> str:
        provider_name = (provider or self.realtime_provider).strip().lower()
        key = self.resolve_realtime_api_key(provider=provider_name)
        if key:
            return key
        if provider_name == "openai":
            raise MissingOpenAIAPIKeyError("OPENAI_API_KEY is required at runtime")
        if provider_name == "gemini_live":
            raise MissingRealtimeProviderAPIKeyError(
                provider=provider_name,
                env_var="GEMINI_LIVE_API_KEY",
            )
        raise RuntimeError(
            f"Unsupported realtime provider {provider_name!r}: no api-key resolution path is defined."
        )

    def resolve_realtime_model(self, *, provider: str | None = None) -> str:
        provider_name = (provider or self.realtime_provider).strip().lower()
        if provider_name == "openai":
            return self.openai_realtime_model
        if provider_name == "gemini_live":
            return self.gemini_live_model
        raise RuntimeError(f"Unsupported realtime provider {provider_name!r}")

    def resolve_realtime_base_url(self, *, provider: str | None = None) -> str | None:
        provider_name = (provider or self.realtime_provider).strip().lower()
        if provider_name == "gemini_live":
            base_url = (self.gemini_live_base_url or "").strip()
            return base_url or None
        return None

    def resolve_realtime_endpoint(self, *, provider: str | None = None) -> str | None:
        provider_name = (provider or self.realtime_provider).strip().lower()
        if provider_name == "gemini_live":
            endpoint = (self.gemini_live_endpoint or "").strip()
            return endpoint or None
        return None

    def _resolve_vision_provider_scoped_api_key(self, *, provider: str) -> str | None:
        provider_name = provider.strip().lower()
        if provider_name == "mistral":
            key = (self.vision_mistral_api_key or "").strip()
            return key or None
        if provider_name == "openai":
            key = (self.vision_openai_api_key or "").strip()
            return key or None
        if provider_name == "azure_openai":
            key = (self.vision_azure_openai_api_key or "").strip()
            return key or None
        if provider_name == "gemini":
            key = (self.vision_gemini_api_key or "").strip()
            return key or None
        if provider_name == "claude":
            key = (self.vision_claude_api_key or "").strip()
            return key or None
        if provider_name == "groq":
            key = (self.vision_groq_api_key or "").strip()
            return key or None
        return None

    def _resolve_vision_provider_scoped_base_url(self, *, provider: str) -> str | None:
        provider_name = provider.strip().lower()
        if provider_name == "mistral":
            base_url = (self.vision_mistral_base_url or "").strip()
            return base_url or None
        if provider_name == "openai":
            base_url = (self.vision_openai_base_url or "").strip()
            return base_url or None
        if provider_name == "gemini":
            base_url = (self.vision_gemini_base_url or "").strip()
            return base_url or None
        if provider_name == "claude":
            base_url = (self.vision_claude_base_url or "").strip()
            return base_url or None
        if provider_name == "groq":
            base_url = (self.vision_groq_base_url or "").strip()
            return base_url or None
        return None

    def resolve_vision_provider_endpoint(self, *, provider: str | None = None) -> str | None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        if provider_name == "azure_openai":
            endpoint = (self.vision_azure_openai_endpoint or "").strip()
            return endpoint or None
        return None

    def resolve_vision_provider_api_version(self, *, provider: str | None = None) -> str | None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        if provider_name == "azure_openai":
            api_version = (self.vision_azure_openai_api_version or "").strip()
            return api_version or None
        return None

    def resolve_vision_provider_deployment(self, *, provider: str | None = None) -> str | None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        if provider_name == "azure_openai":
            deployment = (self.vision_azure_openai_deployment or "").strip()
            if deployment:
                return deployment
            model_name = (self.vision_memory_model or "").strip()
            return model_name or None
        return None

    def resolve_vision_provider_region(self, *, provider: str | None = None) -> str | None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        if provider_name == "bedrock":
            region = (self.vision_bedrock_region or "").strip()
            return region or None
        return None

    def resolve_vision_provider_aws_access_key_id(
        self,
        *,
        provider: str | None = None,
    ) -> str | None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        if provider_name == "bedrock":
            access_key_id = (self.vision_bedrock_aws_access_key_id or "").strip()
            return access_key_id or None
        return None

    def resolve_vision_provider_aws_secret_access_key(
        self,
        *,
        provider: str | None = None,
    ) -> str | None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        if provider_name == "bedrock":
            secret_access_key = (self.vision_bedrock_aws_secret_access_key or "").strip()
            return secret_access_key or None
        return None

    def resolve_vision_provider_aws_session_token(
        self,
        *,
        provider: str | None = None,
    ) -> str | None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        if provider_name == "bedrock":
            session_token = (self.vision_bedrock_aws_session_token or "").strip()
            return session_token or None
        return None

    def resolve_vision_provider_api_key(self, *, provider: str | None = None) -> str | None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        key = self._resolve_vision_provider_scoped_api_key(provider=provider_name)
        if key:
            return key
        return None

    def resolve_vision_provider_base_url(self, *, provider: str | None = None) -> str | None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        base_url = self._resolve_vision_provider_scoped_base_url(provider=provider_name)
        if base_url:
            return base_url
        return None

    def require_vision_provider_api_key(self, *, provider: str | None = None) -> str:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        if provider_name == "bedrock":
            raise RuntimeError(
                "BEDROCK does not use API keys. Configure AWS credentials and region settings."
            )
        key = self.resolve_vision_provider_api_key(provider=provider_name)
        if key:
            return key
        if provider_name == "mistral":
            raise RuntimeError(
                "VISION_MISTRAL_API_KEY "
                "is required when VISION_MEMORY_ENABLED=true and VISION_MEMORY_PROVIDER=mistral"
            )
        raise RuntimeError(
            f"Missing vision provider API key for provider={provider_name!r}. "
            f"Set VISION_{provider_name.upper()}_API_KEY."
        )

    def validate_vision_provider_credentials(self, *, provider: str | None = None) -> None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        if provider_name == "bedrock":
            return
        key = self.require_vision_provider_api_key(provider=provider_name)
        model_name = (self.vision_memory_model or "").strip()
        if provider_name == "mistral" and model_name and key == model_name:
            raise RuntimeError(
                "VISION_MISTRAL_API_KEY is invalid: "
                "it matches VISION_MEMORY_MODEL. Set an API key, not a model id."
            )
        if provider_name == "mistral" and key.lower().startswith("mistralai/"):
            raise RuntimeError(
                "VISION_MISTRAL_API_KEY looks like a model id, "
                "not an API key."
            )

    def has_tavily_api_key(self) -> bool:
        return bool((self.tavily_api_key or "").strip())


def _load_credentials_settings() -> dict[str, str | None]:
    return {
        "openai_api_key": os.getenv("OPENAI_API_KEY"),
        "vision_mistral_api_key": os.getenv("VISION_MISTRAL_API_KEY"),
        "vision_mistral_base_url": _get_env("VISION_MISTRAL_BASE_URL"),
        "vision_openai_api_key": os.getenv("VISION_OPENAI_API_KEY"),
        "vision_openai_base_url": _get_env("VISION_OPENAI_BASE_URL"),
        "vision_azure_openai_api_key": os.getenv("VISION_AZURE_OPENAI_API_KEY"),
        "vision_azure_openai_endpoint": _get_env("VISION_AZURE_OPENAI_ENDPOINT"),
        "vision_azure_openai_api_version": _get_env("VISION_AZURE_OPENAI_API_VERSION"),
        "vision_azure_openai_deployment": _get_env("VISION_AZURE_OPENAI_DEPLOYMENT"),
        "vision_gemini_api_key": os.getenv("VISION_GEMINI_API_KEY"),
        "vision_gemini_base_url": _get_env("VISION_GEMINI_BASE_URL"),
        "vision_claude_api_key": os.getenv("VISION_CLAUDE_API_KEY"),
        "vision_claude_base_url": _get_env("VISION_CLAUDE_BASE_URL"),
        "vision_bedrock_region": _get_env("VISION_BEDROCK_REGION"),
        "vision_bedrock_aws_access_key_id": os.getenv("VISION_BEDROCK_AWS_ACCESS_KEY_ID"),
        "vision_bedrock_aws_secret_access_key": os.getenv(
            "VISION_BEDROCK_AWS_SECRET_ACCESS_KEY"
        ),
        "vision_bedrock_aws_session_token": os.getenv("VISION_BEDROCK_AWS_SESSION_TOKEN"),
        "vision_groq_api_key": os.getenv("VISION_GROQ_API_KEY"),
        "vision_groq_base_url": _get_env("VISION_GROQ_BASE_URL"),
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
        "gemini_live_api_key": os.getenv("GEMINI_LIVE_API_KEY"),
        "gemini_live_model": (
            _get_env("GEMINI_LIVE_MODEL") or "gemini-2.0-flash-live-001"
        ).strip(),
        "gemini_live_base_url": _get_env("GEMINI_LIVE_BASE_URL"),
        "gemini_live_endpoint": _get_env("GEMINI_LIVE_ENDPOINT"),
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
    backend_storage_backend = (_get_env("BACKEND_STORAGE_BACKEND") or "local").strip().lower()
    backend_database_url = (_get_env("BACKEND_DATABASE_URL") or "").strip() or None
    backend_object_store_provider = (
        _get_env("BACKEND_OBJECT_STORE_PROVIDER") or "filesystem"
    ).strip().lower()
    backend_object_store_bucket = (_get_env("BACKEND_OBJECT_STORE_BUCKET") or "").strip() or None
    backend_object_store_prefix = (_get_env("BACKEND_OBJECT_STORE_PREFIX") or "").strip() or None
    return {
        "backend_data_dir": backend_data_dir,
        "backend_sqlite_path": backend_sqlite_path,
        "backend_storage_backend": backend_storage_backend,
        "backend_database_url": backend_database_url,
        "backend_object_store_provider": backend_object_store_provider,
        "backend_object_store_bucket": backend_object_store_bucket,
        "backend_object_store_prefix": backend_object_store_prefix,
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
            maximum=5,
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
        "backend_rate_limit_http_ip_max_requests": _parse_int_env(
            "BACKEND_RATE_LIMIT_HTTP_IP_MAX_REQUESTS",
            default=30,
            minimum=1,
        ),
        "backend_rate_limit_http_window_seconds": _parse_int_env(
            "BACKEND_RATE_LIMIT_HTTP_WINDOW_SECONDS",
            default=60,
            minimum=1,
        ),
    }

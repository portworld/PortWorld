from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv
from dotenv import find_dotenv

from backend.memory.lifecycle import DEFAULT_SESSION_MEMORY_RETENTION_DAYS

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_ENV_PATH = _BACKEND_ROOT / ".env"


DEFAULT_INSTRUCTIONS = "You are a concise assistant. Keep answers short, clear, and practical."
STORAGE_BACKEND_MANAGED = "managed"
SUPPORTED_STORAGE_BACKENDS = {"local", STORAGE_BACKEND_MANAGED}
SUPPORTED_OBJECT_STORE_PROVIDERS = {"filesystem", "gcs", "s3", "azure_blob"}
DEFAULT_VISION_MODELS_BY_PROVIDER: dict[str, str] = {
    "mistral": "ministral-3b-2512",
    "nvidia_integrate": "mistralai/ministral-14b-instruct-2512",
    "openai": "gpt-4.1-mini",
    "gemini": "gemini-2.0-flash",
    "claude": "claude-3-5-sonnet-latest",
    "bedrock": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "groq": "llama-3.2-90b-vision-preview",
}

_NVIDIA_VISION_HOST_MARKERS = (
    "integrate.api.nvidia.com",
    "api.nvcf.nvidia.com",
    "build.nvidia.com",
    "docs.api.nvidia.com",
)

_VISION_PROVIDER_API_KEY_ATTR_BY_PROVIDER: Mapping[str, str] = {
    "mistral": "vision_mistral_api_key",
    "nvidia_integrate": "vision_nvidia_api_key",
    "openai": "vision_openai_api_key",
    "azure_openai": "vision_azure_openai_api_key",
    "gemini": "vision_gemini_api_key",
    "claude": "vision_claude_api_key",
    "groq": "vision_groq_api_key",
}

_VISION_PROVIDER_BASE_URL_ATTR_BY_PROVIDER: Mapping[str, str] = {
    "mistral": "vision_mistral_base_url",
    "nvidia_integrate": "vision_nvidia_base_url",
    "openai": "vision_openai_base_url",
    "gemini": "vision_gemini_base_url",
    "claude": "vision_claude_base_url",
    "groq": "vision_groq_base_url",
}

_VISION_PROVIDER_MODEL_ATTR_BY_PROVIDER: Mapping[str, str] = {
    "mistral": "vision_mistral_model",
    "nvidia_integrate": "vision_nvidia_model",
    "openai": "vision_openai_model",
    "azure_openai": "vision_azure_openai_model",
    "gemini": "vision_gemini_model",
    "claude": "vision_claude_model",
    "bedrock": "vision_bedrock_model",
    "groq": "vision_groq_model",
}

_VISION_PROVIDER_REGION_ATTR_BY_PROVIDER: Mapping[str, str] = {
    "bedrock": "vision_bedrock_region",
}

_VISION_PROVIDER_AWS_ACCESS_KEY_ID_ATTR_BY_PROVIDER: Mapping[str, str] = {
    "bedrock": "vision_bedrock_aws_access_key_id",
}

_VISION_PROVIDER_AWS_SECRET_ACCESS_KEY_ATTR_BY_PROVIDER: Mapping[str, str] = {
    "bedrock": "vision_bedrock_aws_secret_access_key",
}

_VISION_PROVIDER_AWS_SESSION_TOKEN_ATTR_BY_PROVIDER: Mapping[str, str] = {
    "bedrock": "vision_bedrock_aws_session_token",
}


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


def load_environment_files(
    backend_env_path: Path | None = None,
    *,
    discover_secondary_env: bool = True,
) -> None:
    primary_env_path = (backend_env_path or _BACKEND_ENV_PATH).resolve()
    load_dotenv(dotenv_path=primary_env_path)

    secondary_env_path: Path | None = None
    if discover_secondary_env:
        discovered = find_dotenv(usecwd=True)
        if discovered:
            secondary_env_path = Path(discovered).resolve()
            load_dotenv(dotenv_path=secondary_env_path)
        else:
            load_dotenv()

    # Keep an internal hint for resolving relative extension paths from env.
    if secondary_env_path is not None and secondary_env_path.is_file():
        os.environ["PORTWORLD_INTERNAL_ENV_BASE_DIR"] = str(secondary_env_path.parent)
    else:
        os.environ["PORTWORLD_INTERNAL_ENV_BASE_DIR"] = str(primary_env_path.parent)


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


def _parse_optional_path_env(*names: str) -> Path | None:
    raw = _get_env(*names)
    if raw is None:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    parsed = Path(candidate).expanduser()
    if parsed.is_absolute():
        return parsed.resolve()
    base_dir = os.getenv("PORTWORLD_INTERNAL_ENV_BASE_DIR")
    if base_dir:
        return (Path(base_dir).expanduser() / parsed).resolve()
    return (Path.cwd() / parsed).resolve()


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    vision_mistral_api_key: str | None
    vision_mistral_model: str | None
    vision_mistral_base_url: str | None
    vision_nvidia_api_key: str | None
    vision_nvidia_model: str | None
    vision_nvidia_base_url: str | None
    vision_openai_api_key: str | None
    vision_openai_model: str | None
    vision_openai_base_url: str | None
    vision_azure_openai_api_key: str | None
    vision_azure_openai_model: str | None
    vision_azure_openai_endpoint: str | None
    vision_azure_openai_api_version: str | None
    vision_azure_openai_deployment: str | None
    vision_gemini_api_key: str | None
    vision_gemini_model: str | None
    vision_gemini_base_url: str | None
    vision_claude_api_key: str | None
    vision_claude_model: str | None
    vision_claude_base_url: str | None
    vision_bedrock_region: str | None
    vision_bedrock_model: str | None
    vision_bedrock_aws_access_key_id: str | None
    vision_bedrock_aws_secret_access_key: str | None
    vision_bedrock_aws_session_token: str | None
    vision_groq_api_key: str | None
    vision_groq_model: str | None
    vision_groq_base_url: str | None
    tavily_api_key: str | None
    tavily_base_url: str | None
    backend_bearer_token: str | None
    realtime_provider: str
    realtime_model: str
    realtime_voice: str
    realtime_instructions: str
    realtime_include_turn_detection: bool
    realtime_enable_manual_turn_fallback: bool
    realtime_manual_turn_fallback_delay_ms: int
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
    backend_object_store_name: str | None
    backend_object_store_endpoint: str | None
    backend_object_store_prefix: str | None
    backend_max_vision_request_bytes: int
    backend_max_vision_frame_bytes: int
    backend_session_memory_retention_days: int
    vision_memory_enabled: bool
    vision_memory_provider: str
    vision_short_term_window_seconds: int
    vision_min_analysis_gap_seconds: int
    vision_scene_change_hamming_threshold: int
    vision_provider_max_rps: int
    vision_provider_timeout_seconds: int
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
    memory_consolidation_enabled: bool
    memory_consolidation_timeout_ms: int
    portworld_extensions_manifest: Path | None
    portworld_extensions_python_path: Path | None
    backend_profile: str
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

    @classmethod
    def from_env(cls) -> "Settings":
        backend_profile = (_get_env("BACKEND_PROFILE") or "development").strip().lower()
        vision_settings = _load_vision_settings()
        return cls(
            **_load_credentials_settings(),
            **_load_realtime_settings(),
            **_load_storage_settings(),
            **vision_settings,
            **_load_tooling_settings(
                vision_memory_enabled=bool(vision_settings["vision_memory_enabled"])
            ),
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
                "BACKEND_STORAGE_BACKEND=managed."
            )
        if self.backend_object_store_provider == "filesystem":
            raise RuntimeError(
                "BACKEND_OBJECT_STORE_PROVIDER cannot be 'filesystem' when "
                "BACKEND_STORAGE_BACKEND=managed."
            )
        if self.backend_object_store_provider == "azure_blob" and self.backend_object_store_endpoint is None:
            raise RuntimeError(
                "BACKEND_OBJECT_STORE_ENDPOINT must be set when "
                "BACKEND_OBJECT_STORE_PROVIDER=azure_blob."
            )
        if self.backend_object_store_name is None:
            raise RuntimeError(
                "BACKEND_OBJECT_STORE_NAME must be set when "
                "BACKEND_STORAGE_BACKEND=managed."
            )
        if self.backend_object_store_prefix is None:
            raise RuntimeError(
                "BACKEND_OBJECT_STORE_PREFIX must be set when "
                "BACKEND_STORAGE_BACKEND=managed."
            )

    def require_openai_api_key(self) -> str:
        return self.require_realtime_api_key(provider="openai")

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
            return self.realtime_model
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

    def _resolve_provider_scoped_attr(
        self,
        *,
        provider: str,
        attr_by_provider: Mapping[str, str],
    ) -> str | None:
        provider_name = provider.strip().lower()
        attr_name = attr_by_provider.get(provider_name)
        if not attr_name:
            return None
        raw_value = getattr(self, attr_name, None)
        value = (raw_value or "").strip()
        return value or None

    def _resolve_vision_provider_scoped_api_key(self, *, provider: str) -> str | None:
        return self._resolve_provider_scoped_attr(
            provider=provider,
            attr_by_provider=_VISION_PROVIDER_API_KEY_ATTR_BY_PROVIDER,
        )

    def _resolve_vision_provider_scoped_base_url(self, *, provider: str) -> str | None:
        return self._resolve_provider_scoped_attr(
            provider=provider,
            attr_by_provider=_VISION_PROVIDER_BASE_URL_ATTR_BY_PROVIDER,
        )

    def _resolve_vision_provider_scoped_model(self, *, provider: str) -> str | None:
        return self._resolve_provider_scoped_attr(
            provider=provider,
            attr_by_provider=_VISION_PROVIDER_MODEL_ATTR_BY_PROVIDER,
        )

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
            return self._resolve_vision_provider_scoped_model(provider=provider_name)
        return None

    def resolve_vision_provider_model(self, *, provider: str | None = None) -> str | None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        model_name = self._resolve_vision_provider_scoped_model(provider=provider_name)
        if model_name:
            return model_name
        default_model_name = DEFAULT_VISION_MODELS_BY_PROVIDER.get(provider_name, "").strip()
        return default_model_name or None

    def resolve_vision_provider_region(self, *, provider: str | None = None) -> str | None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        return self._resolve_provider_scoped_attr(
            provider=provider_name,
            attr_by_provider=_VISION_PROVIDER_REGION_ATTR_BY_PROVIDER,
        )

    def resolve_vision_provider_aws_access_key_id(
        self,
        *,
        provider: str | None = None,
    ) -> str | None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        return self._resolve_provider_scoped_attr(
            provider=provider_name,
            attr_by_provider=_VISION_PROVIDER_AWS_ACCESS_KEY_ID_ATTR_BY_PROVIDER,
        )

    def resolve_vision_provider_aws_secret_access_key(
        self,
        *,
        provider: str | None = None,
    ) -> str | None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        return self._resolve_provider_scoped_attr(
            provider=provider_name,
            attr_by_provider=_VISION_PROVIDER_AWS_SECRET_ACCESS_KEY_ATTR_BY_PROVIDER,
        )

    def resolve_vision_provider_aws_session_token(
        self,
        *,
        provider: str | None = None,
    ) -> str | None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        return self._resolve_provider_scoped_attr(
            provider=provider_name,
            attr_by_provider=_VISION_PROVIDER_AWS_SESSION_TOKEN_ATTR_BY_PROVIDER,
        )

    def resolve_vision_provider_api_key(self, *, provider: str | None = None) -> str | None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        return self._resolve_vision_provider_scoped_api_key(provider=provider_name)

    def resolve_vision_provider_base_url(self, *, provider: str | None = None) -> str | None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        return self._resolve_vision_provider_scoped_base_url(provider=provider_name)

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
                "is required when VISION_MEMORY_PROVIDER=mistral and either "
                "VISION_MEMORY_ENABLED=true or MEMORY_CONSOLIDATION_ENABLED=true"
            )
        if provider_name == "nvidia_integrate":
            raise RuntimeError(
                "VISION_NVIDIA_API_KEY "
                "is required when VISION_MEMORY_PROVIDER=nvidia_integrate and either "
                "VISION_MEMORY_ENABLED=true or MEMORY_CONSOLIDATION_ENABLED=true"
            )
        raise RuntimeError(
            f"Missing vision provider API key for provider={provider_name!r}. "
            f"Set VISION_{provider_name.upper()}_API_KEY."
        )

    def resolve_memory_consolidation_provider(self) -> str:
        provider_name = self.vision_memory_provider.strip().lower()
        return provider_name or "mistral"

    def resolve_memory_consolidation_model(self) -> str | None:
        return self.resolve_vision_provider_model(
            provider=self.resolve_memory_consolidation_provider()
        )

    def validate_vision_provider_credentials(self, *, provider: str | None = None) -> None:
        provider_name = (provider or self.vision_memory_provider).strip().lower()
        if provider_name == "bedrock":
            return
        key = self.require_vision_provider_api_key(provider=provider_name)
        model_name = (self.resolve_vision_provider_model(provider=provider_name) or "").strip()
        if provider_name == "mistral" and model_name and key == model_name:
            raise RuntimeError(
                "VISION_MISTRAL_API_KEY is invalid: "
                "it matches the configured Mistral model. Set an API key, not a model id."
            )
        if provider_name == "mistral" and key.lower().startswith("mistralai/"):
            raise RuntimeError(
                "VISION_MISTRAL_API_KEY looks like a model id, "
                "not an API key."
            )
        base_url = (self.resolve_vision_provider_base_url(provider=provider_name) or "").strip()
        if provider_name == "mistral" and _looks_like_nvidia_vision_host(base_url):
            raise RuntimeError(
                "VISION_MISTRAL_BASE_URL points at an NVIDIA Integrate/NIM endpoint. "
                "Set VISION_MEMORY_PROVIDER=nvidia_integrate instead."
            )
        if provider_name == "mistral" and _looks_like_nvidia_integrate_model(model_name):
            raise RuntimeError(
                "VISION_MISTRAL_MODEL looks like an NVIDIA Integrate model id. "
                "Set VISION_MEMORY_PROVIDER=nvidia_integrate instead."
            )

    def has_tavily_api_key(self) -> bool:
        return bool((self.tavily_api_key or "").strip())


def _load_credentials_settings() -> dict[str, str | None]:
    return {
        "openai_api_key": os.getenv("OPENAI_API_KEY"),
        "vision_mistral_api_key": os.getenv("VISION_MISTRAL_API_KEY"),
        "vision_mistral_model": _get_env("VISION_MISTRAL_MODEL"),
        "vision_mistral_base_url": _get_env("VISION_MISTRAL_BASE_URL"),
        "vision_nvidia_api_key": os.getenv("VISION_NVIDIA_API_KEY"),
        "vision_nvidia_model": _get_env("VISION_NVIDIA_MODEL"),
        "vision_nvidia_base_url": _get_env("VISION_NVIDIA_BASE_URL"),
        "vision_openai_api_key": os.getenv("VISION_OPENAI_API_KEY"),
        "vision_openai_model": _get_env("VISION_OPENAI_MODEL"),
        "vision_openai_base_url": _get_env("VISION_OPENAI_BASE_URL"),
        "vision_azure_openai_api_key": os.getenv("VISION_AZURE_OPENAI_API_KEY"),
        "vision_azure_openai_model": _get_env("VISION_AZURE_OPENAI_MODEL"),
        "vision_azure_openai_endpoint": _get_env("VISION_AZURE_OPENAI_ENDPOINT"),
        "vision_azure_openai_api_version": _get_env("VISION_AZURE_OPENAI_API_VERSION"),
        "vision_azure_openai_deployment": _get_env("VISION_AZURE_OPENAI_DEPLOYMENT"),
        "vision_gemini_api_key": os.getenv("VISION_GEMINI_API_KEY"),
        "vision_gemini_model": _get_env("VISION_GEMINI_MODEL"),
        "vision_gemini_base_url": _get_env("VISION_GEMINI_BASE_URL"),
        "vision_claude_api_key": os.getenv("VISION_CLAUDE_API_KEY"),
        "vision_claude_model": _get_env("VISION_CLAUDE_MODEL"),
        "vision_claude_base_url": _get_env("VISION_CLAUDE_BASE_URL"),
        "vision_bedrock_region": _get_env("VISION_BEDROCK_REGION"),
        "vision_bedrock_model": _get_env("VISION_BEDROCK_MODEL"),
        "vision_bedrock_aws_access_key_id": os.getenv("VISION_BEDROCK_AWS_ACCESS_KEY_ID"),
        "vision_bedrock_aws_secret_access_key": os.getenv(
            "VISION_BEDROCK_AWS_SECRET_ACCESS_KEY"
        ),
        "vision_bedrock_aws_session_token": os.getenv("VISION_BEDROCK_AWS_SESSION_TOKEN"),
        "vision_groq_api_key": os.getenv("VISION_GROQ_API_KEY"),
        "vision_groq_model": _get_env("VISION_GROQ_MODEL"),
        "vision_groq_base_url": _get_env("VISION_GROQ_BASE_URL"),
        "tavily_api_key": os.getenv("TAVILY_API_KEY"),
        "tavily_base_url": _get_env("TAVILY_BASE_URL"),
    }


def _load_realtime_settings() -> dict[str, str | int | bool | None]:
    return {
        "backend_bearer_token": (_get_env("BACKEND_BEARER_TOKEN") or "").strip() or None,
        "realtime_provider": (_get_env("REALTIME_PROVIDER") or "openai").strip().lower(),
        "realtime_model": os.getenv("REALTIME_MODEL", "gpt-realtime"),
        "realtime_voice": os.getenv("REALTIME_VOICE", "ash"),
        "realtime_instructions": os.getenv(
            "REALTIME_INSTRUCTIONS",
            DEFAULT_INSTRUCTIONS,
        ),
        "realtime_include_turn_detection": _parse_bool_env(
            "REALTIME_INCLUDE_TURN_DETECTION",
            default=True,
        ),
        "realtime_enable_manual_turn_fallback": _parse_bool_env(
            "REALTIME_ENABLE_MANUAL_TURN_FALLBACK",
            default=True,
        ),
        "realtime_manual_turn_fallback_delay_ms": _parse_int_env(
            "REALTIME_MANUAL_TURN_FALLBACK_DELAY_MS",
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
    backend_object_store_name = (_get_env("BACKEND_OBJECT_STORE_NAME") or "").strip() or None
    backend_object_store_endpoint = (_get_env("BACKEND_OBJECT_STORE_ENDPOINT") or "").strip() or None
    backend_object_store_prefix = (_get_env("BACKEND_OBJECT_STORE_PREFIX") or "").strip() or None
    return {
        "backend_data_dir": backend_data_dir,
        "backend_sqlite_path": backend_sqlite_path,
        "backend_storage_backend": backend_storage_backend,
        "backend_database_url": backend_database_url,
        "backend_object_store_provider": backend_object_store_provider,
        "backend_object_store_name": backend_object_store_name,
        "backend_object_store_endpoint": backend_object_store_endpoint,
        "backend_object_store_prefix": backend_object_store_prefix,
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
        "vision_provider_timeout_seconds": _parse_int_env(
            "VISION_PROVIDER_TIMEOUT_SECONDS",
            default=45,
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


def _looks_like_nvidia_vision_host(base_url: str) -> bool:
    candidate = base_url.strip().lower()
    if not candidate:
        return False
    return any(marker in candidate for marker in _NVIDIA_VISION_HOST_MARKERS)


def _looks_like_nvidia_integrate_model(model_name: str) -> bool:
    candidate = model_name.strip().lower()
    if not candidate:
        return False
    return "/" in candidate


def _load_tooling_settings(
    *,
    vision_memory_enabled: bool,
) -> dict[str, str | int | bool | Path | None]:
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
        "memory_consolidation_enabled": _parse_bool_env(
            "MEMORY_CONSOLIDATION_ENABLED",
            default=vision_memory_enabled,
        ),
        "memory_consolidation_timeout_ms": _parse_int_env(
            "MEMORY_CONSOLIDATION_TIMEOUT_MS",
            default=30000,
            minimum=1000,
        ),
        "portworld_extensions_manifest": _parse_optional_path_env(
            "PORTWORLD_EXTENSIONS_MANIFEST",
        ),
        "portworld_extensions_python_path": _parse_optional_path_env(
            "PORTWORLD_EXTENSIONS_PYTHON_PATH",
        ),
    }


def _load_server_settings(*, backend_profile: str) -> dict[str, str | int | list[str]]:
    return {
        "backend_profile": backend_profile,
        "backend_forwarded_allow_ips": _parse_csv_env(
            "BACKEND_FORWARDED_ALLOW_IPS",
            default="127.0.0.1,::1",
        ),
        "host": _get_env("HOST") or "0.0.0.0",
        "port": _parse_int_env("PORT", default=8080),
        "log_level": _get_env("LOG_LEVEL") or "INFO",
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

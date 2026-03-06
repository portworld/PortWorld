from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_BACKEND_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_BACKEND_ENV_PATH)
load_dotenv()


DEFAULT_INSTRUCTIONS = "You are a concise assistant. Keep answers short, clear, and practical."


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_int_env(name: str, *, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name)
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


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_realtime_model: str
    openai_realtime_voice: str
    openai_realtime_instructions: str
    openai_realtime_include_turn_detection: bool
    openai_realtime_enable_manual_turn_fallback: bool
    openai_realtime_allow_text_audio_fallback: bool
    openai_realtime_uplink_ack_every_n_frames: int
    openai_realtime_manual_turn_fallback_delay_ms: int
    openai_debug_dump_input_audio: bool
    openai_debug_dump_input_audio_dir: str
    openai_debug_trace_ws_messages: bool
    host: str
    port: int
    log_level: str
    cors_origins: list[str]

    @classmethod
    def from_env(cls) -> "Settings":
        origins_raw = os.getenv("CORS_ORIGINS", "*")
        origins = [origin.strip() for origin in origins_raw.split(",") if origin.strip()]

        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_realtime_model=os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime"),
            openai_realtime_voice=os.getenv("OPENAI_REALTIME_VOICE", "ash"),
            openai_realtime_instructions=os.getenv(
                "OPENAI_REALTIME_INSTRUCTIONS", DEFAULT_INSTRUCTIONS
            ),
            openai_realtime_include_turn_detection=_parse_bool_env(
                "OPENAI_REALTIME_INCLUDE_TURN_DETECTION",
                default=True,
            ),
            openai_realtime_enable_manual_turn_fallback=_parse_bool_env(
                "OPENAI_REALTIME_ENABLE_MANUAL_TURN_FALLBACK",
                default=True,
            ),
            openai_realtime_allow_text_audio_fallback=_parse_bool_env(
                "OPENAI_REALTIME_ALLOW_TEXT_AUDIO_FALLBACK",
                default=False,
            ),
            openai_realtime_uplink_ack_every_n_frames=_parse_int_env(
                "OPENAI_REALTIME_UPLINK_ACK_EVERY_N_FRAMES",
                default=20,
                minimum=1,
            ),
            openai_realtime_manual_turn_fallback_delay_ms=_parse_int_env(
                "OPENAI_REALTIME_MANUAL_TURN_FALLBACK_DELAY_MS",
                default=900,
                minimum=100,
            ),
            openai_debug_dump_input_audio=_parse_bool_env(
                "OPENAI_DEBUG_DUMP_INPUT_AUDIO",
                default=False,
            ),
            openai_debug_dump_input_audio_dir=os.getenv(
                "OPENAI_DEBUG_DUMP_INPUT_AUDIO_DIR",
                "backend/debug_audio",
            ),
            openai_debug_trace_ws_messages=_parse_bool_env(
                "OPENAI_DEBUG_TRACE_WS_MESSAGES",
                default=False,
            ),
            host=os.getenv("HOST", "0.0.0.0"),
            port=_parse_int_env("PORT", default=8080),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            cors_origins=origins or ["*"],
        )

    def require_openai_api_key(self) -> str:
        key = (self.openai_api_key or "").strip()
        if not key:
            raise RuntimeError("OPENAI_API_KEY is required at runtime")
        return key


settings = Settings.from_env()

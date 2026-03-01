from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _read_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got: {raw}") from exc


def _read_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got: {raw}") from exc


def _read_csv_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def _default_openai_compat_path(base_url: str, path: str) -> str:
    normalized_path = "/" + path.lstrip("/")
    if base_url.rstrip("/").endswith("/v1"):
        return normalized_path
    return f"/v1{normalized_path}"


def _normalize_voxtral_model(model: str, stt_path: str) -> str:
    candidate = model.strip()
    if not candidate:
        return "voxtral-mini-latest"
    if stt_path.strip().rstrip("/").endswith("/audio/transcriptions"):
        if candidate == "voxtral-mini-transcribe":
            return "voxtral-mini-latest"
        if candidate.startswith("voxtral-mini-transcribe-realtime"):
            return "voxtral-mini-latest"
    return candidate


def _normalize_vision_model(model: str) -> str:
    aliases = {
        "mistral.ministral-3b-instruct": "mistral.ministral-3-3b-instruct",
    }
    candidate = model.strip() or "mistral.ministral-3-3b-instruct"
    return aliases.get(candidate, candidate)


def _load_env() -> None:
    try:
        load_dotenv(interpolate=True)
    except TypeError:
        load_dotenv()


@dataclass(slots=True)
class AppSettings:
    app_name: str
    app_version: str
    cors_origins: list[str]
    request_timeout_s: float
    edge_api_key: str

    max_audio_bytes: int
    max_image_bytes: int
    max_video_bytes: int

    default_voxtral_base_url: str
    default_voxtral_api_key: str
    default_voxtral_stt_path: str
    default_voxtral_model: str
    default_voxtral_language: str

    default_nemotron_base_url: str
    default_nemotron_api_key: str
    default_nemotron_chat_path: str
    default_nemotron_model: str
    default_nemotron_max_tokens: int
    default_nemotron_temperature: float
    default_nemotron_prompt: str

    default_main_llm_base_url: str
    default_main_llm_api_key: str
    default_main_llm_chat_path: str
    default_main_llm_model: str
    default_main_llm_max_tokens: int
    default_main_llm_temperature: float
    default_main_llm_driver: str
    default_main_llm_system_prompt: str

    default_vision_base_url: str
    default_vision_api_key: str
    default_vision_chat_path: str
    default_vision_model: str
    default_vision_temperature: float
    default_vision_max_tokens: int
    default_vision_system_prompt: str
    default_vision_prompt: str

    default_elevenlabs_api_key: str
    default_elevenlabs_voice_id: str
    default_elevenlabs_model_id: str
    default_elevenlabs_speed: float
    default_elevenlabs_output_format: str

    default_trace_backends: list[str]


def load_settings() -> AppSettings:
    _load_env()

    voxtral_base_url = os.getenv("VOXTRAL_BASE_URL", "https://api.mistral.ai/v1").strip()
    voxtral_stt_path = os.getenv(
        "VOXTRAL_STT_PATH",
        _default_openai_compat_path(voxtral_base_url, "/audio/transcriptions"),
    ).strip()
    raw_voxtral_model = os.getenv("VOXTRAL_STT_MODEL", "voxtral-mini-latest").strip()

    main_llm_base_url = os.getenv("MAIN_LLM_BASE_URL", "https://api.mistral.ai/v1").strip()

    vision_base_url = os.getenv("VISION_LLM_BASE_URL", main_llm_base_url).strip()

    return AppSettings(
        app_name=os.getenv("APP_NAME", "Port:🌍 Open Framework").strip(),
        app_version=os.getenv("APP_VERSION", "0.1.0").strip(),
        cors_origins=_read_csv_env("CORS_ORIGINS", ["*"]),
        request_timeout_s=_read_float_env("REQUEST_TIMEOUT_S", 240.0),
        edge_api_key=os.getenv("EDGE_API_KEY", "").strip(),
        max_audio_bytes=_read_int_env("MAX_AUDIO_BYTES", 25_000_000),
        max_image_bytes=_read_int_env("MAX_IMAGE_BYTES", 5_000_000),
        max_video_bytes=_read_int_env("MAX_VIDEO_BYTES", 50_000_000),
        default_voxtral_base_url=voxtral_base_url,
        default_voxtral_api_key=os.getenv("VOXTRAL_API_KEY", "").strip(),
        default_voxtral_stt_path=voxtral_stt_path,
        default_voxtral_model=_normalize_voxtral_model(raw_voxtral_model, voxtral_stt_path),
        default_voxtral_language=os.getenv("VOXTRAL_LANGUAGE", "fr").strip(),
        default_nemotron_base_url=os.getenv("NEMOTRON_BASE_URL", "http://127.0.0.1:8000/v1").strip(),
        default_nemotron_api_key=os.getenv("NEMOTRON_API_KEY", "EMPTY").strip(),
        default_nemotron_chat_path=os.getenv("NEMOTRON_CHAT_PATH", "/chat/completions").strip(),
        default_nemotron_model=os.getenv("NEMOTRON_MODEL", "nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-BF16").strip(),
        default_nemotron_max_tokens=_read_int_env("NEMOTRON_MAX_TOKENS", 700),
        default_nemotron_temperature=_read_float_env("NEMOTRON_TEMPERATURE", 0.2),
        default_nemotron_prompt=os.getenv("NEMOTRON_VIDEO_PROMPT", "Decris cette video en francais, de facon structuree et utile pour aider l'assistant principal.").strip(),
        default_main_llm_base_url=main_llm_base_url,
        default_main_llm_api_key=os.getenv("MAIN_LLM_API_KEY", "").strip(),
        default_main_llm_chat_path=os.getenv("MAIN_LLM_CHAT_PATH", _default_openai_compat_path(main_llm_base_url, "/chat/completions")).strip(),
        default_main_llm_model=os.getenv("MAIN_LLM_MODEL", "mistral-large-latest").strip(),
        default_main_llm_max_tokens=_read_int_env("MAIN_LLM_MAX_TOKENS", 700),
        default_main_llm_temperature=_read_float_env("MAIN_LLM_TEMPERATURE", 0.2),
        default_main_llm_driver=os.getenv("MAIN_LLM_DRIVER", "openai_compat").strip(),
        default_main_llm_system_prompt=os.getenv("MAIN_LLM_SYSTEM_PROMPT", "You are an intelligent voice assistant for Meta Ray-Ban smart glasses operating in a real-time audio-visual ecosystem. Context: You receive visual analysis from an NVIDIA VLM and voice transcriptions from Voxtral, then generate responses that ElevenLabs converts to speech. Your role: Provide concise, useful, and contextually relevant responses in 1-2 sentences maximum. Priorities: 1. Safety first - immediately flag any urgent situations or hazards 2. Clarity - use simple, direct language suitable for voice 3. Practical utility - focus on actionable information Response guidelines: - Generate only the exact spoken text - no artifacts, introductions, or formatting - Adapt tone to natural, conversational speech patterns - Use available visual context to enhance relevance - For urgent matters, begin with 'Important:' or 'Warning:' - Keep responses brief enough for comfortable listening through glasses speakers").strip(),
        default_vision_base_url=vision_base_url,
        default_vision_api_key=os.getenv("VISION_LLM_API_KEY", "").strip(),
        default_vision_chat_path=os.getenv("VISION_LLM_CHAT_PATH", _default_openai_compat_path(vision_base_url, "/chat/completions")).strip(),
        default_vision_model=_normalize_vision_model(os.getenv("VISION_LLM_MODEL", "mistral.ministral-3-3b-instruct")),
        default_vision_temperature=_read_float_env("VISION_LLM_TEMPERATURE", 0.2),
        default_vision_max_tokens=_read_int_env("VISION_LLM_MAX_TOKENS", 350),
        default_vision_system_prompt=os.getenv("VISION_SYSTEM_PROMPT", "You are the visual analysis component of a smart glasses voice assistant system. Your outputs feed directly into the main LLM that generates spoken responses. You receive real-time images from Meta Ray-Ban smart glasses and must provide: 1. Accurate object detection and scene understanding 2. Clear identification of people, actions, and environmental context 3. Extraction of any visible text that might be relevant 4. Immediate flagging of safety concerns or hazards Your analysis enables the voice assistant to provide context-aware responses. Be precise, objective, and comprehensive in your observations.").strip(),
        default_vision_prompt=os.getenv("VISION_PROMPT", "Analyse l'image et reponds uniquement en JSON valide avec les champs: description, should_contact, contact_message.").strip(),
        default_elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY", "").strip(),
        default_elevenlabs_voice_id=os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL").strip(),
        default_elevenlabs_model_id=os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5").strip(),
        default_elevenlabs_speed=_read_float_env("ELEVENLABS_SPEED", 1.0),
        default_elevenlabs_output_format=os.getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128").strip(),
        default_trace_backends=_read_csv_env("TRACE_BACKENDS", ["console"]),
    )


SETTINGS = load_settings()

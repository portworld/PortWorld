"""iOS query processing service — Progressive Intelligence Layering.

Architecture:
    Layer 0 (~0ms):    Send assistant.thinking WS ack immediately.
    Layer 1 (~1-2s):   STT → LLM (text-only, no video/tools) → TTS → stream.
    Layer 2 (parallel): Video + Tools run while Layer 1 audio plays.
    Layer 3 (after L2): If deep context materially changes the answer,
                        stream a natural follow-up ("Also, looking at what
                        I can see...").
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from backend.config.settings import SETTINGS
from backend.core.profile import RuntimeProfile, resolve_runtime_profile
from backend.core.utils import build_messages_for_main_llm, to_data_url
from backend.models.runtime import RuntimeConfig, parse_runtime_config
from backend.providers.elevenlabs import prepare_elevenlabs_live_stream
from backend.providers.mistral import iter_main_llm_tokens
from backend.providers.nvidia import summarize_video
from backend.providers.voxtral import transcribe_audio
from backend.routers.ws import send_thinking_to_session, stream_audio_bytes_to_session
from backend.services.run_log import RUN_LOG, RunLogEntry, _utc_now
from backend.tools.registry import ToolRunResult, run_requested_tools
from backend.tracing.manager import TraceManager, build_trace_manager

logger = logging.getLogger(__name__)

# ── Prompts ───────────────────────────────────────────────────────────────────

# Layer 1: minimal prompt — no mention of visual or tool context so the model
# doesn't hallucinate absent information.
_LAYER1_SYSTEM_PROMPT = (
    "You are Port, a smart-glasses voice assistant. "
    "The user just spoke to you. Answer their question directly and conversationally "
    "in 1-2 short sentences. "
    "You can only hear them right now — no camera context yet. "
    "Never use markdown, bullet points, or asterisks. Your response will be spoken aloud."
)

# Layer 3: bridging phrase injected before the enriched follow-up.
_LAYER3_BRIDGE = "Also, looking at what I can see: "

# Minimum character delta between Layer 1 and Layer 3 responses before we
# bother streaming a follow-up.  Avoids redundant near-identical responses.
_LAYER3_MIN_DELTA_CHARS = 40


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_tools_context(tool_runs: list[ToolRunResult]) -> str | None:
    """Build a structured context string from tool outputs (or None if empty)."""
    if not tool_runs:
        return None
    serializable = [
        {"tool": item.name, "status": item.status, "output": item.output}
        for item in tool_runs
    ]
    return json.dumps(serializable, ensure_ascii=False)


def _responses_differ(layer1: str, layer3: str) -> bool:
    """Return True if the enriched response adds enough new content."""
    return abs(len(layer3) - len(layer1)) >= _LAYER3_MIN_DELTA_CHARS


# ── Core processing ───────────────────────────────────────────────────────────


async def process_ios_query(
    session_id: str,
    query_id: str,
    audio_bytes: bytes,
    video_bytes: bytes,
    metadata: dict[str, Any],
    profile: RuntimeProfile,
    tracer: TraceManager,
) -> None:
    """Process an iOS query bundle using Progressive Intelligence Layering."""
    run = RunLogEntry(
        query_id=query_id,
        session_id=session_id,
        source="ios_query",
        started_at=_utc_now(),
    )

    await tracer.event(
        "ios_query.start",
        data={
            "session_id": session_id,
            "query_id": query_id,
            "audio_bytes": len(audio_bytes),
            "video_bytes": len(video_bytes),
        },
    )

    # ── Layer 0: Instant ack ──────────────────────────────────────────────────
    await send_thinking_to_session(session_id, query_id)

    try:
        run.stt_model = profile.voxtral.model
        run.stt_audio_bytes = len(audio_bytes)
        run.video_model = profile.nemotron.model

        # ── STT (needed by both Layer 1 and Layer 2) ─────────────────────────
        transcript: str | None = None
        if audio_bytes:
            try:
                transcript = await transcribe_audio(
                    profile=profile,
                    tracer=tracer,
                    audio=audio_bytes,
                    content_type="audio/wav",
                    filename="query.wav",
                )
                run.stt_transcript = transcript
                logger.info(
                    f"Query {query_id}: transcript = {transcript[:100] if transcript else 'None'}..."
                )
            except Exception as stt_exc:
                run.stt_error = str(stt_exc)
                await tracer.event(
                    "ios_query.stt_skipped",
                    status="warning",
                    data={"query_id": query_id, "reason": str(stt_exc)},
                )
                logger.warning(
                    f"Query {query_id}: STT failed, continuing without transcript: {stt_exc}"
                )

        # ── Layer 1 + Layer 2 in parallel ──────────────────────────────────
        # Layer 1: fast LLM response on transcript alone (no video, no tools).
        # Layer 2: video summarization + tools run while Layer 1 audio plays.
        # Both are launched as tasks so they overlap.

        layer1_tokens: list[str] = []
        layer2_video: str | None = None
        layer2_tools: list[ToolRunResult] = []

        # Event that Layer 1 sets when it finishes streaming to the client.
        layer1_done_event = asyncio.Event()

        async def _layer1() -> None:
            """Layer 1: STT-only → LLM → TTS → stream."""
            nonlocal layer1_tokens

            messages_l1 = build_messages_for_main_llm(
                history=[],
                user_prompt=transcript or "",
                audio_transcript=transcript,
                video_summary=None,  # no video yet
                image_data_urls=[],
                system_prompt=_LAYER1_SYSTEM_PROMPT,
                tool_context=None,  # no tools yet
            )

            model = profile.main_llm.model
            run.main_llm_model = model
            run.main_llm_system_prompt = _LAYER1_SYSTEM_PROMPT
            run.main_llm_messages_count = len(messages_l1)

            await tracer.event(
                "ios_query.layer1_llm_start",
                data={"model": model, "messages_count": len(messages_l1)},
            )

            async def _l1_token_stream():
                async for token in iter_main_llm_tokens(
                    profile=profile,
                    model=model,
                    messages=messages_l1,
                    tracer=tracer,
                    debug_capture=None,
                ):
                    layer1_tokens.append(token)
                    yield token

            run.tts_model = profile.elevenlabs.model
            run.tts_voice_id = str(profile.options.get("elevenlabs_voice_id", ""))

            audio_stream, _ = await prepare_elevenlabs_live_stream(
                profile=profile,
                tracer=tracer,
                text_iterator=_l1_token_stream(),
                voice_id=None,
                model_id=None,
                speed=None,
                output_format="pcm_16000",
            )

            total_bytes = 0

            async def _counting_stream():
                nonlocal total_bytes
                async for chunk in audio_stream:
                    total_bytes += len(chunk)
                    yield chunk

            await stream_audio_bytes_to_session(
                session_id=session_id,
                response_id=query_id,
                audio_stream=_counting_stream(),
                chunk_size=6400,
            )

            run.main_llm_response = "".join(layer1_tokens).strip()
            run.main_llm_tokens = len(layer1_tokens)
            run.tts_audio_bytes = total_bytes
            run.status = "ok"

            layer1_done_event.set()
            logger.info(
                f"Query {query_id}: Layer 1 complete — "
                f"{len(layer1_tokens)} tokens, {total_bytes} audio bytes"
            )

        async def _layer2_video() -> str | None:
            """Layer 2a: video summarization."""
            if not video_bytes:
                return None
            video_data_url = to_data_url(video_bytes, "video/mp4")
            try:
                result = await summarize_video(
                    profile=profile,
                    tracer=tracer,
                    video_data_url=video_data_url,
                    prompt_hint=transcript or "",
                )
                run.video_summary = result
                logger.info(
                    f"Query {query_id}: video_summary = "
                    f"{result[:100] if result else 'None'}..."
                )
                return result
            except Exception as vid_exc:
                run.video_error = str(vid_exc)
                logger.warning(
                    f"Query {query_id}: video summarization failed: {vid_exc}"
                )
                return None
            finally:
                run.video_prompt_sent = str(
                    profile.prompts.get("nemotron_video_prompt", "")
                )

        async def _layer2_tools() -> list[ToolRunResult]:
            """Layer 2b: tool execution."""
            tool_input = {
                "prompt": transcript or "",
                "transcript": transcript,
                "video_summary": None,  # not available yet
                "history": [],
                "mcp_servers": profile.mcp_servers,
            }
            try:
                results = await run_requested_tools(
                    profile=profile,
                    tracer=tracer,
                    context=tool_input,
                )
                run.tool_runs = [
                    {"tool": item.name, "status": item.status, "output": item.output}
                    for item in results
                ]
                return results
            except Exception as tool_exc:
                logger.warning(f"Query {query_id}: tools failed: {tool_exc}")
                return []

        # Launch Layer 1, video, and tools concurrently
        layer1_task = asyncio.create_task(_layer1())
        video_task = asyncio.create_task(_layer2_video())
        tools_task = asyncio.create_task(_layer2_tools())

        # Wait for all to complete
        layer2_video, layer2_tools, _ = await asyncio.gather(
            video_task,
            tools_task,
            layer1_task,
            return_exceptions=False,
        )

        # ── Layer 3: enriched follow-up if context changes the answer ─────
        if layer2_video or layer2_tools:
            tool_context = _build_tools_context(layer2_tools)

            messages_l3 = build_messages_for_main_llm(
                history=[],
                user_prompt=transcript or "",
                audio_transcript=transcript,
                video_summary=layer2_video,
                image_data_urls=[],
                system_prompt=profile.prompts["main_system_prompt"],
                tool_context=tool_context,
            )

            model = profile.main_llm.model
            layer3_tokens: list[str] = []

            await tracer.event(
                "ios_query.layer3_llm_start",
                data={"model": model, "messages_count": len(messages_l3)},
            )

            async def _l3_token_stream():
                # Emit the bridging phrase first so the follow-up sounds natural
                yield _LAYER3_BRIDGE
                async for token in iter_main_llm_tokens(
                    profile=profile,
                    model=model,
                    messages=messages_l3,
                    tracer=tracer,
                    debug_capture=None,
                ):
                    layer3_tokens.append(token)
                    yield token

            audio_stream_l3, _ = await prepare_elevenlabs_live_stream(
                profile=profile,
                tracer=tracer,
                text_iterator=_l3_token_stream(),
                voice_id=None,
                model_id=None,
                speed=None,
                output_format="pcm_16000",
            )

            layer3_text = "".join(layer3_tokens).strip()

            if _responses_differ("".join(layer1_tokens), layer3_text):
                logger.info(
                    f"Query {query_id}: Layer 3 enrichment differs — streaming follow-up"
                )
                await stream_audio_bytes_to_session(
                    session_id=session_id,
                    response_id=f"{query_id}_enriched",
                    audio_stream=audio_stream_l3,
                    chunk_size=6400,
                )
                logger.info(
                    f"Query {query_id}: Layer 3 complete — {len(layer3_tokens)} tokens"
                )
            else:
                # Drain the generator so the ElevenLabs WebSocket closes cleanly
                async for _ in audio_stream_l3:
                    pass
                logger.info(f"Query {query_id}: Layer 3 skipped — response unchanged")

        await tracer.event("ios_query.complete", data={"query_id": query_id})
        logger.info(f"Query {query_id}: processing complete")

    except Exception as exc:
        run.status = "error"
        run.error = str(exc)
        await tracer.event(
            "ios_query.error",
            status="error",
            data={"query_id": query_id, "error": str(exc)},
        )
        logger.exception(f"Query {query_id} failed: {exc}")
        raise
    finally:
        run.finished_at = _utc_now()
        run.metadata = {
            "agent_id": str(profile.metadata.get("agent_id", "")),
            "agent_name": str(profile.metadata.get("agent_name", "")),
        }
        RUN_LOG.record(run)
        logger.info(f"Query {query_id}: run log recorded (status={run.status})")


# ── Background wrapper ─────────────────────────────────────────────────────────


def create_mock_request():
    """Create a mock Request object for profile resolution."""

    class MockRequest:
        def __init__(self):
            self.headers = {}

    return MockRequest()


async def process_ios_query_background(
    session_id: str,
    query_id: str,
    audio_bytes: bytes,
    video_bytes: bytes,
    metadata: dict[str, Any],
    runtime_config_json: str | None = None,
) -> None:
    """Background task wrapper for iOS query processing."""
    try:
        runtime = parse_runtime_config(runtime_config_json)
        mock_request = create_mock_request()
        profile = resolve_runtime_profile(mock_request, runtime)
        tracer = build_trace_manager(profile.trace)

        await process_ios_query(
            session_id=session_id,
            query_id=query_id,
            audio_bytes=audio_bytes,
            video_bytes=video_bytes,
            metadata=metadata,
            profile=profile,
            tracer=tracer,
        )

    except Exception as exc:
        logger.exception(f"Background processing failed for query {query_id}: {exc}")

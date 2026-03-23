from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.realtime.audio_uplink import ClientAudioUplink
    from backend.realtime.contracts import EnvelopeSender, RealtimeLifecycleAdapter
    from backend.realtime.tool_dispatcher import ToolCallDispatcher

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TurnState:
    audio_seen: bool = False
    response_started: bool = False
    manual_response_sent: bool = False
    manual_finalize_error_sent: bool = False
    last_audio_at_monotonic: float | None = None
    server_vad_speaking: bool = False
    has_active_upstream_response: bool = False
    current_response_id: str | None = None
    cancelled_response_ids: set[str] = field(default_factory=set)
    started_response_ids: set[str] = field(default_factory=set)
    last_stopped_response_id: str | None = None

    def reset(self) -> None:
        self.audio_seen = False
        self.response_started = False
        self.manual_response_sent = False
        self.manual_finalize_error_sent = False
        self.last_audio_at_monotonic = None
        self.current_response_id = None
        self.has_active_upstream_response = False
        self.cancelled_response_ids.clear()
        self.started_response_ids.clear()
        self.last_stopped_response_id = None


@dataclass(slots=True)
class TurnConfig:
    server_turn_detection_enabled: bool = False
    manual_turn_fallback_enabled: bool = True
    manual_turn_fallback_delay_s: float = 0.9
    response_create_starts_turn: bool = True


class TurnManager:
    def __init__(
        self,
        *,
        session_id: str,
        config: TurnConfig,
        state: TurnState,
        upstream_client: "RealtimeLifecycleAdapter",
        audio_uplink: "ClientAudioUplink",
        tool_dispatcher: "ToolCallDispatcher",
        send_envelope: "EnvelopeSender",
    ) -> None:
        self._session_id = session_id
        self._config = config
        self._state = state
        self._upstream_client = upstream_client
        self._audio_uplink = audio_uplink
        self._tool_dispatcher = tool_dispatcher
        self._send_envelope = send_envelope
        self._finalize_task: asyncio.Task[None] | None = None
        self._finalize_lock = asyncio.Lock()

    @property
    def state(self) -> TurnState:
        return self._state

    def on_client_audio(self) -> None:
        self._state.audio_seen = True
        self._state.last_audio_at_monotonic = time.monotonic()
        self._schedule_inactivity_finalize()

    def on_response_created(self) -> None:
        self._state.has_active_upstream_response = True
        self._state.response_started = True
        self._cancel_finalize_task()

    def on_vad_speech_started(self) -> None:
        self._state.server_vad_speaking = True

    def on_vad_speech_stopped(self) -> None:
        self._state.server_vad_speaking = False

    def on_audio_delta(self) -> None:
        self._state.response_started = True

    def reset(self) -> None:
        self._state.reset()
        self._tool_dispatcher.reset_turn_state()
        self._cancel_finalize_task()

    def mark_response_cancelled(self) -> str | None:
        response_id = self._state.current_response_id
        if response_id is None or response_id in self._state.cancelled_response_ids:
            return None
        self._state.cancelled_response_ids.add(response_id)
        self._state.started_response_ids.discard(response_id)
        self._state.last_stopped_response_id = response_id
        self._state.current_response_id = None
        self._state.has_active_upstream_response = False
        return response_id

    def register_audio_delta(self, *, response_id: str) -> bool:
        if response_id in self._state.cancelled_response_ids:
            return False

        should_emit_start = False
        if response_id not in self._state.started_response_ids:
            self._state.started_response_ids.add(response_id)
            if self._state.last_stopped_response_id == response_id:
                self._state.last_stopped_response_id = None
            should_emit_start = True

        self._state.current_response_id = response_id
        self._state.response_started = True
        self._state.has_active_upstream_response = True
        return should_emit_start

    def resolve_response_done_id(self, *, response_id: str | None) -> str | None:
        resolved = response_id
        if resolved is None and len(self._state.started_response_ids) == 1:
            resolved = next(iter(self._state.started_response_ids))
        if resolved is None:
            resolved = self._state.current_response_id
        if resolved is None:
            return None
        if resolved == self._state.last_stopped_response_id:
            return None

        self._state.started_response_ids.discard(resolved)
        if resolved in self._state.cancelled_response_ids:
            self._state.cancelled_response_ids.remove(resolved)
            return None

        self._state.last_stopped_response_id = resolved
        if self._state.current_response_id == resolved:
            self._state.current_response_id = None
        self._state.has_active_upstream_response = False
        return resolved

    def is_response_cancelled(self, *, response_id: str) -> bool:
        return response_id in self._state.cancelled_response_ids

    def client_end_turn_ignore_reason(self) -> str | None:
        if not self._config.server_turn_detection_enabled:
            return None
        if self._state.has_active_upstream_response:
            return "active_upstream_response"
        if self._state.response_started:
            return "response_already_started_for_turn"
        return None

    def check_finalize_blockers(self, reason: str) -> str | None:
        if not self._config.manual_turn_fallback_enabled:
            return "manual_fallback_disabled"

        if reason in {"continuous_uplink_timeout", "speech_stopped"}:
            if self._config.server_turn_detection_enabled:
                return "server_turn_detection_active"

        if reason == "client_end_turn":
            ignore_reason = self.client_end_turn_ignore_reason()
            if ignore_reason is not None:
                return f"client_end_turn_ignored:{ignore_reason}"

        if not self._state.audio_seen:
            return "no_audio_seen"
        if self._state.response_started:
            return "response_already_started"
        if self._state.manual_response_sent:
            return "manual_response_already_sent"
        if self._state.has_active_upstream_response:
            return "active_upstream_response"

        if reason == "continuous_uplink_timeout":
            if self._state.server_vad_speaking:
                return "vad_speaking"
            if self._state.last_audio_at_monotonic is None:
                return "no_audio_timestamp"
            inactivity = time.monotonic() - self._state.last_audio_at_monotonic
            if inactivity < self._config.manual_turn_fallback_delay_s:
                return "inactivity_below_threshold"

        return None

    async def finalize_turn_if_needed(self, *, reason: str) -> None:
        async with self._finalize_lock:
            await self._finalize_turn_impl(reason=reason)

    async def _finalize_turn_impl(self, *, reason: str) -> None:
        blocker = self.check_finalize_blockers(reason)
        if blocker is not None:
            if reason == "client_end_turn":
                logger.debug(
                    "Skipping turn finalize session=%s reason=%s blocker=%s",
                    self._session_id,
                    reason,
                    blocker,
                )
            return

        self._audio_uplink.raise_if_failed()
        drained = await self._audio_uplink.wait_for_drain(timeout_seconds=1.5)
        if not drained:
            if not self._state.manual_finalize_error_sent:
                self._state.manual_finalize_error_sent = True
                await self._send_envelope(
                    "error",
                    {
                        "code": "UPSTREAM_AUDIO_FLUSH_TIMEOUT",
                        "message": "Timed out flushing client audio before turn finalization",
                        "retriable": True,
                    },
                )
            return

        logger.info(
            "Manual turn finalize session=%s reason=%s queued_frames=%s sent_frames=%s",
            self._session_id,
            reason,
            self._audio_uplink.queue_size,
            self._audio_uplink.sent_count,
        )
        await self._upstream_client.commit_client_turn()
        self._state.manual_response_sent = True
        await self._send_response_create(source=f"manual_finalize:{reason}")

    async def _send_response_create(self, source: str) -> None:
        await self._upstream_client.create_response()
        if self._config.response_create_starts_turn:
            self._state.has_active_upstream_response = True
            self._state.response_started = True
        self._cancel_finalize_task()
        logger.info("Upstream response.create sent session=%s source=%s", self._session_id, source)

    def _schedule_inactivity_finalize(self) -> None:
        if not self._config.manual_turn_fallback_enabled:
            return
        if self._config.server_turn_detection_enabled:
            return
        if self._state.response_started or self._state.has_active_upstream_response:
            return
        self._cancel_finalize_task()
        self._finalize_task = asyncio.create_task(
            self._run_inactivity_finalize(),
            name=f"manual_finalize:{self._session_id}",
        )

    async def _run_inactivity_finalize(self) -> None:
        from backend.realtime.client import RealtimeClientError

        try:
            await asyncio.sleep(self._config.manual_turn_fallback_delay_s)
            await self.finalize_turn_if_needed(reason="continuous_uplink_timeout")
        except asyncio.CancelledError:
            return
        except RealtimeClientError as exc:
            logger.warning("Inactivity finalize failed session=%s: %s", self._session_id, exc)
            await self._send_envelope(
                "error",
                {
                    "code": "UPSTREAM_TURN_FINALIZE_FAILED",
                    "message": str(exc) or "Failed to finalize turn upstream",
                    "retriable": True,
                },
            )
        except Exception:
            logger.exception("Unexpected inactivity finalize failure session=%s", self._session_id)
            await self._send_envelope(
                "error",
                {
                    "code": "UPSTREAM_TURN_FINALIZE_FAILED",
                    "message": "Failed to finalize turn upstream",
                    "retriable": True,
                },
            )

    def _cancel_finalize_task(self) -> None:
        task = self._finalize_task
        self._finalize_task = None
        if task is not None:
            task.cancel()

    def cancel_all_tasks(self) -> None:
        self._cancel_finalize_task()

    def is_interrupt_race_expected(
        self,
        *,
        code: str,
        message: str,
        saw_duplicate_tool_call: bool,
    ) -> bool:
        lower_code = code.strip().lower()
        lower_message = message.strip().lower()
        is_interrupt_cancel_race = lower_code == "response_cancel_not_active" or (
            "cancel" in lower_message and "no active response" in lower_message
        )
        if is_interrupt_cancel_race:
            return True

        is_active_response_race = lower_code == "conversation_already_has_active_response" or (
            "already" in lower_message and "active response" in lower_message
        )
        if not is_active_response_race:
            return False

        return (
            self._config.server_turn_detection_enabled
            or self._state.has_active_upstream_response
            or self._state.response_started
            or saw_duplicate_tool_call
        )

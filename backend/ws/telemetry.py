from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from backend.ws.session.session_registry import SessionRecord

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SessionTelemetry:
    connection_id: int
    uplink_ack_every_n_frames: int
    did_log_first_client_audio_frame: bool = False
    did_emit_uplink_ack: bool = False
    uplink_ack_count: int = 0
    client_audio_frame_count: int = 0
    client_audio_total_bytes: int = 0

    def log_receive_shape(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        has_text_payload = isinstance(message.get("text"), str)
        has_bytes_payload = isinstance(
            message.get("bytes"), (bytes, bytearray, memoryview)
        )
        if has_bytes_payload and not has_text_payload:
            return
        logger.debug(
            "WS_RECEIVE_SHAPE connection_id=%s type=%s has_text=%s has_bytes=%s text_len=%s byte_len=%s",
            self.connection_id,
            message_type,
            has_text_payload,
            has_bytes_payload,
            len(message["text"]) if has_text_payload else 0,
            len(message["bytes"]) if has_bytes_payload else 0,
        )

    def record_empty_binary_frame(
        self,
        *,
        active_session: SessionRecord,
        frame_ts_ms: int,
    ) -> dict[str, Any]:
        logger.info(
            "Ignoring empty client audio frame connection_id=%s session=%s ts_ms=%s",
            self.connection_id,
            active_session.session_id,
            frame_ts_ms,
        )
        return {
            "code": "EMPTY_CLIENT_AUDIO_FRAME",
            "message": "Client audio frame payload is empty",
            "retriable": False,
        }

    def record_binary_audio_frame(
        self,
        *,
        active_session: SessionRecord,
        payload_bytes: bytes,
        frame_ts_ms: int,
    ) -> dict[str, int | bool] | None:
        self.client_audio_frame_count += 1
        self.client_audio_total_bytes += len(payload_bytes)
        if not self.did_log_first_client_audio_frame:
            self.did_log_first_client_audio_frame = True
            logger.debug(
                "First client audio frame received connection_id=%s session=%s bytes=%s total_bytes=%s ts_ms=%s",
                self.connection_id,
                active_session.session_id,
                len(payload_bytes),
                self.client_audio_total_bytes,
                frame_ts_ms,
            )
        if (
            self.client_audio_frame_count == 1
            or self.client_audio_frame_count % self.uplink_ack_every_n_frames == 0
        ):
            if self.client_audio_frame_count == 1:
                logger.debug(
                    "WS_UPLINK_ACK_PREP connection_id=%s session=%s frames_received=%s bytes_received=%s",
                    self.connection_id,
                    active_session.session_id,
                    self.client_audio_frame_count,
                    self.client_audio_total_bytes,
                )
            self.did_emit_uplink_ack = True
            self.uplink_ack_count += 1
            return {
                "frames_received": self.client_audio_frame_count,
                "bytes_received": self.client_audio_total_bytes,
            }
        return None

    def log_health_stats(
        self,
        *,
        envelope_session_id: str,
        payload: dict[str, Any],
        as_integral_int: Callable[[Any], int | None],
    ) -> None:
        enqueued = as_integral_int(payload.get("realtime_audio_frames_enqueued"))
        attempted = as_integral_int(payload.get("realtime_audio_frames_send_attempted"))
        sent = as_integral_int(payload.get("realtime_audio_frames_sent"))
        send_failures = as_integral_int(payload.get("realtime_audio_send_failures"))
        last_send_error = payload.get("realtime_audio_last_send_error")
        force_text_audio_fallback = payload.get("realtime_force_text_audio_fallback")
        socket_connection_id = as_integral_int(payload.get("realtime_socket_connection_id"))
        socket_last_outbound_bytes = as_integral_int(payload.get("realtime_socket_last_outbound_bytes"))
        socket_last_outbound_kind = payload.get("realtime_socket_last_outbound_kind")
        socket_binary_send_attempted = as_integral_int(payload.get("realtime_socket_binary_send_attempted"))
        socket_binary_send_completed = as_integral_int(payload.get("realtime_socket_binary_send_completed"))
        socket_last_binary_first_byte = payload.get("realtime_socket_last_binary_first_byte")
        logger.debug(
            "Health stats session=%s ios_enqueued=%s ios_attempted=%s ios_sent=%s ios_send_failures=%s ios_last_send_error=%s ios_force_text_audio_fallback=%s ios_socket_connection_id=%s ios_socket_last_outbound_kind=%s ios_socket_last_outbound_bytes=%s ios_socket_binary_send_attempted=%s ios_socket_binary_send_completed=%s ios_socket_last_binary_first_byte=%s backend_frames=%s backend_bytes=%s uplink_ack_emitted=%s uplink_ack_count=%s",
            envelope_session_id,
            enqueued,
            attempted,
            sent,
            send_failures,
            last_send_error if isinstance(last_send_error, str) else "-",
            force_text_audio_fallback if isinstance(force_text_audio_fallback, bool) else "-",
            socket_connection_id,
            socket_last_outbound_kind if isinstance(socket_last_outbound_kind, str) else "-",
            socket_last_outbound_bytes,
            socket_binary_send_attempted,
            socket_binary_send_completed,
            socket_last_binary_first_byte if isinstance(socket_last_binary_first_byte, str) else "-",
            self.client_audio_frame_count,
            self.client_audio_total_bytes,
            self.did_emit_uplink_ack,
            self.uplink_ack_count,
        )

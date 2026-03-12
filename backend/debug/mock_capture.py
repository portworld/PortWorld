from __future__ import annotations

import logging
import time
from typing import Any

from backend.realtime.audio_dump import PCM16WavDumpWriter
from backend.ws.contracts import now_ms

logger = logging.getLogger(__name__)


class IOSMockCaptureBridge:
    """Debug bridge that captures inbound iOS PCM without upstream OpenAI calls."""

    def __init__(
        self,
        *,
        session_id: str,
        dump_input_audio_enabled: bool = True,
        dump_input_audio_dir: str = "backend/var/debug_audio",
    ) -> None:
        self._session_id = session_id
        self._dump_input_audio_enabled = dump_input_audio_enabled
        self._dump_input_audio_dir = dump_input_audio_dir
        self._frames_received = 0
        self._bytes_received = 0
        self._first_audio_ts: float | None = None
        self._last_audio_ts: float | None = None
        self._closed = False
        self._audio_dump = PCM16WavDumpWriter(
            session_id=session_id,
            dump_dir=dump_input_audio_dir,
            sample_rate=24_000,
            file_name_factory=lambda: f"{session_id}_{now_ms()}_mock.wav",
            label="Mock input audio dump",
            level="warning",
        )

    async def connect_and_start(self) -> None:
        logger.warning(
            "Mock capture mode active session=%s dump_input_audio=%s dump_dir=%s",
            self._session_id,
            self._dump_input_audio_enabled,
            self._dump_input_audio_dir,
        )

    async def append_client_audio(self, payload_bytes: bytes) -> None:
        if not payload_bytes:
            logger.warning(
                "Mock capture received empty audio payload session=%s",
                self._session_id,
            )
            return

        now_s = time.monotonic()
        if self._first_audio_ts is None:
            self._first_audio_ts = now_s
        self._last_audio_ts = now_s
        self._frames_received += 1
        self._bytes_received += len(payload_bytes)
        if self._dump_input_audio_enabled:
            self._audio_dump.append(payload_bytes)

    async def finalize_turn(self, *, reason: str = "client_end_turn") -> None:
        logger.info(
            "Mock capture finalize turn session=%s reason=%s frames=%s bytes=%s",
            self._session_id,
            reason,
            self._frames_received,
            self._bytes_received,
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._audio_dump.close()

    def capture_summary(self) -> dict[str, Any]:
        expected_duration_ms = int(round((self._bytes_received / 2.0 / 24_000.0) * 1000.0))
        wall_duration_ms = 0
        if self._first_audio_ts is not None and self._last_audio_ts is not None:
            wall_duration_ms = max(
                0,
                int(round((self._last_audio_ts - self._first_audio_ts) * 1000.0)),
            )
        return {
            "mode": "mock_capture",
            "frames_received": self._frames_received,
            "bytes_received": self._bytes_received,
            "expected_audio_duration_ms": expected_duration_ms,
            "wall_duration_ms": wall_duration_ms,
            "wav_path": self._audio_dump.file_path,
        }

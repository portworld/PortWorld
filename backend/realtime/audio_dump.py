from __future__ import annotations

import logging
import os
import wave
from typing import Callable

logger = logging.getLogger(__name__)


class PCM16WavDumpWriter:
    def __init__(
        self,
        *,
        session_id: str,
        dump_dir: str,
        sample_rate: int,
        file_name_factory: Callable[[], str],
        label: str,
        level: str = "info",
    ) -> None:
        self._session_id = session_id
        self._dump_dir = dump_dir
        self._sample_rate = sample_rate
        self._file_name_factory = file_name_factory
        self._label = label
        self._level = level
        self._writer: wave.Wave_write | None = None
        self._file_path: str | None = None

    @property
    def file_path(self) -> str | None:
        return self._file_path

    def append(self, payload_bytes: bytes) -> None:
        if not payload_bytes:
            return

        writer = self._writer
        if writer is None:
            writer = self._create_writer()
            if writer is None:
                return
            self._writer = writer

        try:
            writer.writeframes(payload_bytes)
        except Exception as exc:
            logger.warning(
                "Failed writing %s session=%s: %s",
                self._label,
                self._session_id,
                exc,
            )

    def close(self) -> None:
        writer = self._writer
        self._writer = None
        if writer is None:
            return
        try:
            writer.close()
        except Exception as exc:
            logger.warning(
                "Failed closing %s session=%s: %s",
                self._label,
                self._session_id,
                exc,
            )

    def _create_writer(self) -> wave.Wave_write | None:
        try:
            os.makedirs(self._dump_dir, exist_ok=True)
            file_path = os.path.join(self._dump_dir, self._file_name_factory())
            writer = wave.open(file_path, "wb")
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(self._sample_rate)
            self._file_path = file_path
            log = getattr(logger, self._level, logger.info)
            log(
                "%s enabled session=%s path=%s",
                self._label,
                self._session_id,
                file_path,
            )
            return writer
        except Exception as exc:
            logger.warning(
                "Failed creating %s session=%s: %s",
                self._label,
                self._session_id,
                exc,
            )
            return None

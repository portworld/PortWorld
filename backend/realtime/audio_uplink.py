from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from typing import Any, Protocol

from backend.realtime.client import RealtimeClientError

logger = logging.getLogger(__name__)


class UpstreamAudioSender(Protocol):
    async def send_json(self, event: dict[str, Any]) -> None: ...


class ClientAudioUplink:
    def __init__(
        self,
        *,
        session_id: str,
        upstream_client: UpstreamAudioSender,
        queue_maxsize: int = 32,
        drop_log_step: int = 25,
    ) -> None:
        self._session_id = session_id
        self._upstream_client = upstream_client
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=max(1, queue_maxsize))
        self._task: asyncio.Task[None] | None = None
        self._drop_log_step = max(1, drop_log_step)
        self._dropped_oldest_count = 0
        self._sent_count = 0
        self._terminal_error: RealtimeClientError | None = None

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def sent_count(self) -> int:
        return self._sent_count

    def start(self) -> None:
        self.raise_if_failed()
        task = self._task
        if task is not None and not task.done():
            return
        self._task = asyncio.create_task(
            self._run_sender_loop(),
            name=f"client_audio_sender:{self._session_id}",
        )

    def raise_if_failed(self) -> None:
        if self._terminal_error is not None:
            raise self._terminal_error
        task = self._task
        if task is not None and task.done() and not task.cancelled():
            raise RealtimeClientError(
                f"Client audio sender stopped unexpectedly for session {self._session_id}"
            )

    def enqueue(self, payload_bytes: bytes) -> None:
        self.start()
        self.raise_if_failed()
        while True:
            try:
                self._queue.put_nowait(payload_bytes)
                return
            except asyncio.QueueFull:
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    continue
                else:
                    self._queue.task_done()
                self._dropped_oldest_count += 1
                drop_count = self._dropped_oldest_count
                if drop_count == 1 or drop_count % self._drop_log_step == 0:
                    logger.warning(
                        "Client audio queue overflow session=%s policy=drop_oldest dropped=%s queue_max=%s",
                        self._session_id,
                        drop_count,
                        self._queue.maxsize,
                    )

    async def wait_for_drain(self, *, timeout_seconds: float = 1.5) -> bool:
        self.raise_if_failed()
        task = self._task
        if task is None or task.done():
            return True
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning(
                "Timed out draining client audio queue session=%s pending=%s",
                self._session_id,
                self._queue.qsize(),
            )
            return False
        self.raise_if_failed()
        return True

    async def shutdown(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return

        if not task.done():
            while True:
                try:
                    self._queue.put_nowait(None)
                    break
                except asyncio.QueueFull:
                    try:
                        self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    else:
                        self._queue.task_done()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except asyncio.TimeoutError:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        else:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run_sender_loop(self) -> None:
        try:
            while True:
                payload_bytes = await self._queue.get()
                try:
                    if payload_bytes is None:
                        return
                    await self._upstream_client.send_json(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(payload_bytes).decode("ascii"),
                        }
                    )
                    self._sent_count += 1
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            raise
        except RealtimeClientError as exc:
            self._terminal_error = exc
            logger.warning("Client audio sender closed for %s: %s", self._session_id, exc)
        except Exception:
            self._terminal_error = RealtimeClientError(
                f"Unexpected client audio sender failure for {self._session_id}"
            )
            logger.exception(
                "Unexpected client audio sender failure for %s",
                self._session_id,
            )

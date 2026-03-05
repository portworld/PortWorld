from __future__ import annotations

import argparse
import asyncio
import base64
import json
from typing import Any

import websockets

from backend.frame_codec import CLIENT_AUDIO_FRAME_TYPE, encode_frame


def _make_envelope(message_type: str, session_id: str, payload: dict[str, Any]) -> str:
    return json.dumps(
        {
            "type": message_type,
            "session_id": session_id,
            "seq": 0,
            "ts_ms": 1_742_000_000_000,
            "payload": payload,
        }
    )


async def _run(args: argparse.Namespace) -> None:
    async with websockets.connect(args.url) as ws:
        await ws.send(
            _make_envelope(
                "session.activate",
                args.session_id,
                {
                    "session": {"type": "realtime"},
                    "audio_format": {
                        "encoding": "pcm_s16le",
                        "channels": 1,
                        "sample_rate": 24_000,
                    },
                },
            )
        )
        print("recv:", await ws.recv())

        payload = bytes((index % 256 for index in range(args.frame_size_bytes)))
        timestamp_ms = args.timestamp_start_ms
        for frame_index in range(args.frame_count):
            await ws.send(encode_frame(CLIENT_AUDIO_FRAME_TYPE, timestamp_ms, payload))
            print(
                "sent binary frame:",
                frame_index + 1,
                "/",
                args.frame_count,
                "payload_bytes=",
                len(payload),
                "timestamp_ms=",
                timestamp_ms,
            )
            timestamp_ms += args.frame_duration_ms
            if args.frame_interval_ms > 0 and frame_index + 1 < args.frame_count:
                await asyncio.sleep(args.frame_interval_ms / 1000.0)

        if args.send_text_fallback:
            await ws.send(
                _make_envelope(
                    "client.audio",
                    args.session_id,
                    {"audio_b64": base64.b64encode(payload).decode("ascii")},
                )
            )
            print("sent text fallback frame:", len(payload), "bytes")

        remaining_acks = args.expect_ack_count
        while remaining_acks > 0:
            try:
                response = await asyncio.wait_for(ws.recv(), timeout=args.ack_timeout_seconds)
            except asyncio.TimeoutError:
                print(
                    "timed out waiting for ack:",
                    args.expect_ack_count - remaining_acks,
                    "/",
                    args.expect_ack_count,
                )
                break
            print("recv:", response)
            remaining_acks -= 1

        await asyncio.sleep(args.settle_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe backend /ws/session audio framing.")
    parser.add_argument("--url", default="ws://127.0.0.1:8080/ws/session")
    parser.add_argument("--session-id", default="sess_probe")
    parser.add_argument("--settle-seconds", type=float, default=0.5)
    parser.add_argument("--frame-size-bytes", type=int, default=4_080)
    parser.add_argument("--frame-count", type=int, default=1)
    parser.add_argument("--frame-duration-ms", type=int, default=85)
    parser.add_argument("--frame-interval-ms", type=int, default=0)
    parser.add_argument("--timestamp-start-ms", type=int, default=42)
    parser.add_argument("--expect-ack-count", type=int, default=1)
    parser.add_argument("--ack-timeout-seconds", type=float, default=1.0)
    parser.add_argument("--send-text-fallback", action="store_true")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()

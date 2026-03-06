# Backend (Loop A Mock)

FastAPI Loop A mock backend bridging iOS transport to OpenAI Realtime.

## Setup

1. Create a virtual environment and install dependencies:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Configure environment:

```bash
cp .env.example .env
```

`OPENAI_API_KEY` is required to activate realtime streaming (`session.activate`).
`OPENAI_REALTIME_ENABLE_MANUAL_TURN_FALLBACK=true` enables a backend fallback that sends
`input_audio_buffer.commit` + `response.create` when VAD does not start a response.
`OPENAI_REALTIME_ALLOW_TEXT_AUDIO_FALLBACK=true` temporarily accepts `client.audio` text/base64
uplink frames for debugging; production framing is binary audio. When used, backend logs an
explicit deprecation warning and this fallback remains temporary.
`OPENAI_REALTIME_UPLINK_ACK_EVERY_N_FRAMES` configures `transport.uplink.ack` cadence for inbound
audio frames (default `20`, minimum `1`; first frame is always acknowledged).
`OPENAI_DEBUG_DUMP_INPUT_AUDIO=true` stores raw incoming iOS PCM as `.wav` files for debugging.
`OPENAI_DEBUG_MOCK_CAPTURE_MODE=true` bypasses OpenAI realtime and captures inbound iOS audio only.
`OPENAI_DEBUG_TRACE_WS_MESSAGES=true` logs raw websocket receive metadata before routing.

### TLS certificate trust (macOS)

If OpenAI websocket/HTTPS calls fail with `CERTIFICATE_VERIFY_FAILED`, configure a CA bundle:

```bash
cd backend
source .venv/bin/activate
python -c "import certifi; print(certifi.where())"
export SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"
```

If you use a python.org macOS Python build and SSL still fails, run the bundled
`Install Certificates.command` once for that Python version.

## Run

From repository root:

```bash
uvicorn backend.app:app --host 0.0.0.0 --port 8080 --log-level info --reload
```

## TLS diagnostics

Use these probes to differentiate cert issues from auth/model issues:

```bash
# 1) CA trust probe (should return HTTP 401, not SSL errors)
python -c "import urllib.request; urllib.request.urlopen('https://api.openai.com/v1/models')"

# 2) Realtime websocket handshake probe
python - <<'PY'
import asyncio, os, websockets
url = "wss://api.openai.com/v1/realtime?model=gpt-realtime"
headers = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}
async def main():
    try:
        ws = await websockets.connect(url, additional_headers=headers)
    except TypeError:
        ws = await websockets.connect(url, extra_headers=headers)
    print("connected")
    await ws.close()
asyncio.run(main())
PY
```

## Audio input dump (debug)

To verify iOS audio reaches backend, enable:

```bash
OPENAI_DEBUG_DUMP_INPUT_AUDIO=true
OPENAI_DEBUG_DUMP_INPUT_AUDIO_DIR=backend/debug_audio
```

Each session writes a `24kHz/mono/int16` WAV file in the configured directory.

## Mock capture mode (debug iPhone uplink only)

To isolate iPhone -> backend audio transport from OpenAI realtime:

```bash
OPENAI_DEBUG_MOCK_CAPTURE_MODE=true
OPENAI_DEBUG_DUMP_INPUT_AUDIO=true
OPENAI_DEBUG_DUMP_INPUT_AUDIO_DIR=backend/debug_audio
```

In this mode:

- `session.activate` succeeds without `OPENAI_API_KEY`
- inbound client audio frames are acknowledged and written to WAV
- `session.deactivate` emits `debug.capture.summary` with frame/byte/duration stats
- no upstream OpenAI websocket is created

## Websocket probe

Use the local probe to validate that `/ws/session` accepts a control envelope plus audio frames:

```bash
python backend/scripts/ws_probe.py --url ws://127.0.0.1:8080/ws/session --session-id sess_probe
```

## Endpoints

- `GET /healthz`
- `POST /vision/frame` with JSON body: `{"frame_id": "optional-id"}`
- `WS /ws/session` for iOS control envelopes + binary audio frames
- Optional staged compatibility (deprecated): `client.audio` text envelopes when `OPENAI_REALTIME_ALLOW_TEXT_AUDIO_FALLBACK=true`

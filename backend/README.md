# Backend

FastAPI backend for the active iPhone-first assistant runtime.

It bridges the iOS app's session websocket transport to OpenAI Realtime and streams assistant audio back to the phone.

## Current Behavior

- single websocket endpoint: `WS /ws/session`
- session-oriented control flow:
  - `session.activate`
  - `wakeword.detected`
  - `session.end_turn`
  - `session.deactivate`
- mixed transport model:
  - JSON control envelopes for lifecycle / status / playback control
  - binary PCM audio frames for uplink and downlink audio
- active assistant audio path:
  - iPhone microphone PCM -> backend -> OpenAI Realtime
  - OpenAI Realtime output audio -> backend -> iPhone speaker
- interruption behavior:
  - upstream OpenAI session uses `server_vad`
  - `interrupt_response=true` is enabled
  - when upstream speech starts during assistant playback, the bridge sends:
    - upstream `response.cancel`
    - downstream `assistant.playback.control { command: "cancel_response" }`
  - expected `response_cancel_not_active` races are treated as benign and are not surfaced as client-breaking errors
- direct audio forwarding:
  - assistant audio is streamed back directly
  - the earlier backend pacing experiment was removed because it degraded playback quality

## Runtime Modes

### Default realtime mode

- creates an OpenAI Realtime websocket session per active iPhone session
- forwards client audio upstream
- relays assistant playback control + assistant PCM downstream

### Mock capture mode

- enabled with `OPENAI_DEBUG_MOCK_CAPTURE_MODE=true`
- does not connect to OpenAI
- captures and optionally dumps inbound audio only
- useful for isolating iPhone -> backend transport

## WebSocket Contract

### Endpoint

- `WS /ws/session`

### Control envelopes

Important client -> backend envelope types:

- `session.activate`
- `wakeword.detected`
- `session.end_turn`
- `session.deactivate`

Important backend -> client envelope types:

- `session.state`
- `transport.uplink.ack`
- `assistant.playback.control`
- `error`

### Binary audio frames

- iPhone -> backend uses frame type `0x01` (`CLIENT_AUDIO_FRAME_TYPE`)
- backend -> iPhone uses frame type `0x02` (`SERVER_AUDIO_FRAME_TYPE`)
- optional probe frame type `0x03` (`CLIENT_PROBE_FRAME_TYPE`)

Expected active audio format:

- `encoding=pcm_s16le`
- `channels=1`
- `sample_rate=24000`

### Session flow

1. iPhone opens `WS /ws/session`
2. iPhone sends `session.activate`
3. backend validates the declared audio format if provided
4. backend creates a per-session bridge
5. backend emits `session.state { state: "active" }`
6. iPhone sends `wakeword.detected`
7. iPhone streams binary audio uplink frames
8. backend acknowledges uplink periodically via `transport.uplink.ack`
9. backend relays assistant playback control and assistant audio back to the iPhone
10. on sleep, end-turn, deactivate, or disconnect, the backend tears the session down

## Structure

### App and routes

- app entrypoint: `backend/app.py`
- app factory: `backend/api/app.py`
- websocket route: `backend/api/routes/session_ws.py`
- health route: `backend/api/routes/health.py`

### Core config

- runtime settings: `backend/core/settings.py`

### WebSocket/session layer

- control parsing + dispatch: `backend/ws/control_dispatch.py`
- binary frame dispatch: `backend/ws/binary_dispatch.py`
- envelope contracts: `backend/ws/contracts.py`
- binary frame codec: `backend/ws/frame_codec.py`
- session activation: `backend/ws/session_activation.py`
- session registry: `backend/ws/session_registry.py`
- session lifecycle helpers: `backend/ws/session_runtime.py`
- session telemetry and transport diagnostics: `backend/ws/telemetry.py`

### Realtime bridge

- OpenAI Realtime client: `backend/realtime/client.py`
- iOS session bridge: `backend/realtime/bridge.py`
- bridge factory / mode selection: `backend/realtime/factory.py`

### Debug helpers

- inbound WAV dump utilities: `backend/debug/audio_dump.py`
- mock capture bridge: `backend/debug/mock_capture.py`
- local probe script: `backend/scripts/ws_probe.py`

## API Surface

- `GET /healthz`
- `POST /vision/frame`
- `WS /ws/session`

`/vision/frame` still exists, but the active assistant runtime is currently centered on the audio-only websocket conversation loop.

## Setup

From repo root:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Environment

### Required for realtime mode

- `OPENAI_API_KEY`

### Main realtime settings

- `OPENAI_REALTIME_MODEL`
  default: `gpt-realtime`
- `OPENAI_REALTIME_VOICE`
  default: `ash`
- `OPENAI_REALTIME_INSTRUCTIONS`
- `OPENAI_REALTIME_INCLUDE_TURN_DETECTION`
  default: `true`
- `OPENAI_REALTIME_ENABLE_MANUAL_TURN_FALLBACK`
  default: `true`
- `OPENAI_REALTIME_MANUAL_TURN_FALLBACK_DELAY_MS`
  default: `900`, min `100`
- `OPENAI_REALTIME_UPLINK_ACK_EVERY_N_FRAMES`
  default: `20`, min `1`
- `OPENAI_REALTIME_ALLOW_TEXT_AUDIO_FALLBACK`
  default: `false`
  compatibility-only path; not used by the active iPhone runtime

### Debug / local runtime settings

- `OPENAI_DEBUG_DUMP_INPUT_AUDIO`
  default: `false`
- `OPENAI_DEBUG_DUMP_INPUT_AUDIO_DIR`
  default: `backend/var/debug_audio`
- `OPENAI_DEBUG_MOCK_CAPTURE_MODE`
  default: `false`
- `OPENAI_DEBUG_TRACE_WS_MESSAGES`
  default: `false`

### Server settings

- `HOST`
  default: `0.0.0.0`
- `PORT`
  default: `8080`
- `LOG_LEVEL`
  default: `INFO`
- `CORS_ORIGINS`
  default: `*`

## Run

From repo root:

```bash
source backend/.venv/bin/activate
uvicorn backend.app:app --host 0.0.0.0 --port 8080 --log-level info --reload
```

Typical local realtime run:

```bash
export OPENAI_REALTIME_ALLOW_TEXT_AUDIO_FALLBACK=false
export OPENAI_DEBUG_MOCK_CAPTURE_MODE=false
export OPENAI_DEBUG_TRACE_WS_MESSAGES=false
uvicorn backend.app:app --host 0.0.0.0 --port 8080 --log-level info --reload
```

Quick check:

```bash
curl http://127.0.0.1:8080/healthz
```

## Probe Script

Use the local probe script to validate the control + binary framing contract:

```bash
source backend/.venv/bin/activate
python backend/scripts/ws_probe.py \
  --url ws://127.0.0.1:8080/ws/session \
  --session-id sess_probe \
  --frame-size-bytes 4080 \
  --frame-count 24 \
  --frame-duration-ms 85 \
  --frame-interval-ms 85 \
  --expect-ack-count 2
```

Deprecated text fallback probe:

```bash
python backend/scripts/ws_probe.py --send-text-fallback
```

## Debug Modes

### 1. Input audio dump

Enable raw inbound PCM16 WAV dumps:

```bash
export OPENAI_DEBUG_DUMP_INPUT_AUDIO=true
export OPENAI_DEBUG_DUMP_INPUT_AUDIO_DIR=backend/var/debug_audio
```

### 2. Mock capture mode

Use this to isolate iPhone -> backend transport without OpenAI:

```bash
export OPENAI_DEBUG_MOCK_CAPTURE_MODE=true
export OPENAI_DEBUG_DUMP_INPUT_AUDIO=true
export OPENAI_DEBUG_DUMP_INPUT_AUDIO_DIR=backend/var/debug_audio
```

Behavior in mock mode:

- `session.activate` works without `OPENAI_API_KEY`
- inbound client audio is accepted, acknowledged, and optionally dumped
- no upstream OpenAI websocket is created

## Notes

- activate the session before sending binary audio frames; pre-activation binary audio is ignored
- empty audio payloads generate an error envelope
- text/base64 audio envelopes are supported only behind the text-fallback flag and are not part of the preferred active runtime path
- the backend is intentionally shaped around the current iPhone assistant runtime contract, not around a generic reusable realtime platform

## TLS Diagnostics (macOS)

If OpenAI calls fail with `CERTIFICATE_VERIFY_FAILED`:

```bash
source backend/.venv/bin/activate
python -c "import certifi; print(certifi.where())"
export SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"
```

If needed for python.org builds, run `Install Certificates.command` once for that Python version.

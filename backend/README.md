# PortWorld Backend

FastAPI backend for the active PortWorld iPhone-first runtime.

The backend is still centered on a realtime session bridge plus a bounded vision upload path, but Step 4A turns it into a cleaner self-hostable service with explicit runtime ownership, persistent storage bootstrap, and a provider seam that later phases can build on.

## Scope

Current scope:

- single-user self-hosted backend
- PortWorld-specific wire contract
- OpenAI as the only active realtime provider
- bounded image upload through `POST /vision/frame`
- persistent backend state under `BACKEND_DATA_DIR`

Not in scope for Step 4A:

- multi-user hosting
- visual memory generation
- web search or MCP execution
- async long-running jobs
- alternate realtime providers

## API Surface

The active backend surface is:

- `GET /healthz`
- `POST /vision/frame`
- `WS /ws/session`

Step 4A keeps the existing websocket and vision wire contract stable while cleaning up backend internals.

## Runtime Lifecycle

The backend now boots through one runtime-owned lifecycle:

1. FastAPI startup creates `AppRuntime`
2. startup bootstraps storage under `BACKEND_DATA_DIR`
3. routes and websocket handlers resolve dependencies from `app.state.runtime`
4. live session coordination stays in memory for active websocket sessions
5. persistent indexing is written to SQLite and filesystem artifacts

This keeps startup, storage, and provider selection under one explicit owner instead of spreading them across import-time globals.

## Realtime Modes

### Default realtime mode

- enabled with `REALTIME_PROVIDER=openai`
- creates one OpenAI Realtime upstream session per active PortWorld session
- forwards uplink audio from the phone to OpenAI
- relays assistant playback control and assistant audio back to the phone

### Mock capture mode

- enabled with `BACKEND_DEBUG_MOCK_CAPTURE_MODE=true`
- does not connect to OpenAI
- captures inbound audio only
- useful for isolating iPhone -> backend transport issues

Mock capture is a backend debug mode, not a separate realtime provider.

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
4. backend creates a per-session bridge through the configured realtime provider
5. backend emits `session.state { state: "active" }`
6. iPhone sends `wakeword.detected`
7. iPhone streams binary audio uplink frames
8. backend acknowledges uplink periodically via `transport.uplink.ack`
9. backend relays assistant playback control and assistant audio back to the iPhone
10. on sleep, end-turn, deactivate, or disconnect, the backend tears the session down and marks the session as ended in persistent storage

## Storage Model

`BACKEND_DATA_DIR` is the single root for persistent backend artifacts.

Default layout:

- `backend/var/portworld.db`
- `backend/var/user/user_profile.md`
- `backend/var/user/user_profile.json`
- `backend/var/session/<session_id>/session_memory.md`
- `backend/var/session/<session_id>/session_memory.json`
- `backend/var/vision_frames/...`
- `backend/var/debug_audio/...`

### SQLite foundation

The backend bootstraps an idempotent SQLite schema on startup using stdlib `sqlite3`.

Current tables:

- `schema_meta`
- `session_index`
- `artifact_index`

What is persisted today:

- session lifecycle status (`active`, `ended`)
- stored artifact metadata and relative paths
- user/session placeholder memory files for later phases

What is not persisted yet:

- visual summaries
- derived short-term context
- extracted profile facts from conversation

### Vision uploads

`POST /vision/frame` still writes the uploaded JPEG plus a JSON sidecar and now also registers both artifacts in `artifact_index`.

### Session placeholders

When a session activates successfully, the backend ensures:

- `session/<session_id>/session_memory.md`
- `session/<session_id>/session_memory.json`

Those files are placeholders for Step 4B and later work. Step 4A does not generate memory content yet.

## Health

`GET /healthz` returns a compact productized payload:

- `status`
- `service`
- `realtime_provider`
- `realtime_model`
- `storage`
- `ws_path`
- `vision_path`
- `mock_capture_mode`

`service` is `portworld-backend`.

`storage` reports `ready` only after startup storage bootstrap succeeds.

## Configuration

### Backend-owned settings

- `REALTIME_PROVIDER`
  default: `openai`
- `BACKEND_DATA_DIR`
  default: `backend/var`
- `BACKEND_SQLITE_PATH`
  default: `<BACKEND_DATA_DIR>/portworld.db`
- `BACKEND_UPLINK_ACK_EVERY_N_FRAMES`
  default: `20`, minimum: `1`
- `BACKEND_ALLOW_TEXT_AUDIO_FALLBACK`
  default: `false`
  compatibility path only; not used by the active iPhone runtime
- `BACKEND_DEBUG_DUMP_INPUT_AUDIO`
  default: `false`
- `BACKEND_DEBUG_DUMP_INPUT_AUDIO_DIR`
  default: `<BACKEND_DATA_DIR>/debug_audio`
- `BACKEND_DEBUG_MOCK_CAPTURE_MODE`
  default: `false`
- `BACKEND_DEBUG_TRACE_WS_MESSAGES`
  default: `false`

### Realtime provider settings

These are still OpenAI-specific in Step 4A:

- `OPENAI_API_KEY`
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
  default: `900`, minimum: `100`

### Server settings

- `HOST`
  default: `0.0.0.0`
- `PORT`
  default: `8080`
- `LOG_LEVEL`
  default: `INFO`
- `CORS_ORIGINS`
  default: `*`

## Local Setup

From repo root:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Typical local `.env` shape:

```dotenv
REALTIME_PROVIDER=openai
BACKEND_DATA_DIR=backend/var
BACKEND_DEBUG_DUMP_INPUT_AUDIO=false
BACKEND_DEBUG_DUMP_INPUT_AUDIO_DIR=backend/var/debug_audio

OPENAI_API_KEY=...
OPENAI_REALTIME_MODEL=gpt-realtime
OPENAI_REALTIME_VOICE=ash
OPENAI_REALTIME_INSTRUCTIONS=You are a concise assistant. Keep answers short, clear, and practical.

HOST=0.0.0.0
PORT=8080
LOG_LEVEL=INFO
CORS_ORIGINS=*
```

## Local Run

From repo root:

```bash
source backend/.venv/bin/activate
uvicorn backend.app:app --host 0.0.0.0 --port 8080 --log-level info --reload
```

Quick health check:

```bash
curl http://127.0.0.1:8080/healthz
```

## Docker Compose

A minimal self-host path is available through the repo root `docker-compose.yml`.

It currently:

- builds one backend service from `backend/Dockerfile`
- loads env from `backend/.env`
- exposes `8080`
- mounts a named volume to `/app/backend/var`
- runs the backend with `uvicorn`
- health-checks `/healthz`

Run:

```bash
docker compose up --build
```

This is the canonical Step 4A self-host path. More polished operator guidance stays in later roadmap work.

## Validation

### Probe script

Use the local websocket probe to validate the control and binary framing contract:

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

### Compile check

```bash
python3 -m compileall backend
```

## Notes

- Missing `OPENAI_API_KEY` does not fail backend startup by itself. It fails when a realtime session actually needs OpenAI.
- Unsupported `REALTIME_PROVIDER` values fail runtime construction and startup.
- Step 4A intentionally keeps the live session registry in memory. SQLite is persistent indexing, not live coordination.
- Product roadmap and later multimodal/backend milestones live under `docs/`, not in this README.

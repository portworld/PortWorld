# PortWorld Backend

FastAPI backend for the active PortWorld iPhone-first runtime.

The backend is centered on a realtime session bridge plus a bounded vision upload path. Steps `4A` and `4B` turn it into a cleaner self-hostable service with explicit runtime ownership, persistent storage bootstrap, and an opt-in visual-memory pipeline built around accepted image observations.

## Scope

Current scope:

- single-user self-hosted backend
- PortWorld-specific wire contract
- OpenAI as the only active realtime provider
- bounded image upload through `POST /vision/frame`
- persistent backend state under `BACKEND_DATA_DIR`
- opt-in visual-memory generation with Mistral

Not in scope for the current backend slice:

- multi-user hosting
- web search or MCP execution
- async long-running jobs
- alternate realtime providers
- user-profile fact promotion from conversation or vision

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
- `backend/var/session/<session_id>/short_term_memory.md`
- `backend/var/session/<session_id>/short_term_memory.json`
- `backend/var/session/<session_id>/session_memory.md`
- `backend/var/session/<session_id>/session_memory.json`
- `backend/var/session/<session_id>/vision_events.jsonl`
- `backend/var/vision_frames/...`
- `backend/var/debug_audio/...`

### SQLite foundation

The backend bootstraps an idempotent SQLite schema on startup using stdlib `sqlite3`.

Current tables:

- `schema_meta`
- `session_index`
- `artifact_index`
- `vision_frame_index`

What is persisted today:

- session lifecycle status (`active`, `ended`)
- stored artifact metadata and relative paths
- vision ingest and processing status
- derived short-term memory artifacts
- derived per-session memory artifacts
- accepted visual observations in `vision_events.jsonl`

What is not persisted yet:

- user-profile facts promoted from conversations
- cross-session semantic memory beyond the profile scaffold

### Vision uploads and derived memory

`POST /vision/frame` still acknowledges quickly after ingest. When `VISION_MEMORY_ENABLED=true`, the backend then:

1. writes the uploaded JPEG plus a JSON sidecar
2. registers ingest artifacts in `artifact_index`
3. records frame status in `vision_frame_index`
4. enqueues the frame into one sequential per-session worker
5. applies cheap gating before any provider call
6. sends accepted frames to the configured vision provider
7. updates:
   - `vision_events.jsonl`
   - `short_term_memory.md`
   - `short_term_memory.json`
   - `session_memory.md`
   - `session_memory.json`

When `VISION_DEBUG_RETAIN_RAW_FRAMES=false`, raw ingest files are deleted after terminal processing. The derived memory artifacts remain on disk.

### Short-term memory

`short_term_memory` is rebuilt on every accepted observation from accepted events inside the last `VISION_SHORT_TERM_WINDOW_SECONDS`.

`short_term_memory.json` contains:

- `session_id`
- `window_start_ts_ms`
- `window_end_ts_ms`
- `current_scene_summary`
- `recent_entities`
- `recent_actions`
- `recent_visible_text`
- `recent_documents`
- `source_frame_ids`

### Session memory

`session_memory` is rolled forward in micro-batches rather than recomputed from the entire session every time.

`session_memory.json` contains:

- `session_id`
- `started_at_ms`
- `updated_at_ms`
- `current_task_guess`
- `environment_summary`
- `recurring_entities`
- `documents_seen`
- `notable_transitions`
- `open_uncertainties`
- `summary_text`

### Profile scaffold

The backend still creates `user/user_profile.md` and `user/user_profile.json`, but Step `4B` does not automatically promote new profile facts into them yet. That remains later work.

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
- `VISION_MEMORY_ENABLED`
  default: `false`
- `VISION_MEMORY_PROVIDER`
  default: `mistral`
- `VISION_MEMORY_MODEL`
  default: `ministral-3b-2512`
- `VISION_SHORT_TERM_WINDOW_SECONDS`
  default: `30`
- `VISION_MIN_ANALYSIS_GAP_SECONDS`
  default: `3`
- `VISION_SCENE_CHANGE_HAMMING_THRESHOLD`
  default: `12`
- `VISION_SESSION_ROLLUP_INTERVAL_SECONDS`
  default: `10`
- `VISION_SESSION_ROLLUP_MIN_ACCEPTED_EVENTS`
  default: `5`
- `VISION_DEBUG_RETAIN_RAW_FRAMES`
  default: `false`
- `REALTIME_TOOLING_ENABLED`
  default: `false`
- `REALTIME_TOOL_TIMEOUT_MS`
  default: `4000`, minimum: `100`
- `REALTIME_WEB_SEARCH_PROVIDER`
  default: `tavily`
- `REALTIME_WEB_SEARCH_MAX_RESULTS`
  default: `3`

### Realtime provider settings

These are still OpenAI-specific for the active realtime path:

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

### Visual-memory provider settings

These are used only when `VISION_MEMORY_ENABLED=true`:

- `MISTRAL_API_KEY`
- `MISTRAL_BASE_URL`

If `VISION_MEMORY_ENABLED=true` and `MISTRAL_API_KEY` is missing, startup fails clearly. When visual memory is disabled, missing Mistral config does not matter.

### Realtime-tooling provider settings

These are used only when `REALTIME_TOOLING_ENABLED=true`:

- `TAVILY_API_KEY`
- `TAVILY_BASE_URL`

Step `4C.1` only adds the runtime-owned tooling foundation. Missing Tavily config does not fail startup. It only means the future `web_search` tool is not available once Step `4C` is implemented further.

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
VISION_MEMORY_ENABLED=false
VISION_MEMORY_PROVIDER=mistral
VISION_MEMORY_MODEL=ministral-3b-2512
VISION_SHORT_TERM_WINDOW_SECONDS=30
VISION_MIN_ANALYSIS_GAP_SECONDS=3
VISION_SCENE_CHANGE_HAMMING_THRESHOLD=12
VISION_SESSION_ROLLUP_INTERVAL_SECONDS=10
VISION_SESSION_ROLLUP_MIN_ACCEPTED_EVENTS=5
VISION_DEBUG_RETAIN_RAW_FRAMES=false
REALTIME_TOOLING_ENABLED=false
REALTIME_TOOL_TIMEOUT_MS=4000
REALTIME_WEB_SEARCH_PROVIDER=tavily
REALTIME_WEB_SEARCH_MAX_RESULTS=3

OPENAI_API_KEY=...
OPENAI_REALTIME_MODEL=gpt-realtime
OPENAI_REALTIME_VOICE=ash
OPENAI_REALTIME_INSTRUCTIONS=You are a concise assistant. Keep answers short, clear, and practical.

MISTRAL_API_KEY=...
TAVILY_API_KEY=

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

This is the canonical self-host path for the current backend slice. More polished operator guidance stays in later roadmap work.

## Validation

### Startup and configuration

Visual memory disabled:

```bash
curl http://127.0.0.1:8080/healthz
```

Expected:

- backend starts normally with `VISION_MEMORY_ENABLED=false`
- `/healthz` reports `service=portworld-backend`
- `/healthz` reports `storage=ready`

Visual memory enabled but misconfigured:

```bash
VISION_MEMORY_ENABLED=true uvicorn backend.app:app --host 127.0.0.1 --port 8080
```

Expected:

- startup fails clearly if `MISTRAL_API_KEY` is missing

### Visual-memory validation

Use a backend config with:

- `VISION_MEMORY_ENABLED=true`
- `MISTRAL_API_KEY=...`
- `VISION_DEBUG_RETAIN_RAW_FRAMES=false`

Then post repeated frames to `/vision/frame` and inspect:

- `session/<session_id>/vision_events.jsonl`
- `session/<session_id>/short_term_memory.json`
- `session/<session_id>/short_term_memory.md`
- `session/<session_id>/session_memory.json`
- `session/<session_id>/session_memory.md`

Useful checks:

- repeated near-identical frames inside the analysis gap should be gated and not all analyzed
- accepted frames should append one event to `vision_events.jsonl`
- accepted frames should rebuild `short_term_memory`
- session rollups should update `session_memory` on the configured cadence

To inspect frame-processing state:

```bash
sqlite3 backend/var/portworld.db "select session_id, frame_id, processing_status, gate_status, gate_reason from vision_frame_index order by ingest_ts_ms desc limit 20;"
```

Expected statuses include:

- `queued`
- `superseded`
- `gated_rejected`
- `analysis_failed`
- `analyzed`

### Raw-frame cleanup

With `VISION_DEBUG_RETAIN_RAW_FRAMES=false`, raw ingest files under `vision_frames/` should be deleted after terminal processing while derived memory artifacts remain.

With `VISION_DEBUG_RETAIN_RAW_FRAMES=true`, raw ingest files should remain on disk for inspection.

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

### Session finalization

After a websocket session ends, verify that:

- pending accepted events have been flushed into `session_memory`
- `session/<session_id>/vision_events.jsonl` remains on disk
- the final `session_memory.json` reflects the last accepted observations

## Notes

- Missing `OPENAI_API_KEY` does not fail backend startup by itself. It fails when a realtime session actually needs OpenAI.
- Unsupported `REALTIME_PROVIDER` values fail runtime construction and startup.
- Visual memory is opt-in. It is enabled only when `VISION_MEMORY_ENABLED=true`.
- Accepted visual observations are stored as derived events. Raw frames are deleted by default after processing.
- Step 4A intentionally keeps the live session registry in memory. SQLite is persistent indexing, not live coordination.
- Product roadmap and later multimodal/backend milestones live under `docs/`, not in this README.

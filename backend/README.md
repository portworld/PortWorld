# PortWorld Backend

FastAPI backend for the active PortWorld iPhone-first runtime.

The backend is centered on a realtime session bridge plus a bounded vision upload path. Steps `4A` and `4B` turn it into a cleaner self-hostable service with explicit runtime ownership, persistent storage bootstrap, and an opt-in visual-memory pipeline built around accepted image observations.

Adaptive routing is active in the visual-memory pipeline. This is still a semantic-memory lane, not a navigation-grade fast-perception lane.

## Scope

Current scope:

- single-user self-hosted backend
- PortWorld-specific wire contract
- OpenAI as the only active realtime provider
- bounded image upload through `POST /vision/frame`
- persistent backend state under `BACKEND_DATA_DIR`
- opt-in visual-memory generation with Mistral
- opt-in realtime tooling with memory tools and optional Tavily search

Not in scope for the current backend slice:

- multi-user hosting
- MCP execution
- async long-running jobs
- alternate realtime providers
- user-profile fact promotion from conversation or vision

## API Surface

The active backend surface is:

- `GET /healthz`
- `GET /profile`
- `PUT /profile`
- `POST /profile/reset`
- `GET /memory/export`
- `POST /memory/session/{session_id}/reset`
- `POST /vision/frame`
- `WS /ws/session`

Auth behavior:

- when `BACKEND_BEARER_TOKEN` is set, `/ws/session`, `/vision/frame`, `/profile`, and `/memory/*` require `Authorization: Bearer <token>`
- when `BACKEND_BEARER_TOKEN` is unset, the backend keeps the current local-dev behavior and does not require auth

Step 4A keeps the existing websocket and vision wire contract stable while cleaning up backend internals.

## Runtime Lifecycle

The backend now boots through one runtime-owned lifecycle:

1. FastAPI startup creates `AppRuntime`
2. startup bootstraps storage under `BACKEND_DATA_DIR`
3. startup sweeps expired ended session-memory sets using `BACKEND_SESSION_MEMORY_RETENTION_DAYS`
4. routes and websocket handlers resolve dependencies from `app.state.runtime`
5. live session coordination stays in memory for active websocket sessions
6. persistent indexing is written to SQLite and filesystem artifacts

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
- `backend/var/session/<session_id>/vision_routing_events.jsonl`
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
- vision routing status, reason, score, and metadata
- derived short-term memory artifacts
- derived per-session memory artifacts
- accepted visual observations in `vision_events.jsonl`
- per-frame routing audit events in `vision_routing_events.jsonl`

What is not persisted yet:

- automatic user-profile facts promoted from conversations
- cross-session semantic memory beyond the profile scaffold

### Vision uploads and derived memory

`POST /vision/frame` still acknowledges quickly after ingest. When `VISION_MEMORY_ENABLED=true`, the backend then:

1. writes the uploaded JPEG plus a JSON sidecar
2. registers ingest artifacts in `artifact_index`
3. records frame status in `vision_frame_index`
4. enqueues the frame into one sequential per-session worker
5. computes cheap frame signals and routes each frame to one action:
   - `drop_redundant`
   - `store_only`
   - `defer_candidate`
   - `analyze_now`
6. enforces provider-wide budget and cooldown state
7. calls the heavy provider only for `analyze_now` frames
8. records one routing audit event per processed frame in `vision_routing_events.jsonl`
9. treats provider `429` as explicit rate-limited outcomes:
   - parses `Retry-After` when present
   - otherwise uses exponential cooldown backoff
   - marks the frame as `analysis_rate_limited` (distinct from ordinary analysis failure)
   - does not immediately retry the same frame
10. updates semantic-memory artifacts only from successful heavy-analysis results
11. updates:
   - `vision_events.jsonl`
   - `short_term_memory.md`
   - `short_term_memory.json`
   - `session_memory.md`
   - `session_memory.json`

When `VISION_DEBUG_RETAIN_RAW_FRAMES=false`, raw ingest files are deleted after terminal processing. Derived memory and routing audit artifacts remain on disk.

### Adaptive routing semantics

The adaptive route actions are:

- `drop_redundant`
  frame is too similar and not needed for freshness
- `store_only`
  frame is indexed but intentionally not analyzed
- `defer_candidate`
  frame is analysis-worthy but provider budget is unavailable; one best deferred candidate is retained per session
- `analyze_now`
  frame is analysis-worthy and provider budget is available

Additional terminal routing status:

- `analysis_rate_limited`
  heavy-analysis attempt hit provider `429`; cooldown is active and the frame is not immediately retried

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

The backend now exposes explicit profile lifecycle behavior:

- `GET /profile` reads the current allowlisted persistent profile facts
- `PUT /profile` writes the allowlisted onboarding fields and rewrites both `user_profile.json` and `user_profile.md`
- `POST /profile/reset` resets only persistent profile memory back to the scaffold state

Current persisted allowlisted profile fields are:

- `name`
- `job`
- `company`
- `preferences`
- `projects`

`user_profile.json` may also contain additive lifecycle metadata under `profile_metadata`, but Step `4C` prompt injection still reads only the top-level allowlisted fields.

The backend still does not automatically promote new profile facts from conversations or vision into the persistent profile store. That remains later work.

### Session-memory lifecycle

Per-session derived memory artifacts have a separate lifecycle from the persistent user profile.

The backend now provides:

- `GET /memory/export`
  returns a bounded zip archive containing:
  - `user_profile.md`
  - `user_profile.json`
  - session derived-memory artifacts
  - `manifest.json`
- `POST /memory/session/{session_id}/reset`
  resets one persisted session-memory set only
  - returns `409` for active sessions
  - returns `404` when that session has no persisted memory set

The export surface intentionally excludes:

- raw vision frames
- debug audio dumps
- unrelated backend runtime state

### Retention

Ended session-memory sets are retained for a bounded period controlled by:

- `BACKEND_SESSION_MEMORY_RETENTION_DAYS`
  default: `30`

Retention behavior:

- expired ended sessions are swept once at backend startup
- expired ended sessions are swept again after session finalization
- active sessions are never removed by retention
- persistent `user_profile.md/json` is never removed by retention
- retention reuses the same deletion path as explicit session reset

### Semantic-memory stability contract

The Step `4B` semantic-memory outputs and shapes remain unchanged:

- `vision_events.jsonl`
- `short_term_memory.md/json`
- `session_memory.md/json`

Only successful heavy-analysis results append to `vision_events.jsonl` and can mutate short-term/session memory. Routed `drop_redundant`, `store_only`, unresolved `defer_candidate`, and `analysis_rate_limited` frames do not mutate semantic memory.

## Realtime Tooling

When `REALTIME_TOOLING_ENABLED=true`, the backend registers a small tool catalog with the active OpenAI realtime session.

Current tool catalog:

- `get_short_term_visual_context`
  returns the current `short_term_memory.json` payload for the active session
- `get_session_visual_context`
  returns the current `session_memory.json` payload for the active session
- `web_search`
  available only when `TAVILY_API_KEY` is configured
  returns bounded snippets-only search results

Tooling policy:

- short-term and session memory are not injected into every turn
- the model is instructed to fetch visual context only when the request depends on it
- tool execution stays backend-side and is not surfaced directly in the iOS UI
- MCP-backed tools are not active in the current backend slice

### Profile injection

When tooling is enabled, the backend appends two compact blocks to the realtime instructions:

- a tool-usage policy block
- a stable profile block when supported fields exist in `user_profile.json`

Current supported injected profile fields:

- `name`
- `job`
- `company`
- `preferences`
- `projects`

## Health

`GET /healthz` is intentionally public and minimal.

It returns:

- `status`
- `service`

`service` is `portworld-backend`.

Detailed provider/runtime/storage state is intentionally not exposed through this endpoint in the current cleanup pass.

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
- `BACKEND_BEARER_TOKEN`
  default: unset
  when set, requires `Authorization: Bearer <token>` on `/ws/session`, `/vision/frame`, `/profile`, and `/memory/*`
- `BACKEND_MAX_VISION_REQUEST_BYTES`
  default: `4000000`
  rejects oversized `/vision/frame` requests using `Content-Length` when present
- `BACKEND_MAX_VISION_FRAME_BYTES`
  default: `2500000`
  rejects oversized decoded JPEG payloads before write
- `BACKEND_SESSION_MEMORY_RETENTION_DAYS`
  default: `30`, minimum: `1`
  removes expired ended session-memory sets at startup and after later session finalization
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
- `VISION_PROVIDER_API_KEY`
  default: unset
- `VISION_PROVIDER_BASE_URL`
  default: unset
- `VISION_SHORT_TERM_WINDOW_SECONDS`
  default: `30`
- `VISION_MIN_ANALYSIS_GAP_SECONDS`
  default: `3`
- `VISION_SCENE_CHANGE_HAMMING_THRESHOLD`
  default: `12`
- `VISION_PROVIDER_MAX_RPS`
  default: `1`
- `VISION_ANALYSIS_HEARTBEAT_SECONDS`
  default: `15`
- `VISION_PROVIDER_BACKOFF_INITIAL_SECONDS`
  default: `5`
- `VISION_PROVIDER_BACKOFF_MAX_SECONDS`
  default: `60`
- `VISION_DEFERRED_CANDIDATE_TTL_SECONDS`
  default: `10`
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

- `VISION_PROVIDER_API_KEY`
- `VISION_PROVIDER_BASE_URL`
- `MISTRAL_API_KEY`
- `MISTRAL_BASE_URL`

The active adapter uses a non-streaming OpenAI-compatible `/v1/chat/completions` request shape. `VISION_PROVIDER_API_KEY` and `VISION_PROVIDER_BASE_URL` are the preferred overrides when pointing the visual-memory runtime at a compatible backend such as NVIDIA NIM. `MISTRAL_API_KEY` and `MISTRAL_BASE_URL` remain supported as backward-compatible fallbacks for the default Mistral path.

If `VISION_MEMORY_ENABLED=true` and neither `VISION_PROVIDER_API_KEY` nor `MISTRAL_API_KEY` is set, startup fails clearly. When visual memory is disabled, missing provider config does not matter.

### Realtime-tooling provider settings

These are used only when `REALTIME_TOOLING_ENABLED=true`:

- `TAVILY_API_KEY`
- `TAVILY_BASE_URL`

Missing Tavily config does not fail startup. It only means `web_search` is omitted from the registered tool catalog for that runtime.

### Server settings

- `HOST`
  default: `0.0.0.0`
- `PORT`
  default: `8080`
- `LOG_LEVEL`
  default: `INFO`
- `CORS_ORIGINS`
  default: `*`
  local-dev default only; production should set explicit allowed origins

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
BACKEND_SESSION_MEMORY_RETENTION_DAYS=30
BACKEND_BEARER_TOKEN=
BACKEND_MAX_VISION_REQUEST_BYTES=4000000
BACKEND_MAX_VISION_FRAME_BYTES=2500000
BACKEND_DEBUG_DUMP_INPUT_AUDIO=false
BACKEND_DEBUG_DUMP_INPUT_AUDIO_DIR=backend/var/debug_audio
VISION_MEMORY_ENABLED=false
VISION_MEMORY_PROVIDER=mistral
VISION_MEMORY_MODEL=ministral-3b-2512
VISION_PROVIDER_API_KEY=
VISION_PROVIDER_BASE_URL=
VISION_SHORT_TERM_WINDOW_SECONDS=30
VISION_MIN_ANALYSIS_GAP_SECONDS=3
VISION_SCENE_CHANGE_HAMMING_THRESHOLD=12
VISION_PROVIDER_MAX_RPS=1
VISION_ANALYSIS_HEARTBEAT_SECONDS=15
VISION_PROVIDER_BACKOFF_INITIAL_SECONDS=5
VISION_PROVIDER_BACKOFF_MAX_SECONDS=60
VISION_DEFERRED_CANDIDATE_TTL_SECONDS=10
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

Typical NVIDIA NIM override for the same provider path:

```dotenv
VISION_MEMORY_ENABLED=true
VISION_MEMORY_PROVIDER=mistral
VISION_MEMORY_MODEL=mistralai/ministral-14b-instruct-2512
VISION_PROVIDER_API_KEY=...
VISION_PROVIDER_BASE_URL=https://integrate.api.nvidia.com
```

## Local Run

From repo root:

```bash
source backend/.venv/bin/activate
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8080 --log-level info --reload
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

The repo-root Docker build context excludes local runtime artifacts through `.dockerignore`, including:

- `backend/var/`
- `backend/.env`
- `backend/.venv/`
- `__pycache__/`
- other non-backend repo trees not needed for the backend image build

Run:

```bash
docker compose up --build
```

This is the canonical self-host path for the current backend slice. More polished operator guidance stays in later roadmap work.

## Validation

### Startup and configuration

Base backend / visual-memory check:

```bash
curl http://127.0.0.1:8080/healthz
```

Expected:

- backend starts normally with `VISION_MEMORY_ENABLED=false`
- `/healthz` reports `service=portworld-backend`
- `/healthz` reports `status=ok`

Auth off:

- `/ws/session`, `/vision/frame`, `/profile`, and `/memory/*` keep current local-dev behavior

Auth on:

- when `BACKEND_BEARER_TOKEN` is set, `/ws/session`, `/vision/frame`, `/profile`, and `/memory/*` require `Authorization: Bearer <token>`

Visual memory enabled but misconfigured:

```bash
VISION_MEMORY_ENABLED=true uvicorn backend.app:app --host 127.0.0.1 --port 8080
```

Expected:

- startup fails clearly if `MISTRAL_API_KEY` is missing

Realtime tooling disabled:

- backend behavior stays the same as the Step `4B` visual-memory slice

Realtime tooling enabled with no Tavily key:

- memory tools are still available
- `web_search` is omitted
- startup still succeeds

Realtime tooling enabled with Tavily configured:

- all three tools are available
- profile injection is enabled if supported fields exist in `user_profile.json`

### Visual-memory validation

Use a backend config with:

- `VISION_MEMORY_ENABLED=true`
- `MISTRAL_API_KEY=...`
- `VISION_DEBUG_RETAIN_RAW_FRAMES=false`

Then post repeated frames to `/vision/frame` and inspect:

- `session/<session_id>/vision_events.jsonl`
- `session/<session_id>/vision_routing_events.jsonl`
- `session/<session_id>/short_term_memory.json`
- `session/<session_id>/short_term_memory.md`
- `session/<session_id>/session_memory.json`
- `session/<session_id>/session_memory.md`

Useful checks:

- requests above `BACKEND_MAX_VISION_REQUEST_BYTES` are rejected with `413`
- decoded JPEGs above `BACKEND_MAX_VISION_FRAME_BYTES` are rejected with `413`
- repeated near-identical frames inside the analysis gap should route to `drop_redundant`
- heavy-analysis-worthy frames should route to `analyze_now` when budget is available
- heavy-analysis-worthy frames should route to `defer_candidate` when provider budget/cooldown is unavailable
- every processed frame should append one routing event in `vision_routing_events.jsonl`
- only successful heavy-analysis frames should append one event to `vision_events.jsonl`
- only successful heavy-analysis frames should rebuild `short_term_memory` and feed session rollups
- `429` should persist as `analysis_rate_limited` and start cooldown

To inspect frame-processing state:

```bash
sqlite3 backend/var/portworld.db "select session_id, frame_id, processing_status, gate_status, gate_reason, routing_status, routing_reason, routing_score from vision_frame_index order by ingest_ts_ms desc limit 20;"
```

Expected statuses include:

- `queued`
- `superseded`
- `gated_rejected`
- `stored_only`
- `deferred`
- `analysis_rate_limited`
- `analysis_failed`
- `analyzed`

### Adaptive routing and cooldown validation

Manual checks for adaptive behavior:

1. Routing outcomes
   - inspect `vision_frame_index.routing_status`, `routing_reason`, `routing_score`, and `routing_metadata_json`
   - inspect `session/<session_id>/vision_routing_events.jsonl` for signal, route decision, provider state, and analysis outcome
2. Deferred candidate behavior
   - during cooldown, upload multiple heavy-analysis-worthy frames
   - verify only one best deferred candidate remains per session
   - verify weaker deferred candidates downgrade to `store_only`
   - verify deferred candidates eventually analyze after cooldown or downgrade after TTL expiry
3. `429` cooldown behavior
   - trigger or simulate provider `429`
   - verify no immediate retry for the same frame
   - verify later frames are still routed while cooldown is active
   - verify heavy-analysis attempts pause until cooldown expires
4. Semantic-memory stability
   - verify `vision_events.jsonl`, `short_term_memory`, and `session_memory` change only after successful heavy-analysis results

### Raw-frame cleanup

With `VISION_DEBUG_RETAIN_RAW_FRAMES=false`, raw ingest files under `vision_frames/` should be deleted after terminal processing while derived memory artifacts remain.

With `VISION_DEBUG_RETAIN_RAW_FRAMES=true`, raw ingest files should remain on disk for inspection.

### Realtime-tooling validation

With `REALTIME_TOOLING_ENABLED=true`, validate:

- `get_short_term_visual_context`
  - returns `available: false` with empty context when no short-term memory has been materialized
  - returns the current `short_term_memory.json` payload once visual memory exists
- `get_session_visual_context`
  - returns `available: false` with empty context when no session memory has been materialized
  - returns the current `session_memory.json` payload once session memory exists
- `web_search`
  - appears only when `TAVILY_API_KEY` is configured
  - returns snippets-only results with:
    - `title`
    - `url`
    - `snippet`
  - returns structured tool errors on invalid input, timeout, or provider failure

Bridge-level checks:

- OpenAI session initialization includes tool descriptors when tooling is enabled
- profile instructions include the tool-usage policy block
- supported profile fields from `user_profile.json` are appended when present
- `response.function_call_arguments.done` is handled correctly
- `response.output_item.done` for function calls is handled correctly
- each tool call produces:
  - one `function_call_output`
  - followed by one `response.create`
- malformed tool arguments do not break the live session
- live tooling reads profile context but cannot write or reset persistent profile memory directly

### Profile lifecycle validation

Validate:

- `GET /profile`
  - returns:
    - `profile`
    - `is_onboarded`
    - `missing_fields`
    - `metadata`
  - reports empty scaffold state as not onboarded
- `PUT /profile`
  - accepts only:
    - `name`
    - `job`
    - `company`
    - `preferences`
    - `projects`
  - rejects unknown fields
  - rewrites both:
    - `user/user_profile.json`
    - `user/user_profile.md`
- `POST /profile/reset`
  - clears only persistent profile memory
  - does not touch any session-memory artifacts

### Memory export and reset validation

Validate:

- `GET /memory/export`
  - returns `application/zip`
  - includes:
    - `user/user_profile.md`
    - `user/user_profile.json`
    - bounded derived session-memory artifacts
    - `manifest.json`
  - excludes:
    - `vision_frames/`
    - `debug_audio/`
- `POST /memory/session/{session_id}/reset`
  - returns `200` for ended sessions with persisted memory
  - returns `409` for active sessions
  - returns `404` when the session memory set is missing

### Retention validation

Validate:

- ended sessions older than `BACKEND_SESSION_MEMORY_RETENTION_DAYS` are removed at startup
- ended sessions older than `BACKEND_SESSION_MEMORY_RETENTION_DAYS` are removed after later session finalization
- active sessions remain present
- persistent user-profile artifacts remain present

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
- Realtime tooling is opt-in. It is enabled only when `REALTIME_TOOLING_ENABLED=true`.
- `web_search` is optional and only appears when Tavily is configured.
- Persistent profile onboarding, memory export, session reset, and retention are now backend-owned HTTP flows.
- Automatic profile promotion from conversations or vision is still not active in the current backend slice.
- MCP-backed tools are not active yet in the current backend slice.
- Step 4A intentionally keeps the live session registry in memory. SQLite is persistent indexing, not live coordination.
- Product roadmap and later multimodal/backend milestones live under `docs/`, not in this README.

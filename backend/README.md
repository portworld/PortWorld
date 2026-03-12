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
- `GET /readyz`
- `GET /profile`
- `PUT /profile`
- `POST /profile/reset`
- `GET /memory/export`
- `POST /memory/session/{session_id}/reset`
- `POST /vision/frame`
- `WS /ws/session`

Auth behavior:

- when `BACKEND_BEARER_TOKEN` is set, `/ws/session`, `/vision/frame`, `/profile`, `/memory/*`, and `/readyz` require `Authorization: Bearer <token>`
- when `BACKEND_PROFILE=production`, startup fails unless `BACKEND_BEARER_TOKEN` is set
- `POST /vision/frame` and websocket session setup are rate-limited per session
- optional per-IP rate limits are controlled by `BACKEND_ENABLE_IP_RATE_LIMITS` (enabled by default in production profile)

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

## Realtime Mode

- enabled with `REALTIME_PROVIDER=openai`
- creates one OpenAI Realtime upstream session per active PortWorld session
- forwards uplink audio from the phone to OpenAI
- relays assistant playback control and assistant audio back to the phone

## WebSocket Contract

### Endpoint

- `WS /ws/session`

### Control envelopes

Important client -> backend envelope types:

- `session.activate`
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
6. iPhone streams binary audio uplink frames
7. backend acknowledges uplink periodically via `transport.uplink.ack`
8. backend relays assistant playback control and assistant audio back to the iPhone
9. on sleep, end-turn, deactivate, or disconnect, the backend tears the session down and marks the session as ended in persistent storage

## Storage Model

`BACKEND_DATA_DIR` is the single root for persistent backend artifacts.

Default layout:

- `backend/var/portworld.db`
- `backend/var/user/user_profile.md`
- `backend/var/user/user_profile.json`
- `backend/var/session/<session_storage_key>/short_term_memory.md`
- `backend/var/session/<session_storage_key>/short_term_memory.json`
- `backend/var/session/<session_storage_key>/session_memory.md`
- `backend/var/session/<session_storage_key>/session_memory.json`
- `backend/var/session/<session_storage_key>/vision_events.jsonl`
- `backend/var/session/<session_storage_key>/vision_routing_events.jsonl`
- `backend/var/vision_frames/<session_storage_key>/<frame_storage_key>.jpg`
- `backend/var/vision_frames/<session_storage_key>/<frame_storage_key>.json`

`session_storage_key` and `frame_storage_key` are deterministic collision-safe path components derived from the logical IDs as `<sanitized-prefix>--<sha256>`. The raw logical IDs remain in SQLite and artifact metadata.

For upgrade compatibility, existing pre-Part-2 session and vision-frame directories using the legacy sanitized naming scheme are still read and reused when present.

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
  writes the archive to a temporary file and streams it back without holding the full zip in RAM
  archive contents:
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

`GET /readyz` is the internal readiness endpoint.

It uses the same bearer-token auth model as other protected routes when `BACKEND_BEARER_TOKEN` is set.

It returns:

- `200` with `status=ready` when required runtime dependencies are available
- `503` with `status=not_ready` when a required dependency is missing

In production profile, failing checks are intentionally redacted to avoid leaking internal runtime configuration details.

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
- `BACKEND_PROFILE`
  default: `development`
  when set to `production`, startup enforces bearer token plus explicit host/CORS policy
- `BACKEND_ALLOWED_HOSTS`
  default: `*`
  local-dev default only; production should set explicit allowed hosts
- `BACKEND_FORWARDED_ALLOW_IPS`
  default: `127.0.0.1,::1`
  passed to Uvicorn `forwarded_allow_ips`; trust forwarded client-IP headers only from these direct peer proxy IPs/CIDRs
- `BACKEND_MAX_VISION_REQUEST_BYTES`
  default: `4000000`
  rejects oversized `/vision/frame` requests while the body is being read, including chunked uploads without a trustworthy `Content-Length`
- `BACKEND_MAX_VISION_FRAME_BYTES`
  default: `2500000`
  rejects oversized decoded JPEG payloads before write
- `BACKEND_SESSION_MEMORY_RETENTION_DAYS`
  default: `30`, minimum: `1`
  removes expired ended session-memory sets at startup and after later session finalization
- `BACKEND_RATE_LIMIT_WS_IP_MAX_ATTEMPTS`
  default: `30`, minimum: `1`
- `BACKEND_RATE_LIMIT_WS_SESSION_MAX_ATTEMPTS`
  default: `6`, minimum: `1`
- `BACKEND_RATE_LIMIT_WS_WINDOW_SECONDS`
  default: `60`, minimum: `1`
- `BACKEND_RATE_LIMIT_VISION_IP_MAX_REQUESTS`
  default: `120`, minimum: `1`
- `BACKEND_RATE_LIMIT_VISION_SESSION_MAX_REQUESTS`
  default: `60`, minimum: `1`
- `BACKEND_RATE_LIMIT_VISION_WINDOW_SECONDS`
  default: `60`, minimum: `1`
- `BACKEND_ENABLE_IP_RATE_LIMITS`
  default: `false` in development profile, `true` in production profile
  enables extra per-IP limiting on websocket setup and vision ingest
- `BACKEND_DEBUG_TRACE_WS_MESSAGES`
  default: `false`
  must remain `false` when `BACKEND_PROFILE=production`
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

### Minimum supported env modes

- realtime-only self-host
  set `OPENAI_API_KEY`; keep `VISION_MEMORY_ENABLED=false` and `REALTIME_TOOLING_ENABLED=false`
- realtime plus visual memory
  set `OPENAI_API_KEY`; set `VISION_MEMORY_ENABLED=true`; prefer `VISION_PROVIDER_API_KEY` and optionally `VISION_PROVIDER_BASE_URL`; `MISTRAL_API_KEY` and `MISTRAL_BASE_URL` remain supported as fallback aliases
- realtime plus tooling
  set `OPENAI_API_KEY`; set `REALTIME_TOOLING_ENABLED=true`; set `TAVILY_API_KEY` only if `web_search` should be available

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
  local-dev default only; production profile requires explicit allowed origins

## Self-Hosting

For setup, supported env modes, Compose usage, persistence behavior, and the compact operator reference, see `docs/BACKEND_SELF_HOSTING.md`.

High-signal self-host summary:

- copy `backend/.env.example` to `backend/.env`
- set `OPENAI_API_KEY`
- optionally enable:
  - `VISION_MEMORY_ENABLED=true` with `VISION_PROVIDER_API_KEY` or `MISTRAL_API_KEY`
  - `REALTIME_TOOLING_ENABLED=true` with optional `TAVILY_API_KEY`
- start with `docker compose up --build`
- verify `GET /healthz`
- optionally verify authenticated `GET /readyz` for dependency readiness

The repo-root `docker-compose.yml` remains the supported self-host entrypoint for this backend slice.

Initial operator CLI:

```bash
python3 -m backend.cli check-config
python3 -m backend.cli check-config --full-readiness
python3 -m backend.cli bootstrap-storage
python3 -m backend.cli export-memory --output /tmp/portworld-memory-export.zip
python3 -m backend.cli serve
```

## Validation

For full operator-facing setup and route workflows, use `docs/BACKEND_SELF_HOSTING.md`.

Useful backend sanity checks:

```bash
docker compose config
python3 -m backend.cli check-config
python3 -m backend.cli check-config --full-readiness
curl http://127.0.0.1:8080/healthz
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8080/readyz
python3 -m compileall backend
```

Dependency packaging:

- `backend/requirements.in` is the human-edited top-level runtime dependency list
- `backend/requirements.txt` is the pinned deploy set used by Docker and CLI-based installs
  it improves determinism but is not yet a fully hashed cross-platform lockfile
- refresh `backend/requirements.txt` intentionally when changing backend runtime dependencies

## Notes

- Missing `OPENAI_API_KEY` does not fail backend startup by itself. It fails when a realtime session actually needs OpenAI.
- `python3 -m backend.cli check-config --full-readiness` is the strict operator preflight mode. It validates provider configuration and runs a storage bootstrap probe.
- Unsupported `REALTIME_PROVIDER` values fail runtime construction and startup.
- Visual memory is opt-in. It is enabled only when `VISION_MEMORY_ENABLED=true`.
- Accepted visual observations are stored as derived events. Raw frames are deleted by default after processing.
- Realtime tooling is opt-in. It is enabled only when `REALTIME_TOOLING_ENABLED=true`.
- `web_search` is optional and only appears when Tavily is configured.
- Persistent profile onboarding, memory export, session reset, and retention are now backend-owned HTTP flows.
- The backend now exposes a small operator CLI for `serve`, `check-config`, `bootstrap-storage`, and `export-memory`.
- The active iPhone runtime no longer emits `wakeword.detected`; `session.activate` is the only required conversation-start control message.
- Audio uplink uses binary websocket frames only (`0x01` client audio, `0x02` server audio); `client.audio` text fallback is not supported.
- The production image excludes `backend/scripts/` and `backend/var/`; Docker copies only the backend runtime packages needed to serve HTTP, WebSocket, storage, and provider integrations.
- Automatic profile promotion from conversations or vision is still not active in the current backend slice.
- MCP-backed tools are not active yet in the current backend slice.
- Step 4A intentionally keeps the live session registry in memory. SQLite is persistent indexing, not live coordination.
- Product roadmap and later multimodal/backend milestones live under `docs/`, not in this README.

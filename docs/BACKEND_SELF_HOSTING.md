# PortWorld Backend Self-Hosting

This is the canonical Step 4E self-host path for the current PortWorld backend slice.

It is intentionally narrow:

- one backend service
- one `docker compose` entrypoint
- one env file at `backend/.env`
- one persistent Docker volume for backend data

## Prerequisites

- Docker with Compose support
- one OpenAI API key for realtime sessions

Optional, depending on enabled features:

- one vision provider key for visual memory:
  - preferred: `VISION_PROVIDER_API_KEY`
  - fallback: `MISTRAL_API_KEY`
- one Tavily API key for `web_search`

## Quick Start

From the repo root:

```bash
cp backend/.env.example backend/.env
```

Edit `backend/.env` for one supported runtime mode:

- realtime-only self-host
  - set `OPENAI_API_KEY`
  - keep `VISION_MEMORY_ENABLED=false`
  - keep `REALTIME_TOOLING_ENABLED=false`
- realtime plus visual memory
  - set `OPENAI_API_KEY`
  - set `VISION_MEMORY_ENABLED=true`
  - prefer `VISION_PROVIDER_API_KEY`
  - optional `VISION_PROVIDER_BASE_URL` for compatible hosted endpoints
  - `MISTRAL_API_KEY` and `MISTRAL_BASE_URL` remain supported fallback aliases
- realtime plus tooling
  - set `OPENAI_API_KEY`
  - set `REALTIME_TOOLING_ENABLED=true`
  - set `TAVILY_API_KEY` only if `web_search` should be available

Start the backend:

```bash
docker compose up --build
```

Verify process health:

```bash
curl http://127.0.0.1:8080/healthz
```

Expected response:

```json
{"status":"ok","service":"portworld-backend"}
```

`GET /healthz` confirms process liveness only. It does not validate upstream provider credentials or provider readiness.

## Persistence

Compose mounts one named volume at `/app/backend/var`.

Persisted backend state lives under `BACKEND_DATA_DIR` and includes:

- `portworld.db`
- `user/user_profile.md`
- `user/user_profile.json`
- `session/<session_storage_key>/...` derived memory artifacts

`session_storage_key` is a deterministic collision-safe path component derived from the logical session ID.

Normal restart path:

```bash
docker compose restart backend
```

Rebuild/upgrade path:

```bash
docker compose up --build -d
```

Both paths preserve the named volume unless it is removed explicitly.

## Operator Reference

When `BACKEND_BEARER_TOKEN` is set, include:

```text
Authorization: Bearer <token>
```

Route reference:

- `GET /profile`
  - read the current persistent profile scaffold or populated profile
- `PUT /profile`
  - write allowlisted profile fields:
    - `name`
    - `job`
    - `company`
    - `preferences`
    - `projects`
- `POST /profile/reset`
  - reset persistent profile memory only
- `GET /memory/export`
  - download one bounded zip archive containing profile artifacts, derived session-memory artifacts, and `manifest.json`
- `POST /memory/session/{session_id}/reset`
  - delete one ended session’s persisted memory set
  - active sessions return `409`
- `VISION_DEBUG_RETAIN_RAW_FRAMES`
  - `false` keeps raw-frame retention off for normal self-host operation
  - `true` keeps raw ingest frames on disk for debug inspection
- `BACKEND_ENABLE_DEVTOOLS_PROTOCOL`
  - keep `false` for normal app traffic
  - set `true` only when using repo devtools that send websocket probe frames such as `backend/devtools/ws_probe.py --probe-count 1`

Minimal examples:

```bash
curl http://127.0.0.1:8080/profile
curl -X POST http://127.0.0.1:8080/profile/reset
curl -OJ http://127.0.0.1:8080/memory/export
curl -X POST http://127.0.0.1:8080/memory/session/<session_id>/reset
```

## Notes

- `BACKEND_BEARER_TOKEN` should be set for any shared or remotely reachable deployment.
- `CORS_ORIGINS=*` is the local-dev default, not a recommended production setting.
- Visual memory keeps derived memory by default and deletes raw frames unless debug retention is enabled.
- When `REALTIME_TOOLING_ENABLED=true` and `TAVILY_API_KEY` is unset, the backend still starts but omits `web_search`.

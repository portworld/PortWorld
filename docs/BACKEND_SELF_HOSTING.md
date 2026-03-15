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

Public install path:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://openclaw.ai/install.sh | bash
```

Manual install fallback for a pinned release tag:

```bash
python3 -m pipx install --force "https://github.com/armapidus/PortWorld/archive/refs/tags/<tag>.zip"
portworld init
```

Source-checkout developer path:

```bash
pipx install . --force
portworld init
```

If you prefer the manual path, `cp backend/.env.example backend/.env` still works. Edit `backend/.env` for one supported runtime mode:

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

The image installs the pinned backend runtime set from `backend/requirements.txt`. The editable top-level dependency list lives in `backend/requirements.in`. This improves install determinism, but it is not yet a fully hashed cross-platform lockfile.

Verify process health:

```bash
curl http://127.0.0.1:8080/livez
```

Expected response:

```json
{"status":"ok","service":"portworld-backend"}
```

`GET /livez` confirms process liveness only. It does not validate upstream provider credentials or provider readiness.
Use `GET /livez` for public and Cloud Run liveness checks. `GET /healthz` remains available as a compatibility alias for older local tooling.
Use `portworld ops check-config --full-readiness` for a stricter preflight that includes provider validation and a storage bootstrap probe. The legacy `python3 -m backend.cli check-config --full-readiness` path still works.

Optional operator CLI commands from the repo root:

```bash
# Install or update the public CLI
curl -fsSL --proto '=https' --tlsv1.2 https://openclaw.ai/install.sh | bash

portworld doctor --target local
portworld ops check-config
portworld ops check-config --full-readiness
portworld ops bootstrap-storage
portworld ops export-memory --output /tmp/portworld-memory-export.zip
```

You can pin the installer to a specific tagged release:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://openclaw.ai/install.sh | bash -s -- --version <tag>
```

For CLI updates without rerunning the installer, the manual fallback is:

```bash
python3 -m pipx install --force "https://github.com/armapidus/PortWorld/archive/refs/tags/<tag>.zip"
```

Public installer flags:

- `--version <tag|latest>` installs a specific release tag or the latest GitHub release
- `--no-init` installs the CLI without running `portworld init`
- `--non-interactive` installs the CLI without attempting interactive setup

Legacy compatibility path:

```bash
python3 -m backend.cli check-config
python3 -m backend.cli check-config --full-readiness
python3 -m backend.cli bootstrap-storage
python3 -m backend.cli export-memory --output /tmp/portworld-memory-export.zip
```

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

The container image now boots through:

```bash
python -m backend.cli serve
```

The production image intentionally excludes:

- `backend/scripts/`
- `backend/var/`

## Operator Reference

When `BACKEND_BEARER_TOKEN` is set, include:

```text
Authorization: Bearer <token>
```

Route reference:

- `GET /livez`
  - public liveness endpoint for local and Cloud Run probes
- `GET /healthz`
  - compatibility liveness alias retained for older tooling
- `GET /profile`
  - read the current persistent profile scaffold or populated profile
- `GET /readyz`
  - internal readiness endpoint
  - when `BACKEND_BEARER_TOKEN` is set, requires bearer auth
  - in production profile, failed checks return redacted detail strings
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

Minimal examples:

```bash
curl http://127.0.0.1:8080/livez
curl http://127.0.0.1:8080/profile
curl -X POST http://127.0.0.1:8080/profile/reset
curl -OJ http://127.0.0.1:8080/memory/export
curl -X POST http://127.0.0.1:8080/memory/session/<session_id>/reset
```

## Cloud Run Migration Notes

Use the public CLI for the managed deploy path:

```bash
portworld doctor --target gcp-cloud-run --project <project> --region <region>
portworld deploy gcp-cloud-run --project <project> --region <region> --cors-origins https://app.example.com
```

Repeat deploys reuse `.portworld/state/gcp-cloud-run.json`. After deploy, use:

- public liveness: `GET /livez`
- authenticated readiness: `GET /readyz`

## Notes

- `BACKEND_BEARER_TOKEN` should be set for any shared or remotely reachable deployment.
- `CORS_ORIGINS=*` is the local-dev default, not a recommended production setting.
- `BACKEND_FORWARDED_ALLOW_IPS` should be set to your reverse proxy/LB peer IPs/CIDRs when deploying behind a proxy.
- `BACKEND_RATE_LIMIT_HTTP_IP_MAX_REQUESTS` defaults to `30` and `BACKEND_RATE_LIMIT_HTTP_WINDOW_SECONDS` defaults to `60`.
- When `BACKEND_ENABLE_IP_RATE_LIMITS=true`, the backend IP-rate-limits `GET /profile`, `PUT /profile`, `POST /profile/reset`, `GET /memory/export`, `GET /memory/session/{session_id}/status`, and `POST /memory/session/{session_id}/reset`.
- In development profile, IP rate limits stay off by default unless you explicitly enable them.
- Visual memory keeps derived memory by default and deletes raw frames unless debug retention is enabled.
- When `REALTIME_TOOLING_ENABLED=true` and `TAVILY_API_KEY` is unset, the backend still starts but omits `web_search`.

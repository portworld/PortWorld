# PortWorld Backend

FastAPI + Uvicorn backend that relays realtime voice sessions through selectable realtime providers, with opt-in visual memory and realtime tooling.

## Features

- **Realtime voice relay** — bridges a WebSocket audio session to the selected realtime provider; streams assistant audio back to the client
- **Persistent memory** — SQLite + filesystem storage for session memory and a user profile, with configurable retention
- **Visual memory** *(opt-in)* — ingests JPEG frames via `POST /vision/frame`, routes them through adaptive scene-change gating, and builds semantic session memory using any OpenAI-compatible vision endpoint (default: Mistral)
- **Realtime tooling** *(opt-in)* — registers memory-recall tools with the active OpenAI session; optionally adds web search via Tavily
- **Bearer token auth** — all non-health endpoints can require `Authorization: Bearer <token>`; production mode enforces this at startup
- **Rate limiting** — sliding-window limits on WebSocket setup, session activation, vision ingest, and protected profile/memory-admin HTTP routes
- **Memory export** — `GET /memory/export` streams a ZIP of all session and profile memory
- **Operator CLI** — `portworld` for init, doctor, deploy, and `ops` workflows; `python -m backend.cli` remains available as a compatibility path for serving and legacy operator commands

## Requirements

- Python 3.11+
- Docker and Docker Compose (for the Docker path)
- Provider credentials for your selected providers:
  - `OPENAI_API_KEY` when `REALTIME_PROVIDER=openai`
  - `GEMINI_LIVE_API_KEY` when `REALTIME_PROVIDER=gemini_live`

## CLI-first quick start

Public install path:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/portworld/PortWorld/main/install.sh | bash
```

The bootstrap installs `uv` automatically and downloads Python 3.11+ when the machine does not
already provide a suitable interpreter. On a fresh machine, `portworld init` now defaults to the
operator-friendly zero-clone workspace flow and can be run from any directory.

Default operator path after install:

```bash
portworld init
cd ~/.portworld/stacks/default
docker compose up -d
portworld doctor --target local
portworld status
```

Manual install fallback for a pinned release version:

```bash
uv tool install "portworld==<version>"
portworld init
```

TestPyPI beta validation note:

- The TestPyPI project page currently shows a bare `pip install -i https://test.pypi.org/simple/ portworld` command.
- That command can fail for `portworld`, because TestPyPI does not necessarily host every transitive dependency.
- For TestPyPI validation, use one of these instead:

```bash
pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ "portworld==<version>"
```

```bash
uv tool install --default-index https://test.pypi.org/simple --index https://pypi.org/simple "portworld==<version>"
```

Source-checkout contributor path:

```bash
pipx install . --force
portworld init
```

Run the contributor/source path from the repo root. The operator path is the default public flow.

For managed deploys, the public path is:

```bash
portworld doctor --target gcp-cloud-run --project <project> --region <region>
portworld deploy gcp-cloud-run --project <project> --region <region> --cors-origins https://app.example.com
```

Repeat Cloud Run deploys reuse `.portworld/state/gcp-cloud-run.json` after explicit flags and current `gcloud` config.

For CLI updates:

```bash
uv tool upgrade portworld
```

You can also rerun the installer or pin it to a specific tagged release:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/portworld/PortWorld/main/install.sh | bash -s -- --version <tag>
```

Published backend runtime image for a tagged release:

```bash
docker pull ghcr.io/portworld/portworld-backend:v<version>
```

Public installer flags:

- `--version <tag|latest>` installs a specific release tag or the latest GitHub release
- `--no-init` installs the CLI without running `portworld init`
- `--non-interactive` installs the CLI without attempting interactive setup

## Running locally

### Docker Compose (recommended)

```bash
cp backend/.env.example backend/.env
# Open backend/.env and set the credentials required by your selected providers
docker compose up --build
```

Data is persisted in a named Docker volume (`portworld_backend_var`).

### Bare Uvicorn

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# Open .env and set the credentials required by your selected providers
python -m backend.cli serve
```

### Verify

```bash
curl http://127.0.0.1:8080/livez
# → {"status":"ok","service":"portworld-backend"}
```

Use `/livez` for public and Cloud Run liveness checks. `/healthz` remains available as a compatibility alias for older local tooling.

## Configuration

Copy `.env.example` to `.env` and edit. The full reference with all options and defaults is in `.env.example`.
Use `portworld providers list` and `portworld providers show <provider_id>` to inspect current provider requirements.
Legacy provider alias keys are not supported. Use canonical provider-scoped keys only.

**Provider selection toggles**

| Variable | Description |
|---|---|
| `REALTIME_PROVIDER` | Realtime provider id (`openai` or `gemini_live`) |
| `VISION_MEMORY_ENABLED` | Set `true` to enable the vision provider pipeline |
| `VISION_MEMORY_PROVIDER` | Vision provider id when vision is enabled |
| `REALTIME_TOOLING_ENABLED` | Set `true` to enable realtime tooling |
| `REALTIME_WEB_SEARCH_PROVIDER` | Search provider id when tooling is enabled (currently `tavily`) |

**Realtime provider required keys**

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | Required when `REALTIME_PROVIDER=openai` |
| `GEMINI_LIVE_API_KEY` | Required when `REALTIME_PROVIDER=gemini_live` |

**Vision provider required keys (when `VISION_MEMORY_ENABLED=true`)**

| Provider id | Required key(s) |
|---|---|
| `mistral` | `VISION_MISTRAL_API_KEY` |
| `openai` | `VISION_OPENAI_API_KEY` |
| `azure_openai` | `VISION_AZURE_OPENAI_API_KEY` plus `VISION_AZURE_OPENAI_ENDPOINT` |
| `gemini` | `VISION_GEMINI_API_KEY` |
| `claude` | `VISION_CLAUDE_API_KEY` |
| `bedrock` | required config: `VISION_BEDROCK_REGION` (optional AWS credentials: `VISION_BEDROCK_AWS_ACCESS_KEY_ID`, `VISION_BEDROCK_AWS_SECRET_ACCESS_KEY`, `VISION_BEDROCK_AWS_SESSION_TOKEN`) |
| `groq` | `VISION_GROQ_API_KEY` |

**Search provider required keys (when `REALTIME_TOOLING_ENABLED=true`)**

| Provider id | Required key(s) |
|---|---|
| `tavily` | `TAVILY_API_KEY` |

**Production hardening**

Set `BACKEND_PROFILE=production` to enforce the following at startup:

| Variable | Description |
|---|---|
| `BACKEND_BEARER_TOKEN` | Required in production; all protected endpoints require `Authorization: Bearer <token>` |
| `CORS_ORIGINS` | Explicit allowed origins (not `*`) |
| `BACKEND_ALLOWED_HOSTS` | Explicit allowed hosts (not `*`) |

**Storage backends**

`BACKEND_STORAGE_BACKEND` supports:

- `local` for SQLite + filesystem (default)
- `managed` for Postgres + object store
- `postgres_gcs` as a compatibility alias that is normalized to `managed`

Managed storage uses these canonical object-store variables:

| Variable | Description |
|---|---|
| `BACKEND_OBJECT_STORE_PROVIDER` | Object-store provider: `gcs`, `s3`, or `azure_blob` for managed backends (`filesystem` is local-only) |
| `BACKEND_OBJECT_STORE_NAME` | Bucket/container name for the managed object store |
| `BACKEND_OBJECT_STORE_ENDPOINT` | Optional custom endpoint (required for `azure_blob`) |
| `BACKEND_OBJECT_STORE_PREFIX` | Prefix used for artifact paths |

Compatibility alias:

- `BACKEND_OBJECT_STORE_BUCKET` is still accepted when `BACKEND_OBJECT_STORE_NAME` is unset.

**Rate limiting**

| Variable | Description |
|---|---|
| `BACKEND_ENABLE_IP_RATE_LIMITS` | Enables IP-based sliding-window rate limits (enabled by default in production profile) |
| `BACKEND_RATE_LIMIT_HTTP_IP_MAX_REQUESTS` | Max requests per IP for protected profile and memory-admin HTTP endpoints within the HTTP rate-limit window |
| `BACKEND_RATE_LIMIT_HTTP_WINDOW_SECONDS` | Sliding-window size in seconds for protected profile and memory-admin HTTP endpoints |

Generate a secure bearer token:

```bash
openssl rand -hex 32
```

## API

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/livez` | None | Public liveness probe |
| `GET` | `/healthz` | None | Compatibility liveness alias |
| `GET` | `/readyz` | Bearer | Readiness probe — checks storage and provider config |
| `WS` | `/ws/session` | Bearer | Realtime voice session |
| `POST` | `/vision/frame` | Bearer | Ingest a base64-encoded JPEG frame |
| `GET` | `/profile` | Bearer | Read user profile |
| `PUT` | `/profile` | Bearer | Update user profile |
| `POST` | `/profile/reset` | Bearer | Reset user profile to empty |
| `GET` | `/memory/export` | Bearer | Download a ZIP of all session and profile memory |
| `GET` | `/memory/session/{id}/status` | Bearer | Per-session memory status |
| `POST` | `/memory/session/{id}/reset` | Bearer | Delete memory for a specific ended session |

Protected profile and memory-admin HTTP routes are IP-rate-limited when `BACKEND_ENABLE_IP_RATE_LIMITS=true`.

## Operator CLI

```bash
# Install or update the public CLI
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/portworld/PortWorld/main/install.sh | bash

# Initialize/update backend/.env through the public CLI
portworld init

# Validate local or Cloud Run readiness
portworld doctor --target local
portworld doctor --target gcp-cloud-run --project <project> --region <region>

# Deploy to Cloud Run
portworld deploy gcp-cloud-run --project <project> --region <region> --cors-origins https://app.example.com

# Wrap legacy operator actions through the public CLI
portworld ops check-config
portworld ops check-config --full-readiness
portworld ops bootstrap-storage
portworld ops export-memory --output /tmp/portworld-memory-export.zip
```

Legacy compatibility path:

```bash
# Start the server
python -m backend.cli serve

# Legacy operator entrypoints still work during migration
python -m backend.cli check-config
python -m backend.cli bootstrap-storage
python -m backend.cli export-memory --output /tmp/portworld-memory-export.zip
```

## Storage

All persistent data lives under `BACKEND_DATA_DIR` (default: `backend/var/`). SQLite tracks session metadata and artifact indexes; session memory, user profile, and vision event journals are stored as JSON and Markdown files on disk.

Session memory is retained for `BACKEND_SESSION_MEMORY_RETENTION_DAYS` days (default: 30) after a session ends. The user profile is never removed by retention.

# PortWorld Backend

FastAPI + Uvicorn backend that relays realtime voice sessions to OpenAI, with opt-in visual memory and realtime tooling.

## Features

- **Realtime voice relay** — bridges a WebSocket audio session to the OpenAI Realtime API; streams assistant audio back to the client
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
- An [OpenAI API key](https://platform.openai.com/api-keys) with Realtime API access

## CLI-first quick start

Public install path:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/armapidus/PortWorld/main/install.sh | bash
portworld doctor --target local
docker compose up --build
```

The bootstrap installs `uv` automatically and downloads Python 3.11+ when the machine does not
already provide a suitable interpreter.

Manual install fallback for a pinned release version:

```bash
uv tool install "portworld==<version>"
portworld init
```

Source-checkout developer path:

```bash
pipx install . --force
portworld init
```

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
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/armapidus/PortWorld/main/install.sh | bash -s -- --version <tag>
```

Public installer flags:

- `--version <tag|latest>` installs a specific release tag or the latest GitHub release
- `--no-init` installs the CLI without running `portworld init`
- `--non-interactive` installs the CLI without attempting interactive setup

## Running locally

### Docker Compose (recommended)

```bash
cp backend/.env.example backend/.env
# Open backend/.env and set OPENAI_API_KEY at minimum
docker compose up --build
```

Data is persisted in a named Docker volume (`portworld_backend_var`).

### Bare Uvicorn

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# Open .env and set OPENAI_API_KEY at minimum
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

**Required**

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key with Realtime API access |

**Opt-in features**

| Variable | Description |
|---|---|
| `VISION_MEMORY_ENABLED` | Set `true` to enable the visual memory pipeline |
| `VISION_PROVIDER_API_KEY` | API key for the vision endpoint (required when vision is enabled) |
| `VISION_PROVIDER_BASE_URL` | Base URL for any OpenAI-compatible vision endpoint (defaults to Mistral) |
| `REALTIME_TOOLING_ENABLED` | Set `true` to register memory and search tools with the realtime session |
| `TAVILY_API_KEY` | Enables the `web_search` tool (only used when tooling is enabled) |

Some OpenAI-compatible Mistral endpoints reject `response_format` / structured-output mode for their tokenizer backend. When that happens, the backend automatically retries once without `response_format` and falls back to prompt-only JSON extraction.

**Production hardening**

Set `BACKEND_PROFILE=production` to enforce the following at startup:

| Variable | Description |
|---|---|
| `BACKEND_BEARER_TOKEN` | Required in production; all protected endpoints require `Authorization: Bearer <token>` |
| `CORS_ORIGINS` | Explicit allowed origins (not `*`) |
| `BACKEND_ALLOWED_HOSTS` | Explicit allowed hosts (not `*`) |

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
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/armapidus/PortWorld/main/install.sh | bash

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

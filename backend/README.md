# PortWorld Backend

FastAPI + Uvicorn backend that powers realtime voice sessions, persistent memory, visual memory, and realtime tooling for PortWorld assistants.

## Features

- **Realtime voice relay** — bridges a WebSocket audio session to the selected realtime provider and streams assistant audio back to the client
- **Persistent memory** — canonical markdown memory files (`USER.md`, `CROSS_SESSION.md`, per-session `SHORT_TERM.md` / `LONG_TERM.md`) with configurable retention
- **Visual memory** *(opt-in)* — ingests JPEG frames via `POST /vision/frame`, routes them through adaptive scene-change gating, and builds semantic memory using pluggable vision providers
- **Durable-memory consolidation** *(opt-in)* — rewrites `USER.md` and `CROSS_SESSION.md` at session close
- **Realtime tooling** *(opt-in)* — memory-recall and web-search tools registered with the active AI session
- **Bearer token auth** — all non-health endpoints can require `Authorization: Bearer <token>`
- **Rate limiting** — sliding-window limits on WebSocket setup, vision ingest, and protected HTTP routes
- **Memory export** — `GET /memory/export` streams a ZIP of all memory artifacts

## Requirements

- Python 3.11+
- Docker and Docker Compose (for the Docker path)
- At least one realtime provider API key (`OPENAI_API_KEY` or `GEMINI_LIVE_API_KEY`)
- Node.js/npm/npx only if using Node-based MCP stdio extensions

## Quickstart

### Docker Compose (recommended)

```bash
cp backend/.env.example backend/.env
```

Open `backend/.env` and set your realtime provider key. The minimum viable setup is a single key:

```dotenv
REALTIME_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

Then start the backend:

```bash
docker compose up --build
```

> Run from the **repository root**, not from `backend/`. The `docker-compose.yml` lives at the repo root.

### Bare Uvicorn

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# Edit .env — set your realtime provider key
uvicorn backend.api.app:create_app --factory --host 127.0.0.1 --port 8080
```

### Verify

Liveness (no auth required):

```bash
curl http://127.0.0.1:8080/livez
# → {"status":"ok","service":"portworld-backend"}
```

Readiness (requires bearer token when auth is enabled):

```bash
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8080/readyz
```

Stricter preflight via the CLI:

```bash
portworld ops check-config --full-readiness
```

Data is persisted in a named Docker volume (`portworld_backend_var`).

## Configuration

The full reference with all options and defaults is in [`.env.example`](.env.example). Copy it to `.env` and edit as needed.

### Minimum Viable Environment

Start with everything optional turned off:

```dotenv
REALTIME_PROVIDER=openai
OPENAI_API_KEY=sk-...

VISION_MEMORY_ENABLED=false
REALTIME_TOOLING_ENABLED=false
```

That's it. One provider, one key.

### Provider Selection

| Variable | Description |
|----------|-------------|
| `REALTIME_PROVIDER` | `openai` or `gemini_live` |
| `VISION_MEMORY_ENABLED` | Set `true` to enable the vision pipeline |
| `VISION_MEMORY_PROVIDER` | Vision provider ID (see table below) |
| `MEMORY_CONSOLIDATION_ENABLED` | Enables durable-memory rewrite at session close |
| `MEMORY_CONSOLIDATION_TIMEOUT_MS` | Consolidation timeout in milliseconds (default `30000`) |
| `REALTIME_TOOLING_ENABLED` | Set `true` to enable realtime tooling |
| `REALTIME_WEB_SEARCH_PROVIDER` | Search provider ID (currently `tavily`) |

### Realtime Provider Keys

| Variable | Required When |
|----------|--------------|
| `OPENAI_API_KEY` | `REALTIME_PROVIDER=openai` |
| `GEMINI_LIVE_API_KEY` | `REALTIME_PROVIDER=gemini_live` |

### Vision Provider Keys

Required when `VISION_MEMORY_ENABLED=true`:

| Provider ID | Required Key(s) |
|-------------|-----------------|
| `mistral` | `VISION_MISTRAL_API_KEY` |
| `nvidia_integrate` | `VISION_NVIDIA_API_KEY` |
| `openai` | `VISION_OPENAI_API_KEY` |
| `azure_openai` | `VISION_AZURE_OPENAI_API_KEY` + `VISION_AZURE_OPENAI_ENDPOINT` |
| `gemini` | `VISION_GEMINI_API_KEY` |
| `claude` | `VISION_CLAUDE_API_KEY` |
| `bedrock` | `VISION_BEDROCK_REGION` (optional: IAM credentials or `AWS_BEARER_TOKEN_BEDROCK`) |
| `groq` | `VISION_GROQ_API_KEY` |

When `MEMORY_CONSOLIDATION_ENABLED=true`, the same `VISION_MEMORY_PROVIDER` credentials are used for durable-memory consolidation.

### Search Provider Keys

Required when `REALTIME_TOOLING_ENABLED=true`:

| Provider ID | Required Key |
|-------------|-------------|
| `tavily` | `TAVILY_API_KEY` |

### Production Hardening

Set `BACKEND_PROFILE=production` to enforce security defaults at startup:

| Variable | Description |
|----------|-------------|
| `BACKEND_BEARER_TOKEN` | Required in production — protects all non-health endpoints |
| `BACKEND_ENABLE_IP_RATE_LIMITS` | Defaults to `true` in production |

Generate a secure bearer token:

```bash
openssl rand -hex 32
```

### Storage Backends

`BACKEND_STORAGE_BACKEND` supports:

| Value | Description |
|-------|-------------|
| `local` | SQLite + filesystem (default) |
| `managed` | Object store for memory + Postgres for operational metadata |

Managed storage variables:

| Variable | Description |
|----------|-------------|
| `BACKEND_OBJECT_STORE_PROVIDER` | `gcs`, `s3`, or `azure_blob` |
| `BACKEND_OBJECT_STORE_NAME` | Bucket or container name |
| `BACKEND_OBJECT_STORE_ENDPOINT` | Custom endpoint (required for `azure_blob`) |
| `BACKEND_OBJECT_STORE_PREFIX` | Prefix for artifact paths |

### Rate Limiting

| Variable | Description |
|----------|-------------|
| `BACKEND_ENABLE_IP_RATE_LIMITS` | Enable sliding-window rate limits (default `true` in production) |
| `BACKEND_RATE_LIMIT_HTTP_IP_MAX_REQUESTS` | Max requests per IP for protected HTTP endpoints |
| `BACKEND_RATE_LIMIT_HTTP_WINDOW_SECONDS` | Window size in seconds |

See [`.env.example`](.env.example) for the full set of rate-limit, tuning, and debug variables.

### Provider Inspection

Use the CLI to explore available providers:

```bash
portworld providers list
portworld providers show <provider_id>
```

## API Reference

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/livez` | None | Public liveness probe |
| `GET` | `/readyz` | Bearer | Readiness probe — checks storage and provider config |
| `WS` | `/ws/session` | Bearer | Realtime voice session |
| `POST` | `/vision/frame` | Bearer | Ingest a base64-encoded JPEG frame |
| `GET` | `/memory/user` | Bearer | Read user memory |
| `PUT` | `/memory/user` | Bearer | Update user memory |
| `POST` | `/memory/user/reset` | Bearer | Reset user memory to empty |
| `GET` | `/memory/export` | Bearer | Download a ZIP of all memory artifacts |
| `GET` | `/memory/sessions/{id}/status` | Bearer | Per-session memory status |
| `POST` | `/memory/sessions/{id}/reset` | Bearer | Delete memory for a specific ended session |

Notes:
- `POST /vision/frame` acknowledges ingest, not completed analysis. Use `GET /memory/sessions/{id}/status` to inspect analysis state.
- `mistral` uses native model IDs such as `ministral-3b-2512`.
- `nvidia_integrate` uses NVIDIA-style model IDs such as `mistralai/ministral-14b-instruct-2512`.

## Storage

All persistent data lives under `BACKEND_DATA_DIR` (default: `backend/var/`).

**Local storage:**

- SQLite tracks session metadata and artifact indexes
- Memory files live under `memory/`:
  - `memory/USER.md`, `memory/CROSS_SESSION.md`
  - `memory/sessions/<key>/SHORT_TERM.md`, `memory/sessions/<key>/LONG_TERM.md`

**Managed storage** (cloud targets):

- Object storage is the source of truth for memory files
- Postgres is used for operational metadata
- `gcp-cloud-run`: Cloud Run + GCS + Cloud SQL Postgres
- `aws-ecs-fargate`: ECS/Fargate + S3 + Postgres
- `azure-container-apps`: Container Apps + Blob Storage + Postgres

> **Production note:** AWS one-click provisions public RDS ingress and Azure one-click provisions PostgreSQL with public access. Validate and tighten before production use.

Session memory is retained for `BACKEND_SESSION_MEMORY_RETENTION_DAYS` days (default: 30). The user profile is never removed by retention.

## Operator CLI

The `portworld` CLI provides operator commands for the backend. Install it from PyPI:

```bash
uv tool install portworld
```

Common commands:

```bash
portworld ops check-config                # validate local config
portworld ops check-config --full-readiness  # full preflight with provider validation
portworld ops bootstrap-storage           # initialize storage
portworld ops export-memory --output /tmp/export.zip  # export all memory
```

See the [CLI README](../portworld_cli/README.md) for the full command reference, deploy workflows, and managed log streaming.

A legacy compatibility path is also available:

```bash
python3 -m backend.cli check-config
python3 -m backend.cli export-memory --output /tmp/export.zip
```

## More Documentation

- [Root README](../README.md) — project overview, quickstart, provider tables
- [CLI README](../portworld_cli/README.md) — CLI commands, deploy, update
- [iOS README](../IOS/README.md) — iOS app setup, Meta DAT, permissions
- [Getting Started](../GETTING_STARTED.md) — extended onboarding for all setup paths
- [.env.example](.env.example) — full configuration reference with comments

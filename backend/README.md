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
- **Operator CLI** — `python -m backend.cli` for serving, config validation, storage bootstrap, and memory export

## Requirements

- Python 3.11+
- Docker and Docker Compose (for the Docker path)
- An [OpenAI API key](https://platform.openai.com/api-keys) with Realtime API access

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
curl http://127.0.0.1:8080/healthz
# → {"status":"ok","service":"portworld-backend"}
```

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
| `GET` | `/healthz` | None | Liveness probe |
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
# Start the server
python -m backend.cli serve

# Validate configuration (add --full-readiness for a storage + provider probe)
python -m backend.cli check-config
python -m backend.cli check-config --full-readiness

# Bootstrap SQLite schema and storage layout
python -m backend.cli bootstrap-storage

# Export all memory to a ZIP
python -m backend.cli export-memory --output /tmp/portworld-memory-export.zip
```

## Storage

All persistent data lives under `BACKEND_DATA_DIR` (default: `backend/var/`). SQLite tracks session metadata and artifact indexes; session memory, user profile, and vision event journals are stored as JSON and Markdown files on disk.

Session memory is retained for `BACKEND_SESSION_MEMORY_RETENTION_DAYS` days (default: 30) after a session ends. The user profile is never removed by retention.

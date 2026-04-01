# PortWorld Backend

FastAPI + Uvicorn backend that relays realtime voice sessions through selectable realtime providers, with opt-in visual memory and realtime tooling.

## Features

- **Realtime voice relay** — bridges a WebSocket audio session to the selected realtime provider; streams assistant audio back to the client
- **Persistent memory** — canonical markdown memory files (`USER.md`, `CROSS_SESSION.md`, per-session `SHORT_TERM.md` / `LONG_TERM.md`) with configurable retention
- **Visual memory** *(opt-in)* — ingests JPEG frames via `POST /vision/frame`, routes them through adaptive scene-change gating, and builds semantic session memory using pluggable vision providers, including native Mistral and NVIDIA Integrate
- **Durable-memory consolidation** *(opt-in)* — rewrites `USER.md` and `CROSS_SESSION.md` at session close using the same provider/model surface as visual memory
- **Realtime tooling** *(opt-in)* — registers memory-recall tools with the active OpenAI session; optionally adds web search via Tavily
- **Bearer token auth** — all non-health endpoints can require `Authorization: Bearer <token>`; production mode enforces this at startup
- **Rate limiting** — sliding-window limits on WebSocket setup, session activation, vision ingest, and protected profile/memory-admin HTTP routes
- **Memory export** — `GET /memory/export` streams a ZIP of all session and profile memory
- **Operator CLI** — `portworld` for init, doctor, deploy, and `ops` workflows

## Requirements

- Python 3.11+
- Node.js/npm/npx for Node-based MCP stdio extensions (the public `install.sh` bootstrap installs these in user space when missing)
- Docker and Docker Compose (for the Docker path)
- Provider credentials for your selected providers:
  - `OPENAI_API_KEY` when `REALTIME_PROVIDER=openai`
  - `GEMINI_LIVE_API_KEY` when `REALTIME_PROVIDER=gemini_live`

## CLI-first quick start

Public install path:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/portworld/PortWorld/main/install.sh | bash
```

The bootstrap installs `uv` automatically, downloads Python 3.11+ when the machine does not
already provide a suitable interpreter, and bootstraps Node.js/npm/npx in user space for MCP
Node launchers when those tools are missing. For published/container runtimes, the backend image
now carries its own Node runtime so stdio Node MCP servers do not depend on host PATH state. On
a fresh machine, `portworld init` now defaults to the operator-friendly zero-clone workspace flow
and can be run from any directory.

Default operator path after install:

```bash
portworld init
cd ~/.portworld/stacks/default
docker compose up -d
portworld doctor --target local
portworld status
```

Example extension manifests for the filesystem MCP server:

- local/source runtime: [mcp-filesystem-local.extensions.json](../docs/operations/examples/mcp-filesystem-local.extensions.json)
- published/container runtime: [mcp-filesystem-published.extensions.json](../docs/operations/examples/mcp-filesystem-published.extensions.json)

Manual install fallback for a pinned release version:

```bash
uv tool install "portworld==<version>"
portworld init
```

TestPyPI package-index validation note:

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

For managed deploys in the current MVP, use one of the public managed targets:

```bash
portworld doctor --target gcp-cloud-run --gcp-project <project> --gcp-region <region>
portworld deploy gcp-cloud-run --project <project> --region <region> --cors-origins https://app.example.com

portworld doctor --target aws-ecs-fargate --aws-region <region>
portworld deploy aws-ecs-fargate --region <region> --cors-origins https://app.example.com

portworld doctor --target azure-container-apps --azure-subscription <subscription> --azure-resource-group <resource-group> --azure-region <region>
portworld deploy azure-container-apps --subscription <subscription> --resource-group <resource-group> --region <region> --cors-origins https://app.example.com
```

Repeat deploys reuse the target state file under `.portworld/state/` after explicit flags and current cloud CLI auth/config:

- `.portworld/state/gcp-cloud-run.json`
- `.portworld/state/aws-ecs-fargate.json`
- `.portworld/state/azure-container-apps.json`

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
uvicorn backend.api.app:create_app --factory --host 127.0.0.1 --port 8080
```

### Verify

```bash
curl http://127.0.0.1:8080/livez
# → {"status":"ok","service":"portworld-backend"}
```

Use `/livez` for public and Cloud Run liveness checks.

## Configuration

Copy `.env.example` to `.env` and edit. The full reference with all options and defaults is in `.env.example`.
Use `portworld providers list` and `portworld providers show <provider_id>` to inspect current provider requirements.
Legacy provider alias keys are not supported. Use canonical provider-scoped keys only.

**Provider selection toggles**

| Variable | Description |
|---|---|
| `REALTIME_PROVIDER` | Realtime provider id (`openai` or `gemini_live`) |
| `VISION_MEMORY_ENABLED` | Set `true` to enable the vision provider pipeline |
| `VISION_MEMORY_PROVIDER` | Vision provider id when vision is enabled (`mistral`, `nvidia_integrate`, `openai`, `azure_openai`, `gemini`, `claude`, `bedrock`, or `groq`) |
| `VISION_PROVIDER_TIMEOUT_SECONDS` | Vision provider request timeout budget in seconds. Applies to vision analysis across providers; default `45` |
| `MEMORY_CONSOLIDATION_ENABLED` | Enables durable-memory rewrite at session close; reuses `VISION_MEMORY_PROVIDER` and that provider's credentials/model. Defaults to the current `VISION_MEMORY_ENABLED` value when unset |
| `MEMORY_CONSOLIDATION_TIMEOUT_MS` | Durable-memory consolidation timeout budget in milliseconds; default `30000` |
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
| `nvidia_integrate` | `VISION_NVIDIA_API_KEY` |
| `openai` | `VISION_OPENAI_API_KEY` |
| `azure_openai` | `VISION_AZURE_OPENAI_API_KEY` plus `VISION_AZURE_OPENAI_ENDPOINT` |
| `gemini` | `VISION_GEMINI_API_KEY` |
| `claude` | `VISION_CLAUDE_API_KEY` |
| `bedrock` | required config: `VISION_BEDROCK_REGION` (optional AWS credentials: `VISION_BEDROCK_AWS_ACCESS_KEY_ID`, `VISION_BEDROCK_AWS_SECRET_ACCESS_KEY`, `VISION_BEDROCK_AWS_SESSION_TOKEN`) |
| `groq` | `VISION_GROQ_API_KEY` |

When `MEMORY_CONSOLIDATION_ENABLED=true`, the same `VISION_MEMORY_PROVIDER` credentials and model are also used for durable-memory consolidation, even if `VISION_MEMORY_ENABLED=false`.

**Search provider required keys (when `REALTIME_TOOLING_ENABLED=true`)**

| Provider id | Required key(s) |
|---|---|
| `tavily` | `TAVILY_API_KEY` |

**Production hardening**

Set `BACKEND_PROFILE=production` to enforce the following at startup:

| Variable | Description |
|---|---|
| `BACKEND_BEARER_TOKEN` | Required in production; all protected endpoints require `Authorization: Bearer <token>` |

**Storage backends**

`BACKEND_STORAGE_BACKEND` supports:

- `local` for SQLite + filesystem (default)
- `managed` for object-store-backed memory plus Postgres operational metadata

Managed storage uses these canonical object-store variables for memory files:

| Variable | Description |
|---|---|
| `BACKEND_OBJECT_STORE_PROVIDER` | Object-store provider: `gcs`, `s3`, or `azure_blob` for managed backends (`filesystem` is local-only) |
| `BACKEND_OBJECT_STORE_NAME` | Bucket/container name for the managed object store |
| `BACKEND_OBJECT_STORE_ENDPOINT` | Optional custom endpoint (required for `azure_blob`) |
| `BACKEND_OBJECT_STORE_PREFIX` | Prefix used for artifact paths |

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
| `GET` | `/readyz` | Bearer | Readiness probe — checks storage and provider config |
| `WS` | `/ws/session` | Bearer | Realtime voice session |
| `POST` | `/vision/frame` | Bearer | Ingest a base64-encoded JPEG frame |
| `GET` | `/memory/user` | Bearer | Read user memory |
| `PUT` | `/memory/user` | Bearer | Update user memory |
| `POST` | `/memory/user/reset` | Bearer | Reset user memory to empty |
| `GET` | `/memory/export` | Bearer | Download a ZIP of all session and user-memory artifacts |
| `GET` | `/memory/sessions/{id}/status` | Bearer | Per-session memory status |
| `POST` | `/memory/sessions/{id}/reset` | Bearer | Delete memory for a specific ended session |

Protected profile and memory-admin HTTP routes are IP-rate-limited when `BACKEND_ENABLE_IP_RATE_LIMITS=true`.

Provider notes:

- `mistral` is the native Mistral adapter and should use native model ids such as `ministral-14b-2512`.
- `nvidia_integrate` is the NVIDIA Integrate/NIM OpenAI-compatible adapter and should use NVIDIA-style model ids such as `mistralai/ministral-14b-instruct-2512`.
- `POST /vision/frame` acknowledges ingest, not completed analysis. Use `GET /memory/sessions/{id}/status` to inspect recent frame analysis state.

## Operator CLI

```bash
# Install or update the public CLI
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/portworld/PortWorld/main/install.sh | bash

# Initialize/update backend/.env through the public CLI
portworld init

# Validate local or managed readiness
portworld doctor --target local
portworld doctor --target gcp-cloud-run --gcp-project <project> --gcp-region <region>
portworld doctor --target aws-ecs-fargate --aws-region <region>
portworld doctor --target azure-container-apps --azure-subscription <subscription> --azure-resource-group <resource-group> --azure-region <region>

# Deploy to a managed target
portworld deploy gcp-cloud-run --project <project> --region <region> --cors-origins https://app.example.com
portworld deploy aws-ecs-fargate --region <region> --cors-origins https://app.example.com
portworld deploy azure-container-apps --subscription <subscription> --resource-group <resource-group> --region <region> --cors-origins https://app.example.com

# Read managed deployment logs
portworld logs gcp-cloud-run --since 24h --limit 50
portworld logs aws-ecs-fargate --since 24h --limit 50
portworld logs azure-container-apps --since 24h --limit 50

# Redeploy the active managed target
portworld update deploy --tag <image-tag>

# Operator actions through the public CLI
portworld ops check-config
portworld ops check-config --full-readiness
portworld ops bootstrap-storage
portworld ops export-memory --output /tmp/portworld-memory-export.zip
```

## Storage

All persistent data lives under `BACKEND_DATA_DIR` (default: `backend/var/`).

Local storage:

- SQLite tracks session metadata and artifact indexes
- memory files live under `memory/`:
  - `memory/USER.md`
  - `memory/CROSS_SESSION.md`
  - `memory/sessions/<session_storage_key>/SHORT_TERM.md`
  - `memory/sessions/<session_storage_key>/LONG_TERM.md`
  - optional debug journals such as `EVENTS.ndjson`

Managed storage (managed targets):

- object storage is the source of truth for memory files
- Postgres is used for operational metadata in the current MVP backend
- `gcp-cloud-run`: Cloud Run + GCS + Cloud SQL Postgres
- `aws-ecs-fargate`: ECS/Fargate + CloudFront + ALB + S3 + Postgres operational metadata
- `azure-container-apps`: Container Apps + Blob Storage + Postgres operational metadata
- current MVP hardening note:
  AWS one-click provisions public RDS ingress and Azure one-click provisions PostgreSQL public access; validate and tighten before production use

Session memory is retained for `BACKEND_SESSION_MEMORY_RETENTION_DAYS` days (default: 30) after a session ends. The user profile is never removed by retention.

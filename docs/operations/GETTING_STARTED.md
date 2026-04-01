# PortWorld Getting Started

This is the canonical onboarding and setup path for PortWorld.

Use this document if you want to:

- run PortWorld locally through the supported operator flow
- contribute from a source checkout
- work on the backend only
- build the iOS app against a reachable PortWorld backend

For subsystem-specific detail after first setup:

- backend runtime reference: [../../backend/README.md](../../backend/README.md)
- CLI/operator reference: [../../portworld_cli/README.md](../../portworld_cli/README.md)
- iOS app reference: [../../IOS/README.md](../../IOS/README.md)

## Minimum Supported Platforms And Tools

### Backend and CLI

- macOS or Linux
- Python 3.11+
- Docker and Docker Compose for the default local operator path
- Node.js/npm/npx only when using Node-based MCP extensions outside the published/container path

### iOS

- iPhone-focused app targeting iOS 17.0+
- Xcode with iOS 17 support
- a reachable PortWorld backend for meaningful runtime validation

## Supported Setup Paths

### 1. Default operator path

This is the recommended path if you want to run PortWorld locally without developing the repo itself.

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/portworld/PortWorld/main/install.sh | bash
portworld init
cd ~/.portworld/stacks/default
docker compose up -d
portworld doctor --target local
portworld status
```

Expected result:

- the local workspace initializes successfully
- the backend container becomes healthy
- `portworld doctor --target local` reports the local runtime as ready enough to continue
- `portworld status` shows the local workspace/runtime state

### 2. Source-checkout contributor path

Use this when you are editing PortWorld itself.

```bash
git clone https://github.com/portworld/PortWorld.git
cd PortWorld
pipx install . --force
portworld init
```

Use this path for:

- backend development
- CLI development
- repo-backed debugging

### 3. Backend-only contributor path

Use this when you want the fastest route to a local backend from a repo checkout.

```bash
git clone https://github.com/portworld/PortWorld.git
cd PortWorld
cp backend/.env.example backend/.env
docker compose up --build
```

Then validate liveness:

```bash
curl http://127.0.0.1:8080/livez
```

### 4. iOS contributor path

Use this when you want to build the iOS app against a local backend.

```bash
git clone https://github.com/portworld/PortWorld.git
cd PortWorld
cp backend/.env.example backend/.env
docker compose up --build
open IOS/PortWorld.xcodeproj
```

Then:

1. Build the `PortWorld` scheme.
2. Configure the backend base URL in the app or local config template.
3. Validate backend setup in-app against the running local deployment.

The iOS-specific configuration, Meta DAT setup, permissions, and build constraints remain in [../../IOS/README.md](../../IOS/README.md).

## Minimum Viable Backend Environment

The exhaustive backend environment reference is [../../backend/.env.example](../../backend/.env.example).
Use that file as the source of truth for supported variables and defaults.

### Realtime-only path

Start from:

```bash
cp backend/.env.example backend/.env
```

Then keep these defaults unless you intentionally want more features:

- `VISION_MEMORY_ENABLED=false`
- `REALTIME_TOOLING_ENABLED=false`
- `MEMORY_CONSOLIDATION_ENABLED=` left unset

Choose one realtime provider:

- `REALTIME_PROVIDER=openai` requires `OPENAI_API_KEY`
- `REALTIME_PROVIDER=gemini_live` requires `GEMINI_LIVE_API_KEY`

Optional production/local-hardening settings:

- `BACKEND_PROFILE=production` requires `BACKEND_BEARER_TOKEN`
- internet-exposed deployments should use explicit `CORS_ORIGINS` and `BACKEND_ALLOWED_HOSTS`

If you enable optional features, use the provider-scoped keys documented in [../../backend/README.md](../../backend/README.md) and [../../backend/.env.example](../../backend/.env.example).

## First Success

### Backend

Liveness:

```bash
curl http://127.0.0.1:8080/livez
```

Expected response:

```json
{"status":"ok","service":"portworld-backend"}
```

Readiness:

- `/livez` confirms process liveness only
- authenticated `/readyz` checks storage and provider configuration
- `portworld ops check-config --full-readiness` is the stricter CLI preflight

Example readiness probe:

```bash
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8080/readyz
```

### CLI

After the default operator path:

- `portworld doctor --target local` should complete without a fatal local-runtime failure
- `portworld status` should show the initialized local workspace/runtime state

### iOS

The first meaningful iOS success state is:

1. the app builds successfully in Xcode
2. backend setup/validation succeeds against a reachable PortWorld deployment
3. the app can proceed through the current onboarding/runtime flow supported by your setup

For iOS-specific runtime expectations and constraints, continue with [../../IOS/README.md](../../IOS/README.md).

## What To Read Next

- Read [../../backend/README.md](../../backend/README.md) for:
  - provider/env reference
  - backend runtime details
  - API and storage details
  - backend-specific verification guidance
- Read [../../portworld_cli/README.md](../../portworld_cli/README.md) for:
  - CLI commands
  - managed deploy flows
  - install/update specifics
  - production cautions for managed targets
- Read [../../IOS/README.md](../../IOS/README.md) for:
  - iOS project layout
  - Meta DAT setup
  - runtime configuration inputs
  - permissions, capabilities, and build/validate constraints

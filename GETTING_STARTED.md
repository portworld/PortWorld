# PortWorld Getting Started

This is the canonical onboarding document for PortWorld.

PortWorld currently has two practical setup paths:

1. install the published CLI with `install.sh`
2. clone the repo and set everything up from source

If you want to use the iOS app, use the source path. The iOS app is not currently distributed through the App Store because the Wearables DAT SDK blocks that route, so iOS setup requires a repo checkout and Xcode.

For subsystem-specific detail after initial setup:

- backend runtime reference: [backend/README.md](backend/README.md)
- CLI/operator reference: [portworld_cli/README.md](portworld_cli/README.md)
- iOS app reference: [IOS/README.md](IOS/README.md)

## Minimum Supported Platforms And Tools

### CLI and backend

- macOS or Linux
- Python 3.11+
- Docker and Docker Compose for local backend runs

### iOS

- macOS
- Xcode with iOS 17 support
- iPhone-focused app targeting iOS 17.0+
- a reachable PortWorld backend for meaningful app validation

## Path 1. Install With `install.sh`

Use this path if you want the published CLI and PortWorld-managed setup flow without cloning the repo first.

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/portworld/PortWorld/main/install.sh | bash
portworld init
```

Then validate the result:

```bash
portworld doctor --target local
portworld status
```

Use this path when:

- you want the published CLI experience
- you want PortWorld to manage the local workspace for you
- you do not need the iOS app from this machine yet

Notes:

- this path is centered on the CLI and backend runtime
- if you later want to work with the iOS app, switch to the source path below

## Path 2. Set Up From Source

Use this path if you want the full product setup from one repo checkout, especially backend plus iOS app together.

### 1. Clone the repo

```bash
git clone https://github.com/portworld/PortWorld.git
cd PortWorld
```

If you need a specific branch, check it out after entering the repo.

### 2. Create a local Python environment

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
```

Optional sanity check:

```bash
portworld --version
```

### 3. Run repo-backed init

Use explicit flags so setup stays in the repo checkout instead of switching to a published workspace:

```bash
portworld init --project-mode local --runtime-source source --setup-mode manual
```

This path is the source-backed setup flow. It is the right choice when you want:

- the backend config written into the repo checkout
- the local backend started from the source checkout
- repo-local iOS defaults synced from the setup result

### 4. Validate the local backend

```bash
portworld doctor --target local
portworld status
curl http://127.0.0.1:8080/livez
```

Expected liveness response:

```json
{"status":"ok","service":"portworld-backend"}
```

### 5. Open the iOS project

```bash
open IOS/PortWorld.xcodeproj
```

Then:

1. let Xcode resolve Swift Package dependencies
2. select the `PortWorld` scheme
3. build the app
4. validate backend setup in-app against the backend you started locally

The iOS-specific Meta DAT setup, permissions, and runtime constraints remain in [IOS/README.md](IOS/README.md).

## First Success

The first meaningful success state for the source path is:

1. `portworld init --project-mode local --runtime-source source --setup-mode manual` completes successfully
2. `portworld doctor --target local` completes without a fatal local-runtime failure
3. `curl http://127.0.0.1:8080/livez` returns `{"status":"ok","service":"portworld-backend"}`
4. Xcode opens `IOS/PortWorld.xcodeproj` and the `PortWorld` scheme builds
5. the app can validate a reachable PortWorld backend in its onboarding flow

## What To Read Next

- Read [backend/README.md](backend/README.md) for:
  - provider and env reference
  - backend runtime details
  - API and storage details
  - backend-specific verification guidance
- Read [portworld_cli/README.md](portworld_cli/README.md) for:
  - CLI commands
  - managed deploy flows
  - install and update specifics
  - production cautions for managed targets
- Read [IOS/README.md](IOS/README.md) for:
  - iOS project layout
  - Meta DAT setup
  - runtime configuration inputs
  - permissions, capabilities, and build constraints

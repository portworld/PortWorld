# Port:🌍 iOS Client (PortWorld)

This README describes the current iOS app as it exists today.

The goal is to make the active architecture easy to understand:

- what is active now
- what is active now for both `phone` and `glasses` runtime routes
- what is historical only

For implementation authority, prefer the docs in `../docs/` and the active code in `IOS/PortWorld/`.

## Current Status

The active app is one assistant runtime with two routes:

- `phone`
  the stable everyday path
- `glasses`
  DAT-gated and mock-friendly, with live HFP audio when available and labeled phone fallback during development

The app is still operationally iPhone-first, but the main runtime now surfaces and owns both routes.

The phone path remains:

1. User taps `Activate Assistant`
2. App enters armed listening on iPhone
3. Saying `Hey mario` opens a backend conversation
4. User speech streams from the iPhone microphone to `/ws/session`
5. Assistant audio plays through the iPhone speaker
6. Saying `goodbye mario` or ending the turn stops only the active conversation
7. The app returns to armed listening and can repeat the cycle

This is the current implementation authority for the phone route inside the shared assistant runtime.

## What Works Today

- Phone assistant activation from the main app flow
- Wake phrase detection on iPhone
- Realtime microphone uplink to the backend
- Assistant playback through iPhone speaker
- Spoken sleep command to end the active conversation
- Re-arming after conversation end
- Repeated wake -> converse -> sleep cycles
- Assistant interruption / barge-in handling
- Local mock-backend validation of the phone runtime
- DAT configuration and registration from the app shell
- Glasses route selection and DAT session lifecycle ownership
- Mock-device-assisted glasses lifecycle validation
- Live HFP glasses audio when bidirectional Bluetooth HFP is available
- Labeled phone-audio fallback for mock / non-hardware glasses development

## Active Source Tree

The active app code lives under:

```text
IOS/PortWorld/
├── PortWorldApp.swift
├── Views/
│   ├── MainAppView.swift
│   ├── AssistantRuntimeView.swift
│   └── Components/
├── ViewModels/
│   └── AssistantRuntimeViewModel.swift
├── Runtime/
│   ├── Assistant/
│   ├── AudioIO/
│   ├── Config/
│   ├── Playback/
│   ├── Transport/
│   └── Wake/
├── Audio/
├── FutureHardware/
├── Utilities/
└── Assets.xcassets/
```

## Active Ownership Map

| Area | Current owner |
|---|---|
| App entry and top-level routing | `PortWorldApp`, `MainAppView` |
| Assistant UI state and actions | `AssistantRuntimeView`, `AssistantRuntimeViewModel`, `AssistantRuntimeStatus` |
| Runtime orchestration and conversation lifecycle | `Runtime/Assistant/` |
| Backend websocket transport and wire contract | `Runtime/Transport/` |
| Assistant playback and route/interruption handling | `Runtime/Playback/` |
| Wake and sleep detection | `Runtime/Wake/` |
| Phone and glasses audio route bridges | `Runtime/AudioIO/` |
| Shared audio engine and capture support | `Audio/` |
| DAT integration, glasses lifecycle, and mock workflow | `FutureHardware/` |

## Architecture Snapshot

The current architecture is easiest to understand in four layers.

### 1. App Shell

- `PortWorldApp.swift`
- `MainAppView.swift`

This layer owns app startup and entry into the assistant experience.

### 2. Active Assistant Runtime

- `Views/AssistantRuntimeView.swift`
- `ViewModels/AssistantRuntimeViewModel.swift`
- `Runtime/Assistant/`
- `Runtime/Transport/`
- `Runtime/Playback/`
- `Runtime/Wake/`
- `Runtime/AudioIO/`
- `Audio/`

This is the working assistant runtime and should be treated as the active product architecture for both routes.

### 3. Retained Future Hardware Layer

- `FutureHardware/ViewModels/`
- `FutureHardware/Coordinators/`
- `FutureHardware/Views/`
- DAT SDK integration and mock-device support

This exists because DAT integration, glasses lifecycle, and mock-device workflow still live in a bounded slice even though the main assistant runtime now consumes that state.

### 4. Historical Context

- git history

Git history is useful for migration context and historical reasoning only.

## Dependency Status

### Active Runtime Dependency

- Backend conversation gateway at `/ws/session`

### Retained Future-Hardware Dependency

- [meta-wearables-dat-ios](https://github.com/facebook/meta-wearables-dat-ios) v0.5.0

The DAT SDK remains in the project because later work will extend the cleaned phone runtime toward glasses support.

## Permissions And Configuration

### Active Runtime Needs

The active assistant runtime depends on:

- Microphone permission
- Speech recognition permission
- Runtime config for backend connection:
  - `SON_BACKEND_BASE_URL` or `SON_WS_URL`
  - `SON_WS_PATH` when using a base URL
  - optional `SON_API_KEY`
  - optional `SON_BEARER_TOKEN`

### DAT / Glasses Setup

The codebase contains DAT-related integration surfaces, URL schemes, and hardware-oriented configuration because the glasses route is now part of the active runtime.

You still do not need physical glasses to understand the main runtime, but the active architecture now includes:

- app-scoped DAT configuration
- Meta registration / unregistration handling
- glasses session lifecycle
- mock-device development workflow

## Local Validation

For the active assistant loop, the most relevant local path is:

1. run the backend locally
2. activate the assistant in the iOS app
3. verify wake -> conversation -> sleep -> re-arm
4. optionally verify interruption / barge-in behavior

The local mock backend remains useful for low-cost control-flow validation of:

- wake detection
- session activation
- uplink start
- spoken sleep handling
- clean conversation teardown

## Documentation Map

Use these docs as the current source of truth:

- [IOS/AGENTS.md](AGENTS.md)
  iOS implementation and verification guidance for active work
- [backend/README.md](../backend/README.md)
  backend runtime, environment contract, and local operator workflow
- [portworld_cli/README.md](../portworld_cli/README.md)
  install, workspace bootstrap, update paths, and CLI entrypoints
- [docs/operations/CLI_RELEASE_PROCESS.md](../docs/operations/CLI_RELEASE_PROCESS.md)
  release workflow and tagging policy

Historical context lives in git history. It is not implementation authority for new assistant work.

## Recommended Mental Model

When working in the iOS app, assume this ordering:

1. trust the active runtime in `IOS/PortWorld/` first
2. treat `FutureHardware/` as the bounded DAT / glasses capability layer consumed by the main runtime
3. treat git history as historical context only

If a file or flow conflicts with the working assistant runtime, the active runtime should win unless the task is explicitly about legacy migration or historical comparison.

# Port:🌍 iOS Client (PortWorld)

This README describes the current state of the iOS app as it exists today.

The main goal is clarity:

- what is active and working now
- what is retained for future Meta glasses / DAT work
- what is legacy or reference-only

Do not use older archived plans or older runtime folders as the source of truth for the current assistant behavior.

## Current Status

The active assistant runtime is now an iPhone-first, phone-only loop:

1. User taps `Activate Assistant`
2. App enters armed listening on iPhone
3. Saying `Hey mario` opens a backend conversation
4. User speech streams from the iPhone microphone
5. Assistant speech plays through the iPhone speaker
6. Saying `goodbye mario` or tapping the explicit end action ends only the current conversation
7. The app returns to armed listening and can repeat the cycle

This path is the current implementation authority for the iOS assistant.

## What Works Today

These behaviors reflect the active, working app path:

- Phone-only assistant activation from the main app flow
- Wake phrase detection on iPhone
- First-launch wake reliability
- Backend conversation startup through `/ws/session`
- Realtime uplink from iPhone microphone
- Assistant playback through iPhone speaker
- Spoken sleep command to end the active conversation
- Re-arming after conversation end
- Repeated wake -> converse -> sleep cycles
- Local mock backend validation for the phone-only runtime

## What Is Active In The App

These files and modules reflect the current assistant path that should be trusted first when working on the app:

```text
IOS/PortWorld/
├── PortWorldApp.swift
├── Views/
│   ├── MainAppView.swift
│   ├── HomeScreenView.swift
│   └── PhoneAssistantRuntimeView.swift
├── ViewModels/
│   ├── PhoneAssistantRuntimeViewModel.swift
│   └── PhoneAssistantRuntimeStore.swift
├── Runtime/
│   ├── AssistantRuntimeController.swift
│   ├── BackendSessionClient.swift
│   ├── PhoneAudioIO.swift
│   ├── AssistantPlaybackEngine.swift
│   ├── WakePhraseDetector.swift
│   ├── WakeWordEngine.swift
│   ├── RuntimeConfig.swift
│   ├── RuntimeProtocols.swift
│   └── RuntimeTypes.swift
└── Audio/
    ├── AudioCollectionManager.swift
    ├── AudioCollectionTypes.swift
    ├── AudioSessionArbiter.swift
    └── WavFileWriter.swift
```

### Active Ownership Map

| Area | Current owner |
|---|---|
| App entry and top-level routing | `PortWorldApp`, `MainAppView`, `HomeScreenView` |
| Phone assistant UI state | `PhoneAssistantRuntimeViewModel`, `PhoneAssistantRuntimeStore` |
| Runtime state machine and conversation lifecycle | `AssistantRuntimeController` |
| Backend websocket transport | `BackendSessionClient` |
| Wake and sleep detection | `WakePhraseDetector`, `WakeWordEngine` |
| Phone microphone / speaker path | `PhoneAudioIO`, `AssistantPlaybackEngine` |
| Shared audio engine and capture support | `AudioCollectionManager` and related audio files |

## What Exists But Is Not The Active Product Path

These parts of the codebase still exist, but they should not be mistaken for the current working assistant architecture.

### Retained For Future Hardware Work

- `IOS/PortWorld/ViewModels/WearablesViewModel.swift`
- `IOS/PortWorld/Coordinators/DeviceSessionCoordinator.swift`
- `IOS/PortWorld/Coordinators/MockDeviceController.swift`
- DAT SDK integration and mock-device support
- Photo / video / wearable stream surfaces

These remain useful because the forward plan is to take the cleaned phone-only runtime and layer Meta glasses compatibility on top of it.

They are not the primary assistant path today.

### Archived Or Legacy Runtime Artifacts

- `IOS/Legacy/AssistantRuntime/`
- older stream-oriented assistant flows
- older coordinator/orchestrator runtime layers that predate the current simplified phone-only runtime

These are historical reference only.

Do not extend them for new assistant behavior unless a migration task explicitly says so.

### Reference-Only Phone Slice

- `IOS/PhoneOnly/`

This folder exists as a personal reference snapshot of the reduced phone-only source surface.

It is:

- useful for understanding what files the phone-only assistant depends on
- useful for future cleanup and code-quality work

It is not:

- the production app target
- a standalone shipping app
- the implementation authority over `IOS/PortWorld/`

## Architecture Snapshot

The current architecture should be read in four layers.

### 1. App Shell

- `PortWorldApp.swift`
- `MainAppView.swift`
- `HomeScreenView.swift`

This layer owns app startup, navigation, and the entry into the assistant experience.

### 2. Active Phone-Only Assistant Runtime

- `PhoneAssistantRuntimeView`
- `PhoneAssistantRuntimeViewModel`
- `PhoneAssistantRuntimeStore`
- `AssistantRuntimeController`
- `BackendSessionClient`
- `PhoneAudioIO`
- `AssistantPlaybackEngine`
- `WakePhraseDetector`
- `WakeWordEngine`

This is the current runtime that works end-to-end and should be treated as the active product architecture.

### 3. Retained Hardware Integration Layer

- `WearablesViewModel`
- `DeviceSessionCoordinator`
- `MockDeviceController`
- DAT SDK dependency

This layer exists because the long-term app direction still includes Meta glasses support and mock-device development.

It is retained, but it is not the current runtime backbone.

### 4. Archived Runtime History

- `IOS/Legacy/AssistantRuntime/`
- archived docs in `docs/archived/`

This is useful for migration context only.

## Dependency Status

### Active Runtime Dependency

- Backend gateway at `/ws/session` for the phone-only assistant conversation flow

### Retained Dependency

- [meta-wearables-dat-ios](https://github.com/facebook/meta-wearables-dat-ios) v0.4.0

The DAT SDK is still present because the app will later grow from phone-only into glasses-connected mode.

That does not mean the active assistant runtime is currently glasses-first.

## Permissions And Configuration

### Active Phone-Only Runtime Needs

The current working assistant path depends on:

- Microphone permission
- Speech recognition permission
- Runtime config for backend connection:
  - `SON_BACKEND_BASE_URL` or `SON_WS_URL`
  - `SON_WS_PATH` when using a base URL
  - optional `SON_API_KEY`
  - optional `SON_BEARER_TOKEN`

### Retained Hardware-Oriented Setup

The codebase still contains Meta / DAT-related integration surfaces, URL schemes, and hardware-oriented configuration.

Those are retained because the app will move toward glasses compatibility later, but they are not the minimum required setup for understanding the working phone-only runtime.

## Local Validation

For the active phone-only assistant loop, the most relevant local path is:

1. run the backend gateway locally
2. activate the assistant in the iOS app
3. verify wake -> conversation -> sleep -> re-arm

The local mock backend remains useful for low-cost control-flow validation of:

- wake detection
- session activation
- uplink start
- spoken sleep handling
- clean conversation teardown

## Documentation Map

Use these docs as the current source of truth:

- [docs/IOS_AUDIO_ONLY_ASSISTANT_PLAN.md](../docs/IOS_AUDIO_ONLY_ASSISTANT_PLAN.md)
  - current phone-only assistant behavior contract
- [docs/IOS_PHONEONLY_TO_GLASSES_ROADMAP.md](../docs/IOS_PHONEONLY_TO_GLASSES_ROADMAP.md)
  - sequencing from phone-only cleanup toward glasses and later vision support

Historical context lives in:

- [docs/archived/](../docs/archived/)

Archived docs are not implementation authority for new assistant work.

## Recommended Mental Model Going Forward

When working in the iOS app, assume this ordering:

1. trust the active phone-only runtime first
2. treat DAT / glasses code as retained future integration work
3. treat `IOS/PhoneOnly/` as reference-only
4. treat `IOS/Legacy/AssistantRuntime/` and `docs/archived/` as historical context only

If a file or flow conflicts with the working phone-only runtime, the phone-only runtime is the one that should win unless the task is explicitly about hardware reintegration or migration.


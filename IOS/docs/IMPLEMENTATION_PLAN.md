# PortWorld iOS — Implementation Plan (v1.0)

> **Baseline:** Hackathon MVP (v4) — all runtime features are implemented but the code has
> concurrency debt, testability gaps, UX/UI hackathon artifacts, and production reliability issues.
>
> **Target:** Consumer-quality v1.0 matching `PRD.md` and `ARCHITECTURE.md`.
>
> Each phase is self-contained and leaves the app in a compilable, runnable state.
> Never leave the codebase broken between commits.

---

## Current State → Target State

The module map in `ARCHITECTURE.md §2` describes the **target** state. The table below maps every file that exists **today** to where it ends up after the refactor. Use this to orient yourself in the codebase before starting any phase.

### Source Files (IOS/PortWorld/)

| Current file                               | Lines | Fate                                                                                            | Target file(s)                                                             |  Phase |
| ------------------------------------------ | ----: | ----------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- | -----: |
| `PortWorldApp.swift`                       |    40 | **Modify** — wrap SDK init in do/catch, remove RegistrationView sibling                         | `PortWorldApp.swift`                                                       |  P1-09 |
| `Audio/AudioCollectionManager.swift`       |   782 | **Modify** — extract `AudioChunkProcessor` docs, add arbiter calls, fix route observer          | `Audio/AudioCollectionManager.swift`                                       |     P2 |
| `Audio/AudioCollectionTypes.swift`         |    64 | **Keep**                                                                                        | `Audio/AudioCollectionTypes.swift`                                         |      — |
| `Audio/WavFileWriter.swift`                |    53 | **Keep**                                                                                        | `Audio/WavFileWriter.swift`                                                |      — |
| `Runtime/SessionOrchestrator.swift`        |   762 | **Modify** — inject dependencies via protocols, add outbound buffer, fix health loop stacking   | `Runtime/SessionOrchestrator.swift`                                        | P1, P2 |
| `Runtime/AssistantPlaybackEngine.swift`    |   680 | **Modify** — wrap prints, fix buffer count, add arbiter                                         | `Runtime/AssistantPlaybackEngine.swift`                                    |     P2 |
| `Runtime/RuntimeTypes.swift`               |   575 | **Modify** — extract `Clocks.nowMs()` to `Utilities/Clocks.swift`, remove duplicate clock enums | `Runtime/RuntimeTypes.swift` + `Utilities/Clocks.swift`                    |     P1 |
| `Runtime/EventLogger.swift`                |    59 | **Modify** — add JSONL file sink                                                                | `Runtime/EventLogger.swift`                                                |     P3 |
| `Runtime/SessionWebSocketClient.swift`     |   401 | **Modify** — fix stale task guard, reset backoff on pong                                        | `Runtime/SessionWebSocketClient.swift`                                     |     P2 |
| `Runtime/WakeWordEngine.swift`             |   417 | **Modify** — convert to actor, add circuit breaker, fix normalizePhrase                         | `Runtime/WakeWordEngine.swift`                                             |     P2 |
| `Runtime/QueryEndpointDetector.swift`      |   193 | **Rewrite** — DispatchQueue → actor                                                             | `Runtime/QueryEndpointDetector.swift`                                      |     P2 |
| `Runtime/QueryBundleBuilder.swift`         |   251 | **Modify** — fix try?, add exponential backoff, extract protocol                                | `Runtime/QueryBundleBuilder.swift`                                         | P1, P2 |
| `Runtime/VisionFrameUploader.swift`        |   348 | **Rewrite** — DispatchQueue → actor, callback URLSession → async/await                          | `Runtime/VisionFrameUploader.swift`                                        |     P2 |
| `Runtime/RollingVideoBuffer.swift`         |   355 | **Rewrite** — DispatchQueue → actor, add temp-file cleanup, add cancellation                    | `Runtime/RollingVideoBuffer.swift`                                         |     P2 |
| `Runtime/RuntimeConfig.swift`              |   256 | **Modify** — fix silenceTimeoutMs default (2000→5000), guard force-unwraps                      | `Runtime/RuntimeConfig.swift`                                              | P0, P2 |
| `Runtime/ExampleMediaPipelineTester.swift` |   353 | **Move** — out of main target → `DeveloperTools/` (exclude from Release)                        | `DeveloperTools/ExampleMediaPipelineTester.swift`                          |     P0 |
| `ViewModels/StreamSessionViewModel.swift`  |   617 | **Split** — decompose into SessionStateStore + SessionViewModel + property forwarding           | `ViewModels/SessionStateStore.swift` + `ViewModels/SessionViewModel.swift` |     P1 |
| `ViewModels/WearablesViewModel.swift`      |   152 | **Modify** — replace Meta copyright, minor cleanup                                              | `ViewModels/WearablesViewModel.swift`                                      |     P0 |
| `Views/HomeScreenView.swift`               |   448 | **Replace** — rebuild as 3-page onboarding                                                      | `Views/Onboarding/OnboardingContainerView.swift` + pages                   |     P4 |
| `Views/NonStreamView.swift`                |   457 | **Replace** — rebuild as StandbyView with design tokens                                         | `Views/Session/StandbyView.swift`                                          |     P4 |
| `Views/StreamView.swift`                   |   137 | **Replace** — rebuild as LiveSessionView + HUD                                                  | `Views/Session/LiveSessionView.swift` + `SessionHUDView.swift`             |     P4 |
| `Views/StreamSessionView.swift`            |    53 | **Replace** — rebuild as SessionContainerView                                                   | `Views/Session/SessionContainerView.swift`                                 |     P4 |
| `Views/MainAppView.swift`                  |    37 | **Modify** — receive `.onOpenURL`, connect to coordinators                                      | `Views/MainAppView.swift`                                                  |     P1 |
| `Views/PhotoPreviewView.swift`             |   115 | **Modify** — design system tokens, replace copyright                                            | `Views/Photo/PhotoPreviewView.swift`                                       |     P4 |
| `Views/RegistrationView.swift`             |    30 | **Delete** — `.onOpenURL` moves to `MainAppView`                                                | —                                                                          |     P1 |
| `Views/Components/TipRowView.swift`        |    59 | **Keep** — update tokens                                                                        | `Views/Common/TipRowView.swift`                                            |     P4 |
| `Views/Components/CircleButton.swift`      |    41 | **Keep** — update tokens                                                                        | `Views/Common/CircleButton.swift`                                          |     P4 |
| `Views/Components/CustomButton.swift`      |    83 | **Replace** — rebuild as `PrimaryButton`                                                        | `Views/Common/PrimaryButton.swift`                                         |     P4 |

### Files To Create (not in codebase yet)

| Target file                                   | Purpose                                 | Phase |
| --------------------------------------------- | --------------------------------------- | ----: |
| `Audio/AudioSessionArbiter.swift`             | Single owner of AVAudioSession category |    P2 |
| `Runtime/RuntimeProtocols.swift`              | Service protocols for DI                |    P1 |
| `ViewModels/SessionStateStore.swift`          | `@Observable` store for all UI state    |    P1 |
| `ViewModels/OnboardingViewModel.swift`        | Permission flow state machine           |    P4 |
| `Coordinators/DeviceSessionCoordinator.swift` | DAT StreamSession lifecycle             |    P1 |
| `Coordinators/RuntimeCoordinator.swift`       | Wires coordinator → orchestrator        |    P1 |
| `Utilities/Clocks.swift`                      | Single `Clocks.nowMs()`                 |    P1 |
| `Utilities/KeychainCredentialStore.swift`     | Secure credential storage               |    P3 |
| `Utilities/NWReachability.swift`              | NWPathMonitor wrapper                   |    P3 |
| `DesignSystem/Colors.swift`                   | Semantic colour tokens                  |    P4 |
| `DesignSystem/Typography.swift`               | Font presets                            |    P4 |
| `DesignSystem/Spacing.swift`                  | Grid constants                          |    P4 |
| `DesignSystem/Icons.swift`                    | SF Symbol enum                          |    P4 |
| `Views/Onboarding/*`                          | 3-page onboarding flow                  |    P4 |
| `Views/Session/SessionContainerView.swift`    | Root session container                  |    P4 |
| `Views/Session/StandbyView.swift`             | Pre-activation view                     |    P4 |
| `Views/Session/LiveSessionView.swift`         | Camera feed + HUD                       |    P4 |
| `Views/Session/SessionHUDView.swift`          | Status pill overlay                     |    P4 |
| `Views/Settings/SettingsView.swift`           | Preferences + dev tools                 |    P4 |
| `Views/Common/StatusBadge.swift`              | Reusable status indicator               |    P4 |
| `Views/Common/WaveformView.swift`             | Animated audio waveform                 |    P4 |
| `Config/Debug.xcconfig`                       | Debug build config                      |    P0 |
| `Config/Release.xcconfig`                     | Release build config                    |    P0 |

### Test Files (IOS/PortWorldTests/)

| File                                           | Status     | Phase |
| ---------------------------------------------- | ---------- | ----: |
| `WSMessageCodecTests.swift` (458 lines)        | **Exists** |     — |
| `EventLoggerTests.swift` (257 lines)           | **Exists** |     — |
| `ManualWakeWordEngineTests.swift` (144 lines)  | **Exists** |     — |
| `QueryEndpointDetectorTests.swift` (210 lines) | **Exists** |     — |
| `RuntimeConfigTests.swift`                     | To create  |    P5 |
| `ClocksTests.swift`                            | To create  |    P5 |
| `AudioCollectionManagerTests.swift`            | To create  |    P5 |
| `AssistantPlaybackEngineTests.swift`           | To create  |    P5 |
| `SessionOrchestratorTests.swift`               | To create  |    P5 |
| `VisionFrameUploaderTests.swift`               | To create  |    P5 |
| `RollingVideoBufferTests.swift`                | To create  |    P5 |
| `QueryBundleBuilderTests.swift`                | To create  |    P5 |
| `WavFileWriterTests.swift`                     | To create  |    P5 |
| `SFSpeechWakeWordEngineTests.swift`            | To create  |    P5 |
| `SessionWebSocketClientTests.swift`            | To create  |    P5 |
| Snapshot tests                                 | To create  |    P5 |

**Totals:** 28 source files (7 818 lines) → 28 modified/replaced + 22 new files. 4 existing test files + 12 new test files.

---

## Execution Order

```
Phase 0  →  Phase 1  →  Phase 2  ─┬─►  Phase 3 (data layer)
(cleanup)   (DI + decompose)        │
                                   ├─►  Phase 4 (UI/UX)
                                   │         │
                                   │    Phase 5 (tests)
                                   │    (written alongside 3 + 4)
                                   │
                                   └─►  Phase 6 (realtime streaming)
                                        (can start after Phase 2)
```

Phases 3, 4, 5 can proceed in parallel once Phase 2 is complete.

---

## Phase 0 — Purge Hackathon Artifacts

**Outcome:** Clean compilable baseline, no LAN IPs, no stale copy, no accidental secrets.

### Phase 0 status (2026-03-03)

- ✅ **P0-01 complete.**
  - `SON_BACKEND_BASE_URL` now uses `$(BACKEND_BASE_URL)` in `IOS/Info.plist`.
  - `IOS/Config/Debug.xcconfig`, `IOS/Config/Release.xcconfig`, and `IOS/Config/Config.xcconfig.template` exist and are wired in project build configurations.
- ✅ **P0-02 complete.**
  - Removed `com.apple.developer.associated-domains` from `IOS/PortWorld.entitlements`.
- ✅ **P0-03 complete (open-source adjustment).**
  - Meta boilerplate headers were removed from app Swift files.
  - Project copyright line was then removed by decision because the repository is open-source.
- ✅ **P0-04 complete with approved simplification.**
  - `ExampleMediaPipelineTester` and `ExampleMedia/` were removed from production code paths and from the codebase.
  - `PortWorldDev` shared scheme was added for developer workflow continuity.
- ✅ **P0-05 complete.**
  - `RuntimeConfig.silenceTimeoutMs` default changed from `2_000` to `5_000`.
- ✅ **P0-06 complete.**
  - Replaced stale user-facing copy (`"Camera Access"` → `"Microphone Access"` and removed `"OPEN SOURCE BOOST"`).
- ✅ **P0-07 complete.**
  - Added `audio` to `UIBackgroundModes` in `IOS/Info.plist`.
- ✅ **Build verification complete.**
  - xcodebuild simulator build succeeds for `PortWorld` after Phase 0 changes.

### P0-01 Remove committed LAN IP

**File:** `IOS/Info.plist`  
**Action:**

1. Change `SON_BACKEND_BASE_URL` value from `http://172.16.0.104:8082` to `http://127.0.0.1:8080`.
2. Create `IOS/Config/Debug.xcconfig` (gitignored) with `BACKEND_BASE_URL = http://192.168.x.x:8080`.
3. Create `IOS/Config/Release.xcconfig` with `BACKEND_BASE_URL = https://api.portworld.app`.
4. Create `IOS/Config/Config.xcconfig.template` (committed) showing the structure with placeholder values.
5. Add `Config/*.xcconfig` to `.gitignore` (never commit real URLs or keys).
6. In `Info.plist`, replace the hardcoded value with `$(BACKEND_BASE_URL)`.
7. In Xcode project settings, assign the appropriate xcconfig to each scheme configuration.

**Verify:** `grep -r "172.16.0" IOS/` returns no results.

---

### P0-02 Remove unused entitlement

**File:** `IOS/PortWorld.entitlements`  
**Action:** Delete the `com.apple.developer.associated-domains` entry (`applinks:www.didro.dev`). No Universal Link handling exists in the codebase. The `portworld://` custom URL scheme handles all callbacks.

---

### P0-03 Strip stale copyright headers

**Files affected:** `StreamSessionViewModel.swift` and any other file with the Meta Platforms boilerplate header that was inherited from the DAT SDK sample code and not replaced.  
**Action:** Replace with: `// Copyright © 2026 PortWorld. All rights reserved.`

---

### P0-04 Move `ExampleMediaPipelineTester` out of the main target

**Files:** `ExampleMediaPipelineTester.swift`, `ExampleMedia/` folder  
**Action:**

1. Create a new group `IOS/PortWorld/DeveloperTools/`.
2. Move `ExampleMediaPipelineTester.swift` into it.
3. In Xcode, remove `ExampleMediaPipelineTester.swift` and all files in `ExampleMedia/` from the **PortWorld** target membership.
4. Create a separate **PortWorldDev** scheme that includes these files (or a unit test target that exercises the pipeline).
5. Remove the `ExampleMediaPipelineTester` instance from `HomeScreenView.swift`.
6. Remove the `ExampleMediaPipelineTester` instance from `StreamSessionViewModel.swift`.

**Rationale:** The tester embeds a hardcoded French prompt, duplicates `WavFileWriter` logic, and reconfigures the shared audio session category — all incompatible with a production build.

---

### P0-05 Fix `silenceTimeoutMs` default

**File:** `RuntimeConfig.swift`  
**Action:** Change `silenceTimeoutMs: 2_000` default to `5_000`. This fixes the mismatch between code and every PRD document.

---

### P0-06 Fix stale user-facing copy

**Files:** `GettingStartedSheetView` (inside `NonStreamView.swift`), `HomeScreenView.swift`  
**Action:**

1. "Camera Access" label → "Microphone Access"
2. Remove any "OPEN SOURCE BOOST" artifact strings

---

### P0-07 Add `audio` to background modes

**File:** `IOS/Info.plist`  
**Action:** Add `audio` to the `UIBackgroundModes` array. Without this, `AVAudioEngine` can be suspended by iOS when the app backgrounds, breaking both capture and playback.

---

## Phase 1 — Architecture: Dependency Injection and Decomposition

**Outcome:** All major services are injectable. The God-ViewModel is split. Each file has one clear ownership. The app compiles and runs identically to Phase 0.

### Phase 1 status (2026-03-03)

- ✅ **Phase 1 complete.**
- ✅ `SessionOrchestrator.Dependencies` finalized for DI: clock injection, direct `eventLogger`, playback engine factory, and activation-time service construction.
- ✅ `RuntimeProtocols.swift` and concrete wiring aligned for protocol-based handler binding (no orchestrator concrete-cast dependency for websocket/uploader callbacks).
- ✅ ViewModel decomposition complete: `SessionStateStore` is the UI state owner, coordinator wiring lives in `RuntimeCoordinator`, and `SessionViewModel` is a thin activation/deactivation shell.
- ✅ `RegistrationView` removal and root `.onOpenURL` migration to `MainAppView` complete.
- ✅ SDK init failure path hardened in `PortWorldApp` (no unconditional `Wearables.shared` usage after configure failure).
- ✅ Intermediary Phase 1 regressions closed: stable `SessionViewModel` lifetime in `StreamSessionView`, protocol-safe runtime wiring, and clock semantics/visibility aligned with plan intent.
- ✅ **Post-review verification pass complete (2026-03-03):**
  - Fixed P0 regression: `.deactivating` → `.inactive` state transition was missing, causing stuck UI after deactivation.
  - Unified timestamp source: `AudioCollectionManager.nowMs()` now delegates to `Clocks.nowMs()`.
  - Consistency cleanup: `EventLoggerProtocol` visibility aligned to `internal`, `StreamSessionViewModel` typealias removed, bare `print()` wrapped.
- ✅ Build verification complete via Xcode (zero errors, zero warnings).

### P1-01 Create `Utilities/Clocks.swift` — single timestamp source

**New file:** `IOS/PortWorld/Utilities/Clocks.swift`

```swift
enum Clocks {
    static func nowMs() -> Int64 { Int64(Date().timeIntervalSince1970 * 1000) }
}
```

**Then remove** the five duplicate utilities: `RuntimeClock`, `WakeWordClock`, `QueryEndpointClock`, `VisionFrameClock`, `RollingVideoClock` from their respective files. Replace every callsite with `Clocks.nowMs()`.

---

### P1-02 Extract `SessionOrchestrator.Dependencies`

**File:** `SessionOrchestrator.swift`  
**Action:**

1. Define a `Dependencies` struct (already partially existing as inner `AudioCollectionHooks`) that holds all injected services:
   ```swift
   struct Dependencies {
       var makeWebSocketClient: (RuntimeConfig) -> SessionWebSocketClientProtocol
       var makeVisionFrameUploader: (RuntimeConfig) -> VisionFrameUploaderProtocol
       var makeRollingVideoBuffer: (RuntimeConfig) -> RollingVideoBufferProtocol
       var makeQueryBundleBuilder: (RuntimeConfig) -> QueryBundleBuilderProtocol
       var eventLogger: EventLoggerProtocol
       var audioBufferDurationProvider: () -> Int
       var clock: () -> Int64
       static var live: Dependencies { ... }
   }
   ```
2. Replace all five `lazy var` properties with instances created from `Dependencies` at `activate()` time.
3. Update `init(config:dependencies:)` — `dependencies` defaults to `.live`.

---

### P1-03 Define service protocols

**New file:** `IOS/PortWorld/Runtime/RuntimeProtocols.swift`  
Define `@MainActor` protocols mirroring the public surface of each service:

- `SessionWebSocketClientProtocol`
- `VisionFrameUploaderProtocol`
- `RollingVideoBufferProtocol`
- `QueryBundleBuilderProtocol`
- `EventLoggerProtocol`
- `WakeWordEngineProtocol` (already exists as `WakeWordEngine`)
- `AssistantPlaybackEngineProtocol`

Each protocol exposes only the methods and properties called by `SessionOrchestrator`. Concrete classes `conform` to their respective protocol. This is the foundation for mock injection in tests (Phase 5).

---

### P1-04 Create `SessionStateStore`

**New file:** `IOS/PortWorld/ViewModels/SessionStateStore.swift`  
**Action:**

1. Create `@MainActor @Observable final class SessionStateStore`.
2. Move all `@Published` properties that are consumed by views out of `StreamSessionViewModel` into the store. This includes: `sessionState`, `wakeState`, `queryState`, `isStreaming`, `assistantStatus`, `lastErrorMessage`, `runtimePhotoUploadCount`, etc.
3. `SessionOrchestrator` writes to the store via a captured reference.
4. Views read from the store using `@Environment` or direct `@State` passing.

---

### P1-05 Create `DeviceSessionCoordinator`

**New file:** `IOS/PortWorld/Coordinators/DeviceSessionCoordinator.swift`  
**Action:** Extract from `StreamSessionViewModel`:

- DAT `StreamSession` lifecycle (`startStream()`, `stopStream()`)
- `AutoDeviceSelector` management
- Camera frame forwarding (`handleIncomingVideoFrame()`)
- Photo capture (`capturePhoto()`)
- Frame → `RollingVideoBuffer` relay
- Frame → `VisionFrameUploader` relay

`DeviceSessionCoordinator` is initialised with injected closure hooks for the above relays so it has no direct dependency on `SessionOrchestrator`.

---

### P1-06 Create `RuntimeCoordinator`

**New file:** `IOS/PortWorld/Coordinators/RuntimeCoordinator.swift`  
**Action:** Extract from `StreamSessionViewModel`:

- `AudioCollectionManager` ownership and lifecycle
- `SessionOrchestrator` ownership
- Wiring from `DeviceSessionCoordinator` callbacks → `SessionOrchestrator`
- Scene phase handling (`scenePhaseDidChange()`)
- Audio PCM frame forwarding to wake engine

`RuntimeCoordinator` takes `DeviceSessionCoordinator` and `SessionStateStore` as init dependencies.

---

### P1-07 Slim down `StreamSessionViewModel`

**File:** `StreamSessionViewModel.swift`  
**Action:** After P1-04 through P1-06, this file should only contain:

- References to `DeviceSessionCoordinator` and `RuntimeCoordinator`
- Activation / deactivation entry point
- Forwarding any remaining properties the views still need

Target: < 150 lines. Rename to `SessionViewModel.swift`.

---

### P1-08 Merge `RegistrationView` into `MainAppView`

**Files:** `RegistrationView.swift`, `MainAppView.swift`  
**Action:**

1. Move the `.onOpenURL` handler from `RegistrationView` to `MainAppView`.
2. Delete `RegistrationView.swift`.
3. Remove the sibling `RegistrationView()` from `PortWorldApp.swift`'s `WindowGroup`.

---

### P1-09 Fix SDK init failure in `PortWorldApp.swift`

**File:** `PortWorldApp.swift`  
**Action:**

1. Wrap `WearablesInterface.configure()` in a `do/catch`.
2. On failure, set `@State var sdkInitError: String?`.
3. Mount a non-dismissable `.alert` on the root view showing the error and a "Quit" button.
4. Remove the `#if DEBUG` guard that was silently swallowing failures in release builds.

---

## Phase 2 — Runtime Hardening

**Outcome:** All 55 identified correctness, reliability, and concurrency bugs (enumerated below) are fixed. The existing test suite stays green throughout.

<details>
<summary><strong>Bug Registry (55 items)</strong> — click to expand</summary>

Severity: **P0** = crash / data-loss risk, **P1** = incorrect behaviour, **P2** = maintainability / spec violation.

#### A. Concurrency Violations (ARCHITECTURE §6) — 12 bugs

| #   | File                           | Description                                                                                    | Sev |
| --- | ------------------------------ | ---------------------------------------------------------------------------------------------- | --- |
| 1   | `QueryEndpointDetector.swift`  | Plain `class` + `DispatchQueue` isolation instead of `actor`                                   | P1  |
| 2   | `VisionFrameUploader.swift`    | Plain `class` + `DispatchQueue` isolation instead of `actor`                                   | P1  |
| 3   | `RollingVideoBuffer.swift`     | Plain `class` + two `DispatchQueue`s instead of `actor`                                        | P1  |
| 4   | `WakeWordEngine.swift`         | `SFSpeechWakeWordEngine` uses `DispatchQueue` for isolation instead of `actor`                 | P1  |
| 5   | `WakeWordEngine.swift`         | `ManualWakeWordEngine.isListening` plain `var` read/written from multiple contexts — data race | P0  |
| 6   | `WakeWordEngine.swift`         | `SFSpeechWakeWordEngine.isListening` accessed without actor isolation                          | P0  |
| 7   | `VisionFrameUploader.swift`    | Callback-based `URLSession.dataTask` instead of `async/await` per §6                           | P1  |
| 8   | `AudioCollectionManager.swift` | `AudioChunkProcessor` marked `@unchecked Sendable` without mandatory justification comment     | P2  |
| 9   | `NonStreamView.swift`          | `DispatchQueue.main.async` in `GettingStartedSheetView` — DispatchQueue outside audio tap      | P2  |
| 10  | `SessionOrchestrator.swift`    | `handleQueryEnded()` nils `activeQueryContext` before WS send completes — race with next cycle | P1  |
| 11  | `SessionOrchestrator.swift`    | `deactivate()` does not cancel in-flight upload `Task` — leaked async work                     | P1  |
| 12  | — (missing file)               | `AudioSessionArbiter` does not exist; ACM and APE configure `AVAudioSession` independently     | P0  |

#### B. Bare `print()` Violations (§11) — 10 files, ~72 call sites

| #   | File                               |                        Count | Sev |
| --- | ---------------------------------- | ---------------------------: | --- |
| 13  | `AssistantPlaybackEngine.swift`    |                          ~30 | P2  |
| 14  | `SessionOrchestrator.swift`        |                          ~15 | P2  |
| 15  | `AudioCollectionManager.swift`     |                           ~3 | P2  |
| 16  | `EventLogger.swift`                | 1 (production path fallback) | P1  |
| 17  | `ExampleMediaPipelineTester.swift` |                           ~8 | P2  |
| 18  | `VisionFrameUploader.swift`        |                           ~3 | P2  |
| 19  | `WakeWordEngine.swift`             |                           ~4 | P2  |
| 20  | `QueryBundleBuilder.swift`         |                           ~3 | P2  |
| 21  | `RollingVideoBuffer.swift`         |                           ~2 | P2  |
| 22  | `StreamSessionViewModel.swift`     |                           ~3 | P2  |

#### C. Silent `try?` on I/O Paths (§11) — 4 bugs

| #   | File                               | Description                                                                                      | Sev |
| --- | ---------------------------------- | ------------------------------------------------------------------------------------------------ | --- |
| 23  | `QueryBundleBuilder.swift`         | `try? JSONEncoder().encode(metadata)` silently drops encoding error                              | P1  |
| 24  | `ExampleMediaPipelineTester.swift` | Multiple `try? Data(contentsOf:)` loading bundled media                                          | P1  |
| 25  | `AudioCollectionManager.swift`     | `loadChunkIndex()` uses `compactMap { try? }` — drops malformed entries silently                 | P2  |
| 26  | `RuntimeTypes.swift`               | `WSMessageCodec.decode` uses `try?` on inner payload — returns `.unknown` instead of propagating | P1  |

#### D. Correctness Bugs — 24 bugs

| #   | File                            | Description                                                                                        | Sev |
| --- | ------------------------------- | -------------------------------------------------------------------------------------------------- | --- |
| 27  | `RuntimeConfig.swift`           | `silenceTimeoutMs` defaults to 2000; PRD specifies 5000                                            | P0  |
| 28  | `RuntimeConfig.swift`           | Multiple `URL(string:)!` force-unwraps — crash on malformed plist                                  | P0  |
| 29  | `PortWorldApp.swift`            | SDK init failure caught only in `#if DEBUG`; silently swallowed in Release                         | P0  |
| 30  | `PortWorldApp.swift`            | `RegistrationView` as sibling in `WindowGroup` instead of `.onOpenURL` on `MainAppView`            | P1  |
| 31  | `HomeScreenView.swift`          | Stale `"OPEN SOURCE BOOST"` artifact string visible to users                                       | P2  |
| 32  | `NonStreamView.swift`           | `GettingStartedSheetView` labels camera icon as "Camera Access" — should be "Microphone"           | P2  |
| 33  | `SessionOrchestrator.swift`     | `sessionRestartCount += 1` fires in `activate()` — increments on first start                       | P1  |
| 34  | `SessionOrchestrator.swift`     | No outbound message queue; messages during `reconnecting` silently dropped                         | P0  |
| 35  | `SessionOrchestrator.swift`     | `startHealthLoop()` never cancels previous Task — health loops stack                               | P1  |
| 36  | `AudioCollectionManager.swift`  | Route-change observer registered before `prepareAudioSession()` — fires with stale config          | P1  |
| 37  | `AudioCollectionManager.swift`  | `stop()` from `.failed` doesn't clear `stats.lastError` — ghost error on restart                   | P1  |
| 38  | `AudioCollectionManager.swift`  | `readPCM16Payload()` hardcodes 44-byte WAV header — breaks on extended-format headers              | P1  |
| 39  | `AudioCollectionManager.swift`  | `teardownEngineIfNeeded()` stops engine without checking `AssistantPlaybackEngine` pending buffers | P0  |
| 40  | `AssistantPlaybackEngine.swift` | `startResponse()` zeros `pendingBufferCount` — orphaned completions underflow                      | P1  |
| 41  | `AssistantPlaybackEngine.swift` | Defensive `max(0, count - 1)` clamp masks logic bug — should assert in DEBUG                       | P2  |
| 42  | `AssistantPlaybackEngine.swift` | Stuck-detection timer hardcoded to 10 s — no RuntimeConfig override                                | P2  |
| 43  | `SessionWebSocketClient.swift`  | `connect()` guard checks `task == nil` but ignores `.canceling`/`.completed` — blocks reconnect    | P0  |
| 44  | `SessionWebSocketClient.swift`  | Ping loop doesn't reset backoff on successful pong                                                 | P2  |
| 45  | `RollingVideoBuffer.swift`      | No temp-file cleanup or launch-time sweep — disk grows unbounded                                   | P1  |
| 46  | `RollingVideoBuffer.swift`      | `exportClip()` has no `Task.checkCancellation()` — can't interrupt long encode                     | P1  |
| 47  | `WakeWordEngine.swift`          | `SFSpeechWakeWordEngine` has no circuit breaker — error restarts spin-loop indefinitely            | P0  |
| 48  | `WakeWordEngine.swift`          | `normalizePhrase()` lowercases but doesn't strip punctuation                                       | P1  |
| 49  | `VisionFrameUploader.swift`     | No `frameDropCount` counter when upload skipped — metric lost                                      | P2  |
| 50  | `QueryBundleBuilder.swift`      | Retry uses fixed 1 s delay — no exponential backoff                                                | P2  |

#### E. Structural / Spec-Compliance — 5 bugs

| #   | File                               | Description                                                                           | Sev |
| --- | ---------------------------------- | ------------------------------------------------------------------------------------- | --- |
| 51  | `RuntimeTypes.swift` + 4 files     | Five duplicate `nowMs()` clock enums — should be single `Clocks.nowMs()`              | P2  |
| 52  | 10 View/ViewModel files            | Meta copyright boilerplate — must be replaced with project header                     | P2  |
| 53  | `ExampleMediaPipelineTester.swift` | Calls `.playback` category on shared `AVAudioSession` — clobbers HFP config           | P0  |
| 54  | `ExampleMediaPipelineTester.swift` | Hardcoded French prompt — should use `RuntimeConfig` or be locale-aware               | P2  |
| 55  | `Info.plist`                       | `SON_BACKEND_BASE_URL` contains hardcoded LAN IP — must be empty (xcconfig injection) | P0  |

**Totals:** 11 P0 · 23 P1 · 21 P2

</details>

### P2-01 Introduce `AudioSessionArbiter`

**New file:** `IOS/PortWorld/Audio/AudioSessionArbiter.swift`

```swift
actor AudioSessionArbiter {
    enum Lease { case playAndRecordHFP, playbackOnly }
    func acquireLease(_ lease: Lease) async throws { ... }
    func releaseLease() async { ... }
}
```

- `AudioCollectionManager` acquires `.playAndRecordHFP` at session start; releases at stop.
- `AssistantPlaybackEngine` never calls `AVAudioSession` directly — it relies on the existing lease.
- `DeveloperPipelineTester` acquires `.playbackOnly` only after confirming no capture lease is held.

---

### P2-02 `AudioCollectionManager` fixes

1. **Clear `lastError` on recovery:** In `stop()` called from `.failed` state, set `stats.lastError = nil`.
2. **Route-change observer ordering:** Move `NotificationCenter.addObserver` for `AVAudioSession.routeChangeNotification` to inside `prepareAudioSession()`, after the session is activated, not in `init()`.
3. **PCM extraction:** Replace `wavData.subdata(in: 44 ..< ...)` with a proper skip of the RIFF header by parsing the `data` chunk offset. This prevents silent corruption for any WAV with extended format chunks. The simplest fix: pass raw PCM buffers through a parallel path instead of re-parsing the written WAV.
4. **Shared engine stop:** In `teardownEngineIfNeeded()`, do not call `sharedAudioEngine.stop()` while `AssistantPlaybackEngine` has pending buffers. Add a check via a protocol method `hasActivePendingPlayback() -> Bool` before stopping.

---

### P2-03 `AssistantPlaybackEngine` fixes

1. **`ensureEngineRunning` guard:** Wrap the `audioEngine.start()` call: `guard !audioEngine.isRunning else { return }`.
2. **Audio session ordering:** Add a guard at the top of `appendPCMData` that verifies `AVAudioSession.sharedInstance().category == .playAndRecord`. If the category is incorrect, log the event and early-return without scheduling; this prevents the engine from starting with the wrong session configuration.
3. **`pendingBufferCount` desync:** Replace the two `Int` counters with a single `pendingBufferCount: Int` that is incremented atomically at schedule-time and decremented exactly once in the completion callback. Remove `max(0, ...)` guards — if they fire, that's a logic bug, not a defensive case.
4. **`startResponse()` reset:** Instead of unconditionally zeroing `pendingBufferCount`, check if it is `> 0` and emit a warning log event before resetting.
5. **Remove all bare `print()` calls:** Replace with `eventLogger?.log(...)` or `os_log(.debug, ...)` inside `#if DEBUG`.

---

### P2-04 `SessionOrchestrator` fixes

1. **`sessionRestartCount` initialisation:** Set to `0`. Increment only in `handleDeactivate()`, not in `activate()`.
2. **Outbound message buffer:** Implement an `outboundQueue: [WSOutboundMessage]` (max 20). When `sendOutbound()` is called while `notConnected`, append to queue. On connection established, drain queue before resuming normal sends. Discard messages older than 60s.
3. **Cancel in-flight upload on deactivate:** Store the `Task<Void, Never>` returned by `Task { await queryBundleBuilder.upload(...) }` as `currentUploadTask`. In `deactivate()`, call `currentUploadTask?.cancel(); currentUploadTask = nil`.
4. **`activeQueryContext = nil` race:** Preserve `activeQueryContext` through the `query.bundle.uploaded` send sequence. Only nil it after the WS send has been enqueued.
5. **Replace all `print("[DEBUG]")` calls:** Use `os_log(.debug, log: orchestratorLog, ...)` where `orchestratorLog` is a file-scoped `OSLog` instance.

---

### P2-05 `SFSpeechWakeWordEngine` circuit-breaker

**File:** `WakeWordEngine.swift`

1. Add `consecutiveErrorCount: Int = 0`.
2. In `handleRecognitionUpdateLocked()` on error: increment counter. If `>= 5`, transition to `.failed` and do **not** restart. Emit a log event.
3. After a successful recognition result, reset `consecutiveErrorCount = 0`.
4. Add transcript normalisation before `contains()`: strip punctuation using `CharacterSet.punctuationCharacters`, collapse multiple spaces.

---

### P2-06 `SessionWebSocketClient` stale task fix

**File:** `SessionWebSocketClient.swift`  
In `connect()`: before the `guard webSocketTask == nil` early-exit, check `webSocketTask?.state`. If the state is `.canceling` or `.completed`, set `webSocketTask = nil` and proceed with a fresh connection.

---

### P2-07 `RollingVideoBuffer` cleanup and cancellation

**File:** `RollingVideoBuffer.swift`

1. **Temp file registry:** Keep a `Set<URL>` of all temp MP4 files written. In `stop()`, delete all. On `exportInterval()` — after a successful query upload (signalled by completion callback) — delete the specific file.
2. **Launch sweep:** In `init()`, scan `FileManager.default.temporaryDirectory/clips/` and delete any stale `.mp4` files from previous sessions.
3. **Cancellation:** Wrap `exportInterval()` body in `try Task.checkCancellation()` at the top and after the `AVAssetWriter.finishWriting()` call. Callers cancel via `Task.cancel()`.

---

### P2-08 `QueryBundleBuilder` error propagation

**File:** `QueryBundleBuilder.swift`  
Replace `guard let metadataJSON = try? JSONEncoder().encode(metadata)` with a proper `do { metadataJSON = try ... } catch { throw QueryBundleError.metadataEncodingFailed(error) }`.

---

### P2-09 `VisionFrameUploader` — convert to `async/await`

**File:** `VisionFrameUploader.swift`

1. Replace the callback-based `URLSession.dataTask` implementation with `async let _ = URLSession.shared.data(for: request)`.
2. Manage the rate-limiting 1fps gate using a `Timer.publish` stream or `Task.sleep` in an `actor`-isolated upload loop.
3. Add `frameDropCount: Int` counter; increment when a new frame arrives before the previous upload completes. Include in health stats.

---

### P2-10 Concurrency audit

Run `xcodebuild test` with `-strictConcurrency=complete` enabled in the build settings. Address any Swift 6 data-race warnings. Priority files: `WakeWordEngine` (`isListening` property isolation), `AudioCollectionManager` (shared engine reference).

---

## Phase 3 — Data Layer and Feature Completeness

**Outcome:** All P0 and P1 missing features from the PRD closed.

### P3-01 `KeychainCredentialStore`

**New file:** `IOS/PortWorld/Utilities/KeychainCredentialStore.swift`

```swift
struct KeychainCredentialStore {
    static func store(apiKey: String) throws { ... }
    static func retrieve() throws -> String? { ... }
    static func clear() throws { ... }
}
```

- On first launch, seed from `Info.plist` `SON_API_KEY` value (if non-empty).
- `RuntimeConfig` reads from keychain, not directly from plist.
- Add a "Reset credentials" option in Settings (clears keychain; requires re-entry).

---

### P3-02 JSONL on-disk event log

**File:** `EventLogger.swift`

1. Add a `JSONLFileSink` inner class that writes events to `applicationSupportDirectory/logs/events-N.jsonl`.
2. Cap each file at 5MB; rotate to `events-(N+1).jsonl` on overflow. Keep max 3 files; delete oldest on rotation.
3. Wire the sink as the second destination in `EventLogger.init()` (first: in-memory circular buffer, as today).
4. Add `exportCurrentLog() -> URL` for the developer export feature.

---

### P3-03 `NWReachability` wrapper

**New file:** `IOS/PortWorld/Utilities/NWReachability.swift`

```swift
@MainActor final class NWReachability: ObservableObject {
    @Published var isConnected: Bool = true
    func startMonitoring() { ... }   // NWPathMonitor
}
```

- Inject into `SessionWebSocketClient`. When `isConnected` becomes `false`, suspend the reconnect backoff loop. On restoration, trigger an immediate reconnect attempt.
- Surface a "No internet" banner in `SessionHUDView` when disconnected.

---

### P3-04 Vision frame drop telemetry

**Files:** `VisionFrameUploader.swift`, `RuntimeTypes.swift` (health.stats payload)

1. Add `frameDropCount: Int` to `VisionFrameUploader`.
2. Include `frame_drop_count` and `frame_drop_rate` in the `health.stats` WS payload.
3. Reset counters after each health emission.

---

### P3-05 App metadata in WS payloads

**File:** `RuntimeTypes.swift` (health.stats struct), `QueryBundleBuilder.swift` (metadata part)  
Add `app_version`, `device_model`, `os_version` to:

- `HealthStatsPayload`
- `QueryMetadata`

Read from `Bundle.main.infoDictionary["CFBundleShortVersionString"]`, `UIDevice.current.model`, `UIDevice.current.systemVersion`.

---

### P3-06 Configurable silence timeout and wake phrase

**Files:** `RuntimeConfig.swift`, `SettingsView.swift` (Phase 4)

1. Read `silenceTimeoutMs` from `UserDefaults` (key: `portworld.silenceTimeoutMs`) if set; fall back to `RuntimeConfig.silenceTimeoutMs`.
2. Read `wakePhrase` from `UserDefaults` (key: `portworld.wakePhrase`); default `"hey mario"`.
3. Both are written by `SettingsView` and re-read on next session activation.

---

### P3-07 Streaming query bundle upload

**File:** `QueryBundleBuilder.swift`

1. Replace `Data(contentsOf: videoURL)` full-load with `InputStream`-based chunked write.
2. Build the multipart body by streaming from disk rather than assembling one `Data` blob in RAM.
3. Use `URLSession.uploadTask(withStreamedRequest:)`.
4. This eliminates the RAM spike for large video segments.

---

## Phase 4 — UX / UI Redesign

**Outcome:** Consumer-quality interface matching Apple HIG; adaptive light/dark; accessible; no debug UI in production.

### P4-01 Create `DesignSystem/`

**New files:** `Colors.swift`, `Typography.swift`, `Spacing.swift`, `Icons.swift`  
See `ARCHITECTURE.md §10` for the full token set. All existing hardcoded colours (`Color.white.opacity(0.1)`, `"appPrimaryColor"`) are replaced with design system tokens. Asset catalog gains adaptive colour variants for each semantic role.

---

### P4-02 Rebuild `HomeScreenView` → `OnboardingContainerView`

1. Replace the current single-screen approach with a 3-page `TabView(.page)` onboarding.
2. Page 1 — value proposition: hero illustration (SF Symbol composite) + headline + subheadline.
3. Page 2 — permissions: request microphone + speech recognition here using `AVAudioSession.requestRecordPermission` and `SFSpeechRecognizer.requestAuthorization`. Show a friendly explanation. If denied, surface an "Open Settings" link.
4. Page 3 — connect glasses: existing registration CTA card + registration status badge.
5. All `HomeGlassCard`, `HomeStateBadge`, `HomeProgressRow`, `HomeFeatureRow` private subviews are updated to use design system tokens.
6. Backend test button moves to Settings (developer section).
7. Verify the `.onOpenURL` handler is on `MainAppView` (moved there in P1-08). `OnboardingContainerView` does **not** own the URL handler — it delegates to the parent via `WearablesViewModel`.

---

### P4-03 Build `DevicePairingView`

**New file:** `IOS/PortWorld/Views/Pairing/DevicePairingView.swift`

- Shown after onboarding when device is not connected.
- Animated three-state ring: `.searching` (slow pulse) → `.found` (faster pulse + device name) → `.connected` (solid green fill).
- Uses `WearablesViewModel.deviceConnectionState`.
- "Having trouble?" link to a troubleshooting sheet.

---

### P4-04 Rebuild `NonStreamView` → `StandbyView`

**New file:** `IOS/PortWorld/Views/Session/StandbyView.swift`

1. Full-screen dark background with ambient orb accent.
2. Centre: large ring button — "Hold to activate". Uses `.simultaneousGesture(LongPressGesture(minimumDuration: 0.8))`. Visual feedback: ring fills during hold, vibrates on completion (`.impactOccurred()`).
3. Connecting phase: ring becomes a spinner with a "Connecting…" label underneath.
4. **Developer mode** (hidden by default; toggle in Settings): `DisclosureGroup` containing `RuntimeStatusPanelView`. In Release builds, `RuntimeStatusPanelView` is conditionally compiled out with `#if DEBUG`.
5. Remove activate/test action bar; settings icon in nav bar instead.

---

### P4-05 Rebuild `StreamView` → `LiveSessionView`

**New file:** `IOS/PortWorld/Views/Session/LiveSessionView.swift`

1. Full-screen camera feed (unchanged).
2. Top: minimal status pill (`.ultraThinMaterial` background, 44pt height): wake state icon + session duration timer.
3. While assistant is speaking: animated waveform pill replaces the status pill.
4. Bottom: `.sheet` with `.presentationDetents([.height(120)])` — always partially visible. Contains: Camera button, Wake button. Swipe up to expand with additional controls.
5. "End session" is a destructive button inside the expanded bottom sheet (requires a second confirmation tap).
6. **Wake state ring animation:** centred behind the status pill — concentric rings animate from idle (dim) → listening (slow pulse) → active (fast pulse, blue) → processing (spinner).
7. Error toast: bottom-anchored banner with message + "Retry" CTA; auto-dismissed after 5s.

---

### P4-06 Build `SettingsView`

**New file:** `IOS/PortWorld/Views/Settings/SettingsView.swift`  
Sections:

1. **Assistant** — Silence timeout slider (1–10s, labeled `"End query after X seconds of silence"`); Wake phrase text field.
2. **Account** — Meta account status; "Re-register with Meta" button.
3. **Developer** (visible only in Debug builds or if developer mode toggled) — Runtime telemetry live view; "Export logs" button; Backend URL override field.
4. **About** — App version + build number; link to privacy policy.

---

### P4-07 Rebuild `PhotoPreviewView`

**File:** `IOS/PortWorld/Views/Photo/PhotoPreviewView.swift`

1. Remove the `DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { showShareSheet = true }` auto-open.
2. Show two explicit buttons: "Save to Photos" (calls `UIImageWriteToSavedPhotosAlbum`) and "Share" (presents `ActivityView`).
3. Haptic feedback on save success.

---

### P4-08 Accessibility pass

Go through every view after P4-01–P4-07 and:

1. Add `.accessibilityLabel(...)` to every `CircleButton` / icon-only element.
2. Replace hardcoded font sizes with Dynamic Type variants from `DS.Type`.
3. Add `AccessibilityNotification.announcement(...)` calls in `SessionStateStore` setters for `wakeState`, `queryState`, and `lastErrorMessage`.
4. Verify VoiceOver focus order in onboarding and live session screens.

---

### P4-09 Light / dark mode

1. All colours switched from hardcoded dark-only values to design system adaptive tokens (done in P4-01).
2. Asset catalog: add light variants for all colour assets.
3. Run the app in light mode on the Simulator and verify every screen.

---

## Phase 5 — Test Suite

**Outcome:** > 70% code coverage on `Runtime/` and `Audio/`; all major runtime scenarios have unit tests; snapshot tests lock in the UI.

### P5-01 `RuntimeConfigTests`

**File:** `PortWorldTests/RuntimeConfigTests.swift`

- Load from a mock `[String: Any]` dict (no real plist needed).
- Assert HTTP → WS URL conversion for both `http://` and `https://`.
- Assert all key defaults when keys are absent.
- Assert `silenceTimeoutMs` reads `UserDefaults` override when set.

---

### P5-02 `ClocksTests`

**File:** `PortWorldTests/ClocksTests.swift`

- `nowMs()` is within ±50ms of `Date().timeIntervalSince1970 * 1000`.
- Monotonic: two consecutive calls are non-decreasing.

---

### P5-03 `AudioCollectionManagerTests`

**File:** `PortWorldTests/AudioCollectionManagerTests.swift`

- State machine: `idle → startAudio() → active → stopAudio() → idle`.
- Error recovery: enter `.failed`, call `stopAudio()`, assert `state == .idle` and `stats.lastError == nil`.
- WAV chunk emission: mock `AudioChunkProcessor` sink; assert correct chunk count after N mock tap invocations.
- Route-change observer is not registered before `prepareAudioSession()` completes.

---

### P5-04 `AssistantPlaybackEngineTests`

**File:** `PortWorldTests/AssistantPlaybackEngineTests.swift`  
Mock `AVAudioEngine` and `AVAudioPlayerNode` via protocols:

- `appendPCMData` increments `pendingBufferCount`.
- `cancelResponse()` resets `pendingBufferCount`.
- Stuck watchdog timer fires after 5s with pending buffers and no completions; verify recovery method called.
- `startResponse()` emits warning log when `pendingBufferCount > 0`.

---

### P5-05 `SessionOrchestratorTests`

**File:** `PortWorldTests/SessionOrchestratorTests.swift`  
Use mock implementations from `RuntimeProtocols.swift`:

- Full wake → query → upload flow: verify correct outbound WS messages in order.
- `deactivate()` while upload in flight: verify `Task.cancel()` is called.
- Outbound buffer: send wake event while WS disconnected; reconnect; verify message drained.
- `sessionRestartCount` is `0` after first activation; `1` after first deactivation + reactivation.
- `activeQueryContext` is still non-nil during `query.bundle.uploaded` send.

---

### P5-06 `VisionFrameUploaderTests`

**File:** `PortWorldTests/VisionFrameUploaderTests.swift`  
Mock `URLSession` via `URLSessionProtocol`:

- Only one upload per second; subsequent frames within the window are dropped.
- `frameDropCount` increments on every dropped frame.
- Retry fires on HTTP 503.
- No upload when `isActive == false`.

---

### P5-07 `RollingVideoBufferTests`

**File:** `PortWorldTests/RollingVideoBufferTests.swift`

- Append 10 frames at `maxDuration = 5s`; verify only the most recent 5s are retained.
- `exportInterval()` produces a valid MP4 at the temp URL (use `AVURLAsset` to verify track count and duration).
- Temp files are deleted after `stop()`.
- `exportInterval()` task can be cancelled; temp file is cleaned up on cancellation.

---

### P5-08 `QueryBundleBuilderTests`

**File:** `PortWorldTests/QueryBundleBuilderTests.swift`  
Mock `URLSession`:

- Multipart body contains three parts in order: `metadata`, `audio`, `video`.
- Retry fires on HTTP 429 with backoff; succeeds on third attempt.
- `Task.cancel()` propagates into the upload coroutine.
- `metadata` JSON encoding error surfaces as `QueryBundleError.metadataEncodingFailed`.

---

### P5-09 `WavFileWriterTests`

**File:** `PortWorldTests/WavFileWriterTests.swift`

- Write 1000 PCM16 samples at 8kHz; verify RIFF header bytes at offsets 0–43.
- `chunkSize` field equals `file size - 8`.
- `sampleRate` field encodes correctly as little-endian UInt32.

---

### P5-10 `SFSpeechWakeWordEngineTests`

**File:** `PortWorldTests/SFSpeechWakeWordEngineTests.swift`

- Circuit-breaker: inject 5 consecutive recognition errors; verify engine enters `.failed` and does not restart.
- Transcript normalisation: `"hey,mario"` → match. `"hey  mario"` (double space) → match. `"MARIO"` (uppercase) → match.
- Cooldown: second trigger within cooldown window is suppressed.

---

### P5-11 `SessionWebSocketClientTests`

**File:** `PortWorldTests/SessionWebSocketClientTests.swift`

- Stale task cleanup: inject completed task; call `connect()`; verify fresh connection attempted.
- Sequence number increments on each send.
- Backoff bounds: verify exponential delay is between `min * 0.8` and `max * 1.2`.

---

### P5-12 UI Snapshot Tests

**File:** `PortWorldTests/SnapshotTests.swift`  
Using `swift-snapshot-testing` (add as SPM dependency):

- `OnboardingContainerView` — page 1, 2, 3 (light + dark).
- `DevicePairingView` — searching, connected states (light + dark).
- `StandbyView` — idle, connecting states (light + dark).
- `LiveSessionView` — idle, active-query, assistant-speaking (light + dark).
- `SettingsView` (light + dark).

Snapshots are committed to the repo and checked in CI.

---

## Phase 6 — Realtime Streaming Foundation

**Outcome:** The batch query pipeline is replaced by a persistent audio streaming architecture. The app can open a bidirectional audio session triggered by wake word and closed by sleep word.

> **Prerequisite:** Phases 0–2 complete (clean codebase, DI, hardened runtime). Phases 3–5 can proceed in parallel.

### P6-01 Define `RealtimeTransport` protocol and types

**New files:** `IOS/PortWorld/Runtime/Transport/RealtimeTransport.swift`, `TransportTypes.swift`

Define the protocol and supporting types as specified in ARCHITECTURE.md §14.3:

- `RealtimeTransport` protocol (connect, disconnect, sendAudio, sendControl, events)
- `TransportEvent`, `TransportState`, `TransportConfig`, `AudioStreamFormat`
- `TransportControlMessage` — enum of JSON control message types
- `TransportError` — typed transport errors (connectionFailed, authError, timeout, protocolError)

### P6-02 Implement `GatewayTransport` adapter

**New file:** `IOS/PortWorld/Runtime/Transport/GatewayTransport.swift`

1. Wraps `SessionWebSocketClient` (reuse existing actor).
2. Adds binary frame support per §14.4 (1-byte type + 8-byte LE timestamp + raw PCM).
3. Routes text frames through existing JSON codec; binary frames through new audio path.
4. Conforms to `RealtimeTransport`.
5. Maps existing WS control messages (`session.state`, `assistant.playback.control`, `health.pong`, `error`) to `TransportEvent.controlReceived`.

### P6-03 Add sleep word to `SFSpeechWakeWordEngine`

**File:** `IOS/PortWorld/Runtime/WakeWordEngine.swift`

1. Extend `WakeWordEngine` protocol: `var sleepPhrases: [String]`.
2. `SFSpeechWakeWordEngine` recognises both wake and sleep phrases during streaming.
3. Sleep word detection fires `delegate.handleSleepDetected()` on `SessionOrchestrator`.
4. Add `SON_SLEEP_PHRASE` to `RuntimeConfig` (default: `"goodbye mario"`).

### P6-04 Refactor `AudioCollectionManager` for streaming output

**File:** `IOS/PortWorld/Audio/AudioCollectionManager.swift`

1. Add a `audioStreamSink: ((Data, Int64) -> Void)?` callback alongside the existing tap.
2. When `audioStreamSink` is set, PCM frames from the input tap are forwarded directly (no WAV chunking).
3. `AudioChunkProcessor` is bypassed entirely when streaming — no disk writes for the audio path.
4. RMS speech-activity feedback remains active (UI waveform).
5. SFSpeech feed remains active (wake/sleep word detection).

### P6-05 Evolve `SessionOrchestrator` state machine

**File:** `IOS/PortWorld/Runtime/SessionOrchestrator.swift`

1. Add new states: `.streaming`, `.disconnecting` (alongside existing states).
2. Wake word triggers `transport.connect()` → enters `.streaming`.
3. Sleep word triggers `transport.disconnect()` → enters `.disconnecting` → `.idle`.
4. In `.streaming` state: forward `AudioCollectionManager` PCM to `transport.sendAudio()`.
5. Consume `transport.events` async stream: route audio to `AssistantPlaybackEngine`, control messages to state updates.
6. Remove `QueryEndpointDetector` usage in streaming path.
7. Remove `QueryBundleBuilder` usage in streaming path.
8. `VisionFrameUploader` continues operating independently (HTTP POST).

### P6-06 Wire transport into `RuntimeCoordinator`

**File:** `IOS/PortWorld/Coordinators/RuntimeCoordinator.swift`

1. Inject `RealtimeTransport` (default: `GatewayTransport`) into `SessionOrchestrator.Dependencies`.
2. Configure `AudioCollectionManager.audioStreamSink` to call `transport.sendAudio()`.
3. Scene-phase handling: on background, keep transport alive (background audio mode); on termination, disconnect gracefully.

### P6-07 Update UI for streaming session state

**Files:** `SessionStateStore.swift`, `StandbyView.swift`, `LiveSessionView.swift`, `SessionHUDView.swift`

1. `SessionStateStore` adds `isStreaming: Bool`, `streamDuration: TimeInterval`.
2. `StandbyView` shows "Say 'hey mario' to start" prompt.
3. `LiveSessionView` shows streaming indicator, elapsed time, "Say 'goodbye mario' to stop" hint.
4. `SessionHUDView` shows connected/reconnecting state badge.

### P6-08 Integration test: end-to-end streaming

**File:** `PortWorldTests/RealtimeTransportTests.swift`

1. Mock `RealtimeTransport` that echoes audio back.
2. Test: connect → send 10 audio frames → receive 10 echoed frames → disconnect.
3. Test: connect → transport drops → auto-reconnect → streaming resumes.
4. Test: sleep word fires → graceful disconnect.

---

## Completion Criteria

| Phase   | Done when                                                                                                                                                                                                                                                                             |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Phase 0 | No LAN IPs in source; no stale copy; `ExampleMediaPipelineTester` excluded from app target; silence timeout default = 5000; `UIBackgroundModes` includes `audio`                                                                                                                      |
| Phase 1 | `StreamSessionViewModel` < 150 lines; `RegistrationView` deleted; all 5 `lazy var` services injectable; `SessionStateStore` exists and all views read from it                                                                                                                         |
| Phase 2 | All 55 bugs listed in the inspection report resolved; `xcodebuild test` green; no bare `print()` or `DispatchQueue.sync` outside audio tap                                                                                                                                            |
| Phase 3 | Log persists to disk; keychain credential store; NWReachability wired; query bundle upload streamed; app metadata in health/query payloads                                                                                                                                            |
| Phase 4 | Light + dark mode pass; all screens rebuilt per spec; `RuntimeStatusPanelView` hidden in release; hold-to-activate gesture; no debug UI visible to user                                                                                                                               |
| Phase 5 | All P5-01 → P5-12 tests exist and pass; `Runtime/` + `Audio/` line coverage > 70%                                                                                                                                                                                                     |
| Phase 6 | `RealtimeTransport` protocol defined; `GatewayTransport` adapter connects and streams binary PCM; sleep word closes session; `AudioCollectionManager` streams PCM to transport instead of disk; `SessionOrchestrator` drives streaming state machine; no regression in existing tests |

# PortWorld iOS — Architecture (v1.0)

> **Status:** Target architecture. The current codebase is the hackathon baseline;
> the implementation plan in `IMPLEMENTATION_PLAN.md` describes how to reach this state.

---

## 1. Guiding Principles

1. **Single responsibility per module.** Each Swift file owns one concept. No God-ViewModels.
2. **Dependency injection over lazy singletons.** Every major service is passed in; nothing is created inside a class it cannot be tested without.
3. **One concurrency model.** `@MainActor` for UI state; Swift `actor` for thread-isolated services; `async/await` for all network calls. No bare `DispatchQueue` except inside the audio engine tap (AVFoundation requirement).
4. **Shared resources have a single owner.** `AVAudioSession` is arbitrated by `AudioSessionArbiter`. `AVAudioEngine` is owned by `AudioCollectionManager` and shared read-only with `AssistantPlaybackEngine`.
5. **Observable state is colocated.** All `@Published` / `@Observable` properties that drive UI live in `SessionStateStore`. Views read from the store; they do not reach into service classes.
6. **No secrets in source.** All backend URLs and API credentials are injected via xcconfig variables and read from Info.plist at runtime. Never committed.
7. **Production-safe logging.** All diagnostics use `os_log` or are gated by `#if DEBUG`. No bare `print()` in release builds.

---

## 2. Module Map

```
IOS/PortWorld/
├── App/
│   └── PortWorldApp.swift          @main — SDK init, root WindowGroup
│
├── DesignSystem/
│   ├── Colors.swift                Semantic colour tokens (light + dark)
│   ├── Typography.swift            Type scale (title/body/caption/label)
│   ├── Spacing.swift               Grid constants (4pt base grid)
│   └── Icons.swift                 SF Symbol name enum
│
├── Views/
│   ├── Onboarding/
│   │   ├── OnboardingContainerView.swift   Paged onboarding; permission requests
│   │   ├── OnboardingPage1View.swift       Value proposition
│   │   ├── OnboardingPage2View.swift       Microphone + speech permissions
│   │   └── OnboardingPage3View.swift       Connect glasses CTA
│   ├── Pairing/
│   │   └── DevicePairingView.swift         Animated connection state ring
│   ├── Session/
│   │   ├── SessionContainerView.swift      Root: onboarding ↔ pairing ↔ active session
│   │   ├── StandbyView.swift               Pre-activation — hold-to-activate card
│   │   ├── LiveSessionView.swift           Full-screen camera feed + HUD
│   │   └── SessionHUDView.swift            Status pill; chime ring animation
│   ├── Settings/
│   │   └── SettingsView.swift              Preferences + developer section
│   ├── Common/
│   │   ├── CircleButton.swift
│   │   ├── PrimaryButton.swift
│   │   ├── StatusBadge.swift
│   │   └── WaveformView.swift              Animated audio waveform pill
│   └── Photo/
│       └── PhotoPreviewView.swift          Full-screen preview + explicit share/save
│
├── ViewModels/
│   ├── SessionStateStore.swift     @Observable store for all UI-facing session state
│   ├── WearablesViewModel.swift    DAT SDK registration + device discovery
│   └── OnboardingViewModel.swift   Permission flow state machine
│
├── Coordinators/
│   ├── DeviceSessionCoordinator.swift  DAT StreamSession, photo capture, frame forwarding
│   └── RuntimeCoordinator.swift        Wires DeviceSessionCoordinator → SessionOrchestrator; owns AudioCollectionManager; scene-phase lifecycle
│
├── Runtime/
│   ├── SessionOrchestrator.swift       Central pipeline coordinator (see §4)
│   ├── SessionWebSocketClient.swift    Swift actor; WS connect/ping/reconnect
│   ├── WakeWordEngine.swift            Protocol + ManualWakeWordEngine + SFSpeechWakeWordEngine
│   ├── QueryEndpointDetector.swift     Silence-timeout VAD; actor-isolated timer
│   ├── QueryBundleBuilder.swift        async/await multipart POST /query
│   ├── VisionFrameUploader.swift       async/await 1 FPS POST /vision/frame
│   ├── RollingVideoBuffer.swift        UIImage → H.264 MP4; temp file cleanup
│   ├── AssistantPlaybackEngine.swift   AVAudioPlayerNode on shared engine
│   ├── EventLogger.swift               Circular in-memory + JSONL on-disk log
│   ├── RuntimeConfig.swift             Reads SON_* keys from Info.plist
│   └── Transport/
│       ├── RealtimeTransport.swift     Protocol: provider-agnostic streaming transport
│       ├── TransportTypes.swift        TransportEvent, TransportState, TransportConfig, AudioStreamFormat
│       └── GatewayTransport.swift      Adapter: wraps SessionWebSocketClient for the PortWorld backend
│
├── Audio/
│   ├── AudioSessionArbiter.swift       Single owner of AVAudioSession category
│   ├── AudioCollectionManager.swift    AVAudioEngine, HFP tap, WAV chunks
│   │     └── (inner) AudioChunkProcessor   @unchecked Sendable; 500ms WAV chunk writer — lives inside AudioCollectionManager, not a separate file
│   ├── AudioCollectionTypes.swift      State enums and metadata types
│   └── WavFileWriter.swift             Static RIFF WAV writer
│
├── Utilities/
│   ├── Clocks.swift                    Clocks.nowMs() — single timestamp source
│   ├── KeychainCredentialStore.swift   Secure credential persistence
│   └── NWReachability.swift            NWPathMonitor wrapper; async publisher
│
└── Runtime/
    └── RuntimeTypes.swift              Protocol types, WS payload structs, codec
```

---

## 3. Layering and Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│  Views  (read SessionStateStore; trigger actions on VMs)     │
└────────────────────────┬────────────────────────────────────┘
                         │ @Observable bindings
┌────────────────────────▼────────────────────────────────────┐
│  ViewModels + SessionStateStore  (@MainActor)                │
│  WearablesViewModel  OnboardingViewModel  SessionStateStore  │
└────────────────────────┬────────────────────────────────────┘
                         │ delegate/async calls
┌────────────────────────▼────────────────────────────────────┐
│  Coordinators  (@MainActor)                                  │
│  DeviceSessionCoordinator      RuntimeCoordinator            │
└────────────────────────┬────────────────────────────────────┘
                         │ frames / audio PCM / lifecycle
┌────────────────────────▼────────────────────────────────────┐
│  Runtime Services                                            │
│  SessionOrchestrator  AudioCollectionManager                 │
│  AssistantPlaybackEngine  AudioSessionArbiter                │
└────────────────────────┬────────────────────────────────────┘
                         │ network I/O
┌────────────────────────▼────────────────────────────────────┐
│  Transport                                                   │
│  SessionWebSocketClient   QueryBundleBuilder                 │
│  VisionFrameUploader      NWReachability                     │
└─────────────────────────────────────────────────────────────┘
```

### Full End-to-End Pipeline

```
DAT Camera (24fps UIImage)
  ─► DeviceSessionCoordinator.handleFrame()
       ├─► RollingVideoBuffer.append()        [local H.264 ring buffer]
       └─► VisionFrameUploader.submit()       [rate-limited to 1fps; async/await]
                                              POST /vision/frame

Bluetooth HFP (AVAudioEngine inputTap)
  ─► AudioCollectionManager
       ├─► AudioChunkProcessor                [500ms WAV chunks → disk]
       ├─► RMS speech-activity                [publishes lastSpeechActivityMs]
       │     ─► SessionOrchestrator.recordSpeechActivity()
       │     ─► QueryEndpointDetector.recordSpeechActivity()
       └─► PCM frame                          [→ SFSpeechWakeWordEngine]

Wake Detection
  ManualWakeWordEngine.triggerManualWake()  [button / hold-to-activate]
  SFSpeechWakeWordEngine transcript match   ["hey mario" + variants]
    ─► SessionOrchestrator.handleWakeDetected()
         ├─► AssistantPlaybackEngine.cancelResponse()
         ├─► play 660Hz chime
         ├─► WS send: wakeword.detected
         └─► QueryEndpointDetector.beginQuery()

Query Active
  QueryEndpointDetector silence timer (200ms tick, default 5s timeout)
    ─► SessionOrchestrator.handleQueryEnded()
         ├─► play 880Hz chime
         ├─► WS send: query.ended
         ├─► AudioCollectionManager.flushPendingChunks()
         ├─► AudioCollectionManager.exportWAVClip(window)   ─► WAV file
         ├─► RollingVideoBuffer.exportInterval(wake-5s..end) ─► MP4 file
         ├─► QueryBundleBuilder.uploadBundle(meta, wav, mp4) POST /query
         │     └─► on success: WS send: query.bundle.uploaded
         └─► temp file cleanup (WAV + MP4 deleted after successful upload)

WebSocket Downlink
  assistant.audio_chunk
    ─► AssistantPlaybackEngine.appendChunk()
         ─► AVAudioPlayerNode.scheduleBuffer()
              ─► HFP route → glasses speakers

  assistant.playback.control  ─► cancel / stop / start response
  session.state               ─► SessionStateStore update
  transport.uplink.ack        ─► confirm backend received uplink audio
  health.pong                 ─► acknowledged

Health Emission (every 10s)
  SessionOrchestrator ─► WS send: health.stats
    { ws_latency_ms, audio_buffer_duration_ms, frame_drop_rate,
      reconnect_attempts, realtime_uplink_confirmed,
      realtime_audio_backend_confirmed_frames,
      app_version, device_model, os_version }
```

---

## 4. `SessionOrchestrator` — Detailed Design

`SessionOrchestrator` is the central runtime coordinator. It owns no UI state and no network transport directly — both are injected.

### Dependencies struct (injected at init)

```swift
struct Dependencies {
    var webSocketClient: SessionWebSocketClientProtocol
    var visionFrameUploader: VisionFrameUploaderProtocol
    var rollingVideoBuffer: RollingVideoBufferProtocol
    var queryBundleBuilder: QueryBundleBuilderProtocol
    var eventLogger: EventLoggerProtocol
    var audioBufferDurationProvider: () -> Int        // capture queue depth
    var clock: () -> Int64                            // Clocks.nowMs()

    static var live: Dependencies { /* default production values */ }
}
```

### State machine

```
          ┌────────────┐
          │   idle     │◄──────────────────────────────────────┐
          └─────┬──────┘                                       │
         activate()                                      deactivate()
                │                                             │
          ┌─────▼──────┐                             ┌────────┴───────┐
          │ connecting │                             │   deactivating │
          └─────┬──────┘                             └────────────────┘
         WS connected                                         ▲
                │                                             │
          ┌─────▼──────┐       socket drop              ┌────┴───────┐
          │   active   │──────────────────────────────►  │reconnecting│
          └─────┬──────┘                                └────────────┘
          wake detected                                       │
                │                                      path restored
          ┌─────▼──────┐                                     │
          │  querying  │◄─────────────────────────────────────┘
          └─────┬──────┘
         silence timeout / forceEnd
                │
          ┌─────▼──────────┐
          │ uploading_bundle│
          └─────┬───────────┘
         upload complete / failed
                │
          back to active ──► repeat
```

### Outbound message buffer

During `reconnecting`, outbound messages (wake events, query events, health stats) are held in an in-memory queue (max 20 messages, FIFO). On reconnect, buffered messages are drained in order before resuming normal emission. Messages older than 60s are discarded.

---

## 5. Audio Session Ownership

`AudioSessionArbiter` is the single point of `AVAudioSession` category configuration.

```
AudioSessionArbiter (singleton)
  ├── requestSession(for: .playAndRecordHFP)  ← AudioCollectionManager
  │     configures .playAndRecord + allowBluetoothHFP + allowBluetooth
  └── returns the configured session lease

AssistantPlaybackEngine
  attaches its playerNode to the shared AVAudioEngine (no session reconfiguration)

DeveloperPipelineTester (dev scheme only)
  requestSession(for: .playback)  ← only valid when capture is not leased
```

Rules:

- Only `AudioCollectionManager` holds the `.playAndRecord` lease during an active session.
- `AssistantPlaybackEngine` never reconfigures the category — it relies on the lease already set.
- Playback-only tools (dev pipeline tester) can only acquire a lease when no capture lease is held.

---

## 6. Concurrency Model

| Layer                                            | Model                                                      | Rationale                                                                |
| ------------------------------------------------ | ---------------------------------------------------------- | ------------------------------------------------------------------------ |
| All Views                                        | `@MainActor`                                               | SwiftUI requirement                                                      |
| `SessionStateStore`                              | `@MainActor @Observable`                                   | binds to views                                                           |
| `WearablesViewModel`, `OnboardingViewModel`      | `@MainActor @ObservableObject`                             | DAT SDK callbacks arrive on main                                         |
| `DeviceSessionCoordinator`, `RuntimeCoordinator` | `@MainActor final class`                                   | orchestrate UI-touching state                                            |
| `SessionOrchestrator`                            | `@MainActor final class`                                   | drives state machine; all callbacks arrive here                          |
| `SessionWebSocketClient`                         | `actor`                                                    | protects URLSession task isolation                                       |
| `QueryEndpointDetector`                          | `actor`                                                    | timer + state isolated from main                                         |
| `AudioCollectionManager` / `AudioChunkProcessor` | `@MainActor` + inner `DispatchQueue` for AVAudioEngine tap | AVFoundation tap runs on audio thread; all observable state back on main |
| `AssistantPlaybackEngine`                        | `@MainActor`                                               | player graph managed on main                                             |
| `VisionFrameUploader`                            | `actor`                                                    | upload-in-flight flag; async/await                                       |
| `RollingVideoBuffer`                             | `actor`                                                    | frame ring and AVAssetWriter isolated                                    |
| `QueryBundleBuilder`                             | stateless `struct` + task                                  | no stored state; cancellable via `Task`                                  |
| `EventLogger`                                    | `@MainActor`                                               | observers always on main                                                 |
| `AudioSessionArbiter`                            | `actor`                                                    | single serialised entry point                                            |

**Banned patterns in this codebase:**

- `DispatchQueue.sync` outside audio tap
- Bare `print()` in non-`#if DEBUG` context
- `@unchecked Sendable` except in `AudioChunkProcessor` (documented exception)
- `try?` that silently discards errors on I/O paths

---

## 7. Configuration and Secrets

All runtime configuration is loaded by `RuntimeConfig.load(from:)` from Info.plist.

| Info.plist key               | xcconfig variable    | Description                            |
| ---------------------------- | -------------------- | -------------------------------------- |
| `SON_BACKEND_BASE_URL`       | `BACKEND_BASE_URL`   | Base HTTP URL; ws/wss derived          |
| `SON_WS_PATH`                | `WS_PATH`            | WebSocket path (default `/ws/session`) |
| `SON_VISION_FRAME_PATH`      | `VISION_FRAME_PATH`  | POST path                              |
| `SON_QUERY_PATH`             | `QUERY_PATH`         | POST path                              |
| `SON_API_KEY`                | `API_KEY`            | From Keychain after first launch       |
| `SON_SILENCE_TIMEOUT_MS`     | `SILENCE_TIMEOUT_MS` | Default `5000`                         |
| `SON_VIDEO_PRE_WAKE_SECONDS` | `VIDEO_PRE_WAKE_S`   | Default `5`                            |

**Developer override:** create `Config.local.xcconfig` (gitignored) and set `BACKEND_BASE_URL = http://192.168.x.x:8080`. Never commit a LAN IP.

---

## 8. Persistence and Storage

| Data                                            | Storage                                                               | Lifecycle                                              |
| ----------------------------------------------- | --------------------------------------------------------------------- | ------------------------------------------------------ |
| API credentials                                 | Keychain (`kSecClassGenericPassword`)                                 | Persistent; user can clear in Settings                 |
| User preferences (silence timeout, wake phrase) | `UserDefaults`                                                        | Persistent                                             |
| WAV chunk files                                 | `FileManager.temporaryDirectory/chunks/`                              | Deleted after successful query upload; swept on launch |
| MP4 query clips                                 | `FileManager.temporaryDirectory/clips/`                               | Deleted after successful upload; swept on launch       |
| Event log JSONL                                 | `FileManager.default.applicationSupportDirectory/logs/events-N.jsonl` | Rolling, max 5MB per file, 3 files retained            |
| Developer export                                | User-chosen path via `UIDocumentPickerViewController`                 | User-controlled                                        |

---

## 9. Navigation Structure

```
PortWorldApp
└── WindowGroup
    └── SessionContainerView  (@MainActor, reads WearablesViewModel + SessionStateStore)
         ├── OnboardingContainerView     (isOnboardingComplete == false)
         │    ├── OnboardingPage1View
         │    ├── OnboardingPage2View   ← requests mic + speech permissions
         │    └── OnboardingPage3View   ← registration CTA; onOpenURL handler here
         ├── DevicePairingView          (onboarded, device not connected)
         ├── StandbyView                (device connected, session inactive)
         │    └── .sheet → SettingsView
         └── LiveSessionView            (session active)
              └── .sheet → PhotoPreviewView
```

---

## 10. Design System

All visual tokens are defined in `DesignSystem/`. Views import nothing directly from `UIKit`.

### Colour roles (adaptive — light + dark)

| Token                       | Purpose                    |
| --------------------------- | -------------------------- |
| `DS.Colors.background`      | App background             |
| `DS.Colors.surface`         | Card / sheet surface       |
| `DS.Colors.surfaceElevated` | Elevated surface (modals)  |
| `DS.Colors.primary`         | Interactive / brand accent |
| `DS.Colors.destructive`     | Destructive actions        |
| `DS.Colors.labelPrimary`    | Body text                  |
| `DS.Colors.labelSecondary`  | Secondary/supporting text  |
| `DS.Colors.labelTertiary`   | Hints / timestamp text     |

### Type scale

| Token                | Size | Weight  | Usage             |
| -------------------- | ---- | ------- | ----------------- |
| `DS.Type.largeTitle` | 34   | Regular | Screen titles     |
| `DS.Type.title1`     | 28   | Bold    | Section headers   |
| `DS.Type.body`       | 17   | Regular | Body copy         |
| `DS.Type.callout`    | 16   | Medium  | Chips / badges    |
| `DS.Type.caption`    | 12   | Regular | Timestamps; hints |

All text supports Dynamic Type via `.font(.custom(...).dynamic())`.

### Motion

- All state transitions use `withAnimation(.spring(duration: 0.35))`.
- No third-party animation libraries.
- Onboarding page transitions: `AnyTransition.asymmetric(insertion: .move(edge: .trailing), removal: .move(edge: .leading))`.

---

## 11. Error Handling Strategy

Every error surface has three properties:

1. **User message** — short, friendly, actionable (`"Connection lost. Tap to retry."`)
2. **CTA** — a button (`"Retry"`, `"Reconnect"`, `"Open Settings"`)
3. **Log event** — written to `EventLogger` with full technical detail

Raw system error strings (e.g. `"The operation couldn't be completed (NSURLErrorDomain error -1009)"`) are never shown to users.

Errors fall into two tiers:

- **Blocking (modal `.alert`)** — unrecoverable errors that prevent the session from continuing (SDK init failure, missing permissions). `SessionStateStore.alertError: SessionError?` drives a `.alert` modifier on the root view.
- **Transient (non-intrusive toast / banner)** — recoverable failures (upload retry exhausted, temporary network loss). `SessionStateStore.toastError: SessionError?` drives a dismissable banner that auto-hides after 4 seconds.

Raw system error strings are never shown to users.

---

## 12. App Store Compliance Checklist

- [ ] `NSMicrophoneUsageDescription` — present and meaningful
- [ ] `NSSpeechRecognitionUsageDescription` — present and meaningful
- [ ] `NSCameraUsageDescription` — present and meaningful (DAT SDK)
- [ ] `UIBackgroundModes` — includes `audio`, `bluetooth-peripheral`, and `external-accessory` (all three required: `audio` for AVAudioEngine continuity, `bluetooth-peripheral` and `external-accessory` for DAT SDK Bluetooth communication — see `Wearables DAT SDK.md`)
- [ ] `NSAppTransportSecurity` — `NSAllowsLocalNetworking: true` only in Debug scheme; release scheme uses HTTPS-only
- [ ] Privacy manifest (`PrivacyInfo.xcprivacy`) — declares all accessed API categories
- [ ] No LAN IPs committed to repo
- [ ] No API keys in source or Info.plist (xcconfig injection only)
- [ ] Unused `applinks` entitlement removed
- [ ] Minimum deployment target: iOS 17.0

---

## 13. Glossary

| Term                  | Definition                                                                                                                                                               |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **DAT SDK**           | Meta's Device Access Toolkit — the SDK that pairs and communicates with Ray-Ban Meta smart glasses. See `Wearables DAT SDK.md`.                                          |
| **HFP**               | Hands-Free Profile — a Bluetooth audio profile used to route microphone input and speaker output through the glasses. Uses `AVAudioSession` option `.allowBluetoothHFP`. |
| **VAD**               | Voice Activity Detection — the silence-timeout mechanism that determines when a user has stopped speaking. Implemented by `QueryEndpointDetector`.                       |
| **PCM**               | Pulse-Code Modulation — raw uncompressed audio format. The backend returns `pcm_s16le` (signed 16-bit little-endian) at 16 kHz mono.                                     |
| **RIFF WAV**          | Resource Interchange File Format / Waveform — the `.wav` container format used for captured audio chunks. Written by `WavFileWriter`.                                    |
| **HUD**               | Heads-Up Display — the floating overlay shown during a live session (status pill, chime ring animation).                                                                 |
| **xcconfig**          | Xcode configuration file — used to inject build-time settings (`SON_BACKEND_BASE_URL`, API keys) without committing secrets to source.                                   |
| **JSONL**             | JSON Lines — one JSON object per line. Used by `EventLogger` for on-disk log persistence.                                                                                |
| **soak test**         | A manual long-duration test (≥ 30 minutes) verifying memory stability and absence of resource leaks under continuous use.                                                |
| **barge-in**          | Interrupting the assistant mid-playback by triggering a new wake word. Cancels current playback and starts a fresh query cycle.                                          |
| **query bundle**      | The multipart `POST /query` payload containing `metadata` (JSON), `audio` (WAV), and `video` (MP4). Built by `QueryBundleBuilder`.                                       |
| **circuit breaker**   | A pattern that stops retrying after N consecutive failures, entering a `.failed` state that requires explicit user action to reset. Used by `SFSpeechWakeWordEngine`.    |
| **outbound buffer**   | A queue that holds WebSocket messages generated while the connection is in `.reconnecting` state, replaying them once reconnected.                                       |
| **RMS**               | Root Mean Square — a measure of audio signal amplitude used for speech-activity detection in `AudioCollectionManager`.                                                   |
| **SPM**               | Swift Package Manager — used to manage third-party dependencies (e.g., `swift-snapshot-testing` for snapshot tests).                                                     |
| **Loop A**            | The fast, realtime conversational agent loop: streams user audio → model → streams assistant audio. See `docs/AGENTIC_INTEGRATION.md`.                                   |
| **Loop B**            | The slower, async tool-execution loop triggered by Loop A. Returns structured results into the conversation.                                                             |
| **sleep word**        | On-device trigger phrase (SFSpeech) that ends a streaming session. Analogous to wake word.                                                                               |
| **RealtimeTransport** | Swift protocol abstraction for bidirectional audio/control streaming. Provider adapters (gateway, OpenAI Realtime, Gemini Live) conform to it.                           |

---

## 14. Streaming Architecture (Realtime Foundation)

This section describes the target architecture for real-time audio streaming, which replaces the current batch query pipeline. The current pipeline (wake → record → batch upload → receive audio) is refactored into a persistent streaming session.

### 14.1 Activation Model

```
[idle] ──wake word (on-device SFSpeech)──► [streaming] ──sleep word (on-device SFSpeech)──► [idle]
                                               │
                                          manual close / timeout ──► [idle]
```

- **Wake word** (e.g., "hey mario") opens a persistent streaming session.
- The WebSocket connection carries bidirectional audio for the session's lifetime.
- **Sleep word** (e.g., "goodbye mario") closes the session. Detected on-device by the same SFSpeech engine.
- The server may run its own VAD internally to reduce token consumption, but the connection stays open.
- Manual button tap can also open/close the session.

### 14.2 Audio Streaming Pipeline

```
Bluetooth HFP (AVAudioEngine inputTap)
  ─► AudioCollectionManager
       ├─► RMS speech-activity feedback (UI waveform)
       ├─► PCM frames → SFSpeechWakeWordEngine (local, always-on)
       └─► PCM frames → RealtimeTransport.sendAudio()
              ─► WS binary frame: [1-byte type][8-byte ts_ms][raw PCM s16le 24kHz mono]

RealtimeTransport.onAudioReceived()
  ─► WS binary frame → PCM s16le 16kHz mono
       ─► AssistantPlaybackEngine.appendChunk()
              ─► AVAudioPlayerNode.scheduleBuffer()
                   ─► HFP route → glasses speakers
```

**Key design decisions:**

| Decision            | Choice                                         | Rationale                                                           |
| ------------------- | ---------------------------------------------- | ------------------------------------------------------------------- |
| Audio input format  | 24kHz mono PCM s16le                           | Matches current `AudioCollectionManager` realtime conversion path   |
| Audio output format | 16kHz mono PCM s16le (from server)             | Matches current `AssistantPlaybackEngine` pipeline                  |
| WS framing          | Text for JSON control, binary for PCM audio    | Production contract; text-audio fallback is debug-only compatibility |
| Session boundary    | Client-controlled (wake/sleep word, on-device) | Client owns session lifecycle; model doesn't unilaterally close     |
| VAD ownership       | Server/provider handles turn-taking            | Client streams continuously within session; server decides turns    |
| Video path          | Remains HTTP POST at 1fps (decoupled)          | Simpler, independent of audio stream; merge into WS later if needed |

### 14.3 `RealtimeTransport` Protocol

The streaming layer is built behind a Swift protocol from day one, enabling clean provider swaps.

```swift
/// Provider-agnostic realtime audio + control transport.
protocol RealtimeTransport: Sendable {
    /// Open the streaming session. The transport connects and is ready to send/receive.
    func connect(config: TransportConfig) async throws
    /// Close the streaming session gracefully.
    func disconnect() async

    /// Send raw PCM audio to the remote end.
    func sendAudio(_ buffer: Data, timestampMs: Int64) async throws
    /// Send a JSON control message (e.g., barge-in, context update).
    func sendControl(_ message: TransportControlMessage) async throws

    /// Async stream of events from the remote end.
    var events: AsyncStream<TransportEvent> { get }
}

enum TransportEvent {
    case audioReceived(Data, timestampMs: Int64)       // PCM from server
    case controlReceived(TransportControlMessage)       // JSON control
    case stateChanged(TransportState)                   // connected/reconnecting/disconnected
    case error(TransportError)                          // recoverable / fatal
}

enum TransportState {
    case disconnected, connecting, connected, reconnecting
}

struct TransportConfig {
    let endpoint: URL
    let sessionId: String
    let audioFormat: AudioStreamFormat     // sampleRate, channels, encoding
    let headers: [String: String]          // auth, API keys
}

struct AudioStreamFormat {
    let sampleRate: Int      // 24000 (input) or 16000 (output)
    let channels: Int        // 1 (mono)
    let encoding: String     // "pcm_s16le"
}
```

**Adapters planned:**

| Adapter                   | Status  | Description                                           |
| ------------------------- | ------- | ----------------------------------------------------- |
| `GatewayTransport`        | Phase 6 | Connects to the self-hosted PortWorld backend gateway |
| `OpenAIRealtimeTransport` | Future  | Direct connection to OpenAI Realtime API (Mode A)     |
| `GeminiLiveTransport`     | Future  | Direct connection to Gemini Live API (Mode A)         |

### 14.4 WS Binary Framing

Audio frames use WebSocket binary messages with a minimal header:

```
┌──────────┬──────────────┬─────────────────────────────────┐
│ type (1B)│ ts_ms (8B LE)│ raw PCM payload (variable)      │
└──────────┴──────────────┴─────────────────────────────────┘
```

| Type byte | Direction       | Content                        |
| --------- | --------------- | ------------------------------ |
| `0x01`    | Client → Server | PCM s16le audio input          |
| `0x02`    | Server → Client | PCM s16le audio output         |
| `0x03`    | Either          | Reserved (future: video frame) |

Control messages remain JSON text frames using the existing envelope format:

```json
{"type": "...", "session_id": "...", "seq": 0, "ts_ms": 0, "payload": {...}}
```

`client.audio` JSON text envelopes may be accepted temporarily by the backend only when a debug compatibility flag is enabled. They are not the canonical production uplink path.

### 14.5 State Machine Evolution

The `SessionOrchestrator` state machine evolves to support streaming:

```
          ┌────────────┐
          │    idle    │◄──────────────────────────────────────┐
          └─────┬──────┘                                       │
      wake word detected                               sleep word / manual
                │                                             │
          ┌─────▼──────┐                             ┌────────┴───────┐
          │ connecting │                             │  disconnecting │
          └─────┬──────┘                             └────────────────┘
       transport connected                                    ▲
                │                                             │
          ┌─────▼──────┐       transport drop           ┌────┴───────┐
          │ streaming  │───────────────────────────────► │reconnecting│
          └────────────┘                                └────────────┘
                                                              │
                                                       path restored
                                                              │
                                                       back to streaming
```

**Removed states:** `querying`, `uploading_bundle` — these are batch pipeline concepts.
**Removed components for streaming path:** `QueryEndpointDetector` (server handles VAD), `QueryBundleBuilder` (no batch upload).
**Preserved:** Wake/sleep word detection, `AudioCollectionManager` (engine + tap), `AssistantPlaybackEngine`, `RollingVideoBuffer`, `VisionFrameUploader`, `EventLogger`.

### 14.6 What Gets Removed vs Preserved

| Component                                   | Streaming path           | Notes                                                                   |
| ------------------------------------------- | ------------------------ | ----------------------------------------------------------------------- |
| `AudioCollectionManager` (engine, HFP, tap) | **Preserved**            | Tap output feeds `RealtimeTransport.sendAudio()` instead of disk chunks |
| `AudioChunkProcessor` (WAV chunk → disk)    | **Removed**              | No disk-based chunking in streaming path                                |
| `WavFileWriter`                             | **Removed**              | No WAV files in streaming path                                          |
| `QueryEndpointDetector`                     | **Removed**              | Server-side VAD handles turn-taking                                     |
| `QueryBundleBuilder`                        | **Removed**              | No batch multipart upload                                               |
| `AssistantPlaybackEngine`                   | **Preserved**            | Already streams PCM playback; unchanged                                 |
| `SessionWebSocketClient`                    | **Evolved**              | Gains binary frame support; wrapped by `GatewayTransport` adapter       |
| `RollingVideoBuffer`                        | **Preserved**            | Still captures video ring buffer for context                            |
| `VisionFrameUploader`                       | **Preserved**            | 1fps HTTP POST continues independently                                  |
| `SFSpeechWakeWordEngine`                    | **Preserved + extended** | Adds sleep word detection alongside wake word                           |
| `EventLogger`                               | **Preserved**            | Observability is orthogonal to transport                                |

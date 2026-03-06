# PortWorld iOS Realtime Compatibility Review

Date: 2026-03-05

## Purpose

This review answers a narrow question: does the current iOS app behave like this?

1. Continuously waits for the wake word / command
2. Hears the word and opens an audio stream to the backend
3. Backend connects to OpenAI Realtime
4. Conversation runs as microphone audio -> backend -> OpenAI Realtime -> backend -> speaker
5. Hears the sleep word / command and closes the data stream

Short answer: not exactly. The current app is part realtime system, part batch-era architecture, and that mismatch makes it harder to reason about and easier to break against the mock backend.

## Executive Summary

The backend already exposes the right primitive for Phase 6 realtime: `WS /ws/session`, `session.activate`, binary audio uplink, binary audio downlink, and a bridge to OpenAI Realtime. The iOS app does connect to that path on wake.

The main problems are on the iOS side:

- The app does not continuously wait for the wake word from idle. It first requires explicit activation of the assistant runtime.
- Activation starts local audio capture, camera/session streaming, and continuous vision upload before any wake word is heard.
- The sleep word disconnects only the realtime websocket transport. It does not stop local capture or vision upload.
- Realtime playback is hard-coded to expect `pcm_s16le` at `16 kHz`, while the backend/OpenAI realtime path is configured around `24 kHz` audio. This is the strongest protocol-level bug in the current path.
- The runtime still carries batch-era artifacts like `queryURL`, `query.*` payload types, and zeroed bundle metrics. Those artifacts do not drive the active mock-backend path, but they complicate the code and the mental model.

If the target product flow is the 5-step flow above, the app is overbuilt in the wrong places and under-aligned in the protocol details that matter most.

## Expected Flow vs Actual Flow

### Expected

- Idle app listens for wake word
- Wake word opens backend realtime session
- Audio goes upstream continuously
- Assistant audio comes back continuously
- Sleep word ends the stream and returns to listening

### Actual

- User explicitly activates the runtime first from the app UI.
- Activation acquires the audio session lease, starts local microphone capture, starts the DAT stream session, starts the vision uploader, and starts wake listening.
- Wake word then opens the realtime websocket and sends `session.activate`.
- Realtime audio uplink only starts after the websocket connects, the server reports session readiness, and the debug uplink probe/ack path is satisfied.
- Sleep word disconnects realtime transport, but the runtime remains activated, microphone capture remains active, wake listening remains active, and vision upload can continue.

## Review Against The 5-Step Flow

## 1. Continuously Waits For Wake Word / Command

### Result

Partially true only after manual activation. False for app idle state.

### Evidence

- `SessionViewModel.activateAssistantRuntime()` explicitly activates the runtime; it is not passive or always armed: `IOS/PortWorld/ViewModels/SessionViewModel.swift:38-79`
- `SessionOrchestrator.activate()` starts the stream and only then starts the wake engines: `IOS/PortWorld/Runtime/SessionOrchestrator.swift:558-610`
- `RuntimeCoordinator` wires activation to audio session setup, audio capture, and DAT streaming: `IOS/PortWorld/Coordinators/RuntimeCoordinator.swift:32-52`

### What Is Going Wrong

- The app has an "armed runtime" concept before it has a "conversation streaming" concept.
- That is valid for the current PRD, which requires a deliberate activation gesture, but it does not match the simpler flow in this review.
- From a user perspective, the app is not simply "always listening for wake". It is "wait until activated, then listen for wake".

### Apple Docs Context

This is not inherently wrong. This review did not find first-party Apple documentation for a dedicated third-party always-on hotword API. The current implementation uses the Speech framework's live transcription path, which is a reasonable workaround, but it should be described as best-effort wake detection, not as system-level always-on wake word support.

## 2. Wake Word Opens Audio (+ Later Video) Stream To Backend

### Result

Partially true, but the app starts too much before wake.

### Evidence

- Runtime activation starts audio capture and DAT session immediately: `IOS/PortWorld/Coordinators/RuntimeCoordinator.swift:32-52`
- `AudioCollectionManager.start()` installs the audio tap and begins microphone capture before wake: `IOS/PortWorld/Audio/AudioCollectionManager.swift:115-207`
- Vision frames are uploaded whenever the runtime is activated and video frames arrive: `IOS/PortWorld/Runtime/SessionOrchestrator.swift:742-759`
- Wake detection then triggers realtime websocket connect: `IOS/PortWorld/Runtime/SessionOrchestrator.swift:796-827`

### What Is Going Wrong

- The current implementation treats "session activated" as "start gathering everything locally now".
- That means the app is already:
  - capturing microphone audio
  - processing speech locally
  - streaming glasses camera frames into the app
  - uploading vision frames
- by the time the wake word occurs.

### Why This Matters

If the intended flow is "audio stream opens on wake, video later", the app currently violates that boundary. It leaks complexity and potentially privacy/battery cost into the pre-wake phase.

### Refactor Implication

There should be a cleaner split between:

- local pre-wake arming
- realtime conversation streaming

Right now those phases are mixed.

## 3. Backend Connects To OpenAI Realtime

### Result

Yes. This part is broadly correct.

### Evidence

- The mock backend exposes `WS /ws/session`: `backend/routers/ws.py:33-34`
- It requires `session.activate` before binary frames are accepted for an active session: `backend/routers/ws.py:83-87`
- The websocket route validates client audio as `pcm_s16le`, mono, `24_000 Hz`: `backend/routers/ws.py:27-30`
- The backend creates an `OpenAIRealtimeClient` and bridges the websocket session upstream: `backend/routers/ws.py`
- `OpenAIRealtimeClient` configures OpenAI Realtime with input and output PCM at `24_000 Hz`: `backend/openai_realtime_client.py:13-14` and `backend/openai_realtime_client.py:263-289`

### What Is Going Wrong

The backend is not the main architectural mismatch here. It is already shaped around the desired realtime transport. The iOS app still carries older batch-era structures that make it look like both systems need to support both models simultaneously.

## 4. Realtime Conversation Works End To End

### Result

Partially implemented. Protocol alignment is weak enough that this is the most likely place things break in practice.

### Evidence

- iOS realtime uplink is generated as mono `24_000 Hz` PCM: `IOS/PortWorld/Audio/AudioCollectionManager.swift:443-511`
- The backend expects uplink at `24_000 Hz`: `backend/routers/ws.py:27-30`
- Backend OpenAI client configures output PCM at `24_000 Hz`: `backend/openai_realtime_client.py:13-14` and `backend/openai_realtime_client.py:275-279`
- But iOS playback decodes incoming assistant audio as `16_000 Hz`: `IOS/PortWorld/Runtime/SessionOrchestrator.swift:981-987`
- `AssistantPlaybackEngine` rejects sample rates other than `16_000 Hz`: `IOS/PortWorld/Runtime/AssistantPlaybackEngine.swift:24-25` and `IOS/PortWorld/Runtime/AssistantPlaybackEngine.swift:46-55`

### What Is Going Wrong

#### 4.1 Downlink sample-rate mismatch

This is the clearest likely bug.

- Uplink: iOS and backend agree on `24 kHz`
- Downlink: backend/OpenAI path is configured for `24 kHz`
- Playback: iOS expects `16 kHz`

If the backend forwards raw OpenAI PCM without resampling, playback can fail, sound wrong, or produce confusing route/format errors.

#### 4.2 Conversation start is gated by extra readiness machinery

The iOS runtime does not simply connect and start sending audio. It waits for:

- websocket connection
- `session.activate`
- `session.state`
- debug probe send
- probe acknowledgement
- uplink ack watchdog success

This is defensible for diagnostics, but it is a lot of machinery for a mock backend path. It increases the number of ways a conversation can fail before the user hears anything back.

#### 4.3 Wake-word implementation is transcription-based, not hotword-native

- `SFSpeechWakeWordEngine` uses `SFSpeechAudioBufferRecognitionRequest` and transcript matching: `IOS/PortWorld/Runtime/WakeWordEngine.swift:328-529`
- Sleep detection only fires on a final transcript: `IOS/PortWorld/Runtime/WakeWordEngine.swift:501-510`

This means wake/sleep reliability depends on transcription behavior, latency, and availability. That is a valid engineering tradeoff, but it is not a dedicated keyword-spotting stack.

### Apple Docs Context

- `SFSpeechAudioBufferRecognitionRequest` is a live-audio transcription request that runs until `endAudio()` is called.
- `SFSpeechRecognizer` availability can change at runtime.
- `supportsOnDeviceRecognition` must be true for `requiresOnDeviceRecognition` to hold.

Inference: the current Speech-based wake-word path should be treated as best-effort phrase recognition, not as a robust low-latency always-on hotword system.

## 5. Sleep Word Closes The Data Stream

### Result

Only partially true.

### Evidence

- Sleep detection calls `disconnectRealtimeTransport(reason: "sleep")`: `IOS/PortWorld/Runtime/SessionOrchestrator.swift:830-863`
- `disconnectRealtimeTransport()` resets transport state and returns the runtime to `.active`, not `.idle`: `IOS/PortWorld/Runtime/SessionOrchestrator.swift:937-976`
- Full shutdown only happens in `deactivate()`, which also stops wake engines, vision uploader, rolling buffer, playback engine, and capture stream: `IOS/PortWorld/Runtime/SessionOrchestrator.swift:613-675`

### What Is Going Wrong

The sleep word does not end the whole runtime. It only ends the realtime websocket transport.

After sleep:

- wake listening stays available
- local audio capture remains active
- DAT session remains active
- vision uploader can remain active
- the app is still in an armed runtime, not back to a true idle state

That does not match the simpler expectation that "sleep closes the data stream" in the broad sense.

## Over-Engineering And Stale Artifacts

The app feels over-engineered because the active Phase 6 realtime path is still wrapped in batch-era language and structures.

### Clear stale artifacts

- `RuntimeConfig` still carries `queryURL`, and defaults it to `/v1/query`: `IOS/PortWorld/Runtime/RuntimeConfig.swift:11-15` and `IOS/PortWorld/Runtime/RuntimeConfig.swift:114-128`
- The mock backend does not implement `/query`; it currently exposes `/healthz` and `/vision/frame` on HTTP plus `/ws/session` on websocket: `backend/routers/http.py:15-27`
- `SessionOrchestrator` still emits batch compatibility health fields with hard-coded zero bundle counts: `IOS/PortWorld/Runtime/SessionOrchestrator.swift:1634-1639`
- `RuntimeTypes.swift` still defines `query.started`, `query.ended`, `query.bundle.uploaded`, and `QueryMetadata` even though the active transport path is websocket realtime.

### Why these artifacts matter

They create three problems:

- They make the app harder to debug because old and new models coexist.
- They increase the chance of fixing the wrong path.
- They obscure which contracts must actually match the backend today.

## What Can Likely Be Deleted, Isolated, Or Deferred

This section is about the active mock-backend path, not the long-term product roadmap.

### Delete or isolate from the active realtime path

- `QueryBundleBuilder` and batch upload flow, unless `/query` is still intentionally part of the near-term roadmap
- `queryURL` from runtime config if the active app/backend path is websocket-first only
- `query.*` outbound websocket message types if they are not used by the backend
- batch-only health counters that are always zero in Phase 6

### Defer until video is intentionally reintroduced into realtime

- `RollingVideoBuffer` as part of the active conversation path
- any logic that treats pre-wake video capture as required for audio-only realtime conversation

If video is "later", it should not make the audio-only realtime path harder to reason about today.

### Keep

- `AudioCollectionManager`
- `SessionWebSocketClient`
- `GatewayTransport`
- `AssistantPlaybackEngine`
- `DeviceSessionCoordinator`

These are reasonable building blocks. The problem is mostly how they are wired together and how many legacy responsibilities the runtime still carries.

## Recommended Simplification Path

If the product flow is truly the 5-step flow above, the simplest useful refactor is:

### 1. Make the runtime model explicit

Use only two user-visible phases:

- `armed_listening`
- `active_realtime_conversation`

Optionally keep a third internal phase for transport reconnecting.

### 2. Stop treating activation as "start everything"

Choose one of these and document it clearly:

- If activation remains a product requirement, activation should mean "arm wake detection", not "start camera upload and conversation-adjacent pipelines".
- If always-listening is the goal, remove the extra activation layer and let wake word be the entry point.

### 3. Align the audio contract end to end

Pick one downlink PCM format and make all three layers agree:

- iOS playback
- backend frame bridge
- OpenAI Realtime session config

Right now that contract is not aligned.

### 4. Decide whether sleep means "stop websocket" or "return to true idle"

Today it means "stop websocket only".

If sleep should mean "conversation over, no more capture/upload", then it must call the full deactivation path or an equivalent scoped shutdown path.

### 5. Remove batch-era terms from the active realtime runtime

The current vocabulary mixes:

- session
- query
- bundle
- streaming
- wake
- sleep

for a path that is now mostly one long websocket conversation. The naming should reflect the actual mode.

## Official Apple Documentation Used

These docs support the review, especially around audio session setup and Speech framework limits:

- `SFSpeechAudioBufferRecognitionRequest`
  - https://developer.apple.com/documentation/speech/sfspeechaudiobufferrecognitionrequest/
- `Recognizing speech in live audio`
  - https://developer.apple.com/documentation/speech/recognizing-speech-in-live-audio
- `SFSpeechRecognizer`
  - https://developer.apple.com/documentation/speech/sfspeechrecognizer/
- `supportsOnDeviceRecognition`
  - https://developer.apple.com/documentation/speech/sfspeechrecognizer/supportsondevicerecognition/
- `AVAudioSession.Category.playAndRecord`
  - https://developer.apple.com/documentation/avfaudio/avaudiosession/category-swift.struct/playandrecord/
- `AVAudioSession.CategoryOptions.allowBluetoothHFP`
  - https://developer.apple.com/documentation/avfaudio/avaudiosession/categoryoptions-swift.struct/allowbluetoothhfp/
- `Responding to audio route changes`
  - https://developer.apple.com/documentation/avfaudio/responding-to-audio-route-changes/
- `Handling audio interruptions`
  - https://developer.apple.com/documentation/avfaudio/handling-audio-interruptions/

### Apple Docs Implications

- Using `.playAndRecord` with `.allowBluetoothHFP` is the right baseline for this app's audio session.
- Observing route-change and interruption notifications is also appropriate.
- The Speech framework path in this app is built on live transcription, not on a dedicated hotword API.
- On-device speech recognition is conditional on recognizer support and availability.

## Verification Notes

### Confirmed

- iOS build succeeded on 2026-03-05:
  - `xcodebuild build -project IOS/PortWorld.xcodeproj -scheme PortWorld -destination 'generic/platform=iOS Simulator'`
- The mock backend code path is websocket-first and OpenAI-Realtime-backed.

### Blocked during review

- Focused backend pytest collection was blocked by a local `.venv` architecture mismatch (`arm64` wheel loaded from an `x86_64` Python process).
- Focused `xcodebuild test` invocation failed because tests require a concrete simulator destination, not `generic/platform=iOS Simulator`.

These environment blockers do not change the code-level findings above.

## Final Assessment

The current iOS app is not a clean implementation of the desired 5-step realtime conversation loop.

What works:

- The backend contract is moving in the right direction.
- The iOS app does open a realtime websocket on wake.
- The audio session and capture foundation are reasonable.

What is going wrong:

- The runtime is armed manually, not continuously waiting from idle.
- Local capture and vision upload start too early.
- Sleep closes only transport, not the broader runtime.
- The audio downlink contract is inconsistent across iOS, backend, and OpenAI Realtime.
- Batch-era architecture remains embedded in the active runtime and is now mostly a source of confusion.

If the goal is to make the mock backend work reliably and reduce complexity, the first priority is not more features. It is deleting or isolating the stale batch path and making the realtime audio contract exact.

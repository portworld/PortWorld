# iOS Audio-Only Assistant Implementation Plan

## Status

- Date: 2026-03-06
- Scope: iPhone microphone + iPhone speaker only
- Primary target: make the assistant loop work reliably before DAT/glasses and vision input
- Transport path: iOS -> `backend/` gateway -> OpenAI Realtime
- Progress:
  - Step 1 completed
  - Step 2 completed
  - Step 3 completed

## 1. Goal

This plan defines the intended iOS behavior for the first clean assistant runtime:

1. User taps **Activate Assistant**.
2. App enters the assistant screen and arms wake listening.
3. Saying **"Hey mario"** starts a live realtime conversation.
4. The conversation continues until **"goodbye mario"** or an explicit UI end action.
5. After a conversation ends, the app returns to armed listening without leaving the assistant screen.
6. This wake -> converse -> sleep cycle can repeat indefinitely.
7. Tapping **Deactivate Assistant** shuts the whole assistant down.

This document is intentionally implementation-specific, but it does not include code yet.

## 2. Product Behavior to Achieve

### 2.1 Assistant states

The assistant should expose five clear runtime states:

- `inactive`
  - Assistant is not armed.
  - No wake listening.
  - No websocket.
  - No live conversation audio.
- `armed_listening`
  - Assistant screen is active.
  - Wake/sleep detection is running.
  - No websocket is open.
  - No assistant audio is playing.
- `connecting_conversation`
  - Wake was detected.
  - The app is opening a websocket session through the backend.
  - The app is waiting for backend session readiness before streaming mic audio.
- `active_conversation`
  - Mic audio is streaming to the backend.
  - Assistant audio is streaming back from the backend.
  - Wake detection is ignored while a conversation is already active.
- `deactivating`
  - Full shutdown is in progress.

The app should never hide these states behind mixed terminology such as "active but idle but connected". The state machine must match the actual runtime.

### 2.2 User-visible flow

#### Activate Assistant

Expected behavior:

- User enters the assistant screen.
- App requests any missing speech authorization if needed.
- App starts local wake listening.
- App prepares phone-based audio services needed for future conversation.
- App does not open the websocket yet.
- App does not start realtime uplink yet.
- App does not start backend vision upload.
- App does not start DAT/glasses streaming.

#### Wake Word: "Hey mario"

Expected behavior:

- App detects wake phrase.
- App gives immediate user feedback that wake was accepted.
- App creates or assigns a new live conversation session id.
- App opens the backend websocket.
- App sends `session.activate` with the declared iOS audio format.
- App waits for backend `session.state` readiness.
- Only after readiness does the app begin sending live microphone PCM.
- App begins receiving and playing assistant PCM audio through the iPhone speaker.

#### Conversation Loop

Expected behavior:

- User speech is streamed continuously while the conversation is active.
- Assistant speech is played continuously while returned by the backend.
- The app remains in conversation until a real end condition occurs.
- The app does not auto-end just because the user pauses briefly.

#### Sleep Word: "goodbye mario"

Expected behavior:

- App detects sleep phrase during an active conversation.
- App sends `session.end_turn`.
- App stops local uplink immediately.
- App stops active assistant playback for the current conversation.
- App closes the websocket session for that conversation.
- App returns to `armed_listening`.
- The assistant screen remains open and ready for the next wake.

#### Explicit "End Conversation" UI action

Expected behavior:

- This is a fallback for cases where speech detection is inconvenient or unreliable.
- It must follow the same runtime path as the sleep word.
- It ends only the current conversation, not the assistant as a whole.
- After completion, the app returns to `armed_listening`.

#### Deactivate Assistant

Expected behavior:

- Assistant screen exits or returns to the pre-activation state.
- Wake listening stops.
- Any active conversation is terminated.
- Websocket is closed if open.
- Playback is shut down.
- Temporary runtime state is cleared.
- No reconnect attempts remain scheduled.

## 3. Locked Implementation Decisions

These decisions are fixed for this milestone.

### 3.1 Audio source and sink

- Input source is the iPhone microphone only.
- Output sink is the iPhone speaker only.
- Bluetooth HFP routing is not part of this milestone.
- Meta glasses audio routing is not part of this milestone.

### 3.2 Transport architecture

- iOS talks to the existing backend gateway.
- iOS does not connect directly to OpenAI Realtime in this milestone.
- The backend owns the OpenAI Realtime session and provider-specific protocol.

### 3.3 Conversation lifetime

- A conversation starts on wake.
- A conversation ends on sleep word or explicit UI end action.
- The websocket belongs to the conversation, not to the whole activated assistant lifetime.
- The activated assistant can outlive many conversation websocket sessions.

### 3.4 Audio framing contract

- Uplink uses binary websocket frames.
- Frame type for client audio remains `0x01`.
- Frame type for server audio remains `0x02`.
- Frame header remains `1-byte type + 8-byte little-endian timestamp_ms`.
- PCM contract remains `pcm_s16le`, mono, `24_000 Hz`.

### 3.5 Scope exclusions

These are explicitly out of scope for this document's implementation target:

- DAT / Meta wearable stream session behavior
- Glasses microphone input
- Glasses speaker output
- Vision frame upload
- Rolling video buffering
- `/query` batch upload behavior
- local Loop A / direct provider transport

## 4. Current Implementation Gaps to Close

The current runtime does not yet cleanly match the desired model.

### 4.1 Activation currently does too much

Today activation still pre-starts parts of the runtime that belong to later phases or other modes:

- realtime transport connection starts too early
- vision uploader starts too early
- stream dependencies start too early for the desired audio-only path

Target correction:

- `activate()` should only arm the assistant.

### 4.2 Wake currently does not fully own conversation startup

Wake already marks the start of an active streaming intent, but the final runtime must make wake the single source of truth for:

- creating a conversation session
- opening transport
- activating server session
- starting audio uplink

### 4.3 Sleep currently ends the turn but does not fully define conversation shutdown

The final behavior needs a single, clean shutdown path for:

- sleep word
- explicit End Conversation button
- transport failure terminal path
- deactivate during active conversation

### 4.4 Audio-only milestone is still mixed with future hardware/runtime concerns

The active path must not depend on:

- DAT lifecycle
- Bluetooth route assumptions
- camera upload
- batch query compatibility surfaces

Those can remain in the codebase temporarily, but they must be isolated from the active audio-only flow.

## 5. Ownership Map

This plan is organized by primary implementation ownership.

### 5.1 UI and state ownership

Primary owners:

- `SessionViewModel`
- assistant activation screen / assistant runtime screen
- state store mapping used by the UI

Responsibilities:

- reflect assistant lifecycle accurately
- expose the right controls for each state
- prevent contradictory UI actions
- keep user-facing labels simple and truthful

### 5.2 Runtime orchestration ownership

Primary owner:

- `SessionOrchestrator`

Responsibilities:

- own the assistant runtime state machine
- separate assistant activation from conversation startup
- coordinate wake, sleep, connect, disconnect, and deactivate
- keep inactive/armed/conversation transitions coherent

### 5.3 Audio capture and playback ownership

Primary owners:

- `AudioCollectionManager`
- `AssistantPlaybackEngine`
- audio session coordination layer

Responsibilities:

- capture iPhone mic PCM for realtime uplink
- feed wake/sleep detection
- play assistant PCM through iPhone speaker
- stop assuming the active route is glasses-specific

### 5.4 Transport ownership

Primary owners:

- `GatewayTransport`
- `SessionWebSocketClient`
- runtime transport/control types

Responsibilities:

- open and close one websocket per conversation
- send binary uplink frames
- decode binary downlink frames
- handle reconnect only while a live conversation is active

### 5.5 Backend coordination ownership

Primary owner on iOS side:

- transport/orchestrator contract layer

Responsibilities:

- send the right `session.activate`, `session.end_turn`, and `session.deactivate` messages
- wait for backend readiness correctly
- stop relying on deprecated text fallback behavior
- treat backend ack as diagnostics, not core conversation truth

## 6. Detailed Step-by-Step Plan

## Step 1: Define the active assistant state machine

Status:

- Completed on 2026-03-06

Owner:

- Runtime orchestration
- UI state mapping

Intended outcome:

- The app has one explicit state model for assistant activation and conversation lifecycle.

Behavior to achieve:

- `activate()` transitions `inactive -> armed_listening`
- wake transitions `armed_listening -> connecting_conversation`
- backend-ready transitions `connecting_conversation -> active_conversation`
- sleep or End Conversation transitions `active_conversation -> armed_listening`
- `deactivate()` transitions any active assistant state -> `inactive`

Specific design constraints:

- No state may imply an open websocket unless one is actually open.
- No state may imply active audio uplink unless frames are eligible to be sent.
- The UI must derive from this state model rather than reconstruct it from scattered flags.

Definition of done:

- One documented source of truth exists for runtime states and transitions.
- Existing snapshot/store labels are remapped to match these states.

Implementation notes:

- `AssistantRuntimeState` is now the canonical top-level lifecycle model used by runtime + UI.
- `SessionOrchestrator.StatusSnapshot` publishes assistant lifecycle directly instead of forcing the UI to reconstruct it from transport/playback strings.
- `SessionStateStore` and runtime screen controls now derive activation/deactivation behavior from the assistant lifecycle model.
- The assistant runtime screen no longer depends on DAT/video first-frame routing to stay visible.

## Step 2: Make activation an "arm assistant" action only

Status:

- Completed on 2026-03-06

Owner:

- Runtime orchestration
- UI/state

Intended outcome:

- Activating the assistant prepares the app to listen for wake, but does not begin conversation transport.

Behavior to achieve:

- Enter assistant screen.
- Start wake listening.
- Prepare iPhone-based audio services needed for wake and future playback.
- Keep websocket closed.
- Keep realtime conversation audio inactive.

What must stop happening during activation:

- no `connectRealtimeTransport(reason: "activate")`
- no realtime websocket startup
- no backend session activation
- no vision upload startup on the active path
- no DAT stream startup on the active path

Definition of done:

- Activating the assistant does not emit websocket connect behavior until wake is detected.

Implementation notes:

- `activate()` no longer opens websocket transport or sends backend activation traffic.
- The app now exposes a phone-only entry path so the assistant runtime can be reached without Meta registration or active glasses.
- Activation no longer requires camera permission or active DAT device availability on the active audio-only path.
- Activation now prepares phone-side wake/audio services only; DAT stream startup, vision uploader startup, and first-frame wait behavior were removed from the activation path.

## Step 3: Make wake own conversation startup

Status:

- Completed on 2026-03-06

Owner:

- Runtime orchestration
- Transport
- Audio capture

Intended outcome:

- Wake becomes the single entry point into a live conversation.

Behavior to achieve:

- Detect wake phrase.
- Ignore duplicate wake detections while already connecting or conversing.
- Create a new conversation session id.
- Create/connect transport for that conversation.
- Start transport event loop for that conversation only.
- Send `session.activate` after transport connection.
- Wait for backend session readiness before flushing microphone audio.

Audio behavior:

- Mic frames may be locally buffered in a small preroll window if needed.
- Buffered frames must only flush after backend session readiness.
- Audio frames must not be sent before readiness.

Definition of done:

- Wake triggers the full conversation startup path from idle armed state.
- Wake while already active does nothing.

Implementation notes:

- Wake now routes through a single conversation startup path in `SessionOrchestrator`.
- Each wake creates a fresh conversation session id plus fresh transport/event-loop/uplink-worker ownership.
- `session.activate` remains conversation-scoped and is sent only after the wake-started connection is established.
- Realtime mic audio continues to buffer locally until backend `session.state` readiness is observed, then preroll frames flush for that conversation.
- Conversation teardown now releases conversation-scoped transport resources so the next wake starts cleanly.

## Step 4: Simplify the active uplink path to binary only

Status:

- Completed on 2026-03-06

Owner:

- Transport
- Runtime config

Intended outcome:

- The app uses exactly one supported audio uplink contract for the active path.

Behavior to achieve:

- `GatewayTransport.sendAudio` uses binary frame encoding only.
- Debug builds no longer force text/base64 audio fallback for the active runtime.
- Any remaining text fallback support is restricted to explicit diagnostics/probe tooling, not production runtime behavior.

Definition of done:

- The active assistant flow sends binary audio in both Debug and Release builds.
- Runtime UI/telemetry no longer implies text fallback is part of the normal path.

Implementation notes:

- `GatewayTransport.sendAudio` now uses binary websocket frame encoding only on the active runtime path.
- The old active-runtime `client.audio` text/base64 fallback branch was removed from iOS transport sending.
- `RuntimeConfig.realtimeForceTextAudioFallback` is now treated as a legacy compatibility knob and is no longer force-enabled in debug for the active assistant runtime.
- Active health/telemetry payloads no longer advertise text fallback as a normal runtime mode.
- Backend-side deprecated `client.audio` handling remains available for explicit diagnostics/probe tooling, but the iOS assistant runtime no longer depends on it.

## Step 5: Make the iPhone microphone the only active capture source

Owner:

- Audio capture
- Runtime orchestration

Intended outcome:

- The app conversation path works entirely from the phone microphone.

Behavior to achieve:

- Audio capture starts in a phone-compatible configuration.
- Captured PCM is converted to `pcm_s16le`, mono, `24_000 Hz`.
- Wake/sleep detection continues to receive the audio it needs.
- The active conversation path does not require glasses hardware to function.

Design constraint:

- The code may retain shared abstractions that later support DAT, but the active path must not depend on DAT availability.

Definition of done:

- A developer can activate, wake, converse, sleep, and deactivate using only the phone.

## Step 6: Make the iPhone speaker the only active playback sink

Owner:

- Playback
- Audio session coordination

Intended outcome:

- Assistant responses are audible on the iPhone speaker without route-specific glass assumptions.

Behavior to achieve:

- Playback accepts backend binary `0x02` PCM frames.
- Playback expects `pcm_s16le`, mono, `24_000 Hz`.
- Audio session category and routing are valid for phone speaker playback while also supporting microphone capture.
- Conversation shutdown clears pending playback reliably.

Definition of done:

- Assistant speech plays through the phone speaker consistently during the active conversation.

## Step 7: Make sleep and UI end action share one conversation shutdown path

Owner:

- Runtime orchestration
- UI/state
- Transport

Intended outcome:

- All normal conversation-ending actions behave identically.

Behavior to achieve:

- Sleep detection and End Conversation button both invoke the same internal shutdown routine.
- That routine:
  - stops local uplink
  - sends `session.end_turn`
  - cancels/ends playback for the conversation
  - disconnects websocket transport
  - clears per-conversation state
  - returns assistant to `armed_listening`

Important distinction:

- This does not deactivate the assistant.
- Wake listening remains active after conversation shutdown.

Definition of done:

- Sleep and UI end action produce the same final armed state and same cleanup behavior.

## Step 8: Scope reconnect behavior to active conversation only

Owner:

- Transport
- Runtime orchestration

Intended outcome:

- Reconnect behavior is easier to reason about and tied to actual user value.

Behavior to achieve:

- If websocket drops during `active_conversation`, attempt reconnect for that live conversation.
- If websocket drops while only `armed_listening`, do nothing because no websocket should exist.
- If reconnect ultimately fails during a conversation, surface failure clearly and return to `armed_listening`.

Definition of done:

- Reconnect logic is no longer modeled as a permanent activated-assistant websocket concern.

## Step 9: Isolate non-audio milestone surfaces from the active path

Owner:

- Runtime orchestration
- Runtime config
- UI/state

Intended outcome:

- The audio-only milestone is not polluted by later hardware or legacy transport concerns.

Behavior to achieve:

- `VisionFrameUploader` is not started on the active path.
- `RollingVideoBuffer` is not required on the active path.
- `/query` upload logic is not part of the active assistant state machine.
- DAT-specific startup is not part of activate/wake/sleep/deactivate for this milestone.

Allowed temporary compromise:

- Legacy and future-facing code may remain in the repo if it is isolated and does not affect the active path.

Definition of done:

- The runtime path for phone-only audio can be described without mentioning DAT, vision, or `/query`.

## Step 10: Align assistant UI with the new behavior

Owner:

- UI/state

Intended outcome:

- The screen accurately communicates the assistant lifecycle.

Behavior to achieve:

- Pre-activation UI offers **Activate Assistant**.
- Armed screen shows listening/ready status.
- Active conversation screen state clearly shows conversation is live.
- End Conversation control is only available during a live conversation.
- Deactivate Assistant remains available at the assistant level.

UI truthfulness rules:

- Do not show "connected" before wake starts a conversation.
- Do not show "streaming" while only armed.
- Do not show conversation-specific metrics when no conversation is active.

Definition of done:

- A user can infer the system state from the screen without internal knowledge.

## Step 11: Reduce active-path compatibility clutter

Owner:

- Runtime config
- Runtime types
- Runtime orchestration

Intended outcome:

- The active iOS runtime becomes easier to maintain and reason about.

Behavior to achieve:

- Mark `/query` config as inactive for this path.
- Stop routing active behavior through batch-era concepts such as `query.started`, `query.ended`, and upload-centric state labels.
- Keep old symbols only when needed to avoid broad churn, but make them clearly inactive for this assistant flow.

Definition of done:

- Active audio-only docs and runtime state can be read without relying on legacy batch vocabulary.

## 7. Backend Contract Expectations from iOS

The iOS implementation assumes the backend will provide the following behavior.

### Required backend behavior

- `WS /ws/session` remains the active websocket endpoint.
- Backend accepts `session.activate`.
- Backend accepts binary `0x01` audio uplink frames using the existing framing contract.
- Backend emits `session.state` with a ready/active state.
- Backend emits binary `0x02` audio downlink frames.
- Backend accepts `session.end_turn`.

### Important iOS interpretation rules

- `session.state` readiness is required before flushing realtime uplink.
- `transport.uplink.ack` is treated as observability and diagnostics.
- Conversation lifetime on iOS is owned by wake/sleep/UI-end behavior, not by backend debug fallback behavior.

### Coordination note

If backend-side auto-finalize logic ends turns before sleep/UI-end, that is a backend-side blocker against the intended user experience and must be resolved in parallel.

## 8. Validation Plan

This milestone should be validated primarily through build + manual runtime behavior.

### Build expectation

- `xcodebuild build` must succeed with no new warnings introduced by the runtime restructuring.

### Automated test expectations

Add or update tests for the following behaviors:

- activation does not connect transport
- wake opens transport
- wake sends `session.activate`
- no realtime audio is sent before backend readiness
- binary uplink is used on the active path
- sleep ends conversation and returns to armed listening
- End Conversation button matches sleep behavior
- deactivate from armed state performs full shutdown
- deactivate from active conversation performs full shutdown
- reconnect occurs only while a conversation is active

### Manual behavior checklist

- Activate Assistant:
  - enters assistant screen
  - starts listening
  - does not open websocket yet
- Say "Hey mario":
  - websocket opens
  - assistant becomes conversational
  - phone mic input is accepted
  - phone speaker playback works
- Say "goodbye mario":
  - conversation ends
  - websocket closes
  - app remains armed and listening
- Tap End Conversation during an active conversation:
  - same result as sleep word
- Repeat multiple wake/sleep cycles without deactivation
- Tap Deactivate Assistant:
  - full shutdown
  - no leftover active conversation state

## 9. Deliverable Sequence

Recommended execution order:

1. Define and document the state machine.
2. Refactor activation to armed-only behavior.
3. Move websocket startup fully under wake handling.
4. Remove forced text fallback from the active path.
5. Lock audio capture/playback to iPhone-only behavior.
6. Unify sleep and End Conversation shutdown path.
7. Scope reconnect to active conversation only.
8. Isolate DAT/vision/batch surfaces from the active path.
9. Update UI labels and controls to reflect the new behavior.
10. Harden tests and run manual device validation.

## 10. Intended End State

At the end of this implementation, the app should be explainable in one short paragraph:

The user activates the assistant once, which arms wake listening on the phone. Nothing is streamed yet. Saying "Hey mario" starts a live realtime conversation through the backend, using the phone microphone and speaker. Saying "goodbye mario" or tapping End Conversation ends that conversation and returns the app to wake listening. The user can repeat that loop as many times as needed until tapping Deactivate Assistant.

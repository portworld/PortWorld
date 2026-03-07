# iOS Assistant Runtime Simplification Plan

## Status

- Date: 2026-03-07
- Purpose: replace the current active iOS assistant runtime with a smaller, phone-only implementation that is easy to reason about and debug
- Scope: iPhone microphone + iPhone speaker + backend websocket only
- Non-goal: do not fix the current runtime by adding more layers, guards, or compatibility branches
- Current progress:
  - Phase 1 completed
  - Phase 2 completed
  - Phase 3 completed
  - Phase 4 completed

## 1. Why This Reset Is Necessary

The current iOS runtime has accumulated too much code relative to the behavior we actually need right now.

Symptoms:

- the backend was stable, but the iOS active path did not deliver binary audio reliably before the phone-only reset
- runtime success telemetry can disagree with socket-level and backend-level truth
- phone-only assistant behavior is mixed with DAT, camera, video, and historical transport abstractions
- debugging requires chasing state across too many layers

Conclusion:

- the active path should be rebuilt as a minimal vertical slice
- legacy and future-facing code may remain in the repo, but it must be disconnected from the active assistant path

## 2. Target Runtime To Build

The simplified runtime should contain only five active pieces:

1. `AssistantRuntimeController`
   - owns the assistant state machine
   - owns activate, wake, connect, ready, end conversation, and deactivate

2. `PhoneAudioIO`
   - configures the iPhone audio session
   - captures microphone PCM
   - plays assistant PCM to the phone speaker

3. `BackendSessionClient`
   - owns one websocket connection for one conversation
   - sends control envelopes
   - sends binary audio frames
   - receives control envelopes and assistant binary audio

4. `WakePhraseDetector`
   - handles wake phrase detection in armed mode
   - handles sleep phrase detection during active conversation

5. minimal SwiftUI state/store
   - renders the assistant lifecycle truthfully
   - exposes only the controls needed now

Everything else is outside the active path.

## 3. Locked Simplification Decisions

### 3.1 Keep

- the backend contract in `backend/README.md`
- the five assistant runtime states already defined in `docs/IOS_AUDIO_ONLY_ASSISTANT_PLAN.md`
- conversation-scoped websocket ownership
- binary websocket audio framing
- phone microphone input
- phone speaker output

### 3.2 Remove From Active Path

- DAT / Meta device gating
- vision uploader startup
- rolling video buffer startup
- camera permission coupling
- transport compatibility branches for old modes
- UI routing that depends on first-frame, device-stream, or DAT state
- any active-path assumption that playback should route to glasses or Bluetooth
- batch query compatibility behavior

### 3.3 Allow To Remain In Repo

- DAT code
- camera code
- historical runtime code
- archived docs

But these must not participate in the new active assistant path.

## 4. New Runtime Architecture

### 4.1 `AssistantRuntimeController`

This replaces the current orchestration center for the active path.

Responsibilities:

- expose a single assistant state enum:
  - `inactive`
  - `armedListening`
  - `connectingConversation`
  - `activeConversation`
  - `deactivating`
- start phone audio + wake detection on activate
- create a fresh `BackendSessionClient` on wake
- send `session.activate`
- wait for backend `session.state == active`
- start binary mic uplink only after backend readiness
- stop conversation on sleep phrase or explicit end action
- return to `armedListening` after conversation end
- fully stop audio + wake + websocket on deactivate

It should not own:

- DAT lifecycle
- video upload
- photo upload
- device onboarding logic

### 4.2 `PhoneAudioIO`

Responsibilities:

- own one simple audio session profile for phone mic + phone speaker
- expose two outputs:
  - wake/sleep PCM stream
  - conversation uplink PCM stream
- expose one playback input for assistant PCM
- avoid route-specific policy for glasses

Rules:

- no HFP-first assumptions
- no DAT microphone support
- no separate runtime path for legacy capture modes

### 4.3 `BackendSessionClient`

Responsibilities:

- open one websocket per conversation
- send:
  - `session.activate`
  - `wakeword.detected`
  - `session.end_turn`
  - `session.deactivate`
- send binary client audio frames only
- receive:
  - `session.state`
  - `transport.uplink.ack`
  - playback control envelopes
  - binary assistant audio frames

This client should be intentionally small:

- no fallback text audio mode
- no alternate transport abstraction
- no old provider compatibility branch
- no conversation reuse across wake cycles

### 4.4 `WakePhraseDetector`

Responsibilities:

- detect wake only while `armedListening`
- detect sleep only while `activeConversation`
- expose events to `AssistantRuntimeController`

Rules:

- duplicate wake while connecting/active is ignored
- sleep and explicit end conversation must use the same shutdown path

### 4.5 UI / Store

The active runtime UI should only care about:

- assistant state
- last error text
- whether backend is connecting, ready, or disconnected
- simple transport diagnostics

The runtime screen should always be available for:

- `armedListening`
- `connectingConversation`
- `activeConversation`
- `deactivating`

The UI should not depend on:

- DAT registration
- active glasses session
- video frame availability
- camera readiness

## 5. Implementation Plan

### Phase 1: Carve Out The New Path

- add new phone-only runtime types alongside the existing runtime
- introduce `AssistantRuntimeController`, `PhoneAudioIO`, and `BackendSessionClient`
- wire a single phone-only entry path in the app to this new runtime
- keep the current implementation present but no longer used by the active path

Definition of done:

- the app can enter the assistant screen through the new runtime without touching DAT or camera code

Status: completed

Implementation notes:

- Added the new phone-only runtime types:
  - `AssistantRuntimeController`
  - `PhoneAudioIO`
  - `BackendSessionClient`
  - `WakePhraseDetector`
  - `PhoneAssistantRuntimeStore`
  - `PhoneAssistantRuntimeViewModel`
  - `PhoneAssistantRuntimeView`
- Added a dedicated phone-only runtime route from onboarding.
- The new phone-only runtime was introduced alongside the legacy runtime instead of replacing it in-place.
- Legacy DAT/glasses runtime files remained in the repo for later cleanup.

### Phase 2: Restore The Core Loop

- implement activate -> armed listening
- implement wake -> connect -> backend ready -> active conversation
- implement assistant PCM playback
- implement sleep/end conversation -> armed listening
- implement deactivate -> inactive

Definition of done:

- the phone-only assistant loop works end-to-end against the existing backend

Status: completed

Implementation notes:

- `AssistantRuntimeController` now owns the phone-only lifecycle:
  - `inactive`
  - `armedListening`
  - `connectingConversation`
  - `activeConversation`
  - `deactivating`
- `PhoneAudioIO` prepares the iPhone mic + speaker path for the assistant.
- `BackendSessionClient` now owns one websocket per conversation and sends binary client audio frames only.
- Wake starts a fresh conversation session, sends `session.activate` and `wakeword.detected`, and then moves into live uplink.
- End conversation returns the runtime to armed listening.
- Deactivate returns the runtime to inactive.
- Binary websocket client-audio uplink was validated against the backend and now reaches `/ws/session` correctly.
- Runtime observability for backend control events and uplink acknowledgements was added to the new phone-only path.
- Known remaining gap after Phase 2:
  - first-launch wake reliability is still unstable and remains follow-up work inside the new runtime
  - this does not change Phase 2 completion because the phone-only end-to-end loop now exists and is the active implementation path

### Phase 3: Delete Active-Path Legacy Coupling

- remove old active-path calls into DAT, camera, vision, rolling video, and legacy query behavior
- delete now-unused runtime mapping code and compatibility surfaces from the active path
- simplify UI/store bindings to the new state model only

Definition of done:

- the new assistant path no longer depends on legacy runtime subsystems

Status: completed

Implementation notes:

- `MainAppView` now routes the primary assistant flow to `PhoneAssistantRuntimeView`.
- The phone-only runtime is the only normal assistant path from app navigation.
- The old `StreamSessionView` / `SessionViewModel` / `RuntimeCoordinator` / `SessionOrchestrator` stack remains in the repo but is no longer the active path.
- `WearablesViewModel` now exposes explicit phone-only mode entry instead of using legacy runtime entry semantics.
- Home/onboarding copy was updated so DAT and Meta onboarding are optional future hardware context rather than active assistant prerequisites.
- Legacy assistant files were labeled as historical/legacy entry points to reduce accidental reuse during new runtime work.

### Phase 4: Clean Up And Shrink

- remove dead code made unreachable by the simplification
- move any still-needed old code behind clearly inactive legacy boundaries
- reduce logging and telemetry to what is actually useful for the new path

Definition of done:

- the active runtime is small enough to understand in one read-through

Status: completed

Implementation notes:

- The pre-simplification assistant runtime stack now lives under `IOS/Legacy/AssistantRuntime/` instead of being mixed into the active `IOS/PortWorld/` tree.
- The moved legacy area includes the old assistant views, store/view model, coordinator, orchestrator, and the legacy websocket/video transport helpers.
- `QueryBundleBuilder.swift` and `QueryEndpointDetector.swift` were removed because they no longer had active callers after the simplification.
- Shared runtime contracts in `IOS/PortWorld/Runtime/RuntimeProtocols.swift` were trimmed so the active phone-only path no longer carries legacy uploader, rolling-video, or query-bundle interfaces by default.
- `IOS/README.md` was updated so the documented active architecture now matches the current phone-only runtime instead of the old `SessionViewModel` / `RuntimeCoordinator` path.
- The fresh-launch wake phrase bug was intentionally left out of Phase 4 and remains follow-up work inside the phone-only runtime.

## 6. Deletion Targets

The following should be treated as default deletion or disconnection targets for the active runtime:

- activation-time DAT startup
- activation-time vision uploader setup
- activation-time rolling video buffer setup
- transport fallback branches for text/base64 client audio
- UI gating on active device / stream / first frame
- phone-path playback errors that assume glasses output is required
- health metrics kept only to explain removed transport modes

If a piece of code exists only to support the current non-working active path, prefer deletion over adaptation.

## 7. Verification

### Required runtime checks

1. Activate assistant.
   Expected: state becomes `armedListening`; no websocket open yet.

2. Trigger wake.
   Expected: one fresh websocket connection opens; `session.activate` is sent; state becomes `connectingConversation`.

3. Receive backend ready.
   Expected: state becomes `activeConversation`; mic uplink starts only after readiness.

4. Speak briefly.
   Expected: backend receives binary client audio and emits `transport.uplink.ack`.

5. Receive assistant audio.
   Expected: assistant PCM plays through the iPhone speaker.

6. Say sleep phrase or tap End Conversation.
   Expected: conversation ends and state returns to `armedListening`.

7. Tap Deactivate Assistant.
   Expected: state becomes `inactive` and all runtime resources stop cleanly.

### Required engineering checks

- build succeeds with zero errors
- no new warnings beyond pre-existing known warnings
- backend validation passes with:
  - `OPENAI_REALTIME_ALLOW_TEXT_AUDIO_FALLBACK=false`
  - `OPENAI_DEBUG_MOCK_CAPTURE_MODE=true`
- backend logs show binary client audio reception
- iOS diagnostics no longer disagree with backend truth

## 8. Phone-Only Speaker Playback Resolution

Final outcome:

- the phone-only runtime now plays assistant response audio through the iPhone speaker end-to-end
- the remaining response-to-speaker bug was iOS-side only
- backend transport, OpenAI Realtime output, and backend forwarding were already healthy

Resolved root cause:

1. `BackendSessionClient` -> `AssistantRuntimeController` delivery
   - the client was receiving `assistant.playback.control` and binary `serverAudio`
   - the controller was not reliably consuming those events through the `AsyncStream` bridge
   - replacing the `AsyncStream` handoff with a direct callback from `BackendSessionClient` to `AssistantRuntimeController` fixed the missing playback-event delivery

2. conversation lifecycle noise during startup / restore
   - pre-connect cleanup was able to emit a synthetic `.closed` event before a live websocket existed
   - foreground restore hooks could also start playback recovery while the assistant was still inactive
   - both behaviors were gated so the phone-only runtime only restores or emits close lifecycle events when there is a real active conversation path

3. playback graph and speaker-route hardening
   - `PhoneAudioIO` now applies a speaker fallback only when output has dropped to the built-in receiver
   - wired and Bluetooth routes are left alone
   - `AssistantPlaybackEngine` now observes `AVAudioEngineConfigurationChangeNotification`, restores the graph when needed, and uses `.dataPlayedBack` callbacks for real playback-drain diagnostics

What was validated:

- iPhone microphone capture works
- iPhone -> backend websocket binary uplink works
- backend -> OpenAI Realtime audio output works
- backend forwards `assistant.playback.control` and binary server-audio frames correctly
- `AssistantRuntimeController` now consumes playback-control and server-audio events directly
- `PhoneAudioIO.appendAssistantPCMData(...)` is called on response audio delivery
- the playback engine schedules and drains assistant buffers
- assistant speech is audible on the iPhone speaker in the phone-only runtime

Implementation notes:

- `BackendSessionClient` now emits controller events through a direct callback instead of an `AsyncStream`
- `AssistantRuntimeController` keeps controller-side diagnostics for consumed backend events and playback handoff
- `PhoneAudioIO` enforces `.speaker` only as a fallback from the built-in receiver
- `AssistantPlaybackEngine` recovers from engine configuration changes and logs actual played-back completion

Remaining follow-up:

- keep the extra playback diagnostics only as long as they continue to help phone-only runtime validation
- treat future playback regressions as controller-handoff, graph-recovery, or route-selection issues first before revisiting backend transport

## 9. Relationship To Existing Docs

- `docs/IOS_AUDIO_ONLY_ASSISTANT_PLAN.md` remains the product behavior contract
- this document is the implementation reset plan for getting there
- if the current code conflicts with this simplification plan, prefer simplification over preserving old abstractions

## 10. Explicit Non-Goals For This Reset

- Meta glasses support
- DAT microphone or speaker support
- camera upload
- video buffering
- batch query migration
- direct provider connection from iOS
- broad architectural reuse of the current runtime just because it already exists

The reset should optimize for a working phone-only assistant, not for preserving historical code.

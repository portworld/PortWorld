# iOS Wake Phrase Debug Notes

## Status

- Date: 2026-03-07
- Scope: wake phrase reliability in the new phone-only assistant runtime
- Wake phrase: `hey mario`
- Current behavior:
  - wake often fails on a fresh app launch during the first `Activate Assistant`
  - wake usually works on subsequent activate/deactivate cycles in the same launch
  - once wake is detected, the backend conversation loop works and binary audio uplink works

## Context

This document records the wake-phrase debugging work done after the new phone-only runtime became the active assistant path.

The relevant active runtime files are:

- `IOS/PortWorld/Runtime/AssistantRuntimeController.swift`
- `IOS/PortWorld/Runtime/WakePhraseDetector.swift`
- `IOS/PortWorld/Runtime/WakeWordEngine.swift`
- `IOS/PortWorld/Runtime/PhoneAudioIO.swift`
- `IOS/PortWorld/Runtime/BackendSessionClient.swift`

The issue described here is specifically about the **wake detector path**, not the backend transport path.

## What Was Confirmed Working

The following are no longer the blocker:

- phone-only runtime routing
- microphone capture
- backend websocket connection
- `session.activate`
- `wakeword.detected`
- binary websocket client-audio uplink
- backend `transport.uplink.ack`
- active conversation startup after wake

This means the main remaining wake issue is:

- first-launch armed listening is unreliable before the first successful wake

## Observed Log Pattern

The repeated failure pattern was:

1. Fresh app launch.
2. Tap `Activate Assistant`.
3. Phone audio session comes up correctly.
4. Realtime PCM frames are emitted continuously.
5. Speech framework reports transient `No speech detected`.
6. `hey mario` does not wake the assistant on that first armed cycle.
7. After deactivate/reactivate, wake detection typically works.

Important implication:

- the assistant is receiving mic audio
- the backend is not involved yet
- the problem is in the local wake/speech lifecycle

## Apple Docs Consulted

The debugging work referenced Apple Speech framework docs for:

- `SFSpeechRecognizer`
- `SFSpeechAudioBufferRecognitionRequest`
- `SFSpeechRecognizerDelegate`
- `SFSpeechRecognitionTaskDelegate`
- `shouldReportPartialResults`
- `Recognizing speech in live audio`

Main takeaway:

- Apple’s live-audio pattern is simple: create a live request/task, append microphone buffers continuously, handle partial results, and react to recognizer availability changes.
- The docs did not reveal a special first-launch workaround or hidden configuration flag.

## Changes Attempted

### 1. Rebuild the speech recognizer on every `startListening()`

Change:

- `SFSpeechWakeWordEngine.startListening()` was changed to rebuild the recognizer instead of reusing an existing one.

Reason:

- a full deactivate/reactivate cycle worked, and that cycle recreated more state than a normal restart path did

Result:

- did not fix the first-launch wake miss on its own

### 2. Throttle repeated transient `No speech detected` logs

Change:

- transient Speech errors were throttled to reduce console spam

Reason:

- make wake debugging readable

Result:

- useful for debugging
- no functional wake reliability improvement

### 3. Delayed wake recognizer refresh after arming

Change:

- after entering `armedListening`, the controller scheduled a delayed recognizer restart

Reason:

- hypothesis: the speech recognizer was starting too early while the audio route/session was still settling

Result:

- not reliable
- later evidence suggested this restart strategy was actively harmful because it interrupted the live recognition task mid-arm

### 4. Replace delayed restart with delayed single start

Change:

- instead of starting immediately and restarting later, the controller waited through a warm-up delay and only then started wake listening once

Reason:

- closer to Apple’s live-audio guidance than immediate start + delayed reset

Result:

- improved the startup model
- did not solve the first-launch failure by itself

### 5. One-time recognizer recovery on first transient error

Change:

- on the first transient `No speech detected` error during the initial listening window, the wake engine attempted a one-time recognizer rebuild + task restart

Reason:

- manual deactivate/reactivate seemed to succeed because it recreated recognizer state more aggressively than a simple task restart

Result:

- did not solve the first-launch miss

### 6. One-time cold-start wake primer

Change:

- on the first arm after app launch, the controller ran a hidden primer cycle:
  - warm-up delay
  - temporary wake-listening start
  - brief settle period
  - stop
  - final real wake-listening start

Reason:

- mimic the user’s manual “second cycle works” workaround internally

Result:

- did not reliably solve the problem

### 7. Warm-up task generation guards

Change:

- the controller added a generation counter so stale warm-up tasks from previous armed cycles could not continue running later

Reason:

- logs showed repeated warm-up start messages that suggested old scheduled tasks were still firing

Result:

- the guard was correct to add
- but it was not sufficient to resolve the first-launch wake issue

### 8. Hoist phone runtime ownership to app navigation

Change:

- the phone runtime view model was moved out of `PhoneAssistantRuntimeView` and made app-owned in `MainAppView`

Reason:

- repeated warm-up logs suggested there might be multiple hidden runtime/controller instances created by view lifetime churn

Result:

- this was the correct structural cleanup
- but it still did not eliminate the first-launch wake failure

## Current Conclusions

What we know:

- the issue is not backend transport
- the issue is not mic capture being absent
- the issue is not simply “recognizer object reused incorrectly”
- the issue is not solved by delayed restart, delayed start, first-error rebuild, or one-time primer

What remains likely:

- a first-launch Speech framework readiness issue still exists somewhere between:
  - initial app audio session activation
  - first `SFSpeechRecognizer` task creation
  - first usable partial-result delivery
- there may still be hidden state in Speech framework startup that is not exposed by current logs

## Practical Decision

The wake issue is unresolved, but it should not block Phase 4 cleanup work.

Reason:

- the new phone-only runtime is still the correct active architecture
- the wake issue is now isolated to a much smaller code path
- Phase 4 can proceed while this remains a focused follow-up bug in the new runtime

## Recommended Follow-Up When Returning To This

When revisiting wake reliability later, start from these questions:

1. Should `SFSpeechRecognizer` remain the wake implementation at all for this product path?
2. Should first-launch armed state be split into:
   - `arming`
   - `armedListening`
   so the UI only claims readiness after a stronger local signal?
3. Should the wake engine expose explicit debug events for:
   - recognizer created
   - recognition task created
   - first buffer appended after start
   - first partial result received
   - task restarted after transient error
4. Should the app perform a dedicated one-time Speech warm-up earlier in app lifecycle instead of at assistant activation?

## Short Summary

The new phone-only runtime fixed the transport path and isolated the wake detector into a manageable code path, but first-launch wake reliability is still unresolved.

The main value of this debugging round was not the final fix; it was narrowing the failure down to the Speech-based wake lifecycle on fresh app startup.

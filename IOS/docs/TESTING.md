# PortWorld iOS — Test Strategy (v1.0)

> **Supersedes:** `IOS/PortWorld/docs/PRD_ACCEPTANCE.md` (hackathon T1–T13)

---

## 1. Layers

| Layer           | Tool                   | Scope                                                                |
| --------------- | ---------------------- | -------------------------------------------------------------------- |
| **Unit**        | XCTest                 | Individual classes, state machines, codecs, utilities                |
| **Integration** | XCTest                 | Multi-component flows with real `URLSession` against a local backend |
| **Snapshot**    | swift-snapshot-testing | Visual regression on all primary views (light + dark)                |
| **Manual**      | Checklist below        | Hardware-dependent flows (glasses, HFP audio, DAT SDK)               |

Automated tests (unit + integration + snapshot) are intended to run on every PR via CI (CI pipeline planned for v1.1 — see PRD §8).  
Manual tests are run before every release candidate.

---

## 2. Unit Test Inventory

Each file maps to a test file in `PortWorldTests/`. See `IMPLEMENTATION_PLAN.md §Phase 5` for the detailed spec of each.
Phase 6 is complete and streaming-first behavior is canonical. Batch-orchestrator scenarios are marked as superseded where applicable.

| Test file                                 | Class under test          | Key scenarios                                                  |
| ----------------------------------------- | ------------------------- | -------------------------------------------------------------- |
| `RuntimeConfigTests`                      | `RuntimeConfig`           | URL conversion, defaults, UserDefaults override                |
| `ClocksTests`                             | `Clocks`                  | Monotonic, within tolerance                                    |
| `WSMessageCodecTests` _(existing)_        | `WSMessageCodec`          | All inbound/outbound types, snake_case keys, malformed input   |
| `EventLoggerTests` _(existing)_           | `EventLogger`             | Sink, retention, clear, fields                                 |
| `ManualWakeWordEngineTests` _(existing)_  | `ManualWakeWordEngine`    | Trigger, listening toggle, PCM no-op                           |
| `QueryEndpointDetectorTests` _(existing)_ | `QueryEndpointDetector`   | Legacy utility coverage: silence timeout, forceEnd, isActive lifecycle |
| `AudioCollectionManagerTests`             | `AudioCollectionManager`  | State machine, error recovery, chunk emission                  |
| `AssistantPlaybackEngineTests`            | `AssistantPlaybackEngine` | Buffer count, stuck watchdog, cancel                           |
| `SessionOrchestratorTests`                | `SessionOrchestrator`     | Superseded (legacy batch): wake→query→upload flow, deactivate/cancel, message buffer |
| `SessionOrchestratorStreamingTests` _(existing)_ | `SessionOrchestrator` | Streaming lifecycle: wake/connect, audio send, sleep/disconnect, transport event handling |
| `VisionFrameUploaderTests`                | `VisionFrameUploader`     | Rate limiting, drop count, retry, cancel                       |
| `RollingVideoBufferTests`                 | `RollingVideoBuffer`      | Eviction, MP4 export, temp file cleanup, cancellation          |
| `QueryBundleBuilderTests`                 | `QueryBundleBuilder`      | Legacy utility coverage: part order, retry, cancel, encoding error |
| `WavFileWriterTests`                      | `WavFileWriter`           | Legacy utility coverage: RIFF header correctness               |
| `SFSpeechWakeWordEngineTests`             | `SFSpeechWakeWordEngine`  | Circuit-breaker, transcript normalisation, cooldown            |
| `SessionWebSocketClientTests`             | `SessionWebSocketClient`  | Stale task, sequence number, backoff bounds                    |
| `TransportFrameCodecTests` _(existing)_   | Transport frame codec     | Binary frame encode/decode for realtime PCM transport          |
| `GatewayTransportTests` _(existing)_      | `GatewayTransport`        | Transport event mapping and realtime send/receive behavior     |

### Coverage target

- `Runtime/` directory: ≥ 70% line coverage
- `Audio/` directory: ≥ 70% line coverage
- `ViewModels/` directory: ≥ 50% line coverage

Run coverage report:

```bash
xcodebuild test \
  -scheme PortWorld \
  -enableCodeCoverage YES \
  -resultBundlePath TestResults.xcresult \
  | xcbeautify
```

---

## 3. Snapshot Test Inventory

Snapshots are stored in `PortWorldTests/__Snapshots__/`. Committed to source. Regenerated with `record: true` when UI intentionally changes.

| Snapshot              | States                                  | Themes      |
| --------------------- | --------------------------------------- | ----------- |
| `OnboardingPage1View` | default                                 | light, dark |
| `OnboardingPage2View` | permissions denied, permissions granted | light, dark |
| `OnboardingPage3View` | unregistered, registered                | light, dark |
| `DevicePairingView`   | searching, found, connected             | light, dark |
| `StandbyView`         | idle, connecting, error                 | light, dark |
| `LiveSessionView`     | idle, recording, processing, speaking   | light, dark |
| `SessionHUDView`      | all wake states                         | dark        |
| `SettingsView`        | default (debug mode off, debug mode on) | light, dark |
| `PhotoPreviewView`    | default                                 | light, dark |

---

## 4. Manual Acceptance Tests

Run on a physical iPhone (16 Pro or similar) with Meta Ray-Ban Gen 2 glasses paired and a local backend running.

### Debug Mock-Device iPhone Validation (No Glasses)

Use this flow only in a Debug build when validating DAT integration without physical glasses.

| Step | Expected |
| --- | --- |
| Enable Mock Device mode in app debug settings | App switches to mock DAT source |
| Activate runtime | Session transitions to active without requiring glasses pairing |
| Observe live stream surface | Simulated video frame appears on iPhone |
| Trigger photo capture | Photo capture succeeds and preview renders |
| Run one query cycle (speak + wait response) | Query audio is captured from iPhone mic and assistant playback is heard on iPhone speaker |
| Disable Mock Device mode | App returns to normal glasses-based flow |

### T1 — Fresh Install Registration

| Step                                           | Expected                                                          |
| ---------------------------------------------- | ----------------------------------------------------------------- |
| Delete app, reinstall, launch                  | Onboarding page 1 shown                                           |
| Complete permission pages                      | Microphone + speech recognition granted without crash             |
| Tap "Connect my glasses" → complete Meta OAuth | `portworld://` callback handled; app enters device pairing screen |
| App terminated and relaunched                  | Skips onboarding; goes directly to device pairing or standby      |

**Requirements:** FR-01, FR-03

---

### T2 — Device Pairing and Connection

| Step                                  | Expected                                                |
| ------------------------------------- | ------------------------------------------------------- |
| Wear glasses; open app                | `DevicePairingView` shows "Searching…"                  |
| Glasses power on and are discoverable | State transitions to "Found: [device name]"             |
| Connection completes                  | State transitions to "Connected"; `StandbyView` appears |
| Remove glasses from head (sensor)     | `StandbyView` shows device disconnection signal         |

**Requirements:** FR-02

---

### T3 — Activation (Hold-To-Activate)

| Step                                     | Expected                                                                          |
| ---------------------------------------- | --------------------------------------------------------------------------------- |
| Brief tap on activation ring             | Nothing happens (gesture requires 0.8s hold)                                      |
| Hold 0.8s on activation ring             | Ring fills; haptic fires; `LiveSessionView` appears; session state = `connecting` |
| WS connects                              | HUD shows `active`; capture indicators visible                                    |
| No camera active / glasses not streaming | Standby card shows error indicator; activation still reachable                    |

**Requirements:** FR-04, FR-05

---

### T4 — Continuous Vision Upload

| Step                                            | Expected                                                                   |
| ----------------------------------------------- | -------------------------------------------------------------------------- |
| Hold 3-minute active session under stable Wi-Fi | Server logs show ~1 frame/second arriving at `/vision/frame`               |
| Drop network for 10s then restore               | Frames resume after restoration; frame drop count visible in dev dashboard |
| Check `health.stats` WS messages                | `frame_drop_rate` non-zero during outage, recovers after                   |

**Requirements:** FR-09, FR-10

---

### T5 — Manual Wake Trigger

| Step                                     | Expected                                                                                            |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Session active; tap the wake button once | Wake chime plays (660Hz); HUD shows "Listening"; WS sends `wakeword.detected` with `source: manual` |
| Speak for 3 seconds                      | HUD shows "Recording"; VAD active                                                                   |
| Stop speaking; wait 5 seconds            | Thinking chime plays (880Hz); HUD shows "Processing"; `query.ended` on WS                           |

**Requirements:** FR-12, FR-13, FR-14

---

### T6 — Voice Wake Word Detection

| Step                                    | Expected                                                         |
| --------------------------------------- | ---------------------------------------------------------------- |
| Session active; say "hey mario" clearly | Wake chime plays; HUD shows "Listening" without any button press |
| Ambient speech without wake phrase      | No accidental triggers during 2-minute ambient noise test        |
| Say "hey mario" 5× in 30 seconds        | Only valid triggers fire; cooldown suppresses rapid re-trigger   |

**Requirements:** FR-12, FR-13

---

### T7 — Force-End Query

| Step                                                    | Expected                                                        |
| ------------------------------------------------------- | --------------------------------------------------------------- |
| Trigger a query; while recording, tap wake button again | Query ends immediately with `reason: manualStop`; upload begins |

**Requirements:** FR-14

---

### T8 — Query Bundle Upload

| Step                                          | Expected                                                                                                                             |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Trigger query, speak 5s, wait silence timeout | Bundle created and uploaded to `POST /query`; `query.bundle.uploaded` received                                                       |
| Check server logs                             | Metadata JSON contains `session_id`, `query_id`, `wake_ts_ms`, `query_start_ts_ms`, `query_end_ts_ms`, `app_version`, `device_model` |
| Check bundle audio                            | WAV file is valid; sample rate 8kHz mono                                                                                             |
| Check bundle video                            | MP4 contains ≥5s pre-wake context; H.264 track present                                                                               |
| Query ends while WS is disconnected           | Bundle uploads via HTTP; `query.bundle.uploaded` sent on WS reconnect                                                                |

**Requirements:** FR-15, FR-18, P3-05

---

### T9 — Assistant Audio Playback

| Step                                                | Expected                                           |
| --------------------------------------------------- | -------------------------------------------------- |
| Backend streams `assistant.audio_chunk` after query | Audio heard through glasses speakers               |
| Server sends `cancel_response`                      | Playback stops immediately                         |
| Audio route changes (glasses off head) mid-playback | Playback pauses; resumes when glasses back on head |
| No audio route (glasses disconnected)               | Playback buffers; plays when route restored        |

**Requirements:** FR-19, FR-20, FR-22

---

### T10 — Stuck Playback Recovery

| Step                                              | Expected                                                            |
| ------------------------------------------------- | ------------------------------------------------------------------- |
| Simulate stuck playback (no completion callbacks) | Watchdog triggers after 5s; playback graph restarted; audio resumes |

**Requirements:** FR-21

---

### T11 — WebSocket Reconnect

| Step                                    | Expected                                                              |
| --------------------------------------- | --------------------------------------------------------------------- |
| Kill backend while session active       | HUD shows "Reconnecting…"; backoff timer visible in dev dashboard     |
| Restart backend                         | Session re-enters `active` within 3s; photo upload and capture resume |
| Trigger query during reconnecting state | Query events buffered; sent to server after reconnect                 |

**Requirements:** FR-07

---

### T12 — Background Behaviour

| Step                                         | Expected                                                                    |
| -------------------------------------------- | --------------------------------------------------------------------------- |
| Move app to background during active session | If iOS allows: upload and capture continue (check server logs)              |
| Return to foreground after 30s               | If suspended: reconnect begins immediately; HUD shows reconnecting → active |
| Background with pending query bundle upload  | Upload completes in background if iOS allows; event logged                  |

**Requirements:** FR-08, NFR-09

---

### T13 — 30-Minute Soak Test

| Step                                              | Expected                                                                               |
| ------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Run 15 wake/query/response cycles over 30 minutes | Zero crashes                                                                           |
| Monitor memory in Instruments                     | RSS stays below 200MB; no unbounded growth in video buffer, event log, or audio chunks |
| Check temp directory after session                | No orphaned WAV or MP4 files from completed queries                                    |

**Requirements:** NFR-01, NFR-02

---

### T14 — Query Bundle Latency

| Step                                                                            | Expected                                                                 |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| Trigger a 5-second query; measure from `query.ended` to `query.bundle.uploaded` | Bundle creation + upload < 2s on Wi-Fi (bundle creation portion < 500ms) |

**Requirements:** NFR-03

---

### T15 — Error Recovery UX

| Step                                        | Expected                                                                                |
| ------------------------------------------- | --------------------------------------------------------------------------------------- |
| Take device fully offline; trigger a query  | Non-intrusive error toast: "Couldn't upload query. Tap to retry."                       |
| Backend returns HTTP 500 twice then 200     | Query retries silently; success logged; user sees no error                              |
| SFSpeech recognition fails 5× consecutively | Developer-visible badge in dev dashboard; session stays active; manual wake still works |

**Requirements:** FR-16, FR-29

---

### T16 — Settings Persistence

| Step                                                              | Expected                               |
| ----------------------------------------------------------------- | -------------------------------------- |
| Change silence timeout to 3s; close app; relaunch                 | New session uses 3s timeout            |
| Change wake phrase to "hello world"; trigger query without button | Phrase detection works with new phrase |

**Requirements:** P3-06

---

### T17 — Accessibility

| Step                                            | Expected                                               |
| ----------------------------------------------- | ------------------------------------------------------ |
| Enable VoiceOver; navigate through all screens  | Every interactive element announces a meaningful label |
| Increase text size to Accessibility Extra Large | No text truncation or layout overflow on any screen    |

**Requirements:** FR-28

---

### T18 — Light / Dark Mode

| Step                                                              | Expected                                                             |
| ----------------------------------------------------------------- | -------------------------------------------------------------------- |
| Switch device to light mode; go through onboarding → live session | All screens render correctly; no dark-only hardcoded colours visible |
| Toggle between light and dark while on `StandbyView`              | Colours adapt without visual artifacts                               |

**Requirements:** FR-27

---

## 5. Release Gate

v1.0 is accepted for App Store submission when **all** of the following are true:

- [ ] All unit tests pass (`xcodebuild test`, zero failures)
- [ ] Snapshot tests pass with zero diffs
- [ ] Manual tests T1–T18 all pass on physical hardware
- [ ] `Runtime/` + `Audio/` coverage ≥ 70%
- [ ] 30-minute soak test (T13) completes without crash
- [ ] Memory below 200MB RSS during T13
- [ ] No `print()` in release build (`grep -r "print(" IOS/PortWorld/ --include="*.swift"` returns only `#if DEBUG`-gated results)
- [ ] No LAN IP in app source (`grep -r "192.168\|172.16\|10.0.0" IOS/PortWorld IOS/Info.plist IOS/PortWorld.entitlements IOS/PortWorld.xcodeproj` returns nothing)
- [ ] Privacy manifest (`PrivacyInfo.xcprivacy`) complete
- [ ] App Store screenshot set prepared (6.5" + 5.5", ≥ 3 screens)

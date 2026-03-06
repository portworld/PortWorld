# PortWorld iOS — Product Requirements Document (v1.0)

> **Supersedes:** `IOS/PortWorld/docs/PRD.md` (hackathon v4)
> **Status:** Active — governs the v1.0 production release targeting App Store distribution.

---

## 1. Mission and Goals

### Mission

PortWorld is an iOS companion app for Meta Ray-Ban Gen 2 smart glasses that connects the wearer's live visual and audio context to AI agents — enabling hands-free, voice-guided assistance in real-world workflows.

### v1.0 Goals

| Goal                         | Description                                                                    |
| ---------------------------- | ------------------------------------------------------------------------------ |
| **Consumer-distributable**   | Publishable on the App Store with acceptable UX/UI quality                     |
| **Reliability-first**        | Zero crashes in the core pipeline; stable under repeated query cycles          |
| **Production data flows**    | Correct, bounded, and observable audio/video/vision pipelines                  |
| **Agent-ready data quality** | High quality, well-structured media bundles that maximise agent output quality |
| **Developer continuity**     | Architecture and test suite that supports confident ongoing development        |

### Non-Goals for v1.0

- Finalised AI agent or multi-agent orchestration policy (handled server-side)
- Non-iOS platforms
- Android Meta glasses support
- In-app LLM inference
- Fully automated CI/CD or TestFlight release pipeline (deferred to v1.1)
- Advanced social / sharing features
- **Mode A (Direct-to-Provider BYOK)** — connecting iOS directly to realtime model APIs without a gateway (deferred to v2.0; requires v1.3 streaming architecture + provider API validation)
- **Local Loop A runtime** — running the conversational agent on-device (deferred to v2.1)

---

## 2. Users and Use Cases

### Primary User

Professionals needing hands-free AI guidance while their hands are occupied (plumbers, electricians, field technicians). The app must work reliably with minimal on-screen interaction.

### Key Use Cases

| ID      | Use Case                                                                                      |
| ------- | --------------------------------------------------------------------------------------------- |
| `UC-01` | Register the app with Meta AI and pair glasses; do it once and it persists                    |
| `UC-02` | Activate the assistant with a deliberate gesture (no accidental start)                        |
| `UC-03` | Trigger a voice query hands-free; receive spoken answer through glasses speakers              |
| `UC-04` | Continuous scene understanding — glasses camera context is always available to the backend    |
| `UC-05` | Session recovery after phone goes into pocket / loses signal — reconnects without user action |
| `UC-06` | Review and share captured moment photos from glasses camera                                   |
| `UC-07` | Adjust assistant sensitivity (silence timeout, wake phrase) in settings                       |

---

## 3. Functional Requirements

### Registration and Device

| ID      | Requirement                                                                                                                                                                     |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `FR-01` | App registers with Meta AI via OAuth callback (`portworld://` custom URL scheme). Registration persists across launches.                                                        |
| `FR-02` | App discovers compatible Meta wearable devices and surfaces connection state with clear onboarding.                                                                             |
| `FR-03` | All required permissions (microphone, speech recognition) are requested during onboarding — not mid-session. Camera permission is requested by the DAT SDK during registration. |

### Session Lifecycle

| ID      | Requirement                                                                                                                                                             |
| ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `FR-04` | One deliberate activation gesture (hold-to-activate) starts the session. The gesture prevents accidental activation.                                                    |
| `FR-05` | Activation opens the WebSocket control plane, starts continuous photo upload, and begins local video + audio capture.                                                   |
| `FR-06` | Deactivation cleanly stops all capture, cancels any in-flight uploads, and closes the WebSocket.                                                                        |
| `FR-07` | On WebSocket disconnect, the app reconnects automatically using exponential backoff (min 500ms, max 30s). Reconnect is surfaced in the HUD but requires no user action. |
| `FR-08` | On app background, capture and reconnect continue while iOS permits. On foreground, a dropped session reconnects immediately without user action.                       |

### Vision Pipeline

| ID      | Requirement                                                                                                                       |
| ------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `FR-09` | App uploads JPEG frames at up to 1 FPS to `POST /vision/frame` during an active session.                                          |
| `FR-10` | Frame upload must not block the query pipeline. Dropped frames are counted and reported in health stats.                          |
| `FR-11` | Frame upload FPS is configurable in `RuntimeConfig` (default `1`; range `0.5–2`). FPS must not exceed `2` on any connection type. |

### Query Pipeline

| ID      | Requirement                                                                                                                                                                                   |
| ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| `FR-12` | Query is triggered either by manual button tap or by voice wake word detection (`SFSpeech` on-device, default phrase `"hey mario"`).                                                          |
| `FR-13` | Wake word detection emits `wakeword.detected` on the WebSocket with source (`manual`                                                                                                          | `voice`) and timestamp. |
| `FR-14` | Query end is detected by VAD silence timeout (default `5s`, user-adjustable `1–10s`). User can also force-end with a second button tap.                                                       |
| `FR-15` | On query end: app extracts the audio clip (WAV) and video segment (5s pre-wake + query duration, H.264 MP4) and uploads as `multipart/form-data` to `POST /query`.                            |
| `FR-16` | Query bundle upload uses 2 retry attempts with backoff on HTTP 429 / 5xx. On final failure, the user sees a **transient toast/banner error** (not a modal alert). The session remains active. |
| `FR-17` | All temp files (WAV, MP4) produced by a query cycle are deleted after a successful upload and swept on app launch.                                                                            |
| `FR-18` | `POST /query` multipart parts are `metadata` (JSON), `audio` (WAV), `video` (MP4), in that order. See §5 Transport Contracts below for the full wire contract.                                |

### Assistant Audio Playback

| ID      | Requirement                                                                                                                        |
| ------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `FR-19` | Incoming `assistant.audio_chunk` payloads (PCM s16le at 16kHz mono) are decoded and played through glasses speakers via HFP route. |
| `FR-20` | Playback supports server-driven `start_response`, `stop_response`, `cancel_response` control messages.                             |
| `FR-21` | A stuck-playback watchdog restarts the playback graph if no buffer completion fires within 5s while buffers are pending.           |
| `FR-22` | On route change (e.g. glasses disconnected), playback pauses; on route restoration, playback resumes.                              |

### Event Logging and Health

| ID      | Requirement                                                                                                                                                                                         |
| ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `FR-23` | Every significant lifecycle event (wake, query start/end, bundle upload, WS state change, error) is written to `EventLogger` with millisecond timestamp, session ID, and query ID where applicable. |
| `FR-24` | Events are persisted to a rolling JSONL file (max 5MB, 3 generations) in `applicationSupportDirectory`.                                                                                             |
| `FR-25` | Health stats are emitted via WebSocket every 10s: WS round-trip latency, frame drop rate, audio buffer duration, reconnect attempt count, `app_version`, `device_model`, `os_version`.              |
| `FR-26` | Developer mode (Settings toggle, only visible in Debug builds) exposes the live telemetry dashboard and a "Export logs" button.                                                                     |

### UX / UI

| ID      | Requirement                                                                                                   |
| ------- | ------------------------------------------------------------------------------------------------------------- |
| `FR-27` | App supports both light and dark appearance; all colours use semantic adaptive tokens from the design system. |
| `FR-28` | All interactive elements have `.accessibilityLabel` values. Text supports Dynamic Type.                       |
| `FR-29` | Error messages presented to users are friendly and actionable — no raw `NSError` strings.                     |
| `FR-30` | Photo capture from glasses is previewed full-screen; explicit user action (button) opens the iOS share sheet. |

---

## 4. Non-Functional Requirements

| ID       | Requirement                                                                                                                                          |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `NFR-01` | **Zero crashes** in the defined acceptance test scenarios (see `TESTING.md`).                                                                        |
| `NFR-02` | **Memory bounded.** Video ring buffer, audio chunk queue, and event log all have enforced size limits. No unbounded growth over a 30-minute session. |
| `NFR-03` | **Query bundle latency.** Bundle creation (audio export + video encode) must complete within 2s of query end on a cold device.                       |
| `NFR-04` | **Photo upload bandwidth.** 1 FPS JPEG target must not saturate a 4G LTE connection when combined with query uploads.                                |
| `NFR-05` | **Reconnect latency.** After a clean network restoration, session must re-enter `active` state within 3s.                                            |
| `NFR-06` | **Battery / CPU.** Rolling local capture + 1 FPS upload must be sustainable for a 30-minute session without abnormal battery drain.                  |
| `NFR-07` | **Privacy.** No audio, video, or vision data is stored persistently beyond the temp-file lifetime defined in FR-17.                                  |
| `NFR-08` | **Secrets.** No API keys, tokens, or IP addresses committed to source. All injected via xcconfig.                                                    |
| `NFR-09` | **Background audio.** `UIBackgroundModes` must include `audio`; AVAudioEngine must not be suspended by iOS while the session is active.              |

---

## 5. Transport Contracts

### Control Plane: WebSocket

**Endpoint:** `wss://<host><WS_PATH>`  
**Auth:** `Authorization: Bearer <token>` header on handshake  
**Ping interval:** 15s client-to-server  
**Reconnect policy:** exponential backoff 500ms–30s, ±20% jitter

**Outbound messages (client → server):**

| Type                    | Trigger                        |
| ----------------------- | ------------------------------ |
| `wakeword.detected`     | Wake trigger (manual or voice) |
| `health.stats`          | Every 10s                      |
| `health.ping`           | Every 15s                      |
| Binary frame `0x01`     | Realtime PCM uplink (`pcm_s16le`, mono, 24kHz) |

**Inbound messages (server → client):**

| Type                         | Action                                                 |
| ---------------------------- | ------------------------------------------------------ |
| `assistant.playback.control` | `start_response` / `stop_response` / `cancel_response` |
| `assistant.thinking`         | Haptic + HUD update                                    |
| `session.state`              | Update session state display                           |
| `transport.uplink.ack`       | Confirms backend receipt of client audio frames        |
| `health.pong`                | Acknowledge ping                                       |
| Binary frame `0x02`          | Realtime PCM downlink (`pcm_s16le`, mono, 16kHz)       |

Legacy `query.*` websocket messages and `assistant.audio_chunk` payloads are batch-era contracts and are not the active Phase 6 realtime streaming path.

### HTTP: Vision Frame

```
POST /vision/frame
Content-Type: application/json

{
  "session_id": "<uuid>",
  "ts_ms": <Int64>,
  "frame_b64": "<base64 JPEG>"
}
```

### HTTP: Query Bundle

```
POST /query
Content-Type: multipart/form-data; boundary=<boundary>

Part: metadata   (application/json)
{
  "session_id": "<uuid>",
  "query_id": "<uuid>",
  "trigger_source": "manual" | "voice",
  "wake_ts_ms": <Int64>,
  "query_start_ts_ms": <Int64>,
  "query_end_ts_ms": <Int64>,
  "video_start_ts_ms": <Int64>,
  "app_version": "<CFBundleShortVersionString>",
  "device_model": "<UIDevice.model>",
  "os_version": "<UIDevice.systemVersion>"
}

Part: audio      (audio/wav)
  PCM16 mono 8kHz WAV clip, query duration

Part: video      (video/mp4)
  H.264 MP4, 5s pre-wake + query duration
```

---

## 6. Failure Modes and Recovery

| Scenario                                  | Expected Behaviour                                                                                                    |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| WebSocket drops during query recording    | Query continues recording; `query.ended` and bundle upload proceed normally; WS events buffered and sent on reconnect |
| WebSocket drops during bundle upload      | Upload continues via HTTP (independent of WS); `query.bundle.uploaded` sent when WS is back                           |
| Bundle upload fails (2 retries exhausted) | Temp files deleted; non-intrusive toast error shown; session remains active                                           |
| Audio route lost mid-playback             | Playback paused; resumed when HFP route is restored                                                                   |
| AVAudioEngine stopped unexpectedly        | Both `AudioCollectionManager` and `AssistantPlaybackEngine` restart engine on next operation                          |
| App suspended by iOS                      | Capture stops; on foreground, session reconnects within 3s; event logged                                              |
| Speech recognition error loop             | Circuit-breaker: 5 consecutive errors → engine enters `.failed`; user sees actionable badge; can retry from settings  |
| No internet                               | `NWReachability` pauses reconnect loop; on path restoration, reconnects immediately                                   |

---

## 7. Release Acceptance Criteria

A v1.0 release is accepted when:

1. All tests in `TESTING.md` T1–T18 pass on a physical iPhone with paired Meta Ray-Ban glasses.
2. No crashes in a 30-minute soak test (repeated wake/query cycles).
3. Memory usage stays below 200MB RSS during a 30-minute session.
4. App passes a basic Apple HIG review (adaptive colours, Dynamic Type, accessibility labels, no raw errors shown to user).
5. Privacy manifest (`PrivacyInfo.xcprivacy`) is complete.
6. No LAN IP, API key, or Bearer token committed to source.
7. App Store screenshot set (6.5" + 5.5") prepared — minimum 3 screens.

---

## 8. Version Roadmap

| Version | Primary focus                                                                                                                                                                                                                                           |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `v1.0`  | Stable consumer app; all v4 hackathon features hardened; full test coverage on runtime; App Store-ready                                                                                                                                                 |
| `v1.1`  | CI/CD + TestFlight automation; push notification for async agent responses; background audio improvements                                                                                                                                               |
| `v1.2`  | Multiple agent profiles; query history view; export session logs from app                                                                                                                                                                               |
| `v1.3`  | **Realtime streaming foundation** — persistent audio streaming session (wake word → stream → sleep word); `RealtimeTransport` protocol + `GatewayTransport` adapter; batch query pipeline removed; server-side VAD                                      |
| `v2.0`  | **Mode A (Direct-to-Provider)** — iOS connects directly to realtime model APIs (OpenAI Realtime, Gemini Live) via `RealtimeTransport` adapters; no gateway required. Blocked until v1.3 streaming architecture is stable + provider APIs are validated. |
| `v2.1`  | Multi-sensor fusion; Android glasses support (if Meta extends DAT SDK); local Loop A runtime (on-device model inference)                                                                                                                                |

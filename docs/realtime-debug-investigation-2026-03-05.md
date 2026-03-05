# Realtime Debug Investigation - March 5, 2026

## Context
- Goal: make iOS app receive assistant responses through backend websocket bridge to OpenAI Realtime.
- Stack under test:
  - iOS app (`GatewayTransport` / `SessionOrchestrator`)
  - Backend websocket endpoint: `ws://<mac-ip>:8080/ws/session`
  - Backend upstream: `wss://api.openai.com/v1/realtime?model=gpt-realtime`

## What We Tested

### 1. Backend -> OpenAI Realtime connectivity
- Initial error:
  - `Failed to connect to realtime endpoint ...`
  - Root cause discovered: TLS trust issue (`SSLCertVerificationError`).
- Actions taken:
  - Added cert handling guidance (`certifi`, `SSL_CERT_FILE`).
  - Fixed malformed `.env` line where API key had accidental shell command text appended.
- Result:
  - Backend no longer reports upstream TLS connect errors.
  - Upstream session activation path now succeeds.

### 2. iOS -> Backend reachability
- Initial failures:
  - ATS `-1022` for `http://<mac-ip>:8080/...`
  - websocket `-1009` offline in hotspot/client-isolation scenarios.
- Actions taken:
  - Moved to working LAN setup where phone and Mac can reach each other.
  - Verified from Mac:
    - `curl http://127.0.0.1:8080/healthz` -> 200
    - `curl http://<mac-ip>:8080/healthz` -> 200
- Result:
  - Backend receives iOS HTTP traffic and websocket connections reliably.

### 3. Realtime session protocol schema
- Observed backend warning:
  - `Unknown parameter: 'session.turn_detection'`
- Action:
  - Switched session config back to accepted shape:
    - `session.audio.input.turn_detection`
- Result:
  - Schema mismatch warning resolved.

### 4. Missing assistant response despite connected transport
- Symptom:
  - iOS logs show:
    - `transport.state.changed -> connected`
    - `transport.control.received -> session.state`
    - `realtime.uplink.first_frame`
  - But no assistant audio response on phone.
- Backend correlation logging added for:
  - inbound control envelopes
  - first binary client audio frame
  - health ping handling

## Current Evidence (Latest)

### Backend logs show control plane only
- Received:
  - `session.activate`
  - `health.stats`
  - `wakeword.detected`
  - `health.ping`
- Not received:
  - `First client audio frame received ...` (binary frame log never appears)
  - No debug audio dump output files despite enabling dump mode.

### iOS logs show local uplink marker
- iOS emits:
  - `realtime.uplink.first_frame`
  - repeated health stats and health pongs
- But backend never sees corresponding binary frames.

### New correlation evidence (same date, later run)
- Backend receives:
  - `session.activate`
  - `wakeword.detected`
  - `health.ping`
  - `health.stats`
- Backend health correlation shows:
  - `ios_enqueued`, `ios_attempted`, `ios_sent` increase continuously (e.g. 41 -> 142 -> 247).
  - `ios_send_failures=0`.
  - `backend_frames=0`, `backend_bytes=0` remain flat.
- This means iOS believes uplink sends are succeeding, but FastAPI/Uvicorn never receives any audio payload on `/ws/session`.

### Fallback experiment result
- Implemented fallback to send audio as text control envelopes (`client.audio` with base64) instead of binary frames.
- Backend added parser for `client.audio`.
- Result unchanged:
  - Backend still receives only standard control types (`session.activate`, `health.*`, `wakeword.detected`).
  - No `client.audio` control messages observed.
  - No binary frames observed.

## What Worked
- Backend process starts/stays healthy.
- iOS can connect websocket and send control envelopes.
- Backend can parse/handle `session.activate` and `health.ping`.
- Upstream OpenAI handshake/session init now works under corrected TLS/env.
- iOS uplink instrumentation confirms active enqueue + send attempts + successful send returns.

## What Did Not Work
- No binary audio frames from iOS reach backend (`ws.receive(bytes=...)` path not hit).
- No text-audio fallback envelopes (`client.audio`) reach backend either.
- No `.wav` debug dump files created (because no binary payloads arrive at bridge).
- No downstream assistant response audio (expected since no upstream input audio observed server-side).

## Where We Stand (Most Likely Root Cause)
- High-confidence bottleneck is **websocket message-type delivery mismatch between iOS client transport and FastAPI/Uvicorn receive path**:
- Control envelopes reliably arrive.
- Audio payload messages (binary and fallback text type) never surface in backend receive loop.
- Since iOS reports successful send attempts with zero send errors, this no longer points to simple app-side gating; it points to transport/runtime delivery semantics for non-standard WS message flow in current stack.

## Recommended Next Diagnostic Step (No Architecture Change)
1. Add backend raw ASGI websocket message tracing (log exact `message.keys()` / message `type` for every `websocket.receive`) before current routing logic.
2. Run an independent websocket probe from macOS to `/ws/session` that sends:
   - one JSON control frame,
   - one binary frame,
   - one `client.audio` text frame,
   and verify which frame types FastAPI receives.
3. If probe works but iOS still fails:
   - capture iOS-side packet trace/proxy (or URLSessionTaskMetrics if available for WS) to verify on-wire frame kinds.
4. If probe also fails for non-control frames:
   - isolate server/runtime issue (Uvicorn/FastAPI/ws stack handling) independent of app code.

## Resolution Note
- Canonical contract was re-established as:
  - JSON text envelopes for control
  - binary WebSocket frames for audio (`0x01` client uplink, `0x02` server downlink)
- `client.audio` text/base64 fallback is now treated as debug-only compatibility and can be disabled server-side.
- Backend now supports raw websocket receive tracing plus a local websocket probe script (`backend/scripts/ws_probe.py`) to validate control + binary audio delivery independently of the iOS app.
- Backend session setup now requests 24kHz output audio from OpenAI to satisfy current realtime API validation (`>=24000`).

## What We Tried In This Pass

### iOS transport changes
- Restored binary uplink in `GatewayTransport.sendAudio(...)` instead of sending `client.audio` text/base64 envelopes.
- Tightened `SessionWebSocketClient` send semantics so `.suspended` websocket task state is no longer treated as sendable.
- Added extra iOS diagnostics:
  - socket-state logging on outbound websocket sends
  - explicit debug logs when `SessionOrchestrator` drops realtime PCM because the session is not activated, not streaming, not connected, not ready, or has an empty payload
  - explicit debug logs when `session.state` is present but not one of the ready values

### Backend changes
- Added env-gated raw websocket receive tracing in `/ws/session` so every `websocket.receive` can log whether it arrived as text or bytes and how large it was.
- Added sanitized logging for invalid control envelopes before returning `INVALID_CONTROL_ENVELOPE`.
- Kept `client.audio` parsing support only behind a backend debug flag (`OPENAI_REALTIME_ALLOW_TEXT_AUDIO_FALLBACK=false` by default).
- Added a local websocket probe script:
  - `python backend/scripts/ws_probe.py --url ws://127.0.0.1:8080/ws/session --session-id sess_probe`
- Changed backend OpenAI session config to request 24kHz output audio so session initialization is accepted by OpenAI realtime.

### Test coverage added
- Added backend websocket-router integration coverage for `/ws/session`:
  - `session.activate` + binary client audio frame
  - `session.activate` + `client.audio` with fallback disabled
  - `session.activate` + `client.audio` with fallback enabled
- Updated iOS transport tests to assert the restored binary uplink path.
- Added an iOS unit test that verifies suspended websocket tasks reject sends.

### Verification attempts and outcomes
- `xcodebuild build -project IOS/PortWorld.xcodeproj -scheme PortWorld -destination 'generic/platform=iOS'`
  - Result: succeeded after fixing initial Swift isolation warnings in `GatewayTransport`.
- Backend isolated verification with `uv run --isolated ... pytest`:
  - `backend/tests/test_openai_realtime_client.py` -> passed
  - `backend/tests/test_ws_session_route.py` -> passed
  - broader subset (`test_contracts.py`, `test_frame_codec.py`, `test_health_and_vision_routes.py`, `test_ws_bridge_core.py`) -> all passed except one pre-existing failure:
    - `test_manual_turn_fallback_idle_timeout_triggers_once` in `backend/tests/test_ws_bridge_core.py`
- Local repo `.venv` could not be used for pytest on this machine due an architecture mismatch in `pydantic_core` and missing dependencies, so isolated `uv` runs were used instead.

## Notes
- Several unrelated system-level logs (`FigApplicationStateMonitor`, QUIC parser noise) were observed but are not causal based on current evidence.
- OpenAI API key appeared in plaintext in local `.env` during debugging; rotate key after debugging session.

## Update (Current Pass)

### Code changes applied
- `backend/openai_realtime_client.py`
  - Set `OUTPUT_AUDIO_SAMPLE_RATE = 24_000` to resolve upstream rejection:
    - `Invalid 'session.audio.output.format.rate' ... Expected >= 24000`.
- `backend/bridge.py`
  - Added upstream session readiness gate in `connect_and_start()`:
    - starts upstream receive loop,
    - sends `session.update`,
    - waits for confirmed upstream readiness (`session.created` or `session.updated`) before returning.
  - If upstream emits an error before readiness, activation now fails fast with `RealtimeClientError` and does not report active.
- `backend/tests/test_openai_realtime_client.py`
  - Updated expected output sample rate assertion from `16000` to `24000`.
- `backend/tests/test_ws_bridge_core.py`
  - Added readiness tests:
    - `test_connect_and_start_waits_for_upstream_session_ready`
    - `test_connect_and_start_fails_when_upstream_errors_before_ready`

### Commands run and outcomes
- `uv run --isolated --with pytest --with-requirements backend/requirements.txt python -m pytest backend/tests/test_openai_realtime_client.py -q`
  - `3 passed`
- `uv run --isolated --with pytest --with httpx --with-requirements backend/requirements.txt python -m pytest backend/tests/test_ws_session_route.py -q`
  - `3 passed`
- `uv run --isolated --with pytest --with-requirements backend/requirements.txt python -m pytest backend/tests/test_ws_bridge_core.py -q -k 'connect_and_start_waits_for_upstream_session_ready or connect_and_start_fails_when_upstream_errors_before_ready'`
  - `2 passed, 8 deselected`
- `uv run --isolated --with pytest --with-requirements backend/requirements.txt python -m pytest backend/tests/test_ws_bridge_core.py -q`
  - still shows pre-existing failure:
    - `test_manual_turn_fallback_idle_timeout_triggers_once`

### Raw websocket tracing + probe validation
- Backend launched with tracing:
  - `OPENAI_DEBUG_TRACE_WS_MESSAGES=true uv run --isolated --with-requirements backend/requirements.txt python -m uvicorn backend.app:app --host 127.0.0.1 --port 8081 --log-level info`
- Probe command:
  - `PYTHONPATH=. uv run --isolated --with-requirements backend/requirements.txt python backend/scripts/ws_probe.py --url ws://127.0.0.1:8081/ws/session --session-id sess_probe_live --send-text-fallback --settle-seconds 1.0`
- Probe output:
  - received `session.state ... {"state":"active"}`
  - sent one binary frame and one `client.audio` text fallback frame.
- Server trace confirmed receive path:
  - `WS_TRACE ... text_len=...` for `session.activate`
  - `WS_TRACE ... byte_len=13` for binary frame
  - `First client audio frame received ... bytes=4`
  - `WS_TRACE ... text_len=...` for `client.audio`

### Current conclusion
- Backend `/ws/session` receive loop correctly handles both control text and binary audio frames under probe.
- Readiness semantics are now stricter: iOS should only see `session.state=active` after upstream readiness confirmation.
- Remaining field issue (if still observed on device) is now more likely in iOS runtime sequencing or production network/runtime conditions rather than a server inability to receive binary frames.

## Update (Implementation Applied)

### Final diagnosis
- The field failure was not a simple backend inability to receive websocket audio.
- The backend probe and router tests showed `/ws/session` can receive and ack binary client audio correctly.
- The main issue was the iOS uplink contract and sequencing:
  - iOS treated a missing ack within 1 second as proof of transport failure.
  - the backend emitted `transport.uplink.ack` too late, after upstream forwarding, even though the intended meaning was backend receipt.
- Result:
  - iOS would reconnect aggressively during the first utterance,
  - while backend receipt semantics were too coupled to upstream forwarding latency.

### Resolution implemented

#### iOS changes
- Added an explicit internal uplink probe frame type (`0x03`) before live microphone audio is treated as ready in debug builds.
- Increased the uplink ack timeout from `1000ms` to `4000ms`.
- Gated realtime readiness on:
  - websocket connected,
  - backend `session.state=active`,
  - probe ack received when probe mode is enabled.
- Added websocket close diagnostics so the app can log:
  - connection generation,
  - close code,
  - close reason when available.
- Added `transport.uplink.ack` payload support for:
  - `probe_acknowledged`

#### Backend changes
- Added `CLIENT_PROBE_FRAME_TYPE = 0x03` to the websocket frame codec.
- Changed `/ws/session` behavior so `transport.uplink.ack` is emitted immediately after the backend receives a valid client frame:
  - for binary uplink audio,
  - for the internal probe frame,
  - for text fallback frames when that debug path is enabled.
- Kept upstream forwarding failures as separate error events:
  - `UPSTREAM_SEND_FAILED`
- Probe frames are acknowledged but not forwarded upstream.

### Why this changes the conclusion
- Earlier in the day, the evidence suggested a possible websocket message delivery mismatch between iOS and Uvicorn.
- After the probe work and backend route hardening, that conclusion no longer holds as the leading explanation.
- Current high-confidence interpretation:
  - backend transport receive path is sound,
  - upstream readiness handling is sound,
  - the field instability was caused by a brittle iOS/backend ack contract combined with overly aggressive reconnect behavior.

### Verification after implementation

#### Backend
- `uv run --isolated --with pytest --with httpx --with-requirements backend/requirements.txt python -m pytest backend/tests/test_ws_session_route.py -q`
  - Result: `5 passed`
- Coverage now explicitly verifies:
  - binary audio ack payloads include `probe_acknowledged: false`
  - probe frames receive `transport.uplink.ack` with `probe_acknowledged: true`
  - ack is emitted before upstream forward failure is reported

#### iOS
- `xcodebuild build -project IOS/PortWorld.xcodeproj -scheme PortWorld -destination 'generic/platform=iOS Simulator'`
  - Result: `BUILD SUCCEEDED`
- Targeted unit tests were updated for:
  - probe frame send path
  - websocket close propagation
  - probe-gated streaming readiness
  - disconnect close metadata
- A targeted `xcodebuild test` run for the touched suites compiled and signed successfully but hung in this environment before returning XCTest completion, so there is not yet a clean terminal test completion artifact for the iOS subset.

### Files changed in this pass
- iOS runtime:
  - `IOS/PortWorld/Runtime/SessionOrchestrator.swift`
  - `IOS/PortWorld/Runtime/SessionWebSocketClient.swift`
  - `IOS/PortWorld/Runtime/RuntimeProtocols.swift`
  - `IOS/PortWorld/Runtime/RuntimeTypes.swift`
  - `IOS/PortWorld/Runtime/Transport/TransportTypes.swift`
  - `IOS/PortWorld/Runtime/Transport/RealtimeTransport.swift`
  - `IOS/PortWorld/Runtime/Transport/GatewayTransport.swift`
- iOS tests:
  - `IOS/PortWorldTests/GatewayTransportTests.swift`
  - `IOS/PortWorldTests/SessionWebSocketClientTests.swift`
  - `IOS/PortWorldTests/RealtimeTransportTests.swift`
  - `IOS/PortWorldTests/SessionOrchestratorStreamingTests.swift`
- Backend:
  - `backend/frame_codec.py`
  - `backend/routers/ws.py`
  - `backend/tests/test_ws_session_route.py`

### Recommended next validation
1. Run a live device session against the updated backend without `uvicorn --reload`.
2. Confirm the log sequence on first wake:
   - `session.state=active`
   - probe sent
   - `transport.uplink.ack` with `probe_acknowledged=true`
   - first real audio uplink ack inside the 4 second watchdog window
3. If the device still fails while backend shows no frame receipt, capture the new socket close diagnostics from iOS before changing transport implementation again.

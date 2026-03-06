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
- The backend can now distinguish "frame reached backend" from "frame forwarded upstream".
- The iOS side no longer treats normal backend/upstream timing as an immediate transport failure.
- If field failures remain after this change, the remaining cause is more likely in:
  - iOS runtime sequencing,
  - deployment/runtime divergence on device,
  - or intermittent local backend reachability.

## Latest Field Runs (March 6, 2026)

### Successful backend reachability run still reproduces the core bug
- Session observed:
  - `sess_9E82B778-41B1-4458-8E7F-6AAD72423290`
- Backend conditions were healthy in this run:
  - `POST /vision/frame` returned `200 OK`
  - websocket `/ws/session` connected successfully
  - backend received:
    - `session.activate`
    - `health.stats`
    - `wakeword.detected`
    - probe frame
    - the 8 binary sweep frames
    - text debug sweep controls
- Backend still stopped at:
  - `backend_frames=8`
  - `backend_bytes=11072`
- iOS still showed continuous local capture/chunking and successful local send counters:
  - repeated `realtime_pcm_chunk_emitted ... bytes=4080`
  - `realtime_audio_frames_sent` advanced to `77`, then `177`, then beyond `200`
  - `realtime.uplink.transport_send_succeeded` advanced at counts `100` and `200`
- But backend still did **not** receive ongoing live microphone frames beyond the initial sweep.

### Latest marker result
- The newest backend-visible debug markers still did not appear in the successful run:
  - no `Inbound control type=debug.worker_live_audio_path`
  - no `Inbound control type=debug.live_audio_path`
- This remains the strongest indicator that the runtime path currently executing on-device is not entering the newest live-worker / live-transport path we instrumented.

### Separate later runs were invalid due to plain connectivity failure
- Multiple later sessions showed ordinary network/connectivity errors, not the original realtime-delivery bug:
  - `NSURLErrorDomain Code=-1004`
  - `Could not connect to the server.`
  - TCP `RST` / `SYN_SENT` failures to `192.168.1.111:8080`
  - websocket `/ws/session` and `POST /vision/frame` both failed in those runs
- Earlier in the same investigation, there were also separate local-network permission failures:
  - `_NSURLErrorNWPathKey=unsatisfied (Local network prohibited)`
- These failing sessions should not be mixed with the successful `sess_9E82...` run when reasoning about the core transport bug.

### Current best conclusion
- There are now two clearly separated failure classes:
1. When backend reachability is healthy, the original live-audio bug still remains:
   - backend receives only probe + sweep audio
   - backend does not receive ongoing live audio
   - newest worker/live-path markers remain absent
2. Some later field sessions fail for a simpler reason:
   - the backend host/port is not reachable from the phone at that moment
- The highest-signal successful run still points to a runtime-path/deployment divergence or another code path bypassing the edited live-worker implementation.

### Practical guidance for future log review
- Only use runs for root-cause analysis when all of the following are true:
  - backend shows websocket accepted on `/ws/session`
  - `POST /vision/frame` succeeds
  - there is no `-1004 Could not connect to the server`
  - there is no `Local network prohibited`
- In those clean runs, immediately check for:
  - `Inbound control type=debug.worker_live_audio_path`
  - `Inbound control type=debug.live_audio_path`
  - growth of backend `backend_frames` beyond `8`
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

## Update (Payload Sweep Findings)

### What the sweep tested
- After probe acknowledgement, the iOS app sent a debug binary sweep using the normal realtime audio send path with payload sizes:
  - `16`
  - `64`
  - `256`
  - `512`
  - `1024`
  - `2048`
  - `3072`
  - `4080`
- In the same window, the app also sent a text sweep using debug control envelopes with payload sizes up to `2048`.
- These sends were performed on the same websocket connection as the live session (`connection_id=1`).

### Result
- Backend received every binary sweep frame, including the largest encoded binary frame (`4089` bytes total for `4080` bytes payload).
- Backend also received every text sweep control frame.
- Backend emitted a valid `transport.uplink.ack` for the first received binary audio frame:
  - `frames_received=1`
  - `bytes_received=16`
- Backend health then reported:
  - `backend_frames=8`
  - `backend_bytes=11072`
- Meanwhile iOS continued to report successful realtime audio sends:
  - `realtime_audio_frames_sent=137`
  - periodic `realtime.uplink.transport_send_succeeded` logs at counts `100`, `200`, ...
- But backend frame counts stayed flat after the 8 debug binary sweep frames.

### What this disproves
- This is no longer consistent with:
  - binary websocket frames being unsupported on iPhone
  - larger binary frames being dropped because of payload size
  - probe-only success with general binary failure
  - connection churn or reconnect causing sends to land on the wrong socket
- The following are now confirmed working on-device:
  - control text delivery
  - probe binary delivery
  - binary delivery from `16` bytes payload through `4080` bytes payload
  - text control delivery up to at least `2048` bytes payload

### New leading hypothesis
- The active failure is now most likely outbound websocket send concurrency in the iOS app.
- Why this fits the evidence:
  - the debug sweep used direct sequential awaited sends and all of them arrived server-side
  - the live path uses `RealtimePCMUplinkWorker` while other producers also send over the same websocket:
    - health pings
    - health stats
    - session/control events
  - after the sweep succeeds, backend stops receiving additional audio even though iOS keeps reporting successful binary sends
- Current interpretation:
  - `URLSessionWebSocketTask.send(...)` is likely being driven concurrently from multiple call sites in a way that is not producing reliable on-wire delivery for the continuous audio path

### Updated next step
- The next implementation pass should serialize all outbound websocket sends through a single queue in the iOS transport layer.
- That queue must cover:
  - control text sends
  - probe sends
  - realtime audio sends
  - health ping/stats sends
- Add queue-level diagnostics for:
  - enqueue order
  - send start
  - send completion
  - message kind
  - byte count
- Acceptance criterion for the next pass:
  - backend `backend_frames` continues increasing beyond the 8 debug sweep frames during live microphone uplink

### Architectural simplification note
- At this point, the desired product/runtime model should be treated as:
  1. keep wake-word detection local on-device
  2. when `"Hey Mario"` is detected, open a single backend websocket session
  3. continuously stream microphone PCM through that session
  4. continuously receive assistant PCM from the backend on that same session
  5. on sleep word or explicit conversation end, close the realtime session and return to local wake listening
- Current evidence suggests the wake-word portion is already behaving correctly enough for this model.
- The remaining engineering work is therefore less about wake logic and more about making the realtime websocket send path match this simpler continuous-stream design.

## Update (Post-Payload-Sweep Investigation)

### Summary of what we tried after the payload-sweep result
After confirming that:
- backend receives the probe frame,
- backend receives all 8 binary sweep frames up to `4080` bytes payload,
- backend receives all text sweep control frames,
- backend still never receives ongoing live microphone uplink,

we ran a series of focused client-side experiments to determine whether the remaining fault was:
- websocket send concurrency,
- live audio chunk sizing,
- `MainActor` handoff / producer-path differences,
- `URLSessionWebSocketTask.send(.data)` under sustained load,
- or simply the wrong binary being installed on-device.

### 1. Serialized all websocket sends in iOS transport

#### Changes made
- `IOS/PortWorld/Runtime/SessionWebSocketClient.swift`
  - Added a private FIFO outbound queue inside the actor.
  - Routed all outbound paths through the queue:
    - text sends
    - binary sends
    - ping / keepalive sends
  - Added queue diagnostics:
    - `outbound_send_enqueued`
    - `outbound_send_start`
    - `outbound_send_complete`
  - Added generation handling so queued work from an old socket is not sent on a new connection.

#### Why we tried it
- The live audio path, health pings/stats, probe sends, and control sends all share one websocket.
- The sweep succeeding while live audio failed looked consistent with overlapping outbound `send(...)` calls on the shared socket.

#### Result
- The queue behaved as expected in logs.
- Backend still received:
  - probe
  - 8 binary sweep frames
  - text sweep control frames
- Backend still did **not** receive continuous live audio frames after the sweep.

#### Conclusion
- Client-side overlapping websocket sends were a credible risk and are now mitigated.
- However, that was **not sufficient** to restore live microphone delivery.

### 2. Added explicit realtime PCM chunking in the capture path

#### Changes made
- `IOS/PortWorld/Audio/AudioCollectionManager.swift`
  - Added realtime PCM chunk accumulation in `RealtimePCMSinkRelay`.
  - Added chunk emission logs:
    - `realtime_pcm_chunk_emitted index=... bytes=... timestamp_ms=...`
  - Added flush-on-stop for trailing buffered audio.

#### Initial chunk target
- Tried explicit 100 ms / 24 kHz mono `pcm_s16le`.
- This initially produced chunks around `4800` bytes.

#### Result
- Backend still did not receive live audio.
- Since the sweep had only proven successful delivery up to `4080` payload bytes, the first follow-up hypothesis was that `4800` bytes was too large.

### 3. Reduced live chunk size ceiling to 4080 bytes

#### Changes made
- Kept the accumulator but capped live chunk size to `4080` bytes.

#### Why we tried it
- `4080` bytes was the largest payload size the backend had definitely received during the debug sweep.

#### Result
- Logs showed live chunks were now emitted steadily at `4080` bytes.
- Backend still only saw the 8 sweep frames.

#### Conclusion
- The failure is **not** explained by live chunk size being larger than the proven sweep sizes.

### 4. Compared PortWorld live uplink path to the known-good VisionClaw sample

#### What the comparison showed
- VisionClaw uses:
  - a simpler serial producer path,
  - explicit audio accumulation,
  - text/base64 audio over websocket,
  - a serial queue-based send flow.
- PortWorld still differed in several ways:
  - more async hops,
  - a more complex runtime/orchestrator path,
  - binary live audio uplink,
  - actor/Task-based plumbing rather than VisionClaw’s simpler serial queue model.

#### Conclusion from the comparison
- This made a text/base64 live-audio fallback worth trying even though binary sweep/probe traffic was already working.

### 5. Implemented a narrow live-audio text/base64 fallback

#### Changes made
- `IOS/PortWorld/Runtime/Transport/RealtimeTransport.swift`
  - Added `sendLiveAudio(_:, timestampMs:)`.
- `IOS/PortWorld/Runtime/Transport/GatewayTransport.swift`
  - Implemented `sendLiveAudio(...)` as websocket text control:
    - `type = "client.audio"`
    - payload contains:
      - `audio_b64`
      - `timestamp_ms`
  - Left `sendAudio(...)` as binary.
- `IOS/PortWorld/Runtime/SessionOrchestrator.swift`
  - Live mic path switched to `sendLiveAudio(...)`.
  - Debug binary sweep intentionally kept on `sendAudio(...)`.

#### Why we tried it
- Backend already had support for `client.audio` text fallback.
- VisionClaw’s working model uses text/base64 audio.
- If binary `URLSessionWebSocketTask.send(.data)` under sustained live traffic was the issue, text might have bypassed it.

#### Expected signal
- iOS logs should show:
  - `send_audio_frame ... mode=text_base64`
- Backend should show:
  - `client.audio`
  - rising backend audio frame counts beyond the 8 sweep frames.

#### Result
- None of those expected markers appeared.
- Live audio still never showed up on backend.

#### Conclusion
- The text/base64 fallback path was not being exercised at runtime in the observed device runs.

### 6. Added explicit dispatch instrumentation around the live worker path

#### Changes made
- `IOS/PortWorld/Runtime/SessionOrchestrator.swift`
  - Added:
    - `worker_send_live_audio dispatch=gateway_transport ...`
    - `worker_send_live_audio dispatch=protocol_fallback ...`
- `IOS/PortWorld/Runtime/Transport/GatewayTransport.swift`
  - Added:
    - `send_live_audio_text_path_entered ...`

#### Why we tried it
- We needed to determine whether:
  - the worker closure itself was not running,
  - protocol dispatch was falling back to a different implementation,
  - or the live audio path simply was not installed on-device.

#### Result
- Those markers did not appear in the runtime logs provided.
- Backend still only saw the 8 sweep frames.

#### Interpretation at that stage
- Either:
  - the new build was not installed on-device,
  - or the worker closure path still was not truly executing.

### 7. Found and fixed a likely silent no-op in the worker send closure

#### Change made
- `IOS/PortWorld/Runtime/SessionOrchestrator.swift`
  - Removed an optional-chain based send path that could effectively no-op while the worker still counted a success.
  - Replaced it with a concrete captured closure that directly calls:
    - `GatewayTransport.sendLiveAudio(...)` when available,
    - otherwise `RealtimeTransport.sendLiveAudio(...)`.

#### Why we tried it
- Worker-level success metrics (`realtime.uplink.transport_send_succeeded`) are logged after the closure returns.
- If the closure returned without actually sending, those metrics would be misleading.

#### Result
- Build succeeded after the fix.
- Subsequent field logs still did not show the live text markers.

#### Conclusion
- This was a real correctness fix, but still did not explain the full observed mismatch in device runs.

### 8. Investigated deployment / installed-binary mismatch

#### What was verified
- Marker strings were present in source at the expected locations:
  - `GatewayTransport.swift`
  - `SessionOrchestrator.swift`
- Marker strings were also present in the built debug artifact:
  - `PortWorld.app/PortWorld.debug.dylib`
- Production runtime wiring in source still clearly uses `GatewayTransport`:
  - `SessionOrchestrator.Dependencies.live.makeRealtimeTransport`
  - `RuntimeCoordinator`

#### Why this mattered
- Repeated device logs still lacked every new live-path marker.
- That made an “old binary still running on device” hypothesis hard to rule out.

#### Conclusion
- The built artifact definitely contains the new code.
- But observed device logs continued to behave like a runtime that never entered the new live-audio path.

### 9. Disproved “upstream capture failure” as the next explanation

#### Why this needed checking
- One possible explanation for missing live-transport markers was that live PCM never made it out of capture/conversion.

#### Evidence against it
- Device logs repeatedly showed:
  - `realtime_pcm_chunk_emitted index=... bytes=4080 ...`
- That proves:
  - capture callback is alive,
  - realtime conversion path is alive,
  - chunk relay is alive,
  - frames are being produced continuously for the live path.

#### Conclusion
- The failure is downstream of capture/chunk emission.
- It is not an `AudioCollectionManager` or realtime PCM relay dead-end.

### 10. Added backend-visible markers instead of relying only on OSLog

#### First marker
- `IOS/PortWorld/Runtime/Transport/GatewayTransport.swift`
  - On first entry into `sendLiveAudio(...)`, emit one control message:
    - `type = "debug.live_audio_path"`
    - `payload.mode = "text_base64"`

#### Why
- If OSLog visibility was the problem, backend text-control tracing should still reveal the marker.

#### Result
- Backend never received `debug.live_audio_path`.

#### Conclusion
- The running app still never entered `GatewayTransport.sendLiveAudio(...)` in those runs.

### 11. Added an even earlier backend-visible marker at the worker boundary

#### Change made
- `IOS/PortWorld/Runtime/SessionOrchestrator.swift`
  - Before transport-specific dispatch in the worker closure, emit:
    - `type = "debug.worker_live_audio_path"`
    - `payload.mode = "worker_send_frame"`

#### Why
- This gives a hard split:
  - if backend sees `debug.worker_live_audio_path`, the worker closure is executing,
  - if backend sees `debug.live_audio_path`, then transport live-path dispatch is executing,
  - if neither appears, then the newest runtime path is still not the one actually running.

#### Status
- Implemented and built successfully.
- Awaiting a traced field run that confirms whether `debug.worker_live_audio_path` appears on backend.

### 12. Separate but related local-network issue that appeared during the same investigation

#### Symptom
- Repeated photo upload failures:
  - `NSURLErrorDomain Code=-1009`
  - `_NSURLErrorNWPathKey=unsatisfied (Local network prohibited)`
  - failing URL:
    - `http://192.168.1.111:8080/vision/frame`

#### Resolution
- After enabling local network access / reinstalling, later runs showed:
  - `POST /vision/frame` -> `200 OK`

#### Conclusion
- The local-network permission issue affected the vision upload sidecar path.
- It was a real problem, but it was **not** the main cause of the live websocket audio failure.

## Current cumulative conclusions

### What has been ruled out
- Backend inability to receive binary websocket audio in general.
- Backend inability to receive larger binary frames up to `4080` payload bytes.
- Probe-only success with general binary failure.
- Local-network permission as the primary cause of the websocket audio issue.
- Live chunk size (`4800` vs `4080`) as the primary cause.
- Capture / realtime PCM emission failure upstream of transport.
- Basic websocket control connectivity between iOS and backend.

### What remains most likely
The remaining problem is still on the iOS runtime side, specifically somewhere between:
- `realtime_pcm_chunk_emitted`
- `RealtimePCMUplinkWorker.sendFrame`
- live transport dispatch (`sendLiveAudio(...)`)

The unresolved ambiguity is whether:
- the live worker closure is not actually executing in the installed app runtime,
- the installed app is still not the exact latest binary,
- or there is some runtime path divergence where worker success accounting happens without the new markers actually reaching the websocket transport.

## Next expected decisive signal
The next traced run should answer this with one of these backend markers:

1. `Inbound control type=debug.worker_live_audio_path`
   - proves the worker closure executed
   - if `debug.live_audio_path` is still absent after this, the problem is inside transport dispatch

2. `Inbound control type=debug.live_audio_path`
   - proves `GatewayTransport.sendLiveAudio(...)` executed
   - if live audio still does not arrive after this, focus shifts to text live-audio send semantics / backend handling

3. neither marker appears
   - strongest evidence that the device is still not executing the newest runtime path we are editing

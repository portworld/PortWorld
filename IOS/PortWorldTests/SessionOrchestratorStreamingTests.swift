import AVFAudio
import XCTest
@testable import PortWorld

@MainActor
final class SessionOrchestratorStreamingTests: XCTestCase {

  func testWakeDetectionConnectsTransportAndTransitionsTowardActive() async throws {
    let harness = makeHarness()
    var snapshots: [SessionOrchestrator.StatusSnapshot] = []
    harness.orchestrator.onStatusUpdated = { snapshots.append($0) }

    await harness.orchestrator.activate()
    harness.orchestrator.triggerWakeForTesting()

    try await assertEventually {
      await harness.transport.connectCallCount() == 1
    }

    await harness.transport.emit(.stateChanged(.connecting))
    await harness.transport.emit(.stateChanged(.connected))

    try await assertEventually {
      snapshots.contains(where: { $0.sessionState == .connecting })
        && snapshots.contains(where: { $0.sessionState == .streaming })
    }

    XCTAssertTrue(snapshots.contains(where: { $0.playbackState == "streaming_connecting" }))
    XCTAssertTrue(snapshots.contains(where: { $0.playbackState == "streaming" }))

    await harness.orchestrator.deactivate()
  }

  func testProcessRealtimePCMFrameSendsOnlyWhenConnectedAndStreamingWanted() async throws {
    let harness = makeHarness()

    await harness.orchestrator.activate()

    let beforeWake = Data([0x01, 0x02])
    harness.orchestrator.processRealtimePCMFrame(beforeWake, timestampMs: 10)

    harness.orchestrator.triggerWakeForTesting()
    try await assertEventually {
      await harness.transport.connectCallCount() == 1
    }

    let whileConnecting = Data([0x03, 0x04])
    harness.orchestrator.processRealtimePCMFrame(whileConnecting, timestampMs: 20)

    await harness.transport.emit(.stateChanged(.connected))
    let connectedPayload = Data([0x05, 0x06, 0x07])
    harness.orchestrator.processRealtimePCMFrame(connectedPayload, timestampMs: 30)

    try await assertEventually {
      await harness.transport.sentAudioCount() == 1
    }

    let sent = try XCTUnwrap(await harness.transport.lastSentAudio())
    XCTAssertEqual(sent.buffer, connectedPayload)
    XCTAssertEqual(sent.timestampMs, 30)

    await harness.orchestrator.deactivate()
  }

  func testRealtimePCMUplinkDropsOldestWhenQueueIsSaturated() async throws {
    var nowMs: Int64 = 1_000
    let harness = makeHarness(clock: { nowMs })

    await harness.transport.setSendAudioDelayNs(50_000_000)
    await harness.orchestrator.activate()
    harness.orchestrator.triggerWakeForTesting()

    try await assertEventually {
      await harness.transport.connectCallCount() == 1
    }
    await harness.transport.emit(.stateChanged(.connected))

    for timestamp in 0..<64 {
      harness.orchestrator.processRealtimePCMFrame(Data([UInt8(timestamp % 255)]), timestampMs: Int64(timestamp))
    }

    try await assertEventually(timeout: 3.0) {
      await harness.transport.sentAudioCount() == 33
    }

    let timestamps = await harness.transport.sentAudioTimestamps()
    XCTAssertEqual(timestamps.count, 33)
    XCTAssertEqual(timestamps.first, 0)
    XCTAssertEqual(Array(timestamps.dropFirst()), Array(32...63).map(Int64.init))

    nowMs = 2_500
    harness.orchestrator.handleAppDidEnterBackground()

    let healthMessage = try await assertEventuallyValue {
      await harness.transport.lastSentControl(type: "health.stats")
    }
    guard case .number(let dropCountValue) = healthMessage.payload["frame_drop_count"] else {
      XCTFail("Missing frame_drop_count in health payload")
      return
    }
    XCTAssertEqual(Int(dropCountValue), 31)

    await harness.orchestrator.deactivate()
  }

  func testRealtimePCMUplinkNormalPathReportsZeroFrameDrops() async throws {
    var nowMs: Int64 = 5_000
    let harness = makeHarness(clock: { nowMs })

    await harness.orchestrator.activate()
    harness.orchestrator.triggerWakeForTesting()
    try await assertEventually {
      await harness.transport.connectCallCount() == 1
    }
    await harness.transport.emit(.stateChanged(.connected))

    for timestamp in 10..<13 {
      harness.orchestrator.processRealtimePCMFrame(Data([UInt8(timestamp)]), timestampMs: Int64(timestamp))
    }

    try await assertEventually {
      await harness.transport.sentAudioCount() == 3
    }
    let timestamps = await harness.transport.sentAudioTimestamps()
    XCTAssertEqual(timestamps, [10, 11, 12])

    nowMs = 5_900
    harness.orchestrator.handleAppDidEnterBackground()

    let healthMessage = try await assertEventuallyValue {
      await harness.transport.lastSentControl(type: "health.stats")
    }
    guard case .number(let dropCountValue) = healthMessage.payload["frame_drop_count"] else {
      XCTFail("Missing frame_drop_count in health payload")
      return
    }
    XCTAssertEqual(Int(dropCountValue), 0)

    await harness.orchestrator.deactivate()
  }

  func testSleepDetectionDisconnectsAndReturnsSnapshotToIdleListening() async throws {
    let harness = makeHarness()
    var snapshots: [SessionOrchestrator.StatusSnapshot] = []
    harness.orchestrator.onStatusUpdated = { snapshots.append($0) }

    await harness.orchestrator.activate()
    harness.orchestrator.triggerWakeForTesting()

    try await assertEventually {
      await harness.transport.connectCallCount() == 1
    }

    await harness.transport.emit(.stateChanged(.connected))

    let sleepEvent = WakeWordDetectionEvent(
      wakePhrase: "goodbye mario",
      timestampMs: 5_000,
      engine: "manual",
      confidence: 1.0
    )
    try emitSleepDetected(into: harness.orchestrator, event: sleepEvent)

    try await assertEventually {
      await harness.transport.disconnectCallCount() == 1
    }

    await harness.transport.emit(.stateChanged(.disconnected))

    try await assertEventually {
      snapshots.contains(where: { $0.sessionState == .idle && $0.wakeState == .listening })
    }

    let latest = try XCTUnwrap(snapshots.last)
    XCTAssertEqual(latest.sessionState, .idle)
    XCTAssertEqual(latest.wakeState, .listening)

    await harness.orchestrator.deactivate()
  }

  func testNetworkRestoreDuringSlowDisconnectReconnectsInOrder() async throws {
    let harness = makeHarness()
    await harness.transport.setDisconnectDelayNs(150_000_000)

    await harness.orchestrator.activate()
    harness.orchestrator.triggerWakeForTesting()
    try await assertEventually {
      await harness.transport.connectCallCount() == 1
    }
    await harness.transport.emit(.stateChanged(.connected))

    harness.orchestrator.setNetworkAvailable(false)
    harness.orchestrator.setNetworkAvailable(true)

    try await assertEventually(timeout: 2.0) {
      await harness.transport.disconnectCallCount() == 1
    }
    try await assertEventually(timeout: 2.0) {
      await harness.transport.connectCallCount() == 2
    }

    await harness.orchestrator.deactivate()
  }

  func testHealthPongUpdatesRoundTripLatencyInHealthPayloadAndLogs() async throws {
    var nowMs: Int64 = 1_000
    var eventLines: [String] = []
    let harness = makeHarness(clock: { nowMs }, eventSink: { eventLines.append($0) })

    await harness.orchestrator.activate()

    try await assertEventually {
      await harness.transport.sentControlCount(type: "health.ping") >= 1
    }

    nowMs = 1_260
    await harness.transport.emit(.controlReceived(TransportControlMessage(type: "health.pong")))

    nowMs = 2_000
    harness.orchestrator.handleAppDidEnterBackground()

    try await assertEventually {
      guard
        let message = await harness.transport.lastSentControl(type: "health.stats"),
        case .number(let value) = message.payload["ws_round_trip_latency_ms"]
      else {
        return false
      }
      return Int(value) == 260
    }

    let healthStatsLog = try await assertEventuallyValue {
      for line in eventLines.reversed() {
        guard
          let data = line.data(using: .utf8),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          json["name"] as? String == "health.stats",
          let fields = json["fields"] as? [String: Any]
        else {
          continue
        }
        return fields
      }
      return nil
    }
    XCTAssertEqual(healthStatsLog["ws_round_trip_latency_ms"] as? Double, 260)

    await harness.orchestrator.deactivate()
  }

  private func makeHarness(
    clock: @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
    eventSink: ((String) -> Void)? = nil
  ) -> Harness {
    let transport = MockRealtimeTransport()
    let visionUploader = MockVisionFrameUploader()
    let videoBuffer = MockRollingVideoBuffer()
    let playback = MockPlaybackEngine()

    let config = RuntimeConfig(
      backendBaseURL: URL(string: "https://example.invalid")!,
      webSocketURL: URL(string: "wss://example.invalid/ws")!,
      visionFrameURL: URL(string: "https://example.invalid/vision")!,
      queryURL: URL(string: "https://example.invalid/query")!,
      wakeWordMode: .manualOnly
    )

    let deps = SessionOrchestrator.Dependencies(
      startStream: {},
      stopStream: {},
      exportAudioClip: { _ in
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("test-audio-\(UUID().uuidString).wav")
        try Data().write(to: url)
        return url
      },
      flushPendingAudioChunks: {},
      audioBufferDurationProvider: { 0 },
      sharedAudioEngine: AVAudioEngine(),
      clock: clock,
      makeRealtimeTransport: { _ in transport },
      makeVisionFrameUploader: { _ in visionUploader },
      makeRollingVideoBuffer: { _ in videoBuffer },
      makePlaybackEngine: { _, _ in playback },
      eventLogger: EventLogger(sink: eventSink)
    )

    return Harness(
      orchestrator: SessionOrchestrator(config: config, dependencies: deps),
      transport: transport
    )
  }

  private func emitSleepDetected(into orchestrator: SessionOrchestrator, event: WakeWordDetectionEvent) throws {
    guard
      let engine = Mirror(reflecting: orchestrator)
        .children
        .first(where: { $0.label == "manualWakeEngine" })?
        .value as? ManualWakeWordEngine
    else {
      throw TestError.manualWakeEngineNotFound
    }

    guard let callback = engine.onSleepDetected else {
      throw TestError.sleepCallbackNotConfigured
    }

    callback(event)
  }

  private func assertEventually(
    timeout: TimeInterval = 1.5,
    pollIntervalNs: UInt64 = 20_000_000,
    condition: @escaping () async -> Bool,
    file: StaticString = #filePath,
    line: UInt = #line
  ) async throws {
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
      if await condition() {
        return
      }
      try await Task.sleep(nanoseconds: pollIntervalNs)
    }
    XCTFail("Condition not met within \(timeout)s", file: file, line: line)
  }

  private func assertEventuallyValue<T>(
    timeout: TimeInterval = 1.5,
    pollIntervalNs: UInt64 = 20_000_000,
    value: @escaping () async -> T?,
    file: StaticString = #filePath,
    line: UInt = #line
  ) async throws -> T {
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
      if let resolved = await value() {
        return resolved
      }
      try await Task.sleep(nanoseconds: pollIntervalNs)
    }
    XCTFail("Value not available within \(timeout)s", file: file, line: line)
    throw XCTSkip("Timed out waiting for value")
  }

  private struct Harness {
    let orchestrator: SessionOrchestrator
    let transport: MockRealtimeTransport
  }

  private enum TestError: Error {
    case manualWakeEngineNotFound
    case sleepCallbackNotConfigured
  }
}

private actor MockRealtimeTransport: RealtimeTransport {
  private(set) var connectConfigs: [TransportConfig] = []
  private(set) var disconnectCount = 0
  private(set) var sentControls: [TransportControlMessage] = []
  private var disconnectDelayNs: UInt64 = 0
  private var sendAudioDelayNs: UInt64 = 0

  struct SentAudio {
    let buffer: Data
    let timestampMs: Int64
  }
  private(set) var sentAudio: [SentAudio] = []

  let events: AsyncStream<TransportEvent>
  private let continuation: AsyncStream<TransportEvent>.Continuation

  init() {
    var continuationRef: AsyncStream<TransportEvent>.Continuation!
    self.events = AsyncStream { continuation in
      continuationRef = continuation
    }
    self.continuation = continuationRef
  }

  func connect(config: TransportConfig) async throws {
    connectConfigs.append(config)
  }

  func disconnect() async {
    if disconnectDelayNs > 0 {
      try? await Task.sleep(nanoseconds: disconnectDelayNs)
    }
    disconnectCount += 1
  }

  func sendAudio(_ buffer: Data, timestampMs: Int64) async throws {
    if sendAudioDelayNs > 0 {
      try? await Task.sleep(nanoseconds: sendAudioDelayNs)
    }
    sentAudio.append(SentAudio(buffer: buffer, timestampMs: timestampMs))
  }

  func sendControl(_ message: TransportControlMessage) async throws {
    sentControls.append(message)
  }

  func emit(_ event: TransportEvent) {
    continuation.yield(event)
  }

  func connectCallCount() -> Int {
    connectConfigs.count
  }

  func disconnectCallCount() -> Int {
    disconnectCount
  }

  func sentAudioCount() -> Int {
    sentAudio.count
  }

  func sentControlCount(type: String) -> Int {
    sentControls.filter { $0.type == type }.count
  }

  func lastSentControl(type: String) -> TransportControlMessage? {
    sentControls.last { $0.type == type }
  }

  func lastSentAudio() -> SentAudio? {
    sentAudio.last
  }

  func sentAudioTimestamps() -> [Int64] {
    sentAudio.map(\.timestampMs)
  }

  func setDisconnectDelayNs(_ value: UInt64) {
    disconnectDelayNs = value
  }

  func setSendAudioDelayNs(_ value: UInt64) {
    sendAudioDelayNs = value
  }
}

private actor MockVisionFrameUploader: VisionFrameUploaderProtocol {
  private var sessionIDProvider: VisionFrameSessionIDProvider?
  private var onUploadResult: VisionFrameUploadResultHandler?

  func bindHandlers(
    sessionIDProvider: @escaping VisionFrameSessionIDProvider,
    onUploadResult: VisionFrameUploadResultHandler?
  ) {
    self.sessionIDProvider = sessionIDProvider
    self.onUploadResult = onUploadResult
  }

  func start() {}
  func stop() {}
  func consumeFrameDropCount() -> Int { 0 }
  func submitLatestFrame(_ image: UIImage, captureTimestampMs: Int64) {
    _ = image
    _ = captureTimestampMs
  }
}

private actor MockRollingVideoBuffer: RollingVideoBufferProtocol {
  var bufferedDurationMs: Int64 { 0 }

  func append(frame: UIImage, timestampMs: Int64) {
    _ = frame
    _ = timestampMs
  }

  func clear() {}

  func exportInterval(
    startTimestampMs: Int64,
    endTimestampMs: Int64,
    outputURL: URL?,
    bitrate: Int
  ) async throws -> RollingVideoExportResult {
    _ = startTimestampMs
    _ = endTimestampMs
    _ = outputURL
    _ = bitrate

    let output = FileManager.default.temporaryDirectory.appendingPathComponent("mock-video-\(UUID().uuidString).mp4")
    try Data().write(to: output)
    return RollingVideoExportResult(outputURL: output, frameCount: 0, durationMs: 0, bytesWritten: 0)
  }
}

@MainActor
private final class MockPlaybackEngine: AssistantPlaybackEngineProtocol {
  var onRouteChanged: ((String) -> Void)?
  var onRouteIssue: ((String) -> Void)?

  var pendingBufferCount: Int { 0 }
  var pendingBufferDurationMs: Double { 0 }
  var isBackpressured: Bool { false }

  func hasActivePendingPlayback() -> Bool { false }

  func appendChunk(_ payload: AssistantAudioChunkPayload) throws {
    _ = payload
  }

  func appendPCMData(_ pcmData: Data, format incomingFormat: AssistantAudioFormat) throws {
    _ = pcmData
    _ = incomingFormat
  }

  func handlePlaybackControl(_ payload: PlaybackControlPayload) {
    _ = payload
  }

  func cancelResponse() {}
  func shutdown() {}
  func prepareForBackground() {}
  func restoreFromBackground() {}
  func currentRouteDescription() -> String { "mock" }
}

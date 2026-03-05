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
    let sentCountBeforeWake = await harness.transport.sentAudioCount()
    XCTAssertEqual(sentCountBeforeWake, 0)

    harness.orchestrator.triggerWakeForTesting()
    try await assertEventually {
      await harness.transport.connectCallCount() == 1
    }

    let whileConnecting = Data([0x03, 0x04])
    harness.orchestrator.processRealtimePCMFrame(whileConnecting, timestampMs: 20)
    let sentCountWhileConnecting = await harness.transport.sentAudioCount()
    XCTAssertEqual(sentCountWhileConnecting, 0)

    try await emitConnectedAndWaitForStreamingReadiness(harness)
    await harness.transport.clearSentAudioForTesting()
    let sentAudioCountBeforeConnectedFrame = await harness.transport.sentAudioCount()
    harness.orchestrator.processRealtimePCMFrame(Data([0x05, 0x06, 0x07]), timestampMs: 30)

    try await assertEventually(timeout: 5.0) {
      await harness.transport.sentAudioSince(sentAudioCountBeforeConnectedFrame).isEmpty == false
    }

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
    try await emitConnectedAndWaitForStreamingReadiness(harness)
    await harness.transport.clearSentAudioForTesting()

    for timestamp in 0..<64 {
      harness.orchestrator.processRealtimePCMFrame(Data([UInt8(timestamp % 255)]), timestampMs: Int64(timestamp))
    }
    let sentCount = try await waitForStableSentAudioCount(harness, minimum: 32)
    XCTAssertTrue(sentCount == 32 || sentCount == 33)

    let timestamps = await harness.transport.sentAudioTimestamps()
    XCTAssertEqual(timestamps.count, sentCount)
    XCTAssertEqual(Array(timestamps.suffix(31)), Array(33...63).map(Int64.init))

    nowMs = 2_500
    harness.orchestrator.handleAppDidEnterBackground()

    let expectedDroppedCount = 64 - sentCount
    let maxObservedDropCount: Int = try await assertEventuallyValue {
      let messages = await harness.transport.sentControls(messageType: "health.stats")
      let dropCounts = messages.compactMap { message -> Int? in
        guard case .number(let value) = message.payload["frame_drop_count"] else {
          return nil
        }
        return Int(value)
      }
      guard let maxValue = dropCounts.max() else { return nil }
      return maxValue >= expectedDroppedCount ? maxValue : nil
    }
    XCTAssertGreaterThanOrEqual(maxObservedDropCount, expectedDroppedCount)

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
    try await emitConnectedAndWaitForStreamingReadiness(harness)

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
      await harness.transport.lastSentControl(messageType: "health.stats")
    }
    guard case .number(let dropCountValue) = healthMessage.payload["frame_drop_count"] else {
      XCTFail("Missing frame_drop_count in health payload")
      return
    }
    XCTAssertEqual(Int(dropCountValue), 0)

    await harness.orchestrator.deactivate()
  }

  func testNetworkUnavailableDisconnectsAndReturnsSnapshotToIdleListening() async throws {
    let harness = makeHarness()
    var snapshots: [SessionOrchestrator.StatusSnapshot] = []
    harness.orchestrator.onStatusUpdated = { snapshots.append($0) }

    await harness.orchestrator.activate()
    harness.orchestrator.triggerWakeForTesting()

    try await assertEventually {
      await harness.transport.connectCallCount() == 1
    }

    await harness.transport.emit(.stateChanged(.connected))

    harness.orchestrator.setNetworkAvailable(false)

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

    try await assertEventually(timeout: 4.0) {
      await harness.transport.disconnectCallCount() == 1
    }
    try await assertEventually(timeout: 4.0) {
      await harness.transport.connectCallCount() == 2
    }
    let callEvents = await harness.transport.recordedCallEvents()
    let firstDisconnectCompleted = try XCTUnwrap(
      nthCallSequence(callEvents, name: "disconnect.completed", occurrence: 1)
    )
    let secondConnectStarted = try XCTUnwrap(
      nthCallSequence(callEvents, name: "connect.started", occurrence: 2)
    )
    XCTAssertLessThan(firstDisconnectCompleted, secondConnectStarted)

    await harness.orchestrator.deactivate()
  }

  func testHealthPongUpdatesRoundTripLatencyInHealthPayloadAndLogs() async throws {
    var nowMs: Int64 = 1_000
    var eventLines: [String] = []
    let harness = makeHarness(clock: { nowMs }, eventSink: { eventLines.append($0) })

    await harness.orchestrator.activate()

    try await assertEventually {
      await harness.transport.sentControlCount(messageType: "health.ping") >= 1
    }

    nowMs = 1_260
    await harness.transport.emit(.controlReceived(TransportControlMessage(type: "health.pong")))
    try await waitForControlReceivedEventLog(type: "health.pong") { eventLines }

    nowMs = 2_000
    harness.orchestrator.handleAppDidEnterBackground()

    try await assertEventually {
      guard
        let message = await harness.transport.lastSentControl(messageType: "health.stats"),
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

  func testOutboundControlMessagesBufferWhileDisconnectedAndReplayOnConnected() async throws {
    let harness = makeHarness()

    await harness.transport.setRejectControlsWithDisconnected(true)
    await harness.orchestrator.activate()

    harness.orchestrator.triggerWakeForTesting()
    try await assertEventually {
      await harness.transport.connectCallCount() == 1
    }

    let activateBeforeReconnect = await harness.transport.sentControlCount(messageType: "session.activate")
    let wakewordBeforeReconnect = await harness.transport.sentControlCount(messageType: "wakeword.detected")
    XCTAssertEqual(activateBeforeReconnect, 0)
    XCTAssertEqual(wakewordBeforeReconnect, 0)

    await harness.transport.setRejectControlsWithDisconnected(false)
    await harness.transport.emit(.stateChanged(.connected))

    try await assertEventually {
      let activateCount = await harness.transport.sentControlCount(messageType: "session.activate")
      let wakewordCount = await harness.transport.sentControlCount(messageType: "wakeword.detected")
      return activateCount >= 1 && wakewordCount == 1
    }

    await harness.orchestrator.deactivate()
  }

  func testDeactivateDuringDisconnectDelayPreventsReconnectOnNetworkRestore() async throws {
    let harness = makeHarness()
    await harness.transport.setDisconnectDelayNs(150_000_000)

    await harness.orchestrator.activate()
    harness.orchestrator.triggerWakeForTesting()
    try await assertEventually {
      await harness.transport.connectCallCount() == 1
    }
    await harness.transport.emit(.stateChanged(.connected))

    let deactivateTask = Task {
      await harness.orchestrator.deactivate()
    }
    try await Task.sleep(nanoseconds: 20_000_000)

    harness.orchestrator.setNetworkAvailable(false)
    harness.orchestrator.setNetworkAvailable(true)

    await deactivateTask.value

    try await assertEventually(timeout: 2.0) {
      await harness.transport.disconnectCallCount() == 1
    }
    let connectCount = await harness.transport.connectCallCount()
    XCTAssertEqual(connectCount, 1)
  }

  func testSessionRestartCountInHealthStatsPersistsAcrossReactivate() async throws {
    var nowMs: Int64 = 10_000
    let harness = makeHarness(clock: { nowMs })

    await harness.orchestrator.activate()
    nowMs = 10_500
    harness.orchestrator.handleAppDidEnterBackground()

    let firstHealth = try await assertEventuallyValue {
      await harness.transport.lastSentControl(messageType: "health.stats")
    }
    guard case .number(let firstRestartCount) = firstHealth.payload["session_restart_count"] else {
      XCTFail("Missing session_restart_count in initial health payload")
      return
    }
    XCTAssertEqual(Int(firstRestartCount), 0)

    await harness.orchestrator.deactivate()

    nowMs = 11_000
    await harness.orchestrator.activate()
    nowMs = 11_500
    harness.orchestrator.handleAppDidEnterBackground()

    try await assertEventually {
      guard
        let message = await harness.transport.lastSentControl(messageType: "health.stats"),
        case .number(let value) = message.payload["session_restart_count"]
      else {
        return false
      }
      return Int(value) == 1
    }

    await harness.orchestrator.deactivate()
  }

  func testHealthLoopStopsAfterDeactivateAndIgnoresLifecycleHealthTriggers() async throws {
    let harness = makeHarness()

    await harness.orchestrator.activate()

    try await assertEventually {
      await harness.transport.sentControlCount(messageType: "health.ping") >= 1
    }
    let pingCountBeforeDeactivate = await harness.transport.sentControlCount(messageType: "health.ping")
    let statsCountBeforeDeactivate = await harness.transport.sentControlCount(messageType: "health.stats")

    await harness.orchestrator.deactivate()
    harness.orchestrator.handleAppDidEnterBackground()
    harness.orchestrator.handleAppDidBecomeActive()

    try await Task.sleep(nanoseconds: 250_000_000)

    let pingCountAfterDeactivate = await harness.transport.sentControlCount(messageType: "health.ping")
    let statsCountAfterDeactivate = await harness.transport.sentControlCount(messageType: "health.stats")
    XCTAssertEqual(pingCountAfterDeactivate, pingCountBeforeDeactivate)
    XCTAssertEqual(statsCountAfterDeactivate, statsCountBeforeDeactivate)
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

    let harness = Harness(
      orchestrator: SessionOrchestrator(config: config, dependencies: deps),
      transport: transport
    )
    registerHarnessTeardown(harness)
    return harness
  }

  private func registerHarnessTeardown(_ harness: Harness) {
    addTeardownBlock { [orchestrator = harness.orchestrator] in
      await orchestrator.deactivate()
    }
  }

  private func emitConnectedAndWaitForStreamingReadiness(_ harness: Harness) async throws {
    var observedStreaming = false
    let previousHandler = harness.orchestrator.onStatusUpdated
    defer { harness.orchestrator.onStatusUpdated = previousHandler }
    harness.orchestrator.onStatusUpdated = { snapshot in
      previousHandler?(snapshot)
      if snapshot.sessionState == .streaming {
        observedStreaming = true
      }
    }

    await harness.transport.emit(.stateChanged(.connected))

    try await assertEventually {
      observedStreaming
    }
  }

  private func waitForControlReceivedEventLog(
    type expectedType: String,
    eventLinesProvider: @escaping @MainActor () -> [String]
  ) async throws {
    try await assertEventually {
      eventLinesProvider().contains { line in
        guard
          let data = line.data(using: .utf8),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          json["name"] as? String == "transport.control.received",
          let fields = json["fields"] as? [String: Any],
          fields["type"] as? String == expectedType
        else {
          return false
        }
        return true
      }
    }
  }

  private func waitForStableSentAudioCount(
    _ harness: Harness,
    minimum: Int,
    stableForNs: UInt64 = 200_000_000
  ) async throws -> Int {
    try await assertEventuallyValue(timeout: 4.0) {
      let first = await harness.transport.sentAudioCount()
      guard first >= minimum else { return nil }
      try? await Task.sleep(nanoseconds: stableForNs)
      let second = await harness.transport.sentAudioCount()
      return first == second ? second : nil
    }
  }

  private func nthCallSequence(
    _ events: [MockRealtimeTransport.CallEvent],
    name: String,
    occurrence: Int
  ) -> Int? {
    var matchCount = 0
    for event in events where event.name == name {
      matchCount += 1
      if matchCount == occurrence {
        return event.sequence
      }
    }
    return nil
  }

  private func assertEventually(
    timeout: TimeInterval = 3.0,
    pollIntervalNs: UInt64 = 20_000_000,
    condition: @escaping @MainActor () async -> Bool,
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
    timeout: TimeInterval = 3.0,
    pollIntervalNs: UInt64 = 20_000_000,
    value: @escaping @MainActor () async -> T?,
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

}

private actor MockRealtimeTransport: RealtimeTransport {
  struct CallEvent: Sendable, Equatable {
    let sequence: Int
    let name: String
  }

  private(set) var connectConfigs: [TransportConfig] = []
  private(set) var disconnectCount = 0
  private(set) var sentControls: [TransportControlMessage] = []
  private(set) var sentControlTypes: [String] = []
  private var disconnectDelayNs: UInt64 = 0
  private var sendAudioDelayNs: UInt64 = 0
  private var rejectControlsWithDisconnected = false
  private var callEvents: [CallEvent] = []
  private var nextCallSequence = 1

  struct SentAudio: Sendable {
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
    appendCallEvent("connect.started")
    connectConfigs.append(config)
    appendCallEvent("connect.completed")
  }

  func disconnect() async {
    appendCallEvent("disconnect.started")
    if disconnectDelayNs > 0 {
      try? await Task.sleep(nanoseconds: disconnectDelayNs)
    }
    disconnectCount += 1
    appendCallEvent("disconnect.completed")
  }

  func sendAudio(_ buffer: Data, timestampMs: Int64) async throws {
    if sendAudioDelayNs > 0 {
      try? await Task.sleep(nanoseconds: sendAudioDelayNs)
    }
    sentAudio.append(SentAudio(buffer: buffer, timestampMs: timestampMs))
  }

  func sendControl(_ message: TransportControlMessage) async throws {
    let messageType = message.type
    if rejectControlsWithDisconnected {
      throw TransportError.disconnected
    }
    sentControls.append(message)
    sentControlTypes.append(messageType)
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

  func sentControlCount(messageType: String) async -> Int {
    sentControlTypes.filter { $0 == messageType }.count
  }

  func lastSentControl(messageType: String) async -> TransportControlMessage? {
    guard let index = sentControlTypes.lastIndex(of: messageType) else { return nil }
    return sentControls[index]
  }

  func sentControls(messageType: String) async -> [TransportControlMessage] {
    var filtered: [TransportControlMessage] = []
    filtered.reserveCapacity(sentControls.count)
    for (index, type) in sentControlTypes.enumerated() where type == messageType {
      filtered.append(sentControls[index])
    }
    return filtered
  }

  func lastSentAudio() -> SentAudio? {
    sentAudio.last
  }

  func sentAudioTimestamps() -> [Int64] {
    sentAudio.map(\.timestampMs)
  }

  func sentAudioSince(_ index: Int) -> [SentAudio] {
    guard index >= 0 else { return sentAudio }
    guard index < sentAudio.count else { return [] }
    return Array(sentAudio[index...])
  }

  func clearSentAudioForTesting() {
    sentAudio.removeAll(keepingCapacity: false)
  }

  func setDisconnectDelayNs(_ value: UInt64) {
    disconnectDelayNs = value
  }

  func setSendAudioDelayNs(_ value: UInt64) {
    sendAudioDelayNs = value
  }

  func setRejectControlsWithDisconnected(_ value: Bool) {
    rejectControlsWithDisconnected = value
  }

  func recordedCallEvents() -> [CallEvent] {
    callEvents
  }

  private func appendCallEvent(_ name: String) {
    callEvents.append(CallEvent(sequence: nextCallSequence, name: name))
    nextCallSequence += 1
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

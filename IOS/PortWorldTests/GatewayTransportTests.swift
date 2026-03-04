import XCTest
@testable import PortWorld

final class GatewayTransportTests: XCTestCase {

  func testConnectEmitsConnectingThenConnectedWhenMockStateCallbacksInvoked() async throws {
    let webSocket = MockSessionWebSocketClient()
    let transport = GatewayTransport(webSocketClient: webSocket)

    var states: [TransportState] = []
    let eventsExpectation = expectation(description: "receives connecting and connected")
    eventsExpectation.expectedFulfillmentCount = 2

    let eventsTask = Task {
      for await event in transport.events {
        guard case .stateChanged(let state) = event else { continue }
        states.append(state)
        eventsExpectation.fulfill()
        if states.count == 2 { break }
      }
    }

    try await transport.connect(config: Self.makeConfig())
    await webSocket.emitState(.connecting)
    await webSocket.emitState(.connected)

    await fulfillment(of: [eventsExpectation], timeout: 1.0)
    eventsTask.cancel()

    XCTAssertEqual(states, [.connecting, .connected])
  }

  func testRawBinaryServerAudioFrameProducesAudioReceivedEvent() async throws {
    let webSocket = MockSessionWebSocketClient()
    let transport = GatewayTransport(webSocketClient: webSocket)

    var receivedPayload = Data()
    var receivedTimestamp: Int64 = 0
    let eventExpectation = expectation(description: "receives audioReceived")

    let eventsTask = Task {
      for await event in transport.events {
        guard case .audioReceived(let payload, let timestampMs) = event else { continue }
        receivedPayload = payload
        receivedTimestamp = timestampMs
        eventExpectation.fulfill()
        break
      }
    }

    try await transport.connect(config: Self.makeConfig())

    let expectedPayload = Data([0x10, 0x20, 0x30])
    let expectedTs: Int64 = 123_456
    let encoded = TransportBinaryFrameCodec.encode(
      TransportBinaryFrame(
        frameType: .serverAudio,
        timestampMs: expectedTs,
        payload: expectedPayload
      )
    )
    await webSocket.emitRaw(.binary(encoded))

    await fulfillment(of: [eventExpectation], timeout: 1.0)
    eventsTask.cancel()

    XCTAssertEqual(receivedPayload, expectedPayload)
    XCTAssertEqual(receivedTimestamp, expectedTs)
  }

  func testSendAudioWritesClientBinaryFrameTypeToRawSentData() async throws {
    let webSocket = MockSessionWebSocketClient()
    let transport = GatewayTransport(webSocketClient: webSocket)

    let payload = Data([0x01, 0x02, 0x03, 0x04])
    let timestamp: Int64 = 42
    try await transport.sendAudio(payload, timestampMs: timestamp)

    let sent = try XCTUnwrap(await webSocket.lastSentData())
    XCTAssertEqual(sent.first, TransportBinaryFraming.clientAudioTypeByte)

    let decoded = try TransportBinaryFrameCodec.decode(sent)
    XCTAssertEqual(decoded.frameType, .clientAudio)
    XCTAssertEqual(decoded.timestampMs, timestamp)
    XCTAssertEqual(decoded.payload, payload)
  }

  func testSendControlWritesTextPayloadContainingControlType() async throws {
    let webSocket = MockSessionWebSocketClient()
    let transport = GatewayTransport(webSocketClient: webSocket)
    try await transport.connect(config: Self.makeConfig())

    let controlType = "control.sleep_word_detected"
    try await transport.sendControl(
      TransportControlMessage(
        type: controlType,
        payload: ["source": .string("test")]
      )
    )

    let sentText = try XCTUnwrap(await webSocket.lastSentText())
    XCTAssertTrue(sentText.contains(controlType))

    let data = try XCTUnwrap(sentText.data(using: .utf8))
    let decoded = try WSMessageCodec.decodeRawEnvelope(from: data)
    XCTAssertEqual(decoded.type, controlType)
  }

  private static func makeConfig() -> TransportConfig {
    TransportConfig(
      endpoint: URL(string: "wss://example.invalid/ws")!,
      sessionId: "sess_test",
      audioFormat: AudioStreamFormat(sampleRate: 8_000, channels: 1, encoding: "pcm_s16le"),
      headers: ["Authorization": "Bearer test"]
    )
  }
}

private actor MockSessionWebSocketClient: SessionWebSocketClientProtocol {
  private var onStateChange: SessionWebSocketStateHandler?
  private var onRawMessage: SessionWebSocketRawMessageHandler?
  private(set) var sentTexts: [String] = []
  private(set) var sentData: [Data] = []

  func bindHandlers(
    onStateChange: SessionWebSocketStateHandler?,
    onMessage: SessionWebSocketMessageHandler?,
    onError: SessionWebSocketErrorHandler?,
    eventLogger: EventLoggerProtocol?
  ) {
    self.onStateChange = onStateChange
  }

  func bindRawMessageHandler(_ onRawMessage: SessionWebSocketRawMessageHandler?) {
    self.onRawMessage = onRawMessage
  }

  func setNetworkAvailable(_ isAvailable: Bool) {}

  func connect() {}

  func disconnect(closeCode: URLSessionWebSocketTask.CloseCode) {}

  func ensureConnected() {}

  func reconnectAttemptCount() -> Int { 0 }

  func sendText(_ text: String) async throws {
    sentTexts.append(text)
  }

  func sendData(_ data: Data) async throws {
    sentData.append(data)
  }

  func send<Payload: Codable>(type: WSOutboundType, sessionID: String, payload: Payload) async throws {}

  func emitState(_ state: SessionWebSocketConnectionState) {
    onStateChange?(state)
  }

  func emitRaw(_ message: SessionWebSocketRawMessage) {
    onRawMessage?(message)
  }

  func lastSentText() -> String? {
    sentTexts.last
  }

  func lastSentData() -> Data? {
    sentData.last
  }
}

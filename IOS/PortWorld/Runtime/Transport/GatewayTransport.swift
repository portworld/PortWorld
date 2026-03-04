import Foundation

actor GatewayTransport: RealtimeTransport {
  nonisolated let events: AsyncStream<TransportEvent>

  private let webSocketClient: SessionWebSocketClientProtocol
  private var eventsContinuation: AsyncStream<TransportEvent>.Continuation?
  private var transportConfig: TransportConfig?
  private var outboundSequence = 0

  init(webSocketClient: SessionWebSocketClientProtocol) {
    self.webSocketClient = webSocketClient

    var continuation: AsyncStream<TransportEvent>.Continuation?
    self.events = AsyncStream { streamContinuation in
      continuation = streamContinuation
    }
    self.eventsContinuation = continuation
    self.eventsContinuation?.onTermination = { [weak self] _ in
      Task {
        await self?.handleEventsTerminated()
      }
    }
  }

  init(
    webSocketURL: URL,
    requestHeaders: [String: String] = [:],
    urlSession: URLSession = .shared
  ) {
    self.webSocketClient = SessionWebSocketClient(
      url: webSocketURL,
      requestHeaders: requestHeaders,
      urlSession: urlSession,
      onStateChange: nil,
      onMessage: nil,
      onError: nil,
      eventLogger: nil
    )

    var continuation: AsyncStream<TransportEvent>.Continuation?
    self.events = AsyncStream { streamContinuation in
      continuation = streamContinuation
    }
    self.eventsContinuation = continuation
    self.eventsContinuation?.onTermination = { [weak self] _ in
      Task {
        await self?.handleEventsTerminated()
      }
    }
  }

  init(runtimeConfig: RuntimeConfig, urlSession: URLSession = .shared) {
    self.webSocketClient = SessionWebSocketClient(
      url: runtimeConfig.webSocketURL,
      requestHeaders: runtimeConfig.requestHeaders,
      urlSession: urlSession,
      onStateChange: nil,
      onMessage: nil,
      onError: nil,
      eventLogger: nil
    )

    var continuation: AsyncStream<TransportEvent>.Continuation?
    self.events = AsyncStream { streamContinuation in
      continuation = streamContinuation
    }
    self.eventsContinuation = continuation
    self.eventsContinuation?.onTermination = { [weak self] _ in
      Task {
        await self?.handleEventsTerminated()
      }
    }
  }

  func connect(config: TransportConfig) async throws {
    transportConfig = config

    await webSocketClient.bindHandlers(
      onStateChange: { [weak self] state in
        Task {
          await self?.handleWebSocketState(state)
        }
      },
      onMessage: nil,
      onError: { [weak self] error in
        Task {
          await self?.emit(.error(Self.mapWebSocketError(error)))
        }
      },
      eventLogger: nil
    )

    await webSocketClient.bindRawMessageHandler { [weak self] rawMessage in
      Task {
        await self?.handleRawMessage(rawMessage)
      }
    }

    await webSocketClient.connect()
  }

  func disconnect() async {
    await webSocketClient.bindRawMessageHandler(nil)
    await webSocketClient.bindHandlers(
      onStateChange: nil,
      onMessage: nil,
      onError: nil,
      eventLogger: nil
    )
    await webSocketClient.disconnect(closeCode: .normalClosure)
  }

  func sendAudio(_ buffer: Data, timestampMs: Int64) async throws {
    let frame = TransportBinaryFrame(
      frameType: .clientAudio,
      timestampMs: timestampMs,
      payload: buffer
    )
    let encoded = TransportBinaryFrameCodec.encode(frame)
    try await sendRawData(encoded)
  }

  func sendControl(_ message: TransportControlMessage) async throws {
    guard let transportConfig else {
      throw TransportError.disconnected
    }

    let payloadJSON = message.payload.mapValues(Self.convertToRuntimeJSON)
    let sequence = nextOutboundSequence()
    let envelope = WSMessageEnvelope(
      type: message.type,
      sessionID: transportConfig.sessionId,
      seq: sequence,
      payload: JSONValue.object(payloadJSON)
    )
    let encoded = try WSMessageCodec.encodeEnvelope(envelope)
    guard let text = String(data: encoded, encoding: .utf8) else {
      throw TransportError.protocolError
    }

    try await sendRawText(text)
  }

  private func handleRawMessage(_ rawMessage: SessionWebSocketRawMessage) async {
    switch rawMessage {
    case .text(let text):
      guard let data = text.data(using: .utf8) else {
        emit(.error(.protocolError))
        return
      }

      do {
        let envelope = try WSMessageCodec.decodeRawEnvelope(from: data)
        guard case .object(let payload) = envelope.payload else {
          emit(.error(.protocolError))
          return
        }

        let controlMessage = TransportControlMessage(
          type: envelope.type,
          payload: payload.mapValues(Self.convertToTransportJSON)
        )
        emit(.controlReceived(controlMessage))
      } catch {
        emit(.error(.protocolError))
      }

    case .binary(let data):
      do {
        let frame = try TransportBinaryFrameCodec.decode(data)
        guard frame.frameType == .serverAudio else {
          emit(.error(.protocolError))
          return
        }

        emit(.audioReceived(frame.payload, timestampMs: frame.timestampMs))
      } catch {
        emit(.error(.protocolError))
      }
    }
  }

  private func handleWebSocketState(_ state: SessionWebSocketConnectionState) {
    let mappedState: TransportState
    switch state {
    case .idle, .disconnected:
      mappedState = .disconnected
    case .connecting:
      mappedState = .connecting
    case .connected:
      mappedState = .connected
    case .reconnecting:
      mappedState = .reconnecting
    }

    emit(.stateChanged(mappedState))
  }

  private func sendRawText(_ text: String) async throws {
    do {
      try await webSocketClient.sendText(text)
    } catch {
      throw Self.mapWebSocketError(error)
    }
  }

  private func sendRawData(_ data: Data) async throws {
    do {
      try await webSocketClient.sendData(data)
    } catch {
      throw Self.mapWebSocketError(error)
    }
  }

  private func nextOutboundSequence() -> Int {
    defer { outboundSequence += 1 }
    return outboundSequence
  }

  private func emit(_ event: TransportEvent) {
    eventsContinuation?.yield(event)
  }

  private func handleEventsTerminated() {
    eventsContinuation = nil
  }

  private nonisolated static func mapWebSocketError(_ error: Error) -> TransportError {
    guard let webSocketError = error as? SessionWebSocketClientError else {
      return .unknown
    }

    switch webSocketError {
    case .notConnected:
      return .disconnected
    case .transport:
      return .connectionFailed
    case .decoding, .encoding:
      return .protocolError
    case .pingFailed:
      return .timeout
    }
  }

  private nonisolated static func convertToTransportJSON(_ value: JSONValue) -> TransportJSONValue {
    switch value {
    case .string(let string):
      return .string(string)
    case .number(let number):
      return .number(number)
    case .bool(let bool):
      return .bool(bool)
    case .object(let object):
      return .object(object.mapValues(convertToTransportJSON))
    case .array(let array):
      return .array(array.map(convertToTransportJSON))
    case .null:
      return .null
    }
  }

  private nonisolated static func convertToRuntimeJSON(_ value: TransportJSONValue) -> JSONValue {
    switch value {
    case .string(let string):
      return .string(string)
    case .number(let number):
      return .number(number)
    case .bool(let bool):
      return .bool(bool)
    case .object(let object):
      return .object(object.mapValues(convertToRuntimeJSON))
    case .array(let array):
      return .array(array.map(convertToRuntimeJSON))
    case .null:
      return .null
    }
  }
}

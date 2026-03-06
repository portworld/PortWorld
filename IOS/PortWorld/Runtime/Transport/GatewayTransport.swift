import Foundation
import OSLog

actor GatewayTransport: RealtimeTransport {
  nonisolated let events: AsyncStream<TransportEvent>
  nonisolated private static let probePayload = Data([0x50, 0x57, 0x50, 0x31])

  private let webSocketClient: SessionWebSocketClientProtocol
  private let logger = Logger(subsystem: "PortWorld", category: "GatewayTransport")
  private var eventsContinuation: AsyncStream<TransportEvent>.Continuation?
  private var transportConfig: TransportConfig?
  private var outboundSequence = 0
  private var audioSendAttemptCount = 0
  private var audioSendCount = 0
  private let forceTextAudioFallback: Bool

  init(webSocketClient: SessionWebSocketClientProtocol) {
    self.webSocketClient = webSocketClient
    self.forceTextAudioFallback = false

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
    self.forceTextAudioFallback = false

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
    self.forceTextAudioFallback = runtimeConfig.realtimeForceTextAudioFallback
    self.logger.warning(
      "gateway_transport_init force_text_audio_fallback=\(runtimeConfig.realtimeForceTextAudioFallback, privacy: .public)"
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
      onClose: { [weak self] closeInfo in
        Task {
          await self?.handleWebSocketClose(closeInfo)
        }
      },
      onError: { [weak self] error in
        Task {
          await self?.handleWebSocketError(error)
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
      onClose: nil,
      onError: nil,
      eventLogger: nil
    )
    await webSocketClient.disconnect(closeCode: .normalClosure)
  }

  func sendAudio(_ buffer: Data, timestampMs: Int64) async throws {
    audioSendAttemptCount += 1
    if audioSendAttemptCount <= 10 || audioSendAttemptCount % 100 == 0 {
      logger.warning(
        "gateway_send_audio_enter attempts=\(self.audioSendAttemptCount, privacy: .public) payload_bytes=\(buffer.count, privacy: .public) timestamp_ms=\(timestampMs, privacy: .public)"
      )
    }
    if forceTextAudioFallback {
      try await sendControl(
        TransportControlMessage(
          type: "client.audio",
          payload: [
            "audio_b64": .string(buffer.base64EncodedString()),
            "timestamp_ms": .number(Double(timestampMs))
          ]
        )
      )
      audioSendCount += 1
      if audioSendCount <= 10 || audioSendCount % 100 == 0 {
        logger.warning(
          "sent_audio_frame sent=\(self.audioSendCount, privacy: .public) attempts=\(self.audioSendAttemptCount, privacy: .public) payload_bytes=\(buffer.count, privacy: .public) encoded_bytes=\(buffer.count, privacy: .public) timestamp_ms=\(timestampMs, privacy: .public) mode=text_base64_fallback"
        )
      }
      return
    }
    let encodedFrame = await MainActor.run {
      let frame = TransportBinaryFrame(
        frameType: .clientAudio,
        timestampMs: timestampMs,
        payload: buffer
      )
      return TransportBinaryFrameCodec.encode(frame)
    }
    do {
      if audioSendAttemptCount == 1 || audioSendAttemptCount % 100 == 0 {
        logger.warning(
          "send_audio_frame attempts=\(self.audioSendAttemptCount, privacy: .public) payload_bytes=\(buffer.count, privacy: .public) encoded_bytes=\(encodedFrame.count, privacy: .public) timestamp_ms=\(timestampMs, privacy: .public) mode=binary"
        )
      } else {
        logger.debug(
          "send_audio_frame attempts=\(self.audioSendAttemptCount, privacy: .public) payload_bytes=\(buffer.count, privacy: .public) encoded_bytes=\(encodedFrame.count, privacy: .public) timestamp_ms=\(timestampMs, privacy: .public) mode=binary"
        )
      }
      try await sendRawData(encodedFrame)
      audioSendCount += 1
      if audioSendCount <= 10 || audioSendCount % 100 == 0 {
        logger.warning(
          "sent_audio_frame sent=\(self.audioSendCount, privacy: .public) attempts=\(self.audioSendAttemptCount, privacy: .public) payload_bytes=\(buffer.count, privacy: .public) encoded_bytes=\(encodedFrame.count, privacy: .public) timestamp_ms=\(timestampMs, privacy: .public) mode=binary"
        )
      }
    } catch {
      logger.error(
        "failed_audio_frame_send attempts=\(self.audioSendAttemptCount, privacy: .public) sent=\(self.audioSendCount, privacy: .public) payload_bytes=\(buffer.count, privacy: .public) encoded_bytes=\(encodedFrame.count, privacy: .public) timestamp_ms=\(timestampMs, privacy: .public) mode=binary detail=\(String(describing: error), privacy: .public)"
      )
      throw error
    }
  }

  func sendLiveAudio(_ buffer: Data, timestampMs: Int64) async throws {
    try await sendAudio(buffer, timestampMs: timestampMs)
  }

  func sendProbe(timestampMs: Int64) async throws {
    let probePayload = Self.probePayload
    let encodedFrame = await MainActor.run {
      let frame = TransportBinaryFrame(
        frameType: .clientProbe,
        timestampMs: timestampMs,
        payload: probePayload
      )
      return TransportBinaryFrameCodec.encode(frame)
    }
    logger.warning(
      "send_probe_frame payload_bytes=\(probePayload.count, privacy: .public) encoded_bytes=\(encodedFrame.count, privacy: .public) timestamp_ms=\(timestampMs, privacy: .public)"
    )
    try await sendRawData(encodedFrame)
  }

  func sendControl(_ message: TransportControlMessage) async throws {
    guard let transportConfig else {
      throw TransportError.disconnected
    }

    let payloadJSON = message.payload.mapValues(Self.convertToRuntimeJSON)
    let sequence = nextOutboundSequence()
    let envelope = await MainActor.run {
      WSMessageEnvelope(
        type: message.type,
        sessionID: transportConfig.sessionId,
        seq: sequence,
        payload: JSONValue.object(payloadJSON)
      )
    }
    let encoded = try await MainActor.run {
      try WSMessageCodec.encodeEnvelope(envelope)
    }
    guard let text = String(data: encoded, encoding: .utf8) else {
      throw TransportError.protocolError
    }

    try await sendRawText(text)
  }

  func diagnosticsSnapshot() async -> SessionWebSocketDiagnosticsSnapshot {
    await webSocketClient.diagnosticsSnapshot()
  }

  private func handleRawMessage(_ rawMessage: SessionWebSocketRawMessage) async {
    switch rawMessage {
    case .text(let text):
      guard let data = text.data(using: .utf8) else {
        emit(.error(.protocolError))
        return
      }

      do {
        let envelope = try await MainActor.run {
          try WSMessageCodec.decodeRawEnvelope(from: data)
        }
        guard case .object(let payload) = envelope.payload else {
          emit(.error(.protocolError))
          return
        }

        let controlMessage = await MainActor.run {
          TransportControlMessage(
            type: envelope.type,
            payload: payload.mapValues(Self.convertToTransportJSON)
          )
        }
        emit(.controlReceived(controlMessage))
      } catch {
        emit(.error(.protocolError))
      }

    case .binary(let data):
      do {
        let frame = try await MainActor.run {
          try TransportBinaryFrameCodec.decode(data)
        }
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

  private func handleWebSocketError(_ error: Error) {
    logger.error("websocket_error detail=\(error.localizedDescription, privacy: .public)")
    emit(.error(Self.mapWebSocketError(error)))
  }

  private func handleWebSocketClose(_ closeInfo: TransportSocketCloseInfo) {
    logger.warning(
      "websocket_closed connection_id=\(closeInfo.connectionID, privacy: .public) code=\(String(describing: closeInfo.code), privacy: .public) reason=\(closeInfo.reason ?? "-", privacy: .public)"
    )
    emit(.closed(closeInfo))
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

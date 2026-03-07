import Foundation

actor BackendSessionClient {
  struct EventEnvelope: Sendable {
    let id: Int
    let event: Event
  }

  enum ConnectionState: String, Sendable {
    case idle
    case connecting
    case connected
    case disconnected
  }

  enum Event: Sendable {
    case stateChanged(ConnectionState)
    case sessionReady
    case uplinkAcknowledged(RealtimeUplinkAckPayload)
    case serverAudio(Data)
    case playbackControl(PlaybackControlPayload)
    case closed
    case error(String)
  }

  private let webSocketURL: URL
  private let requestHeaders: [String: String]
  private let urlSession: URLSession

  private var webSocketTask: URLSessionWebSocketTask?
  private var receiveTask: Task<Void, Never>?
  private var eventHandler: (@Sendable (EventEnvelope) -> Void)?
  private var connectionState: ConnectionState = .idle
  private var outboundSequence = 0
  private var inboundEventSequence = 0
  private var sessionID: String?
  private var lastOutboundKind = "none"
  private var lastOutboundBytes = 0
  private var binarySendAttemptCount = 0
  private var binarySendSuccessCount = 0
  private var lastBinaryFirstByteHex = "none"
  private var loggedFirstServerAudioFrame = false
  private var inboundServerAudioFrameCount = 0
  private var inboundServerAudioBytes = 0
  private var lastInboundServerAudioBytes = 0
  private var lastPlaybackControlCommand = "none"
  private var isLocallyDisconnecting = false

  init(webSocketURL: URL, requestHeaders: [String: String], urlSession: URLSession = .shared) {
    self.webSocketURL = webSocketURL
    self.requestHeaders = requestHeaders
    self.urlSession = urlSession
  }

  func connect(sessionID: String) {
    disconnect(sendDeactivate: false, emitLifecycleEvents: false)
    self.sessionID = sessionID
    isLocallyDisconnecting = false
    loggedFirstServerAudioFrame = false
    inboundServerAudioFrameCount = 0
    inboundServerAudioBytes = 0
    lastInboundServerAudioBytes = 0
    lastPlaybackControlCommand = "none"

    var request = URLRequest(url: webSocketURL)
    for (name, value) in requestHeaders {
      request.setValue(value, forHTTPHeaderField: name)
    }

    let task = urlSession.webSocketTask(with: request)
    webSocketTask = task
    connectionState = .connecting
    yieldEvent(.stateChanged(.connecting))
    task.resume()
    connectionState = .connected
    yieldEvent(.stateChanged(.connected))
    receiveTask = Task { [weak self] in
      await self?.runReceiveLoop()
    }
  }

  func disconnect(sendDeactivate: Bool = true, emitLifecycleEvents: Bool = true) {
    let activeSessionID = sessionID
    let hadActiveConnection =
      webSocketTask != nil ||
      receiveTask != nil ||
      connectionState == .connecting ||
      connectionState == .connected

    if sendDeactivate, let sessionID {
      Task {
        try? await self.sendTextEnvelope(type: .sessionDeactivate, sessionID: sessionID)
      }
    }

    isLocallyDisconnecting = true
    receiveTask?.cancel()
    receiveTask = nil
    webSocketTask?.cancel(with: .normalClosure, reason: nil)
    webSocketTask = nil
    lastOutboundKind = "none"
    lastOutboundBytes = 0
    connectionState = .disconnected
    sessionID = nil

    guard emitLifecycleEvents, hadActiveConnection else { return }
    yieldEvent(.stateChanged(.disconnected), sessionID: activeSessionID)
    yieldEvent(.closed, sessionID: activeSessionID)
  }

  func connectionStateText() -> String {
    connectionState.rawValue
  }

  func sendSessionActivate() async throws {
    guard let sessionID else { return }
    let sequence = nextOutboundSequence()
    let text = try await MainActor.run {
      let payload = SessionActivatePayload(
        session: .init(type: "realtime"),
        audioFormat: .init(encoding: "pcm_s16le", channels: 1, sampleRate: 24_000)
      )
      return try Self.encodeEnvelopeText(
        type: .sessionActivate,
        sessionID: sessionID,
        sequence: sequence,
        payload: payload
      )
    }
    try await sendPreencodedText(text, kind: WSOutboundType.sessionActivate.rawValue)
  }

  func sendWakewordDetected(_ event: WakeWordDetectionEvent) async throws {
    guard let sessionID else { return }
    let sequence = nextOutboundSequence()
    let text = try await MainActor.run {
      let payload = WakewordDetectedPayload(
        wakePhrase: event.wakePhrase,
        engine: event.engine,
        confidence: event.confidence.map(Double.init)
      )
      return try Self.encodeEnvelopeText(
        type: .wakewordDetected,
        sessionID: sessionID,
        sequence: sequence,
        payload: payload
      )
    }
    try await sendPreencodedText(text, kind: WSOutboundType.wakewordDetected.rawValue)
  }

  func sendEndTurn() async throws {
    guard let sessionID else { return }
    try await sendTextEnvelope(type: .sessionEndTurn, sessionID: sessionID)
  }

  func sendAudioFrame(_ payload: Data, timestampMs: Int64) async throws {
    guard let webSocketTask else { throw SessionWebSocketClientError.notConnected }
    let encodedFrame = await MainActor.run {
      TransportBinaryFrameCodec.encode(
        TransportBinaryFrame(frameType: .clientAudio, timestampMs: timestampMs, payload: payload)
      )
    }
    binarySendAttemptCount += 1
    lastOutboundKind = "client_audio"
    lastOutboundBytes = encodedFrame.count
    lastBinaryFirstByteHex = encodedFrame.first.map { String(format: "0x%02x", $0) } ?? "none"
    try await sendWebSocketMessage(.data(encodedFrame), via: webSocketTask)
    binarySendSuccessCount += 1
  }

  func diagnosticsSnapshot() -> SessionWebSocketDiagnosticsSnapshot {
    SessionWebSocketDiagnosticsSnapshot(
      connectionID: connectionState == .idle ? 0 : 1,
      lastOutboundKind: lastOutboundKind,
      lastOutboundBytes: lastOutboundBytes,
      binarySendAttemptCount: binarySendAttemptCount,
      binarySendSuccessCount: binarySendSuccessCount,
      lastBinaryFirstByteHex: lastBinaryFirstByteHex,
      inboundServerAudioFrameCount: inboundServerAudioFrameCount,
      inboundServerAudioBytes: inboundServerAudioBytes,
      lastInboundServerAudioBytes: lastInboundServerAudioBytes,
      lastPlaybackControlCommand: lastPlaybackControlCommand
    )
  }

  func setEventHandler(_ handler: (@Sendable (EventEnvelope) -> Void)?) {
    eventHandler = handler
  }

  private func runReceiveLoop() async {
    while !Task.isCancelled {
      guard let webSocketTask else { return }

      do {
        let message = try await webSocketTask.receive()
        switch message {
        case .string(let text):
          guard let data = text.data(using: .utf8) else { continue }
          try await handleControlMessage(data)
        case .data(let data):
          try await handleBinaryMessage(data)
        @unknown default:
          yieldEvent(.error("Unsupported websocket message kind."))
        }
      } catch is CancellationError {
        return
      } catch {
        if shouldIgnoreReceiveLoopError(error) {
          return
        }
        yieldEvent(.error(error.localizedDescription))
        return
      }
    }
  }

  private func shouldIgnoreReceiveLoopError(_ error: Error) -> Bool {
    guard isLocallyDisconnecting else { return false }
    let normalized = error.localizedDescription.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    return normalized.contains("socket is not connected")
  }

  private func handleControlMessage(_ data: Data) async throws {
    let rawEnvelope = try await MainActor.run {
      try WSMessageCodec.decodeRawEnvelope(from: data)
    }
    debugLog("Inbound control type=\(rawEnvelope.type)")

    switch rawEnvelope.type {
    case WSInboundType.sessionState.rawValue:
      let envelope = try await MainActor.run {
        try JSONDecoder().decode(WSMessageEnvelope<SessionStatePayload>.self, from: data)
      }
      debugLog("Inbound session.state=\(envelope.payload.state.rawValue)")
      if envelope.payload.state == .active {
        yieldEvent(.sessionReady)
      }
    case "transport.uplink.ack":
      let envelope = try await MainActor.run {
        try JSONDecoder().decode(WSMessageEnvelope<RealtimeUplinkAckPayload>.self, from: data)
      }
      debugLog("Inbound transport.uplink.ack frames=\(envelope.payload.framesReceived) bytes=\(envelope.payload.bytesReceived)")
      yieldEvent(.uplinkAcknowledged(envelope.payload))
    case WSInboundType.assistantPlaybackControl.rawValue:
      let envelope = try await MainActor.run {
        try JSONDecoder().decode(WSMessageEnvelope<PlaybackControlPayload>.self, from: data)
      }
      lastPlaybackControlCommand = envelope.payload.command.rawValue
      debugLog("Inbound assistant.playback.control command=\(envelope.payload.command.rawValue)")
      yieldEvent(.playbackControl(envelope.payload))
    case WSInboundType.error.rawValue:
      let envelope = try await MainActor.run {
        try JSONDecoder().decode(WSMessageEnvelope<RuntimeErrorPayload>.self, from: data)
      }
      debugLog("Inbound error code=\(envelope.payload.code) message=\(envelope.payload.message)")
      yieldEvent(.error(envelope.payload.message))
    default:
      break
    }
  }

  private func handleBinaryMessage(_ data: Data) async throws {
    let frame = try await MainActor.run {
      try TransportBinaryFrameCodec.decode(data)
    }
    guard frame.frameType == .serverAudio else { return }
    inboundServerAudioFrameCount += 1
    inboundServerAudioBytes += frame.payload.count
    lastInboundServerAudioBytes = frame.payload.count
    if loggedFirstServerAudioFrame == false {
      loggedFirstServerAudioFrame = true
      debugLog("Inbound first server audio frame bytes=\(frame.payload.count) timestamp=\(frame.timestampMs)")
    }
    yieldEvent(.serverAudio(frame.payload))
  }

  private func sendTextEnvelope(type: WSOutboundType, sessionID: String) async throws {
    let sequence = nextOutboundSequence()
    let text = try await MainActor.run {
      try Self.encodeEnvelopeText(
        type: type,
        sessionID: sessionID,
        sequence: sequence,
        payload: EmptyPayload()
      )
    }
    try await sendPreencodedText(text, kind: type.rawValue)
  }

  private func sendPreencodedText(_ text: String, kind: String) async throws {
    guard let webSocketTask else { throw SessionWebSocketClientError.notConnected }
    let encoded = Data(text.utf8)
    lastOutboundKind = kind
    lastOutboundBytes = encoded.count
    try await sendWebSocketMessage(.string(text), via: webSocketTask)
  }

  private func nextOutboundSequence() -> Int {
    defer { outboundSequence += 1 }
    return outboundSequence
  }

  @MainActor
  private static func encodeEnvelopeText<Payload: Encodable>(
    type: WSOutboundType,
    sessionID: String,
    sequence: Int,
    payload: Payload
  ) throws -> String {
    let envelope = WSMessageEnvelope(
      type: type.rawValue,
      sessionID: sessionID,
      seq: sequence,
      payload: payload
    )
    let encoded = try WSMessageCodec.encodeEnvelope(envelope)
    guard let text = String(data: encoded, encoding: .utf8) else {
      throw SessionWebSocketClientError.encoding("Unable to encode websocket text envelope.")
    }
    return text
  }

  private func sendWebSocketMessage(
    _ message: URLSessionWebSocketTask.Message,
    via webSocketTask: URLSessionWebSocketTask
  ) async throws {
    try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
      webSocketTask.send(message) { error in
        if let error {
          continuation.resume(throwing: error)
        } else {
          continuation.resume(returning: ())
        }
      }
    }
  }

  private func yieldEvent(_ event: Event, sessionID: String? = nil) {
    inboundEventSequence += 1
    let envelope = EventEnvelope(id: inboundEventSequence, event: event)
    let resolvedSessionID = sessionID ?? self.sessionID ?? "-"
    debugLog("Yielding event#\(envelope.id) session=\(resolvedSessionID) \(describe(event))")
    eventHandler?(envelope)
  }

  private func describe(_ event: Event) -> String {
    switch event {
    case .stateChanged(let state):
      return "state_changed=\(state.rawValue)"
    case .sessionReady:
      return "session_ready"
    case .uplinkAcknowledged(let payload):
      return "uplink_ack frames=\(payload.framesReceived) bytes=\(payload.bytesReceived)"
    case .serverAudio(let data):
      return "server_audio bytes=\(data.count)"
    case .playbackControl(let payload):
      return "playback_control command=\(payload.command.rawValue)"
    case .closed:
      return "closed"
    case .error(let message):
      return "error=\(message)"
    }
  }

  private func debugLog(_ message: String) {
    #if DEBUG
      print("[BackendSessionClient] \(message)")
    #endif
  }
}

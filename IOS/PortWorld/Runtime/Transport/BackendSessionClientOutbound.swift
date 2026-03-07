// Outbound control and audio send path for the assistant backend session client.
import Foundation

extension BackendSessionClient {
  func sendSessionActivate() async throws {
    guard let sessionID else { return }
    let sequence = nextOutboundSequence()
    let payload = AssistantSessionActivatePayload(
      session: .init(type: "realtime"),
      audioFormat: .init(encoding: "pcm_s16le", channels: 1, sampleRate: 24_000)
    )
    let text = try Self.encodeEnvelopeText(
      type: .sessionActivate,
      sessionID: sessionID,
      sequence: sequence,
      payload: payload
    )
    try await sendPreencodedText(text, kind: AssistantWSOutboundType.sessionActivate.rawValue)
  }

  func sendWakewordDetected(_ event: WakeWordDetectionEvent) async throws {
    guard let sessionID else { return }
    let sequence = nextOutboundSequence()
    let payload = AssistantWakeWordDetectedPayload(
      wakePhrase: event.wakePhrase,
      engine: event.engine,
      confidence: event.confidence.map(Double.init)
    )
    let text = try Self.encodeEnvelopeText(
      type: .wakewordDetected,
      sessionID: sessionID,
      sequence: sequence,
      payload: payload
    )
    try await sendPreencodedText(text, kind: AssistantWSOutboundType.wakewordDetected.rawValue)
  }

  func sendEndTurn() async throws {
    guard let sessionID else { return }
    try await sendTextEnvelope(type: .sessionEndTurn, sessionID: sessionID)
  }

  func sendAudioFrame(_ payload: Data, timestampMs: Int64) async throws {
    guard let webSocketTask else { throw AssistantTransportError.notConnected }
    let encodedFrame = AssistantBinaryFrameCodec.encode(
      AssistantBinaryFrame(frameType: .clientAudio, timestampMs: timestampMs, payload: payload)
    )
    binarySendAttemptCount += 1
    lastOutboundKind = "client_audio"
    lastOutboundBytes = encodedFrame.count
    lastBinaryFirstByteHex = encodedFrame.first.map { String(format: "0x%02x", $0) } ?? "none"
    try await sendWebSocketMessage(.data(encodedFrame), via: webSocketTask)
    binarySendSuccessCount += 1
  }

  func sendTextEnvelope(type: AssistantWSOutboundType, sessionID: String) async throws {
    let sequence = nextOutboundSequence()
    let text = try Self.encodeEnvelopeText(
      type: type,
      sessionID: sessionID,
      sequence: sequence,
      payload: AssistantEmptyPayload()
    )
    try await sendPreencodedText(text, kind: type.rawValue)
  }

  func sendPreencodedText(_ text: String, kind: String) async throws {
    guard let webSocketTask else { throw AssistantTransportError.notConnected }
    let encoded = Data(text.utf8)
    lastOutboundKind = kind
    lastOutboundBytes = encoded.count
    try await sendWebSocketMessage(.string(text), via: webSocketTask)
  }

  func nextOutboundSequence() -> Int {
    defer { outboundSequence += 1 }
    return outboundSequence
  }

  static func encodeEnvelopeText<Payload: Encodable>(
    type: AssistantWSOutboundType,
    sessionID: String,
    sequence: Int,
    payload: Payload
  ) throws -> String {
    let envelope = AssistantWSControlEnvelope(
      type: type.rawValue,
      sessionID: sessionID,
      seq: sequence,
      payload: payload
    )
    let encoded = try AssistantWSMessageCodec.encodeEnvelope(envelope)
    guard let text = String(data: encoded, encoding: .utf8) else {
      throw AssistantTransportError.encoding("Unable to encode websocket text envelope.")
    }
    return text
  }

  func sendWebSocketMessage(
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
}

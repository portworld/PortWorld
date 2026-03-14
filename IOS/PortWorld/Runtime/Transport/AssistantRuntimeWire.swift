// Shared websocket and binary framing types for the active assistant runtime.
import Foundation

enum AssistantSessionState: String, Codable, Sendable {
  case idle
  case connecting
  case active
  case streaming
  case reconnecting
  case disconnecting
  case ended
  case failed
}

enum AssistantPlaybackControlCommand: String, Codable, Sendable {
  case startResponse = "start_response"
  case stopResponse = "stop_response"
  case cancelResponse = "cancel_response"
}

nonisolated struct AssistantPlaybackControlPayload: Codable, Sendable {
  let command: AssistantPlaybackControlCommand
  let responseID: String?

  init(command: AssistantPlaybackControlCommand, responseID: String? = nil) {
    self.command = command
    self.responseID = responseID
  }

  private enum CodingKeys: String, CodingKey {
    case command
    case responseID = "response_id"
  }
}

nonisolated struct AssistantRealtimeUplinkAckPayload: Codable, Sendable {
  let framesReceived: Int
  let bytesReceived: Int

  private enum CodingKeys: String, CodingKey {
    case framesReceived = "frames_received"
    case bytesReceived = "bytes_received"
  }
}

nonisolated struct AssistantRuntimeErrorPayload: Codable, Sendable {
  let code: String
  let retriable: Bool
  let message: String
}

nonisolated struct AssistantSessionStatePayload: Codable, Sendable {
  let state: AssistantSessionState
  let detail: String?
}

nonisolated struct AssistantProfileOnboardingReadyPayload: Codable, Sendable {
  let ready: Bool
  let missingRequiredFields: [String]

  private enum CodingKeys: String, CodingKey {
    case ready
    case missingRequiredFields = "missing_required_fields"
  }
}

enum AssistantSessionMode: String, Codable, Sendable {
  case standard = "default"
  case profileOnboarding = "profile_onboarding"
}

nonisolated struct AssistantEmptyPayload: Codable, Sendable {}

nonisolated struct AssistantSessionActivatePayload: Codable, Sendable {
  nonisolated struct SessionInfo: Codable, Sendable {
    let type: String
  }

  nonisolated struct ClientAudioFormat: Codable, Sendable {
    let encoding: String
    let channels: Int
    let sampleRate: Int

    private enum CodingKeys: String, CodingKey {
      case encoding
      case channels
      case sampleRate = "sample_rate"
    }
  }

  let session: SessionInfo
  let audioFormat: ClientAudioFormat
  let mode: AssistantSessionMode?
  let instructions: String?
  let autoStartResponse: Bool?

  private enum CodingKeys: String, CodingKey {
    case session
    case audioFormat = "audio_format"
    case mode
    case instructions
    case autoStartResponse = "auto_start_response"
  }
}

@preconcurrency nonisolated struct AssistantWSControlEnvelope<Payload> {
  let type: String
  let sessionID: String
  let seq: Int
  let tsMs: Int64
  let payload: Payload

  init(type: String, sessionID: String, seq: Int, tsMs: Int64 = Int64(Date().timeIntervalSince1970 * 1000), payload: Payload) {
    self.type = type
    self.sessionID = sessionID
    self.seq = seq
    self.tsMs = tsMs
    self.payload = payload
  }
}

extension AssistantWSControlEnvelope: Sendable where Payload: Sendable {}

private nonisolated struct AssistantWSRawEnvelopeHeader: Decodable, Sendable {
  let type: String
}

enum AssistantWSOutboundType: String, Codable, Sendable {
  case sessionActivate = "session.activate"
  case sessionDeactivate = "session.deactivate"
  case sessionEndTurn = "session.end_turn"
}

enum AssistantWSInboundType: String, Codable, Sendable {
  case sessionState = "session.state"
  case transportUplinkAcknowledged = "transport.uplink.ack"
  case assistantPlaybackControl = "assistant.playback.control"
  case onboardingProfileReady = "onboarding.profile_ready"
  case error
}

@preconcurrency enum AssistantWSMessageCodec {
  nonisolated static func decodeRawEnvelopeType(from data: Data, decoder: JSONDecoder = JSONDecoder()) throws -> String {
    try decoder.decode(AssistantWSRawEnvelopeHeader.self, from: data).type
  }

  nonisolated static func decodeEnvelope<Payload: Decodable>(
    _ payloadType: Payload.Type,
    from data: Data,
    decoder: JSONDecoder = JSONDecoder()
  ) throws -> AssistantWSControlEnvelope<Payload> {
    guard
      let jsonObject = try JSONSerialization.jsonObject(with: data) as? [String: Any],
      let type = jsonObject["type"] as? String,
      let sessionID = jsonObject["session_id"] as? String,
      let seq = jsonObject["seq"] as? Int,
      let tsValue = jsonObject["ts_ms"]
    else {
      throw AssistantTransportError.decoding("Malformed websocket control envelope.")
    }

    let tsMs: Int64
    if let integer = tsValue as? Int64 {
      tsMs = integer
    } else if let integer = tsValue as? Int {
      tsMs = Int64(integer)
    } else if let number = tsValue as? NSNumber {
      tsMs = number.int64Value
    } else {
      throw AssistantTransportError.decoding("Invalid websocket envelope timestamp.")
    }

    let payloadObject = jsonObject["payload"] ?? [:]
    guard JSONSerialization.isValidJSONObject(payloadObject) else {
      throw AssistantTransportError.decoding("Invalid websocket envelope payload object.")
    }

    let payloadData = try JSONSerialization.data(withJSONObject: payloadObject)
    let payload = try decoder.decode(payloadType, from: payloadData)
    return AssistantWSControlEnvelope(type: type, sessionID: sessionID, seq: seq, tsMs: tsMs, payload: payload)
  }

  nonisolated static func encodeEnvelope<Payload: Encodable>(
    _ envelope: AssistantWSControlEnvelope<Payload>,
    encoder: JSONEncoder = JSONEncoder()
  ) throws -> Data {
    let payloadData = try encoder.encode(envelope.payload)
    let payloadObject = try JSONSerialization.jsonObject(with: payloadData)
    let envelopeObject: [String: Any] = [
      "type": envelope.type,
      "session_id": envelope.sessionID,
      "seq": envelope.seq,
      "ts_ms": envelope.tsMs,
      "payload": payloadObject,
    ]
    return try JSONSerialization.data(withJSONObject: envelopeObject)
  }
}

enum AssistantBinaryFrameType: UInt8, Sendable {
  case clientAudio = 0x01
  case serverAudio = 0x02
}

nonisolated struct AssistantBinaryFrame: Sendable, Equatable {
  let frameType: AssistantBinaryFrameType
  let timestampMs: Int64
  let payload: Data
}

@preconcurrency enum AssistantBinaryFrameCodec {
  enum DecodeError: Error, LocalizedError, Sendable {
    case frameTooShort(expectedMinimum: Int, actual: Int)
    case unsupportedFrameType(UInt8)

    nonisolated var errorDescription: String? {
      switch self {
      case .frameTooShort(let expectedMinimum, let actual):
        return "Binary frame too short. Expected at least \(expectedMinimum) bytes, got \(actual)."
      case .unsupportedFrameType(let rawType):
        return "Unsupported binary frame type 0x\(String(format: "%02x", rawType))."
      }
    }
  }

  private nonisolated static let headerSize = 9

  nonisolated static func encode(_ frame: AssistantBinaryFrame) -> Data {
    var data = Data(capacity: headerSize + frame.payload.count)
    data.append(frame.frameType.rawValue)

    var timestampLE = UInt64(bitPattern: frame.timestampMs).littleEndian
    withUnsafeBytes(of: &timestampLE) { bytes in
      data.append(contentsOf: bytes)
    }

    data.append(frame.payload)
    return data
  }

  nonisolated static func decode(_ data: Data) throws -> AssistantBinaryFrame {
    guard data.count >= headerSize else {
      throw DecodeError.frameTooShort(expectedMinimum: headerSize, actual: data.count)
    }

    let headerStart = data.startIndex
    let timestampStart = data.index(after: headerStart)
    let payloadStart = data.index(headerStart, offsetBy: headerSize)

    let rawType = data[headerStart]
    guard let frameType = AssistantBinaryFrameType(rawValue: rawType) else {
      throw DecodeError.unsupportedFrameType(rawType)
    }

    var rawTimestamp: UInt64 = 0
    for (shift, byte) in data[timestampStart..<payloadStart].enumerated() {
      rawTimestamp |= UInt64(byte) << UInt64(shift * 8)
    }

    return AssistantBinaryFrame(
      frameType: frameType,
      timestampMs: Int64(bitPattern: rawTimestamp),
      payload: Data(data[payloadStart..<data.endIndex])
    )
  }
}

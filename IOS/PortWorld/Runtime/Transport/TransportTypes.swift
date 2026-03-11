import Foundation

enum TransportState: Sendable, Equatable {
  case disconnected
  case connecting
  case connected
  case reconnecting
}

struct AudioStreamFormat: Sendable, Equatable {
  let sampleRate: Int
  let channels: Int
  let encoding: String

  init(sampleRate: Int, channels: Int, encoding: String) {
    self.sampleRate = sampleRate
    self.channels = channels
    self.encoding = encoding
  }
}

struct TransportConfig: Sendable, Equatable {
  let endpoint: URL
  let sessionId: String
  let audioFormat: AudioStreamFormat
  let headers: [String: String]

  init(
    endpoint: URL,
    sessionId: String,
    audioFormat: AudioStreamFormat,
    headers: [String: String] = [:]
  ) {
    self.endpoint = endpoint
    self.sessionId = sessionId
    self.audioFormat = audioFormat
    self.headers = headers
  }
}

enum TransportError: Error, Sendable, Equatable {
  case connectionFailed
  case authError
  case timeout
  case protocolError
  case disconnected
  case unknown
}

enum TransportJSONValue: Codable, Sendable, Equatable {
  case string(String)
  case number(Double)
  case bool(Bool)
  case object([String: TransportJSONValue])
  case array([TransportJSONValue])
  case null

  init(from decoder: Decoder) throws {
    let container = try decoder.singleValueContainer()

    if container.decodeNil() {
      self = .null
      return
    }
    if let boolValue = try? container.decode(Bool.self) {
      self = .bool(boolValue)
      return
    }
    if let intValue = try? container.decode(Int.self) {
      self = .number(Double(intValue))
      return
    }
    if let doubleValue = try? container.decode(Double.self) {
      self = .number(doubleValue)
      return
    }
    if let stringValue = try? container.decode(String.self) {
      self = .string(stringValue)
      return
    }
    if let objectValue = try? container.decode([String: TransportJSONValue].self) {
      self = .object(objectValue)
      return
    }
    if let arrayValue = try? container.decode([TransportJSONValue].self) {
      self = .array(arrayValue)
      return
    }

    throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unsupported JSON value")
  }

  func encode(to encoder: Encoder) throws {
    var container = encoder.singleValueContainer()

    switch self {
    case .string(let value):
      try container.encode(value)
    case .number(let value):
      try container.encode(value)
    case .bool(let value):
      try container.encode(value)
    case .object(let value):
      try container.encode(value)
    case .array(let value):
      try container.encode(value)
    case .null:
      try container.encodeNil()
    }
  }
}

nonisolated struct TransportControlMessage: Codable, Sendable, Equatable {
  let type: String
  let payload: [String: TransportJSONValue]

  init(type: String, payload: [String: TransportJSONValue] = [:]) {
    self.type = type
    self.payload = payload
  }
}

enum TransportEvent: Sendable, Equatable {
  case audioReceived(Data, timestampMs: Int64)
  case controlReceived(TransportControlMessage)
  case stateChanged(TransportState)
  case closed(TransportSocketCloseInfo)
  case error(TransportError)
}

enum TransportFrameType: UInt8, Sendable {
  case clientAudio = 0x01
  case serverAudio = 0x02
}

enum TransportBinaryFraming {
  static let headerSize = 9
  static let clientAudioTypeByte: UInt8 = 0x01
  static let serverAudioTypeByte: UInt8 = 0x02
}

nonisolated struct TransportSocketCloseInfo: Sendable, Equatable {
  let connectionID: Int
  let code: Int?
  let reason: String?

  init(connectionID: Int, code: Int?, reason: String?) {
    self.connectionID = connectionID
    self.code = code
    self.reason = reason
  }
}

struct TransportBinaryFrame: Sendable, Equatable {
  let frameType: TransportFrameType
  let timestampMs: Int64
  let payload: Data

  init(frameType: TransportFrameType, timestampMs: Int64, payload: Data) {
    self.frameType = frameType
    self.timestampMs = timestampMs
    self.payload = payload
  }
}

enum TransportBinaryFrameCodec {
  enum DecodeError: Error, Sendable, Equatable {
    case frameTooShort(expectedMinimum: Int, actual: Int)
    case unsupportedFrameType(UInt8)
  }

  static func encode(_ frame: TransportBinaryFrame) -> Data {
    var data = Data(capacity: TransportBinaryFraming.headerSize + frame.payload.count)
    data.append(frame.frameType.rawValue)

    var timestampLE = UInt64(bitPattern: frame.timestampMs).littleEndian
    withUnsafeBytes(of: &timestampLE) { bytes in
      data.append(contentsOf: bytes)
    }

    data.append(frame.payload)
    return data
  }

  static func decode(_ data: Data) throws -> TransportBinaryFrame {
    guard data.count >= TransportBinaryFraming.headerSize else {
      throw DecodeError.frameTooShort(expectedMinimum: TransportBinaryFraming.headerSize, actual: data.count)
    }

    let headerStart = data.startIndex
    let timestampStart = data.index(after: headerStart)
    let payloadStart = data.index(headerStart, offsetBy: TransportBinaryFraming.headerSize)

    let rawType = data[headerStart]
    guard let frameType = TransportFrameType(rawValue: rawType) else {
      throw DecodeError.unsupportedFrameType(rawType)
    }

    var rawTimestamp: UInt64 = 0
    for (shift, byte) in data[timestampStart..<payloadStart].enumerated() {
      rawTimestamp |= UInt64(byte) << UInt64(shift * 8)
    }

    let payload = Data(data[payloadStart..<data.endIndex])
    return TransportBinaryFrame(
      frameType: frameType,
      timestampMs: Int64(bitPattern: rawTimestamp),
      payload: payload
    )
  }
}

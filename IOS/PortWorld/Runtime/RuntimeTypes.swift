import Foundation

public enum SessionState: String, Codable {
  case idle
  case connecting
  case active
  case reconnecting
  case ended
  case failed
}

public enum WakeState: String, Codable {
  case listening
  case triggered
}

public enum QueryState: String, Codable {
  case idle
  case recording
  case processingBundle = "processing_bundle"
  case uploading
  case failed
}

public enum PhotoUploadState: String, Codable {
  case idle
  case uploading
  case failed
}

public enum VideoBufferState: String, Codable {
  case idle
  case capturing
}

public enum AudioBufferState: String, Codable {
  case idle
  case capturing
}

public enum RuntimeState: String, Codable {
  case foregroundActive = "foreground_active"
  case backgroundBestEffort = "background_best_effort"
  case suspended
  case resumed
}

public struct VisionFrameRequest: Codable {
  public let sessionID: String
  public let tsMs: Int64
  public let frameID: String
  public let captureTsMs: Int64
  public let width: Int
  public let height: Int
  public let frameB64: String

  public init(
    sessionID: String,
    tsMs: Int64,
    frameID: String,
    captureTsMs: Int64,
    width: Int,
    height: Int,
    frameB64: String
  ) {
    self.sessionID = sessionID
    self.tsMs = tsMs
    self.frameID = frameID
    self.captureTsMs = captureTsMs
    self.width = width
    self.height = height
    self.frameB64 = frameB64
  }

  private enum CodingKeys: String, CodingKey {
    case sessionID = "session_id"
    case tsMs = "ts_ms"
    case frameID = "frame_id"
    case captureTsMs = "capture_ts_ms"
    case width
    case height
    case frameB64 = "frame_b64"
  }
}

public struct QueryMetadata: Codable {
  public let sessionID: String
  public let queryID: String
  public let wakeTsMs: Int64
  public let queryStartTsMs: Int64
  public let queryEndTsMs: Int64
  public let videoStartTsMs: Int64
  public let videoEndTsMs: Int64

  public init(
    sessionID: String,
    queryID: String,
    wakeTsMs: Int64,
    queryStartTsMs: Int64,
    queryEndTsMs: Int64,
    videoStartTsMs: Int64,
    videoEndTsMs: Int64
  ) {
    self.sessionID = sessionID
    self.queryID = queryID
    self.wakeTsMs = wakeTsMs
    self.queryStartTsMs = queryStartTsMs
    self.queryEndTsMs = queryEndTsMs
    self.videoStartTsMs = videoStartTsMs
    self.videoEndTsMs = videoEndTsMs
  }

  private enum CodingKeys: String, CodingKey {
    case sessionID = "session_id"
    case queryID = "query_id"
    case wakeTsMs = "wake_ts_ms"
    case queryStartTsMs = "query_start_ts_ms"
    case queryEndTsMs = "query_end_ts_ms"
    case videoStartTsMs = "video_start_ts_ms"
    case videoEndTsMs = "video_end_ts_ms"
  }
}

public struct AssistantAudioChunkPayload: Codable {
  public let responseID: String
  public let chunkID: String
  public let codec: String
  public let sampleRate: Int
  public let channels: Int
  public let durationMs: Int
  public let isLast: Bool
  public let bytesB64: String

  public init(
    responseID: String,
    chunkID: String,
    codec: String,
    sampleRate: Int,
    channels: Int,
    durationMs: Int,
    isLast: Bool,
    bytesB64: String
  ) {
    self.responseID = responseID
    self.chunkID = chunkID
    self.codec = codec
    self.sampleRate = sampleRate
    self.channels = channels
    self.durationMs = durationMs
    self.isLast = isLast
    self.bytesB64 = bytesB64
  }

  private enum CodingKeys: String, CodingKey {
    case responseID = "response_id"
    case chunkID = "chunk_id"
    case codec
    case sampleRate = "sample_rate"
    case channels
    case durationMs = "duration_ms"
    case isLast = "is_last"
    case bytesB64 = "bytes_b64"
  }
}

public enum PlaybackControlCommand: String, Codable {
  case startResponse = "start_response"
  case stopResponse = "stop_response"
  case cancelResponse = "cancel_response"
}

public struct PlaybackControlPayload: Codable {
  public let command: PlaybackControlCommand
  public let responseID: String?

  public init(command: PlaybackControlCommand, responseID: String? = nil) {
    self.command = command
    self.responseID = responseID
  }

  private enum CodingKeys: String, CodingKey {
    case command
    case responseID = "response_id"
  }
}

public struct AppEvent: Codable {
  public let name: String
  public let sessionID: String
  public let queryID: String?
  public let tsMs: Int64
  public let fields: [String: JSONValue]

  public init(
    name: String,
    sessionID: String,
    queryID: String? = nil,
    tsMs: Int64 = RuntimeClock.nowMs(),
    fields: [String: JSONValue] = [:]
  ) {
    self.name = name
    self.sessionID = sessionID
    self.queryID = queryID
    self.tsMs = tsMs
    self.fields = fields
  }

  private enum CodingKeys: String, CodingKey {
    case name
    case sessionID = "session_id"
    case queryID = "query_id"
    case tsMs = "ts_ms"
    case fields
  }
}

public struct HealthStatsPayload: Codable {
  public let wakeState: WakeState
  public let queryState: QueryState
  public let queriesCompleted: Int
  public let queryBundlesUploaded: Int
  public let queryBundlesFailed: Int
  public let photoUploadRateEffective: Double
  public let photosUploaded: Int
  public let photosFailed: Int
  public let videoBufferDurationMs: Int
  public let audioBufferDurationMs: Int
  public let wsReconnectAttempts: Int
  /// Number of full session restarts (deactivate+activate cycles).
  /// Unlike wsReconnectAttempts, this persists across session activations.
  public let sessionRestartCount: Int
  /// Estimated pending playback audio duration in milliseconds.
  public let pendingPlaybackDurationMs: Int
  /// Whether playback queue is under backpressure.
  public let playbackBackpressured: Bool
  public let playbackRoute: String

  public init(
    wakeState: WakeState,
    queryState: QueryState,
    queriesCompleted: Int,
    queryBundlesUploaded: Int,
    queryBundlesFailed: Int,
    photoUploadRateEffective: Double,
    photosUploaded: Int,
    photosFailed: Int,
    videoBufferDurationMs: Int,
    audioBufferDurationMs: Int,
    wsReconnectAttempts: Int,
    sessionRestartCount: Int,
    pendingPlaybackDurationMs: Int,
    playbackBackpressured: Bool,
    playbackRoute: String
  ) {
    self.wakeState = wakeState
    self.queryState = queryState
    self.queriesCompleted = queriesCompleted
    self.queryBundlesUploaded = queryBundlesUploaded
    self.queryBundlesFailed = queryBundlesFailed
    self.photoUploadRateEffective = photoUploadRateEffective
    self.photosUploaded = photosUploaded
    self.photosFailed = photosFailed
    self.videoBufferDurationMs = videoBufferDurationMs
    self.audioBufferDurationMs = audioBufferDurationMs
    self.wsReconnectAttempts = wsReconnectAttempts
    self.sessionRestartCount = sessionRestartCount
    self.pendingPlaybackDurationMs = pendingPlaybackDurationMs
    self.playbackBackpressured = playbackBackpressured
    self.playbackRoute = playbackRoute
  }

  private enum CodingKeys: String, CodingKey {
    case wakeState = "wake_state"
    case queryState = "query_state"
    case queriesCompleted = "queries_completed"
    case queryBundlesUploaded = "query_bundles_uploaded"
    case queryBundlesFailed = "query_bundles_failed"
    case photoUploadRateEffective = "photo_upload_rate_effective"
    case photosUploaded = "photos_uploaded"
    case photosFailed = "photos_failed"
    case videoBufferDurationMs = "video_buffer_duration_ms"
    case audioBufferDurationMs = "audio_buffer_duration_ms"
    case wsReconnectAttempts = "ws_reconnect_attempts"
    case sessionRestartCount = "session_restart_count"
    case pendingPlaybackDurationMs = "pending_playback_duration_ms"
    case playbackBackpressured = "playback_backpressured"
    case playbackRoute = "playback_route"
  }
}

public struct RuntimeErrorPayload: Codable {
  public let code: String
  public let retriable: Bool
  public let message: String

  public init(code: String, retriable: Bool, message: String) {
    self.code = code
    self.retriable = retriable
    self.message = message
  }
}

public struct SessionStatePayload: Codable {
  public let state: SessionState
  public let detail: String?

  public init(state: SessionState, detail: String? = nil) {
    self.state = state
    self.detail = detail
  }
}

public struct EmptyPayload: Codable {
  public init() {}
}

public struct WSMessageEnvelope<Payload: Codable>: Codable {
  public let type: String
  public let sessionID: String
  public let seq: Int
  public let tsMs: Int64
  public let payload: Payload

  public init(type: String, sessionID: String, seq: Int, tsMs: Int64 = RuntimeClock.nowMs(), payload: Payload) {
    self.type = type
    self.sessionID = sessionID
    self.seq = seq
    self.tsMs = tsMs
    self.payload = payload
  }

  private enum CodingKeys: String, CodingKey {
    case type
    case sessionID = "session_id"
    case seq
    case tsMs = "ts_ms"
    case payload
  }
}

public struct WSRawMessageEnvelope: Codable {
  public let type: String
  public let sessionID: String
  public let seq: Int
  public let tsMs: Int64
  public let payload: JSONValue

  private enum CodingKeys: String, CodingKey {
    case type
    case sessionID = "session_id"
    case seq
    case tsMs = "ts_ms"
    case payload
  }
}

public enum WSOutboundType: String, Codable {
  case sessionActivate = "session.activate"
  case sessionDeactivate = "session.deactivate"
  case wakewordDetected = "wakeword.detected"
  case queryStarted = "query.started"
  case queryEnded = "query.ended"
  case queryBundleUploaded = "query.bundle.uploaded"
  case healthPing = "health.ping"
  case healthStats = "health.stats"
  case error
}

public enum WSInboundType: String, Codable {
  case sessionState = "session.state"
  case healthPong = "health.pong"
  case assistantAudioChunk = "assistant.audio_chunk"
  case assistantPlaybackControl = "assistant.playback.control"
  case error
}

public enum WSInboundMessage {
  case sessionState(WSMessageEnvelope<SessionStatePayload>)
  case healthPong(WSMessageEnvelope<JSONValue>)
  case assistantAudioChunk(WSMessageEnvelope<AssistantAudioChunkPayload>)
  case assistantPlaybackControl(WSMessageEnvelope<PlaybackControlPayload>)
  case error(WSMessageEnvelope<RuntimeErrorPayload>)
  case unknown(WSRawMessageEnvelope)
}

public enum WSMessageCodec {
  public static func decodeInbound(from data: Data, decoder: JSONDecoder = JSONDecoder()) throws -> WSInboundMessage {
    let rawEnvelope = try decoder.decode(WSRawMessageEnvelope.self, from: data)

    switch rawEnvelope.type {
    case WSInboundType.sessionState.rawValue:
      return .sessionState(try decoder.decode(WSMessageEnvelope<SessionStatePayload>.self, from: data))
    case WSInboundType.healthPong.rawValue:
      return .healthPong(try decoder.decode(WSMessageEnvelope<JSONValue>.self, from: data))
    case WSInboundType.assistantAudioChunk.rawValue:
      return .assistantAudioChunk(try decoder.decode(WSMessageEnvelope<AssistantAudioChunkPayload>.self, from: data))
    case WSInboundType.assistantPlaybackControl.rawValue:
      return .assistantPlaybackControl(try decoder.decode(WSMessageEnvelope<PlaybackControlPayload>.self, from: data))
    case WSInboundType.error.rawValue:
      return .error(try decoder.decode(WSMessageEnvelope<RuntimeErrorPayload>.self, from: data))
    default:
      return .unknown(rawEnvelope)
    }
  }

  public static func decodeRawEnvelope(from data: Data, decoder: JSONDecoder = JSONDecoder()) throws -> WSRawMessageEnvelope {
    try decoder.decode(WSRawMessageEnvelope.self, from: data)
  }

  public static func encodeEnvelope<Payload: Encodable>(
    _ envelope: WSMessageEnvelope<Payload>,
    encoder: JSONEncoder = JSONEncoder()
  ) throws -> Data {
    try encoder.encode(envelope)
  }
}

public enum RuntimeClock {
  nonisolated(unsafe) public static func nowMs() -> Int64 {
    Int64((Date().timeIntervalSince1970 * 1000.0).rounded())
  }
}

public enum JSONValue: Codable, Equatable {
  case string(String)
  case number(Double)
  case bool(Bool)
  case object([String: JSONValue])
  case array([JSONValue])
  case null

  public init(from decoder: Decoder) throws {
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
    if let objectValue = try? container.decode([String: JSONValue].self) {
      self = .object(objectValue)
      return
    }
    if let arrayValue = try? container.decode([JSONValue].self) {
      self = .array(arrayValue)
      return
    }

    throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unsupported JSON value")
  }

  public func encode(to encoder: Encoder) throws {
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

public struct WakewordDetectedPayload: Codable {
  public let wakePhrase: String
  public let engine: String
  public let confidence: Double?

  public init(wakePhrase: String, engine: String, confidence: Double?) {
    self.wakePhrase = wakePhrase
    self.engine = engine
    self.confidence = confidence
  }

  private enum CodingKeys: String, CodingKey {
    case wakePhrase = "wake_phrase"
    case engine
    case confidence
  }
}

public struct QueryStartedPayload: Codable {
  public let queryID: String

  public init(queryID: String) {
    self.queryID = queryID
  }

  private enum CodingKeys: String, CodingKey {
    case queryID = "query_id"
  }
}

public struct QueryEndedPayload: Codable {
  public let queryID: String
  public let reason: String
  public let silenceTimeoutMs: Int
  public let durationMs: Int

  public init(queryID: String, reason: String, silenceTimeoutMs: Int, durationMs: Int) {
    self.queryID = queryID
    self.reason = reason
    self.silenceTimeoutMs = silenceTimeoutMs
    self.durationMs = durationMs
  }

  private enum CodingKeys: String, CodingKey {
    case queryID = "query_id"
    case reason
    case silenceTimeoutMs = "silence_timeout_ms"
    case durationMs = "duration_ms"
  }
}

public struct QueryBundleUploadedPayload: Codable {
  public let queryID: String
  public let uploadStatus: String
  public let audioBytes: Int64
  public let videoBytes: Int64

  public init(queryID: String, uploadStatus: String, audioBytes: Int64, videoBytes: Int64) {
    self.queryID = queryID
    self.uploadStatus = uploadStatus
    self.audioBytes = audioBytes
    self.videoBytes = videoBytes
  }

  private enum CodingKeys: String, CodingKey {
    case queryID = "query_id"
    case uploadStatus = "upload_status"
    case audioBytes = "audio_bytes"
    case videoBytes = "video_bytes"
  }
}

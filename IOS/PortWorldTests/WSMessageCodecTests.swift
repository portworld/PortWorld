import XCTest
@testable import PortWorld

final class WSMessageCodecTests: XCTestCase {

  private let encoder: JSONEncoder = {
    let e = JSONEncoder()
    e.outputFormatting = [.sortedKeys]
    return e
  }()

  private let decoder = JSONDecoder()

  // MARK: - Encoding — snake_case keys

  func testEncodeEnvelopeUsesSnakeCaseKeys() throws {
    let envelope = WSMessageEnvelope(
      type: WSOutboundType.sessionActivate.rawValue,
      sessionID: "sess_123",
      seq: 1,
      tsMs: 1709312400000,
      payload: EmptyPayload()
    )

    let data = try WSMessageCodec.encodeEnvelope(envelope, encoder: encoder)
    let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])

    // Snake_case keys present
    XCTAssertNotNil(json["session_id"])
    XCTAssertNotNil(json["ts_ms"])
    XCTAssertNotNil(json["type"])
    XCTAssertNotNil(json["seq"])

    // No camelCase leaks
    XCTAssertNil(json["sessionID"])
    XCTAssertNil(json["tsMs"])

    // Value correctness
    XCTAssertEqual(json["session_id"] as? String, "sess_123")
    XCTAssertEqual(json["ts_ms"] as? Int, 1709312400000)
    XCTAssertEqual(json["seq"] as? Int, 1)
    XCTAssertEqual(json["type"] as? String, "session.activate")
  }

  func testEncodeWakewordDetectedPayloadKeys() throws {
    let payload = WakewordDetectedPayload(wakePhrase: "hey mario", engine: "manual", confidence: 1.0)
    let envelope = WSMessageEnvelope(
      type: WSOutboundType.wakewordDetected.rawValue,
      sessionID: "sess_1",
      seq: 2,
      tsMs: 1000,
      payload: payload
    )

    let data = try WSMessageCodec.encodeEnvelope(envelope, encoder: encoder)
    let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    let payloadJson = try XCTUnwrap(json["payload"] as? [String: Any])

    XCTAssertEqual(payloadJson["wake_phrase"] as? String, "hey mario")
    XCTAssertEqual(payloadJson["engine"] as? String, "manual")
    XCTAssertEqual(payloadJson["confidence"] as? Double, 1.0)
  }

  func testEncodeQueryEndedPayloadKeys() throws {
    let payload = QueryEndedPayload(queryID: "q_1", reason: "silence_timeout", silenceTimeoutMs: 5000, durationMs: 7500)
    let envelope = WSMessageEnvelope(
      type: WSOutboundType.queryEnded.rawValue,
      sessionID: "sess_1",
      seq: 4,
      tsMs: 2000,
      payload: payload
    )

    let data = try WSMessageCodec.encodeEnvelope(envelope, encoder: encoder)
    let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    let payloadJson = try XCTUnwrap(json["payload"] as? [String: Any])

    XCTAssertEqual(payloadJson["query_id"] as? String, "q_1")
    XCTAssertEqual(payloadJson["silence_timeout_ms"] as? Int, 5000)
    XCTAssertEqual(payloadJson["duration_ms"] as? Int, 7500)
    XCTAssertNil(payloadJson["queryID"])
  }

  // MARK: - Decoding inbound messages

  func testDecodeSessionState() throws {
    let json = """
    {
      "type": "session.state",
      "session_id": "sess_abc",
      "seq": 1,
      "ts_ms": 2000,
      "payload": {
        "state": "active",
        "detail": "connected"
      }
    }
    """.data(using: .utf8)!

    let message = try WSMessageCodec.decodeInbound(from: json)

    guard case .sessionState(let envelope) = message else {
      XCTFail("Expected sessionState, got \(message)")
      return
    }

    XCTAssertEqual(envelope.sessionID, "sess_abc")
    XCTAssertEqual(envelope.seq, 1)
    XCTAssertEqual(envelope.tsMs, 2000)
    XCTAssertEqual(envelope.payload.state, .active)
    XCTAssertEqual(envelope.payload.detail, "connected")
  }

  func testDecodeAssistantAudioChunk() throws {
    let json = """
    {
      "type": "assistant.audio_chunk",
      "session_id": "sess_1",
      "seq": 5,
      "ts_ms": 3000,
      "payload": {
        "response_id": "resp_1",
        "chunk_id": "chunk_1",
        "codec": "pcm_s16le",
        "sample_rate": 8000,
        "channels": 1,
        "duration_ms": 200,
        "is_last": false,
        "bytes_b64": "AAAA"
      }
    }
    """.data(using: .utf8)!

    let message = try WSMessageCodec.decodeInbound(from: json)

    guard case .assistantAudioChunk(let envelope) = message else {
      XCTFail("Expected assistantAudioChunk, got \(message)")
      return
    }

    XCTAssertEqual(envelope.payload.responseID, "resp_1")
    XCTAssertEqual(envelope.payload.chunkID, "chunk_1")
    XCTAssertEqual(envelope.payload.codec, "pcm_s16le")
    XCTAssertEqual(envelope.payload.sampleRate, 8000)
    XCTAssertEqual(envelope.payload.channels, 1)
    XCTAssertEqual(envelope.payload.durationMs, 200)
    XCTAssertEqual(envelope.payload.isLast, false)
    XCTAssertEqual(envelope.payload.bytesB64, "AAAA")
  }

  func testDecodeAssistantAudioChunkIsLast() throws {
    let json = """
    {
      "type": "assistant.audio_chunk",
      "session_id": "sess_1",
      "seq": 6,
      "ts_ms": 3100,
      "payload": {
        "response_id": "resp_1",
        "chunk_id": "chunk_2",
        "codec": "pcm_s16le",
        "sample_rate": 8000,
        "channels": 1,
        "duration_ms": 220,
        "is_last": true,
        "bytes_b64": "AQID"
      }
    }
    """.data(using: .utf8)!

    let message = try WSMessageCodec.decodeInbound(from: json)

    guard case .assistantAudioChunk(let envelope) = message else {
      XCTFail("Expected assistantAudioChunk")
      return
    }

    XCTAssertTrue(envelope.payload.isLast)
  }

  func testDecodePlaybackControl() throws {
    let json = """
    {
      "type": "assistant.playback.control",
      "session_id": "sess_1",
      "seq": 7,
      "ts_ms": 4000,
      "payload": {
        "command": "start_response",
        "response_id": "resp_1"
      }
    }
    """.data(using: .utf8)!

    let message = try WSMessageCodec.decodeInbound(from: json)

    guard case .assistantPlaybackControl(let envelope) = message else {
      XCTFail("Expected assistantPlaybackControl, got \(message)")
      return
    }

    XCTAssertEqual(envelope.payload.command, .startResponse)
    XCTAssertEqual(envelope.payload.responseID, "resp_1")
  }

  func testDecodePlaybackControlStop() throws {
    let json = """
    {
      "type": "assistant.playback.control",
      "session_id": "sess_1",
      "seq": 8,
      "ts_ms": 4100,
      "payload": {
        "command": "stop_response",
        "response_id": "resp_1"
      }
    }
    """.data(using: .utf8)!

    let message = try WSMessageCodec.decodeInbound(from: json)

    guard case .assistantPlaybackControl(let envelope) = message else {
      XCTFail("Expected assistantPlaybackControl")
      return
    }

    XCTAssertEqual(envelope.payload.command, .stopResponse)
  }

  func testDecodeErrorMessage() throws {
    let json = """
    {
      "type": "error",
      "session_id": "sess_1",
      "seq": 10,
      "ts_ms": 5000,
      "payload": {
        "code": "RATE_LIMIT",
        "retriable": true,
        "message": "Too many requests"
      }
    }
    """.data(using: .utf8)!

    let message = try WSMessageCodec.decodeInbound(from: json)

    guard case .error(let envelope) = message else {
      XCTFail("Expected error, got \(message)")
      return
    }

    XCTAssertEqual(envelope.payload.code, "RATE_LIMIT")
    XCTAssertTrue(envelope.payload.retriable)
    XCTAssertEqual(envelope.payload.message, "Too many requests")
  }

  func testDecodeHealthPong() throws {
    let json = """
    {
      "type": "health.pong",
      "session_id": "sess_1",
      "seq": 3,
      "ts_ms": 6000,
      "payload": {}
    }
    """.data(using: .utf8)!

    let message = try WSMessageCodec.decodeInbound(from: json)

    guard case .healthPong(let envelope) = message else {
      XCTFail("Expected healthPong, got \(message)")
      return
    }

    XCTAssertEqual(envelope.sessionID, "sess_1")
    XCTAssertEqual(envelope.seq, 3)
    XCTAssertEqual(envelope.tsMs, 6000)
  }

  // MARK: - Unknown message type

  func testDecodeUnknownTypeReturnsUnknown() throws {
    let json = """
    {
      "type": "future.new_type",
      "session_id": "sess_1",
      "seq": 99,
      "ts_ms": 7000,
      "payload": {"some_field": "some_value"}
    }
    """.data(using: .utf8)!

    let message = try WSMessageCodec.decodeInbound(from: json)

    guard case .unknown(let raw) = message else {
      XCTFail("Expected unknown, got \(message)")
      return
    }

    XCTAssertEqual(raw.type, "future.new_type")
    XCTAssertEqual(raw.sessionID, "sess_1")
    XCTAssertEqual(raw.seq, 99)
  }

  // MARK: - Malformed input

  func testDecodeMalformedJsonThrows() {
    let data = "not valid json {{{".data(using: .utf8)!
    XCTAssertThrowsError(try WSMessageCodec.decodeInbound(from: data))
  }

  func testDecodeAssistantThinking() throws {
    let json = """
    {
      "type": "assistant.thinking",
      "session_id": "sess_1",
      "seq": 11,
      "ts_ms": 8000,
      "payload": {
        "status": "received",
        "query_id": "query_abc"
      }
    }
    """.data(using: .utf8)!

    let message = try WSMessageCodec.decodeInbound(from: json)

    guard case .assistantThinking(let envelope) = message else {
      XCTFail("Expected assistantThinking, got \(message)")
      return
    }

    XCTAssertEqual(envelope.sessionID, "sess_1")
    XCTAssertEqual(envelope.seq, 11)
    XCTAssertEqual(envelope.tsMs, 8000)
    XCTAssertEqual(envelope.payload.status, "received")
    XCTAssertEqual(envelope.payload.queryID, "query_abc")
  }

  func testDecodeMissingRequiredFieldThrows() {
    // Missing session_id
    let json = """
    {
      "type": "session.state",
      "seq": 1,
      "ts_ms": 1000,
      "payload": {"state": "active"}
    }
    """.data(using: .utf8)!

    XCTAssertThrowsError(try WSMessageCodec.decodeInbound(from: json))
  }

  func testDecodeEmptyDataThrows() {
    let data = Data()
    XCTAssertThrowsError(try WSMessageCodec.decodeInbound(from: data))
  }

  // MARK: - Round-trip encode → decode

  func testRoundTripSessionActivate() throws {
    let original = WSMessageEnvelope(
      type: WSOutboundType.sessionActivate.rawValue,
      sessionID: "sess_round",
      seq: 42,
      tsMs: 9999,
      payload: EmptyPayload()
    )

    let data = try WSMessageCodec.encodeEnvelope(original)
    let raw = try WSMessageCodec.decodeRawEnvelope(from: data)

    XCTAssertEqual(raw.type, "session.activate")
    XCTAssertEqual(raw.sessionID, "sess_round")
    XCTAssertEqual(raw.seq, 42)
    XCTAssertEqual(raw.tsMs, 9999)
  }

  func testRoundTripQueryEndedPayload() throws {
    let payload = QueryEndedPayload(
      queryID: "q_1",
      reason: "silence_timeout",
      silenceTimeoutMs: 5000,
      durationMs: 7500
    )
    let original = WSMessageEnvelope(
      type: WSOutboundType.queryEnded.rawValue,
      sessionID: "sess_rt",
      seq: 3,
      tsMs: 12345,
      payload: payload
    )

    let data = try WSMessageCodec.encodeEnvelope(original)
    let decoded = try decoder.decode(WSMessageEnvelope<QueryEndedPayload>.self, from: data)

    XCTAssertEqual(decoded.type, original.type)
    XCTAssertEqual(decoded.sessionID, original.sessionID)
    XCTAssertEqual(decoded.seq, original.seq)
    XCTAssertEqual(decoded.tsMs, original.tsMs)
    XCTAssertEqual(decoded.payload.queryID, "q_1")
    XCTAssertEqual(decoded.payload.reason, "silence_timeout")
    XCTAssertEqual(decoded.payload.silenceTimeoutMs, 5000)
    XCTAssertEqual(decoded.payload.durationMs, 7500)
  }

  func testRoundTripQueryBundleUploadedPayload() throws {
    let payload = QueryBundleUploadedPayload(
      queryID: "q_2",
      uploadStatus: "success",
      audioBytes: 16000,
      videoBytes: 256000
    )
    let original = WSMessageEnvelope(
      type: WSOutboundType.queryBundleUploaded.rawValue,
      sessionID: "sess_rt2",
      seq: 10,
      tsMs: 99999,
      payload: payload
    )

    let data = try WSMessageCodec.encodeEnvelope(original)
    let decoded = try decoder.decode(WSMessageEnvelope<QueryBundleUploadedPayload>.self, from: data)

    XCTAssertEqual(decoded.payload.queryID, "q_2")
    XCTAssertEqual(decoded.payload.uploadStatus, "success")
    XCTAssertEqual(decoded.payload.audioBytes, 16000)
    XCTAssertEqual(decoded.payload.videoBytes, 256000)
  }

  // MARK: - VisionFrameRequest CodingKeys

  func testVisionFrameRequestUsesSnakeCaseKeys() throws {
    let request = VisionFrameRequest(
      sessionID: "sess_v",
      tsMs: 5000,
      frameID: "f_1",
      captureTsMs: 4999,
      width: 640,
      height: 480,
      frameB64: "aGVsbG8="
    )

    let data = try encoder.encode(request)
    let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])

    XCTAssertNotNil(json["session_id"])
    XCTAssertNotNil(json["ts_ms"])
    XCTAssertNotNil(json["frame_id"])
    XCTAssertNotNil(json["capture_ts_ms"])
    XCTAssertNotNil(json["frame_b64"])

    // No camelCase
    XCTAssertNil(json["sessionID"])
    XCTAssertNil(json["frameB64"])
    XCTAssertNil(json["captureTsMs"])
  }

  func testQueryMetadataUsesSnakeCaseDeviceAndVersionKeys() throws {
    let payload = QueryMetadata(
      sessionID: "sess_meta",
      queryID: "query_meta",
      wakeTsMs: 1,
      queryStartTsMs: 2,
      queryEndTsMs: 3,
      videoStartTsMs: 4,
      videoEndTsMs: 5,
      appVersion: "1.2.3",
      deviceModel: "iPhone17,1",
      osVersion: "17.4",
      triggerSource: .voice
    )

    let data = try encoder.encode(payload)
    let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])

    XCTAssertEqual(json["app_version"] as? String, "1.2.3")
    XCTAssertEqual(json["device_model"] as? String, "iPhone17,1")
    XCTAssertEqual(json["os_version"] as? String, "17.4")
    XCTAssertEqual(json["trigger_source"] as? String, "voice")
    XCTAssertNil(json["appVersion"])
    XCTAssertNil(json["deviceModel"])
    XCTAssertNil(json["osVersion"])
    XCTAssertNil(json["triggerSource"])
  }

  func testHealthStatsPayloadUsesSnakeCaseFrameDropAndDeviceVersionKeys() throws {
    let payload = HealthStatsPayload(
      wakeState: .listening,
      queryState: .idle,
      queriesCompleted: 1,
      queryBundlesUploaded: 2,
      queryBundlesFailed: 0,
      photoUploadRateEffective: 0.5,
      photosUploaded: 3,
      photosFailed: 1,
      videoBufferDurationMs: 1000,
      audioBufferDurationMs: 800,
      wsReconnectAttempts: 2,
      wsRoundTripLatencyMs: 123,
      frameDropCount: 7,
      frameDropRate: 0.14,
      sessionRestartCount: 1,
      pendingPlaybackDurationMs: 250,
      playbackBackpressured: false,
      playbackRoute: "speaker",
      appVersion: "1.2.3",
      deviceModel: "iPhone17,1",
      osVersion: "17.4"
    )

    let data = try encoder.encode(payload)
    let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])

    XCTAssertEqual(json["frame_drop_count"] as? Int, 7)
    XCTAssertEqual(json["frame_drop_rate"] as? Double, 0.14)
    XCTAssertEqual(json["ws_round_trip_latency_ms"] as? Int, 123)
    XCTAssertEqual(json["app_version"] as? String, "1.2.3")
    XCTAssertEqual(json["device_model"] as? String, "iPhone17,1")
    XCTAssertEqual(json["os_version"] as? String, "17.4")
    XCTAssertNil(json["frameDropCount"])
    XCTAssertNil(json["frameDropRate"])
    XCTAssertNil(json["wsRoundTripLatencyMs"])
    XCTAssertNil(json["appVersion"])
    XCTAssertNil(json["deviceModel"])
    XCTAssertNil(json["osVersion"])
  }
}

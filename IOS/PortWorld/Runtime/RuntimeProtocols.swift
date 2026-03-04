import Foundation
import UIKit

// Transport/media services are intentionally non-UI surfaces.
// SessionOrchestrator is @MainActor and explicitly hops into these services.
// Keep @MainActor off these protocol contracts to preserve background execution.
typealias SessionWebSocketStateHandler = (SessionWebSocketConnectionState) -> Void
typealias SessionWebSocketMessageHandler = (WSInboundMessage) -> Void
typealias SessionWebSocketRawMessageHandler = (SessionWebSocketRawMessage) -> Void
typealias SessionWebSocketErrorHandler = (SessionWebSocketClientError) -> Void
typealias VisionFrameSessionIDProvider = () -> String?
typealias VisionFrameUploadResultHandler = (VisionFrameUploadResult) -> Void

enum SessionWebSocketRawMessage: Sendable {
  case text(String)
  case binary(Data)
}

/// Actor-isolated transport contract for the websocket control plane.
protocol SessionWebSocketClientProtocol: Actor {
  func bindHandlers(
    onStateChange: SessionWebSocketStateHandler?,
    onMessage: SessionWebSocketMessageHandler?,
    onError: SessionWebSocketErrorHandler?,
    eventLogger: EventLoggerProtocol?
  )
  func bindRawMessageHandler(_ onRawMessage: SessionWebSocketRawMessageHandler?)
  func setNetworkAvailable(_ isAvailable: Bool)
  func connect()
  func disconnect(closeCode: URLSessionWebSocketTask.CloseCode)
  func ensureConnected()
  func reconnectAttemptCount() -> Int
  func sendText(_ text: String) async throws
  func sendData(_ data: Data) async throws
  func send<Payload: Codable>(type: WSOutboundType, sessionID: String, payload: Payload) async throws
}

extension SessionWebSocketClientProtocol {
  func bindHandlers(
    onStateChange: SessionWebSocketStateHandler?,
    onMessage: SessionWebSocketMessageHandler?,
    onRawMessage: SessionWebSocketRawMessageHandler?,
    onError: SessionWebSocketErrorHandler?,
    eventLogger: EventLoggerProtocol?
  ) {
    bindHandlers(
      onStateChange: onStateChange,
      onMessage: onMessage,
      onError: onError,
      eventLogger: eventLogger
    )
    bindRawMessageHandler(onRawMessage)
  }

  func bindRawMessageHandler(_ onRawMessage: SessionWebSocketRawMessageHandler?) {
    // Default no-op preserves compatibility for existing typed-only websocket clients.
  }

  func setNetworkAvailable(_ isAvailable: Bool) {
    // Default no-op preserves compatibility for clients that do not gate reconnects.
  }
}

/// Actor-isolated photo uploader contract.
protocol VisionFrameUploaderProtocol: Actor {
  func bindHandlers(
    sessionIDProvider: @escaping VisionFrameSessionIDProvider,
    onUploadResult: VisionFrameUploadResultHandler?
  )
  func start()
  func stop()
  func consumeFrameDropCount() -> Int
  func submitLatestFrame(_ image: UIImage, captureTimestampMs: Int64)
}

/// Actor-isolated video buffer contract.
protocol RollingVideoBufferProtocol: Actor {
  var bufferedDurationMs: Int64 { get }
  func append(frame: UIImage, timestampMs: Int64)
  func clear()
  func exportInterval(
    startTimestampMs: Int64,
    endTimestampMs: Int64,
    outputURL: URL?,
    bitrate: Int
  ) async throws -> RollingVideoExportResult
}

extension RollingVideoBufferProtocol {
  func exportInterval(
    startTimestampMs: Int64,
    endTimestampMs: Int64
  ) async throws -> RollingVideoExportResult {
    try await exportInterval(
      startTimestampMs: startTimestampMs,
      endTimestampMs: endTimestampMs,
      outputURL: nil,
      bitrate: 2_000_000
    )
  }
}

/// Bundle upload is network-bound work and should stay off the main actor.
protocol QueryBundleBuilderProtocol: AnyObject {
  func uploadQueryBundle(
    metadata: QueryMetadata,
    audioFileURL: URL,
    videoFileURL: URL
  ) async throws -> QueryBundleUploadResult
}

typealias WakeWordEngineProtocol = WakeWordEngine

@MainActor
protocol EventLoggerProtocol: AnyObject {
  func log(
    name: String,
    sessionID: String,
    queryID: String?,
    fields: [String: JSONValue],
    tsMs: Int64?
  )
  func exportCurrentLog() -> URL
}

extension EventLoggerProtocol {
  func log(
    name: String,
    sessionID: String,
    queryID: String? = nil,
    fields: [String: JSONValue] = [:]
  ) {
    log(name: name, sessionID: sessionID, queryID: queryID, fields: fields, tsMs: nil)
  }

  func exportCurrentLog() -> URL {
    URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
  }
}

@MainActor
protocol AssistantPlaybackEngineProtocol: AnyObject {
  var onRouteChanged: ((String) -> Void)? { get set }
  var onRouteIssue: ((String) -> Void)? { get set }
  var pendingBufferCount: Int { get }
  var pendingBufferDurationMs: Double { get }
  var isBackpressured: Bool { get }
  func hasActivePendingPlayback() -> Bool

  func appendChunk(_ payload: AssistantAudioChunkPayload) throws
  func appendPCMData(_ pcmData: Data, format incomingFormat: AssistantAudioFormat) throws
  func handlePlaybackControl(_ payload: PlaybackControlPayload)
  func cancelResponse()
  func shutdown()
  func prepareForBackground()
  func restoreFromBackground()
  func currentRouteDescription() -> String
}

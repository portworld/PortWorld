import Foundation
import UIKit

// Transport/media services are intentionally non-UI surfaces.
// SessionOrchestrator is @MainActor and explicitly hops into these services.
// Keep @MainActor off these protocol contracts to preserve background execution.
typealias SessionWebSocketStateHandler = (SessionWebSocketConnectionState) -> Void
typealias SessionWebSocketMessageHandler = (WSInboundMessage) -> Void
typealias SessionWebSocketErrorHandler = (SessionWebSocketClientError) -> Void
typealias VisionFrameSessionIDProvider = () -> String?
typealias VisionFrameUploadResultHandler = (VisionFrameUploadResult) -> Void

/// Actor-isolated transport contract for the websocket control plane.
protocol SessionWebSocketClientProtocol: Actor {
  func bindHandlers(
    onStateChange: SessionWebSocketStateHandler?,
    onMessage: SessionWebSocketMessageHandler?,
    onError: SessionWebSocketErrorHandler?,
    eventLogger: EventLoggerProtocol?
  )
  func connect()
  func disconnect(closeCode: URLSessionWebSocketTask.CloseCode)
  func ensureConnected()
  func reconnectAttemptCount() -> Int
  func send<Payload: Codable>(type: WSOutboundType, sessionID: String, payload: Payload) async throws
}

/// Queue-isolated photo uploader contract.
/// Implementations should perform network work off-main and surface results via callback.
protocol VisionFrameUploaderProtocol: AnyObject {
  func bindHandlers(
    sessionIDProvider: @escaping VisionFrameSessionIDProvider,
    onUploadResult: VisionFrameUploadResultHandler?
  )
  func start()
  func stop()
  func submitLatestFrame(_ image: UIImage, captureTimestampMs: Int64)
}

/// Video buffer implementations stay thread-isolated off-main.
protocol RollingVideoBufferProtocol: AnyObject {
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
}

@MainActor
protocol AssistantPlaybackEngineProtocol: AnyObject {
  var onRouteChanged: ((String) -> Void)? { get set }
  var onRouteIssue: ((String) -> Void)? { get set }
  var pendingBufferCount: Int { get }
  var pendingBufferDurationMs: Double { get }
  var isBackpressured: Bool { get }

  func appendChunk(_ payload: AssistantAudioChunkPayload) throws
  func appendPCMData(_ pcmData: Data, format incomingFormat: AssistantAudioFormat) throws
  func handlePlaybackControl(_ payload: PlaybackControlPayload)
  func cancelResponse()
  func shutdown()
  func prepareForBackground()
  func restoreFromBackground()
  func currentRouteDescription() -> String
}

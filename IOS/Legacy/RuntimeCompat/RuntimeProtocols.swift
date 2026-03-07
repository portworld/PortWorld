import Foundation

// Shared transport/media services are intentionally non-UI surfaces.
// Legacy assistant-runtime-only contracts live under IOS/Legacy/AssistantRuntime.
// Keep @MainActor off these protocol contracts to preserve background execution.
typealias SessionWebSocketStateHandler = (SessionWebSocketConnectionState) -> Void
typealias SessionWebSocketMessageHandler = (WSInboundMessage) -> Void
typealias SessionWebSocketRawMessageHandler = (SessionWebSocketRawMessage) -> Void
typealias SessionWebSocketCloseHandler = (TransportSocketCloseInfo) -> Void
typealias SessionWebSocketErrorHandler = (SessionWebSocketClientError) -> Void

enum SessionWebSocketRawMessage: Sendable {
  case text(String)
  case binary(Data)
}

struct SessionWebSocketDiagnosticsSnapshot: Sendable, Equatable {
  let connectionID: Int
  let lastOutboundKind: String
  let lastOutboundBytes: Int
  let binarySendAttemptCount: Int
  let binarySendSuccessCount: Int
  let lastBinaryFirstByteHex: String
  let inboundServerAudioFrameCount: Int
  let inboundServerAudioBytes: Int
  let lastInboundServerAudioBytes: Int
  let lastPlaybackControlCommand: String
}

/// Actor-isolated transport contract for the websocket control plane.
protocol SessionWebSocketClientProtocol: Actor {
  func bindHandlers(
    onStateChange: SessionWebSocketStateHandler?,
    onMessage: SessionWebSocketMessageHandler?,
    onClose: SessionWebSocketCloseHandler?,
    onError: SessionWebSocketErrorHandler?,
    eventLogger: EventLoggerProtocol?
  )
  func bindRawMessageHandler(_ onRawMessage: SessionWebSocketRawMessageHandler?)
  func setNetworkAvailable(_ isAvailable: Bool)
  func connect()
  func disconnect(closeCode: URLSessionWebSocketTask.CloseCode)
  func ensureConnected()
  func reconnectAttemptCount() -> Int
  func diagnosticsSnapshot() -> SessionWebSocketDiagnosticsSnapshot
  func sendText(_ text: String) async throws
  func sendData(_ data: Data) async throws
  func send<Payload: Codable>(type: WSOutboundType, sessionID: String, payload: Payload) async throws
}

extension SessionWebSocketClientProtocol {
  func bindHandlers(
    onStateChange: SessionWebSocketStateHandler?,
    onMessage: SessionWebSocketMessageHandler?,
    onRawMessage: SessionWebSocketRawMessageHandler?,
    onClose: SessionWebSocketCloseHandler?,
    onError: SessionWebSocketErrorHandler?,
    eventLogger: EventLoggerProtocol?
  ) {
    bindHandlers(
      onStateChange: onStateChange,
      onMessage: onMessage,
      onClose: onClose,
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

  func diagnosticsSnapshot() -> SessionWebSocketDiagnosticsSnapshot {
    SessionWebSocketDiagnosticsSnapshot(
      connectionID: 0,
      lastOutboundKind: "none",
      lastOutboundBytes: 0,
      binarySendAttemptCount: 0,
      binarySendSuccessCount: 0,
      lastBinaryFirstByteHex: "none",
      inboundServerAudioFrameCount: 0,
      inboundServerAudioBytes: 0,
      lastInboundServerAudioBytes: 0,
      lastPlaybackControlCommand: "none"
    )
  }
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

// Shared websocket session actor that owns transport state and event delivery.
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
    case uplinkAcknowledged(AssistantRealtimeUplinkAckPayload)
    case serverAudio(Data)
    case playbackControl(AssistantPlaybackControlPayload)
    case closed
    case error(String)
  }

  let webSocketURL: URL
  let requestHeaders: [String: String]
  let urlSession: URLSession

  var webSocketTask: URLSessionWebSocketTask?
  var receiveTask: Task<Void, Never>?
  var eventHandler: (@Sendable (EventEnvelope) -> Void)?
  var connectionState: ConnectionState = .idle
  var outboundSequence = 0
  var inboundEventSequence = 0
  var sessionID: String?
  var lastOutboundKind = "none"
  var lastOutboundBytes = 0
  var binarySendAttemptCount = 0
  var binarySendSuccessCount = 0
  var lastBinaryFirstByteHex = "none"
  var loggedFirstServerAudioFrame = false
  var inboundServerAudioFrameCount = 0
  var inboundServerAudioBytes = 0
  var lastInboundServerAudioBytes = 0
  var lastPlaybackControlCommand = "none"
  var isLocallyDisconnecting = false

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

    debugLog(
      "Disconnect requested session=\(activeSessionID ?? "-") sendDeactivate=\(sendDeactivate) emitLifecycleEvents=\(emitLifecycleEvents) hadActiveConnection=\(hadActiveConnection)"
    )

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

    guard emitLifecycleEvents, hadActiveConnection else {
      debugLog("Disconnect completed without lifecycle events")
      return
    }
    yieldEvent(.stateChanged(.disconnected), sessionID: activeSessionID)
    yieldEvent(.closed, sessionID: activeSessionID)
    debugLog("Disconnect completed with disconnected/closed lifecycle events")
  }

  func connectionStateText() -> String {
    connectionState.rawValue
  }

  func diagnosticsSnapshot() -> AssistantTransportDiagnosticsSnapshot {
    AssistantTransportDiagnosticsSnapshot(
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

  func yieldEvent(_ event: Event, sessionID: String? = nil) {
    inboundEventSequence += 1
    let envelope = EventEnvelope(id: inboundEventSequence, event: event)
    let resolvedSessionID = sessionID ?? self.sessionID ?? "-"
    if shouldLogEvent(event) {
      debugLog("Yielding event#\(envelope.id) session=\(resolvedSessionID) \(describe(event))")
    }
    eventHandler?(envelope)
  }

  func shouldLogEvent(_ event: Event) -> Bool {
    switch event {
    case .stateChanged, .sessionReady, .playbackControl, .closed, .error:
      return true
    case .uplinkAcknowledged, .serverAudio:
      return false
    }
  }

  func describe(_ event: Event) -> String {
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

  func debugLog(_ message: String) {
    #if DEBUG
      print("[BackendSessionClient] \(message)")
    #endif
  }
}

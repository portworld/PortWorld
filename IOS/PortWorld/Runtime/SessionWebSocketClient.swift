import Foundation
import OSLog

public enum SessionWebSocketConnectionState: Equatable {
  case idle
  case connecting
  case connected
  case reconnecting(attempt: Int, nextDelayMs: UInt64)
  case disconnected
}

public enum SessionWebSocketClientError: Error, LocalizedError {
  case notConnected
  case transport(String)
  case decoding(String)
  case encoding(String)
  case pingFailed(String)

  public var errorDescription: String? {
    switch self {
    case .notConnected:
      return "WebSocket is not connected."
    case .transport(let message):
      return "WebSocket transport error: \(message)"
    case .decoding(let message):
      return "WebSocket payload decode error: \(message)"
    case .encoding(let message):
      return "WebSocket payload encode error: \(message)"
    case .pingFailed(let message):
      return "WebSocket ping failed: \(message)"
    }
  }
}

actor SessionWebSocketClient: SessionWebSocketClientProtocol {
  typealias WebSocketTaskStateProvider = @Sendable (URLSessionWebSocketTask) -> URLSessionTask.State
  typealias ReconnectJitterProvider = @Sendable (ClosedRange<Double>) -> Double

  private enum OutboundSendOperation {
    case text(String)
    case data(Data)
    case ping
  }

  private struct OutboundSendRequest {
    let enqueueOrder: Int
    let connectionID: Int
    let messageKind: String
    let byteCount: Int
    let operation: OutboundSendOperation
    let continuation: CheckedContinuation<Void, Error>
  }

  private let url: URL
  private let requestHeaders: [String: String]
  private let urlSession: URLSession
  private let baseReconnectDelayMs: UInt64
  private let maxReconnectDelayMs: UInt64
  private let pingIntervalMs: UInt64
  private let webSocketTaskStateProvider: WebSocketTaskStateProvider
  private let reconnectJitterProvider: ReconnectJitterProvider
  private let logger = Logger(subsystem: "PortWorld", category: "SessionWebSocketClient")
  private var onStateChange: SessionWebSocketStateHandler?
  private var onMessage: SessionWebSocketMessageHandler?
  private var onRawMessage: SessionWebSocketRawMessageHandler?
  private var onClose: SessionWebSocketCloseHandler?
  private var onError: SessionWebSocketErrorHandler?
  private var eventLogger: EventLoggerProtocol?

  private var webSocketTask: URLSessionWebSocketTask?
  private var receiveTask: Task<Void, Never>?
  private var pingTask: Task<Void, Never>?
  private var reconnectTask: Task<Void, Never>?
  private var state: SessionWebSocketConnectionState = .idle
  private var shouldReconnect = false
  private var isNetworkAvailable = true
  private var reconnectAttempt = 0
  private var outboundSeq = 0
  private var outboundEnqueueOrder = 0
  private var hasPublishedConnectedForSocket = false
  private var dataSendAttemptCount = 0
  private var dataSendSuccessCount = 0
  private var binarySendAttemptCount = 0
  private var binarySendSuccessCount = 0
  private var lastBinaryFirstByteHex = "none"
  private var lastOutboundKind = "none"
  private var lastOutboundBytes = 0
  private var nextConnectionID = 0
  private var activeConnectionID = 0
  private var outboundSendQueue: [OutboundSendRequest] = []
  private var isDrainingOutboundSendQueue = false

  init(
    url: URL,
    requestHeaders: [String: String] = [:],
    urlSession: URLSession = .shared,
    baseReconnectDelayMs: UInt64 = 500,
    maxReconnectDelayMs: UInt64 = 30_000,
    pingIntervalMs: UInt64 = 15_000,
    webSocketTaskStateProvider: @escaping WebSocketTaskStateProvider = { $0.state },
    reconnectJitterProvider: @escaping ReconnectJitterProvider = { Double.random(in: $0) },
    onStateChange: SessionWebSocketStateHandler? = nil,
    onMessage: SessionWebSocketMessageHandler? = nil,
    onError: SessionWebSocketErrorHandler? = nil,
    eventLogger: EventLoggerProtocol? = nil
  ) {
    self.url = url
    self.requestHeaders = requestHeaders
    self.urlSession = urlSession
    self.baseReconnectDelayMs = max(100, baseReconnectDelayMs)
    self.maxReconnectDelayMs = max(maxReconnectDelayMs, baseReconnectDelayMs)
    self.pingIntervalMs = max(1_000, pingIntervalMs)
    self.webSocketTaskStateProvider = webSocketTaskStateProvider
    self.reconnectJitterProvider = reconnectJitterProvider
    self.onStateChange = onStateChange
    self.onMessage = onMessage
    self.onError = onError
    self.eventLogger = eventLogger
  }

  public func currentState() -> SessionWebSocketConnectionState {
    state
  }

  public func reconnectAttemptCount() -> Int {
    reconnectAttempt
  }

  public func diagnosticsSnapshot() -> SessionWebSocketDiagnosticsSnapshot {
    SessionWebSocketDiagnosticsSnapshot(
      connectionID: activeConnectionID,
      lastOutboundKind: lastOutboundKind,
      lastOutboundBytes: lastOutboundBytes,
      binarySendAttemptCount: binarySendAttemptCount,
      binarySendSuccessCount: binarySendSuccessCount,
      lastBinaryFirstByteHex: lastBinaryFirstByteHex
    )
  }

  func setNetworkAvailable(_ isAvailable: Bool) {
    guard isNetworkAvailable != isAvailable else { return }
    isNetworkAvailable = isAvailable

    if !isAvailable {
      reconnectTask?.cancel()
      reconnectTask = nil
      return
    }

    guard shouldReconnect, webSocketTask == nil else { return }
    reconnectTask?.cancel()
    reconnectTask = nil
    reconnectAttempt = 0
    publishState(.connecting)
    openSocket()
  }

  public func connect() {
    if let webSocketTask {
      let taskState = webSocketTaskStateProvider(webSocketTask)
      if taskState == .canceling || taskState == .completed {
        self.webSocketTask = nil
      }
    }
    guard webSocketTask == nil, reconnectTask == nil else { return }

    shouldReconnect = true
    reconnectAttempt = 0

    guard isNetworkAvailable else {
      publishState(.disconnected)
      return
    }

    publishState(.connecting)
    openSocket()
  }

  func bindHandlers(
    onStateChange: SessionWebSocketStateHandler?,
    onMessage: SessionWebSocketMessageHandler?,
    onClose: SessionWebSocketCloseHandler?,
    onError: SessionWebSocketErrorHandler?,
    eventLogger: EventLoggerProtocol?
  ) {
    self.onStateChange = onStateChange
    self.onMessage = onMessage
    self.onClose = onClose
    self.onError = onError
    self.eventLogger = eventLogger
  }

  func bindRawMessageHandler(_ onRawMessage: SessionWebSocketRawMessageHandler?) {
    self.onRawMessage = onRawMessage
  }

  public func disconnect(closeCode: URLSessionWebSocketTask.CloseCode = .normalClosure) {
    shouldReconnect = false
    reconnectAttempt = 0
    hasPublishedConnectedForSocket = false
    tearDownRuntimeTasks()
    failPendingOutboundSends(with: .notConnected, connectionID: activeConnectionID)

    if let webSocketTask {
      publishClose(
        TransportSocketCloseInfo(
          connectionID: activeConnectionID,
          code: Int(closeCode.rawValue),
          reason: nil
        )
      )
      webSocketTask.cancel(with: closeCode, reason: nil)
      self.webSocketTask = nil
    }
    activeConnectionID = 0

    publishState(.disconnected)
  }

  public func ensureConnected() {
    guard shouldReconnect else {
      connect()
      return
    }

    if webSocketTask == nil && reconnectTask == nil {
      guard isNetworkAvailable else {
        publishState(.disconnected)
        return
      }
      // Reset reconnect attempt to avoid stale backoff from previous failures.
      // After foreground recovery, we want fresh reconnect timing.
      reconnectAttempt = 0
      publishState(.connecting)
      openSocket()
    }
  }

  public func sendText(_ text: String) async throws {
    try await enqueueOutboundSend(
      operation: .text(text),
      messageKind: outboundMessageKind(forText: text),
      byteCount: text.utf8.count
    )
  }

  public func sendData(_ data: Data) async throws {
    dataSendAttemptCount += 1
    let messageKind = outboundMessageKind(forData: data)
    let connectionID = activeConnectionID
    if dataSendAttemptCount == 1 || dataSendAttemptCount % 100 == 0 {
      logger.warning(
        "send_data connection_id=\(connectionID, privacy: .public) attempt=\(self.dataSendAttemptCount, privacy: .public) kind=\(messageKind, privacy: .public) bytes=\(data.count, privacy: .public)"
      )
    } else {
      logger.debug(
        "send_data connection_id=\(connectionID, privacy: .public) attempt=\(self.dataSendAttemptCount, privacy: .public) kind=\(messageKind, privacy: .public) bytes=\(data.count, privacy: .public)"
      )
    }
    try await enqueueOutboundSend(
      operation: .data(data),
      messageKind: messageKind,
      byteCount: data.count
    )
    dataSendSuccessCount += 1
    if dataSendSuccessCount == 1 || dataSendSuccessCount % 100 == 0 {
      logger.warning(
        "sent_data connection_id=\(connectionID, privacy: .public) sent=\(self.dataSendSuccessCount, privacy: .public) attempts=\(self.dataSendAttemptCount, privacy: .public) kind=\(messageKind, privacy: .public) bytes=\(data.count, privacy: .public)"
      )
    }
  }

  public func sendEnvelope<Payload: Codable>(_ envelope: WSMessageEnvelope<Payload>) async throws {
    let data: Data
    do {
      data = try await MainActor.run {
        try WSMessageCodec.encodeEnvelope(envelope)
      }
    } catch {
      throw SessionWebSocketClientError.encoding(error.localizedDescription)
    }
    try await sendData(data)
  }

  public func send<Payload: Codable>(
    type: WSOutboundType,
    sessionID: String,
    payload: Payload
  ) async throws {
    let sequence = nextOutboundSequence()
    let envelope = await MainActor.run {
      WSMessageEnvelope(
        type: type.rawValue,
        sessionID: sessionID,
        seq: sequence,
        payload: payload
      )
    }
    try await sendEnvelope(envelope)
  }

  public func ping() async throws {
    do {
      try await sendQueuedPing()
      publishConnectedIfReadySignalReceived()
    } catch {
      throw SessionWebSocketClientError.pingFailed(error.localizedDescription)
    }
  }

  private func openSocket() {
    var request = URLRequest(url: url)
    for (name, value) in requestHeaders {
      request.setValue(value, forHTTPHeaderField: name)
    }
    let task = urlSession.webSocketTask(with: request)
    webSocketTask = task
    nextConnectionID += 1
    activeConnectionID = nextConnectionID
    hasPublishedConnectedForSocket = false
    logger.warning("socket_open connection_id=\(self.activeConnectionID, privacy: .public)")
    task.resume()
    startReceiveLoop()
    startPingLoop()
  }

  private func startReceiveLoop() {
    receiveTask?.cancel()
    receiveTask = Task { [weak self] in
      await self?.receiveLoop()
    }
  }

  private func startPingLoop() {
    pingTask?.cancel()
    pingTask = Task { [weak self] in
      await self?.pingLoop()
    }
  }

  private func receiveLoop() async {
    while !Task.isCancelled {
      guard let webSocketTask else { return }

      do {
        let message = try await webSocketTask.receive()
        try Task.checkCancellation()

        switch message {
        case .data(let data):
          onRawMessage?(.binary(data))
          // Raw handler mode consumes binary payloads directly.
          // Avoid routing binary frames into WSInbound JSON decoding in this mode.
          guard onRawMessage == nil else { continue }
          await handleInboundData(data)
        case .string(let text):
          onRawMessage?(.text(text))
          guard let data = text.data(using: .utf8) else {
            publishError(.decoding("Unable to decode UTF-8 text payload"))
            continue
          }
          await handleInboundData(data)
        @unknown default:
          publishError(.decoding("Unsupported WebSocket message kind"))
        }
      } catch is CancellationError {
        return
      } catch {
        await handleTransportFailure(.transport(error.localizedDescription))
        return
      }
    }
  }

  private func pingLoop() async {
    while !Task.isCancelled {
      do {
        guard shouldReconnect else { return }
        try await sendQueuedPing()
        publishConnectedIfReadySignalReceived()
        try await Task.sleep(nanoseconds: pingIntervalMs * 1_000_000)
      } catch is CancellationError {
        return
      } catch {
        await handleTransportFailure(.pingFailed(error.localizedDescription))
        return
      }
    }
  }

  private func sendPing() async throws {
    guard let webSocketTask else { throw SessionWebSocketClientError.notConnected }

    try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
      webSocketTask.sendPing { error in
        if let error {
          continuation.resume(throwing: error)
        } else {
          continuation.resume(returning: ())
        }
      }
    }
  }

  private func sendQueuedPing() async throws {
    try await enqueueOutboundSend(operation: .ping, messageKind: "ws_ping", byteCount: 0)
    reconnectAttempt = 0
  }

  private func enqueueOutboundSend(
    operation: OutboundSendOperation,
    messageKind: String,
    byteCount: Int
  ) async throws {
    guard let webSocketTask else { throw SessionWebSocketClientError.notConnected }
    let taskState = webSocketTaskStateProvider(webSocketTask)
    guard canSendMessage(whenTaskStateIs: taskState) else {
      throw SessionWebSocketClientError.transport("Socket not sendable in state=\(describeTaskState(taskState))")
    }
    let enqueueOrder = nextOutboundEnqueueOrder()
    try await withCheckedThrowingContinuation { continuation in
        let request = OutboundSendRequest(
          enqueueOrder: enqueueOrder,
          connectionID: activeConnectionID,
          messageKind: messageKind,
          byteCount: byteCount,
          operation: operation,
          continuation: continuation
        )
        logOutboundSendEnqueued(request, taskState: taskState)
        outboundSendQueue.append(request)
        drainOutboundSendQueueIfNeeded()
      }
  }

  private func drainOutboundSendQueueIfNeeded() {
    guard !isDrainingOutboundSendQueue else { return }
    isDrainingOutboundSendQueue = true
    Task {
      await drainOutboundSendQueue()
    }
  }

  private func drainOutboundSendQueue() async {
    while !outboundSendQueue.isEmpty {
      let request = outboundSendQueue.removeFirst()
      do {
        try await performOutboundSend(request)
        request.continuation.resume()
      } catch {
        request.continuation.resume(throwing: error)
      }
    }
    isDrainingOutboundSendQueue = false
    if !outboundSendQueue.isEmpty {
      drainOutboundSendQueueIfNeeded()
    }
  }

  private func performOutboundSend(_ request: OutboundSendRequest) async throws {
    guard request.connectionID > 0 else {
      throw SessionWebSocketClientError.notConnected
    }
    guard request.connectionID == activeConnectionID else {
      throw SessionWebSocketClientError.transport("Socket connection changed before send start")
    }
    guard let webSocketTask else {
      throw SessionWebSocketClientError.notConnected
    }

    let taskState = webSocketTaskStateProvider(webSocketTask)
    guard canSendMessage(whenTaskStateIs: taskState) else {
      throw SessionWebSocketClientError.transport("Socket not sendable in state=\(describeTaskState(taskState))")
    }

    logOutboundSendStart(request, taskState: taskState)
    do {
      switch request.operation {
      case .text(let text):
        try await webSocketTask.send(.string(text))
      case .data(let data):
        let firstByteHex = data.first.map { String(format: "0x%02x", $0) } ?? "none"
        binarySendAttemptCount += 1
        lastBinaryFirstByteHex = firstByteHex
        if binarySendAttemptCount == 1 || binarySendAttemptCount % 100 == 0 {
          logger.warning(
            "ws_binary_send_start order=\(request.enqueueOrder, privacy: .public) kind=\(request.messageKind, privacy: .public) bytes=\(data.count, privacy: .public) first_byte=\(firstByteHex, privacy: .public) connection_id=\(request.connectionID, privacy: .public) task_state=\(self.describeTaskState(taskState), privacy: .public) binary_attempts=\(self.binarySendAttemptCount, privacy: .public) binary_completions=\(self.binarySendSuccessCount, privacy: .public)"
          )
        }
        try await webSocketTask.send(.data(data))
        binarySendSuccessCount += 1
        if binarySendSuccessCount == 1 || binarySendSuccessCount % 100 == 0 {
          logger.warning(
            "ws_binary_send_complete order=\(request.enqueueOrder, privacy: .public) kind=\(request.messageKind, privacy: .public) bytes=\(data.count, privacy: .public) first_byte=\(firstByteHex, privacy: .public) connection_id=\(request.connectionID, privacy: .public) binary_attempts=\(self.binarySendAttemptCount, privacy: .public) binary_completions=\(self.binarySendSuccessCount, privacy: .public)"
          )
        }
      case .ping:
        try await sendPing()
      }
      logOutboundSendCompletion(request, taskState: taskState)
    } catch {
      logger.error(
        "outbound_send_failed order=\(request.enqueueOrder, privacy: .public) kind=\(request.messageKind, privacy: .public) bytes=\(request.byteCount, privacy: .public) connection_id=\(request.connectionID, privacy: .public) task_state=\(self.describeTaskState(taskState), privacy: .public) detail=\(error.localizedDescription, privacy: .public)"
      )
      throw SessionWebSocketClientError.transport(error.localizedDescription)
    }
  }

  private func failPendingOutboundSends(
    with error: SessionWebSocketClientError,
    connectionID: Int? = nil
  ) {
    guard !outboundSendQueue.isEmpty else { return }

    var retainedRequests: [OutboundSendRequest] = []
    for request in outboundSendQueue {
      if let connectionID, request.connectionID != connectionID {
        retainedRequests.append(request)
      } else {
        request.continuation.resume(throwing: error)
      }
    }
    outboundSendQueue = retainedRequests
  }

  private func nextOutboundEnqueueOrder() -> Int {
    outboundEnqueueOrder += 1
    return outboundEnqueueOrder
  }

  private func logOutboundSendEnqueued(
    _ request: OutboundSendRequest,
    taskState: URLSessionTask.State
  ) {
    logger.debug(
      "outbound_send_enqueued order=\(request.enqueueOrder, privacy: .public) kind=\(request.messageKind, privacy: .public) bytes=\(request.byteCount, privacy: .public) connection_id=\(request.connectionID, privacy: .public) task_state=\(self.describeTaskState(taskState), privacy: .public)"
    )
  }

  private func logOutboundSendStart(
    _ request: OutboundSendRequest,
    taskState: URLSessionTask.State
  ) {
    logger.debug(
      "outbound_send_start order=\(request.enqueueOrder, privacy: .public) kind=\(request.messageKind, privacy: .public) bytes=\(request.byteCount, privacy: .public) connection_id=\(request.connectionID, privacy: .public) task_state=\(self.describeTaskState(taskState), privacy: .public)"
    )
  }

  private func logOutboundSendCompletion(
    _ request: OutboundSendRequest,
    taskState: URLSessionTask.State
  ) {
    lastOutboundKind = request.messageKind
    lastOutboundBytes = request.byteCount
    logger.debug(
      "outbound_send_complete order=\(request.enqueueOrder, privacy: .public) kind=\(request.messageKind, privacy: .public) bytes=\(request.byteCount, privacy: .public) connection_id=\(request.connectionID, privacy: .public) task_state=\(self.describeTaskState(taskState), privacy: .public)"
    )
  }

  private func outboundMessageKind(forText text: String) -> String {
    guard let data = text.data(using: .utf8) else { return "text_control" }
    guard
      let jsonObject = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
      let type = jsonObject["type"] as? String,
      !type.isEmpty
    else {
      return "text_control"
    }
    return type
  }

  private func outboundMessageKind(forData data: Data) -> String {
    guard let frameType = data.first else { return "binary_frame" }
    switch frameType {
    case TransportBinaryFraming.clientAudioTypeByte:
      return "client_audio"
    case TransportBinaryFraming.clientProbeTypeByte:
      return "client_probe"
    default:
      return String(format: "binary_frame_0x%02x", frameType)
    }
  }

  private func canSendMessage(whenTaskStateIs taskState: URLSessionTask.State) -> Bool {
    switch taskState {
    case .running:
      return true
    case .suspended:
      logger.warning("rejecting_socket_send state=suspended")
      return false
    case .canceling, .completed:
      return false
    @unknown default:
      return false
    }
  }

  private func describeTaskState(_ state: URLSessionTask.State) -> String {
    switch state {
    case .running:
      return "running"
    case .suspended:
      return "suspended"
    case .canceling:
      return "canceling"
    case .completed:
      return "completed"
    @unknown default:
      return "unknown"
    }
  }

  private func handleInboundData(_ data: Data) async {
    do {
      let message = try await MainActor.run {
        try WSMessageCodec.decodeInbound(from: data)
      }
      reconnectAttempt = 0
      publishConnectedIfReadySignalReceived()
      onMessage?(message)
    } catch {
      publishError(.decoding(error.localizedDescription))
    }
  }

  private func handleTransportFailure(_ error: SessionWebSocketClientError) async {
    logger.warning(
      "transport_failure connection_id=\(self.activeConnectionID, privacy: .public) detail=\(error.localizedDescription, privacy: .public)"
    )
    publishError(error)
    publishCloseIfAvailable(for: webSocketTask)
    tearDownSocket(keepReconnect: shouldReconnect)

    guard shouldReconnect else {
      publishState(.disconnected)
      return
    }

    guard isNetworkAvailable else {
      publishState(.disconnected)
      return
    }

    scheduleReconnectIfNeeded()
  }

  private func scheduleReconnectIfNeeded() {
    guard reconnectTask == nil, isNetworkAvailable else { return }

    reconnectAttempt += 1
    let delayMs = reconnectDelayMs(for: reconnectAttempt)
    publishState(.reconnecting(attempt: reconnectAttempt, nextDelayMs: delayMs))

    reconnectTask = Task { [weak self] in
      do {
        try await Task.sleep(nanoseconds: delayMs * 1_000_000)
        await self?.performReconnect()
      } catch {
        // Cancelled.
      }
    }
  }

  private func performReconnect() {
    reconnectTask = nil
    guard shouldReconnect, webSocketTask == nil else { return }

    publishState(.connecting)
    openSocket()
  }

  private func reconnectDelayMs(for attempt: Int) -> UInt64 {
    let boundedAttempt = max(0, min(attempt - 1, 10))
    let multiplier = UInt64(1 << boundedAttempt)

    let scaled = baseReconnectDelayMs.multipliedReportingOverflow(by: multiplier)
    let capped = scaled.overflow ? maxReconnectDelayMs : min(scaled.partialValue, maxReconnectDelayMs)
    let jitter = reconnectJitterProvider(0.8 ... 1.2)
    return UInt64(Double(capped) * jitter)
  }

  private func tearDownRuntimeTasks() {
    reconnectTask?.cancel()
    reconnectTask = nil
    receiveTask?.cancel()
    receiveTask = nil
    pingTask?.cancel()
    pingTask = nil
  }

  private func tearDownSocket(keepReconnect: Bool) {
    receiveTask?.cancel()
    receiveTask = nil
    pingTask?.cancel()
    pingTask = nil
    failPendingOutboundSends(with: .notConnected, connectionID: activeConnectionID)

    if let webSocketTask {
      webSocketTask.cancel(with: .goingAway, reason: nil)
      self.webSocketTask = nil
    }
    hasPublishedConnectedForSocket = false
    activeConnectionID = 0

    if !keepReconnect {
      reconnectTask?.cancel()
      reconnectTask = nil
    }
  }

  private func nextOutboundSequence() -> Int {
    outboundSeq += 1
    return outboundSeq
  }

  private func publishConnectedIfReadySignalReceived() {
    guard shouldReconnect, webSocketTask != nil else { return }
    guard !hasPublishedConnectedForSocket else { return }

    hasPublishedConnectedForSocket = true
    logger.warning("socket_connected connection_id=\(self.activeConnectionID, privacy: .public)")
    publishState(.connected)
  }

  private func publishState(_ newState: SessionWebSocketConnectionState) {
    state = newState
    onStateChange?(newState)

    guard let eventLogger else { return }
    Task {
      await eventLogger.log(
        name: "ws.state",
        sessionID: "unknown",
        fields: [
          "state": .string(String(describing: newState))
        ]
      )
    }
  }

  private func publishError(_ error: SessionWebSocketClientError) {
    onError?(error)

    guard let eventLogger else { return }
    Task {
      await eventLogger.log(
        name: "ws.error",
        sessionID: "unknown",
        fields: [
          "message": .string(error.localizedDescription)
        ]
      )
    }
  }

  private func publishCloseIfAvailable(for task: URLSessionWebSocketTask?) {
    guard let closeInfo = makeCloseInfo(for: task) else { return }
    publishClose(closeInfo)
  }

  private func makeCloseInfo(for task: URLSessionWebSocketTask?) -> TransportSocketCloseInfo? {
    guard let task else { return nil }
    let closeCode = task.closeCode
    let resolvedCode = closeCode == .invalid ? nil : Int(closeCode.rawValue)
    let resolvedReason = decodeCloseReason(task.closeReason)
    guard resolvedCode != nil || resolvedReason != nil || activeConnectionID > 0 else { return nil }
    return TransportSocketCloseInfo(
      connectionID: activeConnectionID,
      code: resolvedCode,
      reason: resolvedReason
    )
  }

  private func decodeCloseReason(_ data: Data?) -> String? {
    guard let data, !data.isEmpty else { return nil }
    if let utf8 = String(data: data, encoding: .utf8) {
      return utf8
    }
    return data.base64EncodedString()
  }

  private func publishClose(_ info: TransportSocketCloseInfo) {
    logger.warning(
      "socket_close_published connection_id=\(info.connectionID, privacy: .public) code=\(String(describing: info.code), privacy: .public) reason=\(info.reason ?? "-", privacy: .public)"
    )
    onClose?(info)
  }

#if DEBUG
  func setWebSocketTaskForTesting(_ task: URLSessionWebSocketTask?) {
    webSocketTask = task
  }

  func setActiveConnectionIDForTesting(_ connectionID: Int) {
    activeConnectionID = connectionID
  }

  func outboundSequenceForTesting() -> Int {
    outboundSeq
  }

  func reconnectDelayMsForTesting(attempt: Int) -> UInt64 {
    reconnectDelayMs(for: attempt)
  }
#endif
}

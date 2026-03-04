import Foundation

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
  private let url: URL
  private let requestHeaders: [String: String]
  private let urlSession: URLSession
  private let baseReconnectDelayMs: UInt64
  private let maxReconnectDelayMs: UInt64
  private let pingIntervalMs: UInt64
  private var onStateChange: SessionWebSocketStateHandler?
  private var onMessage: SessionWebSocketMessageHandler?
  private var onRawMessage: SessionWebSocketRawMessageHandler?
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
  private var hasPublishedConnectedForSocket = false

  init(
    url: URL,
    requestHeaders: [String: String] = [:],
    urlSession: URLSession = .shared,
    baseReconnectDelayMs: UInt64 = 500,
    maxReconnectDelayMs: UInt64 = 30_000,
    pingIntervalMs: UInt64 = 15_000,
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
    if let webSocketTask,
       webSocketTask.state == .canceling || webSocketTask.state == .completed {
      self.webSocketTask = nil
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
    onError: SessionWebSocketErrorHandler?,
    eventLogger: EventLoggerProtocol?
  ) {
    self.onStateChange = onStateChange
    self.onMessage = onMessage
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

    if let webSocketTask {
      webSocketTask.cancel(with: closeCode, reason: nil)
      self.webSocketTask = nil
    }

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
    guard let webSocketTask else { throw SessionWebSocketClientError.notConnected }

    do {
      try await webSocketTask.send(.string(text))
    } catch {
      throw SessionWebSocketClientError.transport(error.localizedDescription)
    }
  }

  public func sendData(_ data: Data) async throws {
    guard let webSocketTask else { throw SessionWebSocketClientError.notConnected }

    do {
      try await webSocketTask.send(.data(data))
    } catch {
      throw SessionWebSocketClientError.transport(error.localizedDescription)
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
      try await sendPing()
      reconnectAttempt = 0
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
    hasPublishedConnectedForSocket = false
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
        try await sendPing()
        reconnectAttempt = 0
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
    publishError(error)
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
    let jitter = Double.random(in: 0.8 ... 1.2)
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

    if let webSocketTask {
      webSocketTask.cancel(with: .goingAway, reason: nil)
      self.webSocketTask = nil
    }
    hasPublishedConnectedForSocket = false

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
}

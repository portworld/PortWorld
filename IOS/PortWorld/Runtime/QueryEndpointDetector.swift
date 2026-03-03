import Foundation

enum QueryEndpointReason: String {
  case silenceTimeout = "silence_timeout"
  case manualStop = "manual_stop"
  case reset = "reset"
}

struct QueryEndpointStartedEvent {
  let queryId: String
  let startedAtMs: Int64
}

struct QueryEndpointEndedEvent {
  let queryId: String
  let startedAtMs: Int64
  let endedAtMs: Int64
  let durationMs: Int64
  let reason: QueryEndpointReason
}

/// Silence-timeout endpoint detector.
///
/// Call `beginQuery` when wake is detected, then feed speech activity with
/// `recordSpeechActivity`. If no speech ping occurs for the configured timeout,
/// the query is ended automatically with reason `silence_timeout`.
final class QueryEndpointDetector {
  typealias StartedHandler = (QueryEndpointStartedEvent) -> Void
  typealias EndedHandler = (QueryEndpointEndedEvent) -> Void
  typealias SpeechPingHandler = (_ queryId: String, _ timestampMs: Int64) -> Void

  var onQueryStarted: StartedHandler?
  var onQueryEnded: EndedHandler?
  var onSpeechActivityPing: SpeechPingHandler?

  var silenceTimeoutMs: Int64 {
    didSet {
      if silenceTimeoutMs < 250 {
        silenceTimeoutMs = 250
        return
      }

      let timeoutMs = silenceTimeoutMs
      Task { [core] in
        await core.updateSilenceTimeoutMs(timeoutMs)
      }
    }
  }

  var isQueryActive: Bool {
    activeLock.lock()
    defer { activeLock.unlock() }
    return cachedIsQueryActive
  }

  private let callbackQueue: DispatchQueue
  private let core: Core
  private let activeLock = NSLock()
  private var cachedIsQueryActive = false

  init(
    silenceTimeoutMs: Int64 = 5_000,
    checkIntervalMs: Int64 = 200,
    callbackQueue: DispatchQueue = .main
  ) {
    self.silenceTimeoutMs = max(250, silenceTimeoutMs)
    self.callbackQueue = callbackQueue
    self.core = Core(
      silenceTimeoutMs: max(250, silenceTimeoutMs),
      checkIntervalMs: max(50, checkIntervalMs)
    )

    Task { [core] in
      await core.setEventSink { [weak self, callbackQueue] event in
        guard let self else {
          return
        }

        switch event {
        case .started(let started):
          Task { @MainActor in
            self.setQueryActive(true)
          }
          callbackQueue.async {
            self.onQueryStarted?(started)
          }

        case .speechPing(let queryId, let timestampMs):
          callbackQueue.async {
            self.onSpeechActivityPing?(queryId, timestampMs)
          }

        case .ended(let ended):
          Task { @MainActor in
            self.setQueryActive(false)
          }
          callbackQueue.async {
            self.onQueryEnded?(ended)
          }
        }
      }
    }
  }

  deinit {
    let core = core
    Task {
      await core.shutdown()
    }
  }

  func beginQuery(queryId: String = "query_\(UUID().uuidString)", startedAtMs: Int64 = Clocks.nowMs()) {
    Task { [core] in
      await core.beginQuery(queryId: queryId, startedAtMs: startedAtMs)
    }
  }

  func recordSpeechActivity(at timestampMs: Int64 = Clocks.nowMs()) {
    Task { [core] in
      await core.recordSpeechActivity(at: timestampMs)
    }
  }

  func forceEnd(reason: QueryEndpointReason = .manualStop, endedAtMs: Int64 = Clocks.nowMs()) {
    Task { [core] in
      await core.forceEnd(reason: reason, endedAtMs: endedAtMs)
    }
  }

  func reset() {
    Task { [core] in
      await core.forceEnd(reason: .reset, endedAtMs: Clocks.nowMs())
    }
  }

  private func setQueryActive(_ isActive: Bool) {
    activeLock.lock()
    cachedIsQueryActive = isActive
    activeLock.unlock()
  }

  private enum CoreEvent {
    case started(QueryEndpointStartedEvent)
    case speechPing(queryId: String, timestampMs: Int64)
    case ended(QueryEndpointEndedEvent)
  }

  private actor Core {
    typealias EventSink = @Sendable (CoreEvent) -> Void

    private struct State {
      var queryId: String
      var startedAtMs: Int64
      var lastSpeechAtMs: Int64
    }

    private var silenceTimeoutMs: Int64
    private let checkIntervalMs: Int64

    private var state: State?
    private var timerTask: Task<Void, Never>?
    private var eventSink: EventSink?
    private var eventBuffer: [CoreEvent] = []

    init(silenceTimeoutMs: Int64, checkIntervalMs: Int64) {
      self.silenceTimeoutMs = silenceTimeoutMs
      self.checkIntervalMs = checkIntervalMs
    }

    func setEventSink(_ sink: @escaping EventSink) {
      eventSink = sink
      for event in eventBuffer {
        sink(event)
      }
      eventBuffer.removeAll()
    }

    func updateSilenceTimeoutMs(_ timeoutMs: Int64) {
      silenceTimeoutMs = max(250, timeoutMs)
    }

    func beginQuery(queryId: String, startedAtMs: Int64) {
      guard state == nil else {
        return
      }

      state = State(
        queryId: queryId,
        startedAtMs: startedAtMs,
        lastSpeechAtMs: startedAtMs
      )
      ensureTimerRunning()
      emit(.started(QueryEndpointStartedEvent(queryId: queryId, startedAtMs: startedAtMs)))
    }

    func recordSpeechActivity(at timestampMs: Int64) {
      guard var current = state else {
        return
      }

      current.lastSpeechAtMs = max(current.lastSpeechAtMs, timestampMs)
      state = current
      emit(.speechPing(queryId: current.queryId, timestampMs: timestampMs))
    }

    func forceEnd(reason: QueryEndpointReason, endedAtMs: Int64) {
      endCurrentQuery(reason: reason, endedAtMs: endedAtMs)
    }

    func shutdown() {
      stopTimer()
      state = nil
    }

    private func ensureTimerRunning() {
      guard timerTask == nil else {
        return
      }

      let intervalNs = UInt64(checkIntervalMs) * 1_000_000
      timerTask = Task { [weak self] in
        while !Task.isCancelled {
          do {
            try await Task.sleep(nanoseconds: intervalNs)
          } catch {
            return
          }

          guard let self else {
            return
          }
          await self.tick()
        }
      }
    }

    private func stopTimer() {
      timerTask?.cancel()
      timerTask = nil
    }

    private func tick() {
      guard let current = state else {
        stopTimer()
        return
      }

      let nowMs = Clocks.nowMs()
      let silenceElapsed = nowMs - current.lastSpeechAtMs
      if silenceElapsed >= silenceTimeoutMs {
        endCurrentQuery(reason: .silenceTimeout, endedAtMs: nowMs)
      }
    }

    private func endCurrentQuery(reason: QueryEndpointReason, endedAtMs: Int64) {
      guard let current = state else {
        return
      }

      stopTimer()
      state = nil

      let endedAt = max(current.startedAtMs, endedAtMs)
      let event = QueryEndpointEndedEvent(
        queryId: current.queryId,
        startedAtMs: current.startedAtMs,
        endedAtMs: endedAt,
        durationMs: endedAt - current.startedAtMs,
        reason: reason
      )

      emit(.ended(event))
    }

    private func emit(_ event: CoreEvent) {
      if let eventSink {
        eventSink(event)
      } else {
        eventBuffer.append(event)
      }
    }
  }
}

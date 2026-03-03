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
      queue.async { [weak self] in
        self?.rescheduleTimerLocked()
      }
    }
  }

  var isQueryActive: Bool {
    queue.sync { state != nil }
  }

  private struct State {
    var queryId: String
    var startedAtMs: Int64
    var lastSpeechAtMs: Int64
  }

  private let queue = DispatchQueue(label: "Runtime.QueryEndpointDetector")
  private let queueKey = DispatchSpecificKey<Void>()
  private let callbackQueue: DispatchQueue
  private let checkIntervalMs: Int64

  private var state: State?
  private var timer: DispatchSourceTimer?

  init(
    silenceTimeoutMs: Int64 = 5_000,
    checkIntervalMs: Int64 = 200,
    callbackQueue: DispatchQueue = .main
  ) {
    self.silenceTimeoutMs = max(250, silenceTimeoutMs)
    self.checkIntervalMs = max(50, checkIntervalMs)
    self.callbackQueue = callbackQueue
    self.queue.setSpecific(key: queueKey, value: ())
  }

  deinit {
    if DispatchQueue.getSpecific(key: queueKey) != nil {
      cancelTimerLocked()
    } else {
      queue.sync {
        cancelTimerLocked()
      }
    }
  }

  func beginQuery(queryId: String = "query_\(UUID().uuidString)", startedAtMs: Int64 = Clocks.nowMs()) {
    queue.async {
      guard self.state == nil else {
        return
      }

      self.state = State(
        queryId: queryId,
        startedAtMs: startedAtMs,
        lastSpeechAtMs: startedAtMs
      )
      self.rescheduleTimerLocked()

      let event = QueryEndpointStartedEvent(queryId: queryId, startedAtMs: startedAtMs)
      self.callbackQueue.async {
        self.onQueryStarted?(event)
      }
    }
  }

  func recordSpeechActivity(at timestampMs: Int64 = Clocks.nowMs()) {
    queue.async {
      guard var current = self.state else {
        return
      }

      current.lastSpeechAtMs = max(current.lastSpeechAtMs, timestampMs)
      self.state = current

      let queryId = current.queryId
      self.callbackQueue.async {
        self.onSpeechActivityPing?(queryId, timestampMs)
      }
    }
  }

  func forceEnd(reason: QueryEndpointReason = .manualStop, endedAtMs: Int64 = Clocks.nowMs()) {
    queue.async {
      self.endCurrentQueryLocked(reason: reason, endedAtMs: endedAtMs)
    }
  }

  func reset() {
    queue.async {
      self.endCurrentQueryLocked(reason: .reset, endedAtMs: Clocks.nowMs())
    }
  }

  private func rescheduleTimerLocked() {
    cancelTimerLocked()

    guard state != nil else {
      return
    }

    let timer = DispatchSource.makeTimerSource(queue: queue)
    let intervalNs = UInt64(checkIntervalMs) * 1_000_000
    timer.schedule(deadline: .now() + .milliseconds(Int(checkIntervalMs)), repeating: .nanoseconds(Int(intervalNs)))
    timer.setEventHandler { [weak self] in
      self?.tickLocked()
    }
    self.timer = timer
    timer.resume()
  }

  private func cancelTimerLocked() {
    timer?.setEventHandler {}
    timer?.cancel()
    timer = nil
  }

  private func tickLocked() {
    guard let current = state else {
      return
    }

    let nowMs = Clocks.nowMs()
    let silenceElapsed = nowMs - current.lastSpeechAtMs
    if silenceElapsed >= silenceTimeoutMs {
      endCurrentQueryLocked(reason: .silenceTimeout, endedAtMs: nowMs)
    }
  }

  private func endCurrentQueryLocked(reason: QueryEndpointReason, endedAtMs: Int64) {
    guard let current = state else {
      return
    }

    cancelTimerLocked()
    state = nil

    let endedAt = max(current.startedAtMs, endedAtMs)
    let event = QueryEndpointEndedEvent(
      queryId: current.queryId,
      startedAtMs: current.startedAtMs,
      endedAtMs: endedAt,
      durationMs: endedAt - current.startedAtMs,
      reason: reason
    )

    callbackQueue.async {
      self.onQueryEnded?(event)
    }
  }
}

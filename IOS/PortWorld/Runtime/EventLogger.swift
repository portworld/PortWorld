import Foundation

@MainActor
public final class EventLogger: EventLoggerProtocol {
  public typealias Sink = (_ line: String) -> Void

  private let maxRetainedEvents: Int
  private let sink: Sink?
  private let encoder: JSONEncoder
  private var events: [AppEvent] = []

  public init(maxRetainedEvents: Int = 500, sink: Sink? = nil) {
    self.maxRetainedEvents = max(1, maxRetainedEvents)
    self.sink = sink
    self.encoder = JSONEncoder()
    self.encoder.outputFormatting = [.sortedKeys]
  }

  public func log(_ event: AppEvent) {
    events.append(event)
    if events.count > maxRetainedEvents {
      events.removeFirst(events.count - maxRetainedEvents)
    }

    let line: String
    if let data = try? encoder.encode(event), let serialized = String(data: data, encoding: .utf8) {
      line = serialized
    } else {
      line = #"{"name":"\#(event.name)","session_id":"\#(event.sessionID)","ts_ms":\#(event.tsMs)}"#
    }

    if let sink {
      sink(line)
    } else {
#if DEBUG
      print(line)
#endif
    }
  }

  public func log(
    name: String,
    sessionID: String,
    queryID: String? = nil,
    fields: [String: JSONValue] = [:],
    tsMs: Int64? = nil
  ) {
    let timestamp = tsMs ?? Clocks.nowMs()
    let event = AppEvent(name: name, sessionID: sessionID, queryID: queryID, tsMs: timestamp, fields: fields)
    log(event)
  }

  public func recentEvents(limit: Int = 100) -> [AppEvent] {
    guard limit > 0 else { return [] }
    return Array(events.suffix(limit))
  }

  public func clear() {
    events.removeAll(keepingCapacity: false)
  }
}

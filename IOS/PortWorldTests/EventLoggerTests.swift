import XCTest
@testable import PortWorld

final class EventLoggerTests: XCTestCase {
  private var managedLoggers: [EventLogger] = []
  private var tempLogDirectories: [URL] = []

  override func tearDown() async throws {
    for logger in managedLoggers {
      await logger.flushDiskWritesForTesting()
    }
    managedLoggers.removeAll()
    tempLogDirectories.forEach(removeDirectory)
    tempLogDirectories.removeAll()
    try await super.tearDown()
  }

  // MARK: - Sink emission

  func testLogEmitsValidJsonToSink() {
    var lines: [String] = []
    let logger = makeLogger(sink: { lines.append($0) })

    logger.log(name: "test.event", sessionID: "sess_1", fields: ["key": .string("val")])

    XCTAssertEqual(lines.count, 1)

    guard let data = lines.first?.data(using: .utf8),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else {
      XCTFail("Sink output is not valid JSON")
      return
    }

    XCTAssertEqual(json["name"] as? String, "test.event")
    XCTAssertEqual(json["session_id"] as? String, "sess_1")
    XCTAssertNotNil(json["ts_ms"])
  }

  func testLogWithQueryIdIncludesQueryId() {
    var lines: [String] = []
    let logger = makeLogger(sink: { lines.append($0) })

    logger.log(name: "query.started", sessionID: "sess_1", queryID: "q_42")

    guard let data = lines.first?.data(using: .utf8),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else {
      XCTFail("Sink output is not valid JSON")
      return
    }

    XCTAssertEqual(json["query_id"] as? String, "q_42")
  }

  func testLogWithoutQueryIdHasNullQueryId() {
    var lines: [String] = []
    let logger = makeLogger(sink: { lines.append($0) })

    logger.log(name: "session.activated", sessionID: "sess_1")

    guard let data = lines.first?.data(using: .utf8),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else {
      XCTFail("Sink output is not valid JSON")
      return
    }

    // query_id should be null or absent
    let queryId = json["query_id"]
    XCTAssertTrue(queryId == nil || queryId is NSNull)
  }

  func testLogWithExplicitTimestamp() {
    var lines: [String] = []
    let logger = makeLogger(sink: { lines.append($0) })

    logger.log(name: "timed", sessionID: "s", tsMs: 1234567890)

    guard let data = lines.first?.data(using: .utf8),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else {
      XCTFail("Sink output is not valid JSON")
      return
    }

    XCTAssertEqual(json["ts_ms"] as? Int, 1234567890)
  }

  func testLogMultipleEventsEmitsMultipleLines() {
    var lines: [String] = []
    let logger = makeLogger(sink: { lines.append($0) })

    logger.log(name: "e1", sessionID: "s")
    logger.log(name: "e2", sessionID: "s")
    logger.log(name: "e3", sessionID: "s")

    XCTAssertEqual(lines.count, 3)
  }

  // MARK: - recentEvents

  func testRecentEventsReturnsLoggedEvents() {
    let logger = makeLogger(sink: { _ in })

    logger.log(name: "e1", sessionID: "s")
    logger.log(name: "e2", sessionID: "s")
    logger.log(name: "e3", sessionID: "s")

    let recent = logger.recentEvents(limit: 10)
    XCTAssertEqual(recent.count, 3)
    XCTAssertEqual(recent[0].name, "e1")
    XCTAssertEqual(recent[1].name, "e2")
    XCTAssertEqual(recent[2].name, "e3")
  }

  func testRecentEventsRespectsLimit() {
    let logger = makeLogger(sink: { _ in })

    for i in 0..<10 {
      logger.log(name: "event_\(i)", sessionID: "s")
    }

    let recent = logger.recentEvents(limit: 3)
    XCTAssertEqual(recent.count, 3)
    // Should return the last 3 events
    XCTAssertEqual(recent[0].name, "event_7")
    XCTAssertEqual(recent[1].name, "event_8")
    XCTAssertEqual(recent[2].name, "event_9")
  }

  func testRecentEventsWithZeroLimitReturnsEmpty() {
    let logger = makeLogger(sink: { _ in })
    logger.log(name: "e", sessionID: "s")

    XCTAssertTrue(logger.recentEvents(limit: 0).isEmpty)
  }

  // MARK: - Retention / pruning

  func testPrunesOldestEventsWhenCapReached() {
    let logger = makeLogger(maxRetainedEvents: 3, sink: { _ in })

    logger.log(name: "a", sessionID: "s")
    logger.log(name: "b", sessionID: "s")
    logger.log(name: "c", sessionID: "s")
    logger.log(name: "d", sessionID: "s") // should evict "a"

    let events = logger.recentEvents(limit: 10)
    XCTAssertEqual(events.count, 3)
    XCTAssertEqual(events[0].name, "b")
    XCTAssertEqual(events[1].name, "c")
    XCTAssertEqual(events[2].name, "d")
  }

  func testRetentionAtExactCapDoesNotPrune() {
    let logger = makeLogger(maxRetainedEvents: 3, sink: { _ in })

    logger.log(name: "a", sessionID: "s")
    logger.log(name: "b", sessionID: "s")
    logger.log(name: "c", sessionID: "s")

    let events = logger.recentEvents(limit: 10)
    XCTAssertEqual(events.count, 3)
    XCTAssertEqual(events[0].name, "a")
  }

  // MARK: - clear

  func testClearRemovesAllEvents() {
    let logger = makeLogger(sink: { _ in })

    logger.log(name: "e1", sessionID: "s")
    logger.log(name: "e2", sessionID: "s")

    logger.clear()

    XCTAssertTrue(logger.recentEvents(limit: 100).isEmpty)
  }

  func testClearThenLogStartsFresh() {
    let logger = makeLogger(sink: { _ in })

    logger.log(name: "old", sessionID: "s")
    logger.clear()
    logger.log(name: "new", sessionID: "s")

    let events = logger.recentEvents(limit: 10)
    XCTAssertEqual(events.count, 1)
    XCTAssertEqual(events[0].name, "new")
  }

  // MARK: - Fields serialization

  func testFieldsAreIncludedInJson() {
    var lines: [String] = []
    let logger = makeLogger(sink: { lines.append($0) })

    logger.log(
      name: "with_fields",
      sessionID: "s",
      fields: [
        "count": .number(42),
        "active": .bool(true),
        "label": .string("test"),
      ]
    )

    guard let data = lines.first?.data(using: .utf8),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let fields = json["fields"] as? [String: Any]
    else {
      XCTFail("Could not parse fields from JSON")
      return
    }

    XCTAssertEqual(fields["count"] as? Double, 42)
    XCTAssertEqual(fields["active"] as? Bool, true)
    XCTAssertEqual(fields["label"] as? String, "test")
  }

  func testEmptyFieldsSerializedAsEmptyObject() {
    var lines: [String] = []
    let logger = makeLogger(sink: { lines.append($0) })

    logger.log(name: "no_fields", sessionID: "s", fields: [:])

    guard let data = lines.first?.data(using: .utf8),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let fields = json["fields"] as? [String: Any]
    else {
      XCTFail("Could not parse JSON")
      return
    }

    XCTAssertTrue(fields.isEmpty)
  }

  // MARK: - AppEvent direct logging

  func testLogAppEventDirectly() {
    var lines: [String] = []
    let logger = makeLogger(sink: { lines.append($0) })

    let event = AppEvent(
      name: "direct.event",
      sessionID: "sess_direct",
      queryID: "q_direct",
      tsMs: 77777,
      fields: ["x": .number(1)]
    )

    logger.log(event)

    XCTAssertEqual(lines.count, 1)

    guard let data = lines.first?.data(using: .utf8),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else {
      XCTFail("Not valid JSON")
      return
    }

    XCTAssertEqual(json["name"] as? String, "direct.event")
    XCTAssertEqual(json["session_id"] as? String, "sess_direct")
    XCTAssertEqual(json["query_id"] as? String, "q_direct")
    XCTAssertEqual(json["ts_ms"] as? Int, 77777)
  }

  // MARK: - Disk persistence

  func testLogPersistsJsonlToDisk() async throws {
    let logger = makeLogger(sink: { _ in })
    let logsDirectory = currentManagedLogsDirectory()

    logger.log(name: "disk.event", sessionID: "sess_disk", tsMs: 123)
    await logger.flushDiskWritesForTesting()

    let currentLogURL = logsDirectory.appendingPathComponent("events-1.jsonl")
    let lines = try readLines(from: currentLogURL)
    XCTAssertEqual(lines.count, 1)

    guard let data = lines[0].data(using: .utf8),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else {
      XCTFail("Disk line is not valid JSON")
      return
    }

    XCTAssertEqual(json["name"] as? String, "disk.event")
    XCTAssertEqual(json["session_id"] as? String, "sess_disk")
    XCTAssertEqual(json["ts_ms"] as? Int, 123)
  }

  func testDiskRotationKeepsThreeFiles() async throws {
    let logger = makeLogger(
      sink: { _ in },
      maxLogFileBytes: 120,
      maxLogFiles: 3
    )
    let logsDirectory = currentManagedLogsDirectory()

    for index in 0..<8 {
      logger.log(
        name: "rotation_\(index)",
        sessionID: "sess_rotation",
        fields: ["payload": .string(String(repeating: "x", count: 80))]
      )
    }
    await logger.flushDiskWritesForTesting()

    let events1 = logsDirectory.appendingPathComponent("events-1.jsonl")
    let events2 = logsDirectory.appendingPathComponent("events-2.jsonl")
    let events3 = logsDirectory.appendingPathComponent("events-3.jsonl")
    let events4 = logsDirectory.appendingPathComponent("events-4.jsonl")

    XCTAssertTrue(FileManager.default.fileExists(atPath: events1.path))
    XCTAssertTrue(FileManager.default.fileExists(atPath: events2.path))
    XCTAssertTrue(FileManager.default.fileExists(atPath: events3.path))
    XCTAssertFalse(FileManager.default.fileExists(atPath: events4.path))

    let mostRecent = try readLines(from: events1).joined(separator: "\n")
    XCTAssertTrue(mostRecent.contains("rotation_7"))
  }

  func testExportCurrentLogReturnsCurrentLogURL() async throws {
    let logger = makeLogger(sink: { _ in })

    logger.log(name: "exported.event", sessionID: "sess_export")
    await logger.flushDiskWritesForTesting()

    let exportedURL = logger.exportCurrentLog()
    XCTAssertEqual(exportedURL.lastPathComponent, "events-1.jsonl")
    XCTAssertTrue(FileManager.default.fileExists(atPath: exportedURL.path))

    let lines = try readLines(from: exportedURL)
    XCTAssertEqual(lines.count, 1)
    XCTAssertTrue(lines[0].contains("exported.event"))
  }

  private func currentManagedLogsDirectory() -> URL {
    guard let logsDirectory = tempLogDirectories.last else {
      XCTFail("No managed logs directory available")
      return FileManager.default.temporaryDirectory
    }
    return logsDirectory
  }

  private func makeTempLogsDirectory() -> URL {
    let root = FileManager.default.temporaryDirectory
      .appendingPathComponent("EventLoggerTests-\(UUID().uuidString)", isDirectory: true)
    do {
      try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    } catch {
      XCTFail("Failed creating temp directory: \(error.localizedDescription)")
    }
    return root
  }

  private func removeDirectory(_ url: URL) {
    do {
      try FileManager.default.removeItem(at: url)
    } catch {
      // Best-effort cleanup for test artifacts.
    }
  }

  private func readLines(from url: URL) throws -> [String] {
    let content = try String(contentsOf: url, encoding: .utf8)
    return content.split(separator: "\n").map(String.init)
  }

  private func makeLogger(
    maxRetainedEvents: Int = 500,
    sink: EventLogger.Sink?,
    maxLogFileBytes: Int = 5 * 1024 * 1024,
    maxLogFiles: Int = 3
  ) -> EventLogger {
    let logsDirectory = makeTempLogsDirectory()
    tempLogDirectories.append(logsDirectory)
    let logger = EventLogger(
      maxRetainedEvents: maxRetainedEvents,
      sink: sink,
      logsDirectoryURL: logsDirectory,
      maxLogFileBytes: maxLogFileBytes,
      maxLogFiles: maxLogFiles
    )
    managedLoggers.append(logger)
    return logger
  }
}

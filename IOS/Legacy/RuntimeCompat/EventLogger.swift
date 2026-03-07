import Foundation
import OSLog

@MainActor
public final class EventLogger: EventLoggerProtocol {
  public typealias Sink = (_ line: String) -> Void
  private typealias DiskStore = EventLogDiskStore
  private static let fallbackLogger = Logger(
    subsystem: Bundle.main.bundleIdentifier ?? "PortWorld",
    category: "EventLogger"
  )
  private let maxRetainedEvents: Int
  private let sink: Sink?
  private let encoder: JSONEncoder
  private let diskStore: DiskStore
  private var events: [AppEvent] = []

  public init(
    maxRetainedEvents: Int = 500,
    sink: Sink? = nil,
    logsDirectoryURL: URL? = nil,
    maxLogFileBytes: Int = 5 * 1024 * 1024,
    maxLogFiles: Int = 3,
    fileManager: FileManager = .default
  ) {
    self.maxRetainedEvents = max(1, maxRetainedEvents)
    self.sink = sink
    self.encoder = JSONEncoder()
    self.encoder.outputFormatting = [.sortedKeys]
    self.diskStore = DiskStore(
      fileManager: fileManager,
      logsDirectoryURL: logsDirectoryURL,
      maxLogFileBytes: maxLogFileBytes,
      maxLogFiles: maxLogFiles
    )
  }

  public func log(_ event: AppEvent) {
    events.append(event)
    if events.count > maxRetainedEvents {
      events.removeFirst(events.count - maxRetainedEvents)
    }

    let line: String
    do {
      let data = try encoder.encode(event)
      if let serialized = String(data: data, encoding: .utf8) {
        line = serialized
      } else {
        line = #"{"name":"\#(event.name)","session_id":"\#(event.sessionID)","ts_ms":\#(event.tsMs)}"#
      }
    } catch {
      line = #"{"name":"\#(event.name)","session_id":"\#(event.sessionID)","ts_ms":\#(event.tsMs)}"#
    }

    if let sink {
      sink(line)
    } else {
#if DEBUG
      Self.fallbackLogger.debug("\(line, privacy: .public)")
#else
      Self.fallbackLogger.debug("\(line, privacy: .private)")
#endif
    }

    diskStore.enqueueAppend(line: line)
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

  public func exportCurrentLog() -> URL {
    diskStore.exportCurrentLog()
  }

  public func clear() {
    events.removeAll(keepingCapacity: false)
  }

  func flushDiskWritesForTesting() async {
    await diskStore.flushPendingWrites()
  }
}

fileprivate struct EventLogDiskStore {
  private static let queueSpecificKey = DispatchSpecificKey<UInt8>()
  private static let queueSpecificValue: UInt8 = 1
  private let fileManager: FileManager
  private let maxLogFileBytes: UInt64
  private let maxLogFiles: Int
  private let ioQueue = DispatchQueue(label: "PortWorld.EventLogger.DiskStore", qos: .utility)
  private let logsDirectoryURL: URL
  private let fallbackLogger = Logger(
    subsystem: Bundle.main.bundleIdentifier ?? "PortWorld",
    category: "EventLoggerDiskStore"
  )

  init(
    fileManager: FileManager,
    logsDirectoryURL: URL?,
    maxLogFileBytes: Int,
    maxLogFiles: Int
  ) {
    self.fileManager = fileManager
    self.maxLogFileBytes = UInt64(max(1, maxLogFileBytes))
    self.maxLogFiles = max(1, maxLogFiles)
    self.logsDirectoryURL = EventLogDiskStore.resolveLogsDirectory(
      fileManager: fileManager,
      explicitLogsDirectoryURL: logsDirectoryURL
    )
    ioQueue.setSpecific(key: Self.queueSpecificKey, value: Self.queueSpecificValue)
  }

  func enqueueAppend(line: String) {
    ioQueue.async {
      self.appendLine(line)
    }
  }

  func exportCurrentLog() -> URL {
    let currentURL = fileURL(index: 1)
    if isOnIOQueue() {
      ensureLogsDirectoryExists()
      ensureFileExists(at: currentURL)
      return currentURL
    }
    // Avoid blocking high-priority callers on the utility queue.
    ensureLogsDirectoryExists()
    ensureFileExists(at: currentURL)
    return currentURL
  }

  func flushPendingWrites() async {
    if isOnIOQueue() { return }
    await withCheckedContinuation { continuation in
      ioQueue.async {
        continuation.resume()
      }
    }
  }

  private func appendLine(_ line: String) {
    ensureLogsDirectoryExists()

    let payload = "\(line)\n"
    guard let data = payload.data(using: .utf8) else {
      fallbackLogger.error("Failed to encode event line as UTF-8 for disk logging")
      return
    }

    rotateIfNeeded(incomingBytes: UInt64(data.count))
    appendData(data, to: fileURL(index: 1))
  }

  private func isOnIOQueue() -> Bool {
    DispatchQueue.getSpecific(key: Self.queueSpecificKey) == Self.queueSpecificValue
  }

  private func rotateIfNeeded(incomingBytes: UInt64) {
    let currentURL = fileURL(index: 1)
    ensureFileExists(at: currentURL)

    let currentFileSize = fileSize(at: currentURL)
    guard currentFileSize > 0, currentFileSize + incomingBytes > maxLogFileBytes else {
      return
    }

    for index in stride(from: maxLogFiles, through: 2, by: -1) {
      let destination = fileURL(index: index)
      let source = fileURL(index: index - 1)

      if fileManager.fileExists(atPath: destination.path) {
        do {
          try fileManager.removeItem(at: destination)
        } catch {
          fallbackLogger.error("Failed removing rotated file \(destination.path, privacy: .public): \(error.localizedDescription, privacy: .public)")
        }
      }

      if fileManager.fileExists(atPath: source.path) {
        do {
          try fileManager.moveItem(at: source, to: destination)
        } catch {
          fallbackLogger.error("Failed rotating file \(source.path, privacy: .public) -> \(destination.path, privacy: .public): \(error.localizedDescription, privacy: .public)")
        }
      }
    }
  }

  private func appendData(_ data: Data, to url: URL) {
    ensureFileExists(at: url)

    do {
      let handle = try FileHandle(forWritingTo: url)
      defer {
        do {
          try handle.close()
        } catch {
          fallbackLogger.error("Failed closing log file handle for \(url.path, privacy: .public): \(error.localizedDescription, privacy: .public)")
        }
      }
      do {
        try handle.seekToEnd()
        try handle.write(contentsOf: data)
      } catch {
        fallbackLogger.error("Failed appending event log data to \(url.path, privacy: .public): \(error.localizedDescription, privacy: .public)")
      }
    } catch {
      fallbackLogger.error("Failed opening log file for append \(url.path, privacy: .public): \(error.localizedDescription, privacy: .public)")
    }
  }

  private func ensureLogsDirectoryExists() {
    if fileManager.fileExists(atPath: logsDirectoryURL.path) { return }

    do {
      try fileManager.createDirectory(
        at: logsDirectoryURL,
        withIntermediateDirectories: true
      )
    } catch {
      fallbackLogger.error("Failed creating logs directory \(self.logsDirectoryURL.path, privacy: .public): \(error.localizedDescription, privacy: .public)")
    }
  }

  private func ensureFileExists(at url: URL) {
    if fileManager.fileExists(atPath: url.path) { return }

    let created = fileManager.createFile(atPath: url.path, contents: Data())
    if !created {
      fallbackLogger.error("Failed creating event log file \(url.path, privacy: .public)")
    }
  }

  private func fileSize(at url: URL) -> UInt64 {
    do {
      let attrs = try fileManager.attributesOfItem(atPath: url.path)
      return attrs[.size] as? UInt64 ?? 0
    } catch {
      fallbackLogger.error("Failed reading log file size \(url.path, privacy: .public): \(error.localizedDescription, privacy: .public)")
      return 0
    }
  }

  private func fileURL(index: Int) -> URL {
    logsDirectoryURL.appendingPathComponent("events-\(index).jsonl")
  }

  private static func resolveLogsDirectory(
    fileManager: FileManager,
    explicitLogsDirectoryURL: URL?
  ) -> URL {
    if let explicitLogsDirectoryURL {
      return explicitLogsDirectoryURL
    }

    if let appSupportURL = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first {
      return appSupportURL.appendingPathComponent("logs", isDirectory: true)
    }

    return fileManager.temporaryDirectory.appendingPathComponent("logs", isDirectory: true)
  }
}

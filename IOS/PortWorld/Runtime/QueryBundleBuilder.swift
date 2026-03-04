import Foundation

struct QueryBundleUploadResult {
  let queryId: String
  let statusCode: Int
  let latencyMs: Int64
  let requestBytes: Int
  let responseBytes: Int
  let audioBytes: Int64
  let videoBytes: Int64
  let attemptCount: Int
  let success: Bool
}

enum QueryBundleBuilderError: LocalizedError {
  case invalidAudioFile(URL)
  case invalidVideoFile(URL)
  case metadataEncodingFailed(Error)
  case timeout
  case transport(message: String)
  case uploadFailed(statusCode: Int, message: String?)

  var errorDescription: String? {
    switch self {
    case .invalidAudioFile(let url):
      return "Audio file does not exist or is unreadable: \(url.path)"
    case .invalidVideoFile(let url):
      return "Video file does not exist or is unreadable: \(url.path)"
    case .metadataEncodingFailed(let error):
      return "Unable to encode query metadata JSON: \(error.localizedDescription)"
    case .timeout:
      return "Query bundle upload timed out"
    case .transport(let message):
      return "Query bundle upload transport error: \(message)"
    case .uploadFailed(let statusCode, let message):
      let suffix = message.flatMap { " (\($0))" } ?? ""
      return "Query bundle upload failed with HTTP \(statusCode)\(suffix)"
    }
  }
}

final class QueryBundleBuilder: QueryBundleBuilderProtocol {
  private let endpointURL: URL
  private let defaultHeaders: [String: String]
  private let urlSession: URLSession
  private let requestTimeoutMs: Int64
  private let maxRetryCount: Int
  private let baseRetryDelayMs: Int64
  private let maxRetryDelayMs: Int64

  init(
    endpointURL: URL,
    defaultHeaders: [String: String] = [:],
    requestTimeoutMs: Int64 = 7_500,
    maxRetryCount: Int = 2,
    baseRetryDelayMs: Int64 = 500,
    maxRetryDelayMs: Int64 = 4_000,
    urlSession: URLSession = .shared
  ) {
    self.endpointURL = endpointURL
    self.defaultHeaders = defaultHeaders
    self.requestTimeoutMs = max(1_000, requestTimeoutMs)
    self.maxRetryCount = max(0, maxRetryCount)
    self.baseRetryDelayMs = max(100, baseRetryDelayMs)
    self.maxRetryDelayMs = max(self.baseRetryDelayMs, maxRetryDelayMs)
    self.urlSession = urlSession
  }

  @discardableResult
  func uploadQueryBundle(
    metadata: QueryMetadata,
    audioFileURL: URL,
    videoFileURL: URL
  ) async throws -> QueryBundleUploadResult {
    let audioBytes = try Self.validateFileSize(at: audioFileURL, invalidFileError: .invalidAudioFile(audioFileURL))
    let videoBytes = try Self.validateFileSize(at: videoFileURL, invalidFileError: .invalidVideoFile(videoFileURL))

    var lastError: Error?
    let maxAttemptCount = maxRetryCount + 1

    for attempt in 1...maxAttemptCount {
      do {
        let boundary = "Boundary-\(UUID().uuidString)"
        let (multipartFileURL, requestBytes) = try makeMultipartTempFile(
          boundary: boundary,
          metadata: metadata,
          audioFileURL: audioFileURL,
          videoFileURL: videoFileURL
        )
        defer { Self.cleanupTempArtifact(at: multipartFileURL) }

        var request = URLRequest(url: endpointURL)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.setValue(String(requestBytes), forHTTPHeaderField: "Content-Length")
        for (name, value) in defaultHeaders {
          request.setValue(value, forHTTPHeaderField: name)
        }
        guard let bodyStream = InputStream(url: multipartFileURL) else {
          throw QueryBundleBuilderError.transport(message: "Unable to open multipart stream at \(multipartFileURL.path)")
        }
        request.httpBodyStream = bodyStream
        request.timeoutInterval = TimeInterval(requestTimeoutMs) / 1000.0

        let startedAt = Date()
        let (responseData, response) = try await streamedUpload(for: request)
        let latencyMs = Int64(Date().timeIntervalSince(startedAt) * 1000)

        guard let httpResponse = response as? HTTPURLResponse else {
          throw QueryBundleBuilderError.transport(message: "No HTTPURLResponse")
        }

        let success = 200..<300 ~= httpResponse.statusCode
        if !success {
          let message = String(data: responseData, encoding: .utf8)
          let serverError = QueryBundleBuilderError.uploadFailed(statusCode: httpResponse.statusCode, message: message)
          if shouldRetry(statusCode: httpResponse.statusCode, attempt: attempt) {
            try await sleepBeforeRetry(attempt: attempt)
            continue
          }
          throw serverError
        }

        return QueryBundleUploadResult(
          queryId: metadata.queryID,
          statusCode: httpResponse.statusCode,
          latencyMs: latencyMs,
          requestBytes: requestBytes,
          responseBytes: responseData.count,
          audioBytes: audioBytes,
          videoBytes: videoBytes,
          attemptCount: attempt,
          success: true
        )
      } catch {
        lastError = mapTransportError(error)
        if shouldRetry(error: error, attempt: attempt) {
          try await sleepBeforeRetry(attempt: attempt)
          continue
        }
        throw (lastError ?? error)
      }
    }

    throw (lastError ?? QueryBundleBuilderError.transport(message: "Unknown upload failure"))
  }

  private func streamedUpload(for request: URLRequest) async throws -> (Data, URLResponse) {
    let delegate = StreamedUploadDelegate()
    let session = URLSession(
      configuration: urlSession.configuration,
      delegate: delegate,
      delegateQueue: nil
    )
    let task = session.uploadTask(withStreamedRequest: request)
    delegate.attach(task: task, session: session)

    return try await withTaskCancellationHandler(operation: {
      try await delegate.awaitResultAndStart()
    }, onCancel: {
      delegate.cancel()
    })
  }

  private func makeMultipartTempFile(
    boundary: String,
    metadata: QueryMetadata,
    audioFileURL: URL,
    videoFileURL: URL
  ) throws -> (URL, Int) {
    let metadataJSON: Data
    do {
      metadataJSON = try JSONEncoder().encode(metadata)
    } catch {
      throw QueryBundleBuilderError.metadataEncodingFailed(error)
    }

    let tempURL = FileManager.default.temporaryDirectory
      .appendingPathComponent("query-upload-\(UUID().uuidString)")
      .appendingPathExtension("multipart")
    guard let outputStream = OutputStream(url: tempURL, append: false) else {
      throw QueryBundleBuilderError.transport(message: "Unable to create multipart temp file stream")
    }

    outputStream.open()
    defer { outputStream.close() }

    do {
      try writeUTF8("--\(boundary)\r\n", to: outputStream)
      try writeUTF8("Content-Disposition: form-data; name=\"metadata\"\r\n", to: outputStream)
      try writeUTF8("Content-Type: application/json\r\n\r\n", to: outputStream)
      try write(metadataJSON, to: outputStream)
      try writeUTF8("\r\n", to: outputStream)

      try writeUTF8("--\(boundary)\r\n", to: outputStream)
      try writeUTF8(
        "Content-Disposition: form-data; name=\"audio\"; filename=\"\(audioFileURL.lastPathComponent)\"\r\n",
        to: outputStream
      )
      try writeUTF8("Content-Type: audio/wav\r\n\r\n", to: outputStream)
      try copyFileContents(from: audioFileURL, to: outputStream, invalidFileError: .invalidAudioFile(audioFileURL))
      try writeUTF8("\r\n", to: outputStream)

      try writeUTF8("--\(boundary)\r\n", to: outputStream)
      try writeUTF8(
        "Content-Disposition: form-data; name=\"video\"; filename=\"\(videoFileURL.lastPathComponent)\"\r\n",
        to: outputStream
      )
      try writeUTF8("Content-Type: video/mp4\r\n\r\n", to: outputStream)
      try copyFileContents(from: videoFileURL, to: outputStream, invalidFileError: .invalidVideoFile(videoFileURL))
      try writeUTF8("\r\n", to: outputStream)

      try writeUTF8("--\(boundary)--\r\n", to: outputStream)
    } catch {
      Self.cleanupTempArtifact(at: tempURL)
      throw error
    }

    let requestBytes = try Self.validateFileSize(
      at: tempURL,
      invalidFileError: QueryBundleBuilderError.transport(message: "Multipart temp file missing")
    )
    return (tempURL, Int(requestBytes))
  }

  private static func validateFileSize(at fileURL: URL, invalidFileError: QueryBundleBuilderError) throws -> Int64 {
    let filePath = fileURL.path
    guard FileManager.default.fileExists(atPath: filePath) else {
      throw invalidFileError
    }

    let sizeNumber: NSNumber
    do {
      let attributes = try FileManager.default.attributesOfItem(atPath: filePath)
      guard let extractedSize = attributes[.size] as? NSNumber else {
        throw invalidFileError
      }
      sizeNumber = extractedSize
    } catch let error as QueryBundleBuilderError {
      throw error
    } catch {
      throw invalidFileError
    }

    return sizeNumber.int64Value
  }

  private static func cleanupTempArtifact(at url: URL) {
    do {
      if FileManager.default.fileExists(atPath: url.path) {
        try FileManager.default.removeItem(at: url)
      }
    } catch {
#if DEBUG
      NSLog("QueryBundleBuilder: failed to remove temp multipart artifact at %@: %@", url.path, error.localizedDescription)
#endif
    }
  }

  private func writeUTF8(_ value: String, to outputStream: OutputStream) throws {
    guard let data = value.data(using: .utf8) else { return }
    try write(data, to: outputStream)
  }

  private func write(_ data: Data, to outputStream: OutputStream) throws {
    try data.withUnsafeBytes { rawBuffer in
      guard let baseAddress = rawBuffer.bindMemory(to: UInt8.self).baseAddress else { return }
      var bytesRemaining = data.count
      var offset = 0
      while bytesRemaining > 0 {
        let bytesWritten = outputStream.write(baseAddress.advanced(by: offset), maxLength: bytesRemaining)
        if bytesWritten < 0 {
          throw QueryBundleBuilderError.transport(
            message: outputStream.streamError?.localizedDescription ?? "Failed writing multipart body stream"
          )
        }
        if bytesWritten == 0 {
          throw QueryBundleBuilderError.transport(message: "Multipart stream write returned zero bytes")
        }
        offset += bytesWritten
        bytesRemaining -= bytesWritten
      }
    }
  }

  private func copyFileContents(
    from sourceURL: URL,
    to outputStream: OutputStream,
    invalidFileError: QueryBundleBuilderError
  ) throws {
    guard let inputStream = InputStream(url: sourceURL) else {
      throw invalidFileError
    }

    inputStream.open()
    defer { inputStream.close() }

    let bufferSize = 64 * 1024
    let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: bufferSize)
    defer { buffer.deallocate() }

    while true {
      let readCount = inputStream.read(buffer, maxLength: bufferSize)
      if readCount < 0 {
        throw QueryBundleBuilderError.transport(
          message: inputStream.streamError?.localizedDescription ?? "Failed reading \(sourceURL.path)"
        )
      }
      if readCount == 0 {
        break
      }

      var totalWritten = 0
      while totalWritten < readCount {
        let written = outputStream.write(
          buffer.advanced(by: totalWritten),
          maxLength: readCount - totalWritten
        )
        if written < 0 {
          throw QueryBundleBuilderError.transport(
            message: outputStream.streamError?.localizedDescription ?? "Failed writing multipart body stream"
          )
        }
        if written == 0 {
          throw QueryBundleBuilderError.transport(message: "Multipart stream write returned zero bytes")
        }
        totalWritten += written
      }
    }
  }

  private func shouldRetry(error: Error, attempt: Int) -> Bool {
    guard attempt <= maxRetryCount else { return false }
    if isCancellation(error) { return false }

    if let queryError = error as? QueryBundleBuilderError {
      switch queryError {
      case .timeout, .transport:
        return true
      default:
        return false
      }
    }

    if let urlError = error as? URLError {
      switch urlError.code {
      case .timedOut, .cannotConnectToHost, .networkConnectionLost, .notConnectedToInternet:
        return true
      default:
        return false
      }
    }

    return false
  }

  private func shouldRetry(statusCode: Int, attempt: Int) -> Bool {
    guard attempt <= maxRetryCount else { return false }
    return statusCode == 429 || (500...599).contains(statusCode)
  }

  private func sleepBeforeRetry(attempt: Int) async throws {
    let bounded = min(max(attempt - 1, 0), 6)
    let multiplier = Int64(1 << bounded)
    let scaled = min(baseRetryDelayMs * multiplier, maxRetryDelayMs)
    let jitter = Double.random(in: 0.85...1.15)
    let delayMs = Int64(Double(scaled) * jitter)
    try await Task.sleep(nanoseconds: UInt64(delayMs) * 1_000_000)
  }

  private func mapTransportError(_ error: Error) -> Error {
    if isCancellation(error) {
      return CancellationError()
    }

    if let queryError = error as? QueryBundleBuilderError {
      return queryError
    }
    if let urlError = error as? URLError {
      if urlError.code == .cancelled {
        return CancellationError()
      }
      if urlError.code == .timedOut {
        return QueryBundleBuilderError.timeout
      }
      return QueryBundleBuilderError.transport(message: urlError.localizedDescription)
    }
    return QueryBundleBuilderError.transport(message: error.localizedDescription)
  }

  private func isCancellation(_ error: Error) -> Bool {
    if error is CancellationError {
      return true
    }
    let nsError = error as NSError
    if nsError.domain == "Swift.CancellationError" {
      return true
    }
    guard let urlError = error as? URLError else {
      return false
    }
    return urlError.code == .cancelled
  }
}

private final class StreamedUploadDelegate: NSObject, URLSessionDataDelegate, URLSessionTaskDelegate {
  private var continuation: CheckedContinuation<(Data, URLResponse), Error>?
  private var response: URLResponse?
  private var responseData = Data()
  private var uploadTask: URLSessionUploadTask?
  private var session: URLSession?
  private let lock = NSLock()

  func attach(task: URLSessionUploadTask, session: URLSession) {
    lock.lock()
    uploadTask = task
    self.session = session
    lock.unlock()
  }

  func cancel() {
    lock.lock()
    let task = uploadTask
    let session = session
    lock.unlock()

    task?.cancel()
    session?.invalidateAndCancel()
  }

  func awaitResultAndStart() async throws -> (Data, URLResponse) {
    try await withCheckedThrowingContinuation { continuation in
      lock.lock()
      self.continuation = continuation
      let task = uploadTask
      lock.unlock()
      task?.resume()
    }
  }

  func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
    lock.lock()
    responseData.append(data)
    lock.unlock()
  }

  func urlSession(
    _ session: URLSession,
    dataTask: URLSessionDataTask,
    didReceive response: URLResponse,
    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void
  ) {
    lock.lock()
    self.response = response
    lock.unlock()
    completionHandler(.allow)
  }

  func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
    lock.lock()
    let continuation = continuation
    self.continuation = nil
    let response = response
    let data = responseData
    lock.unlock()

    defer { session.finishTasksAndInvalidate() }

    if let error {
      continuation?.resume(throwing: error)
      return
    }

    guard let response else {
      continuation?.resume(
        throwing: QueryBundleBuilderError.transport(message: "No response received from streamed upload task")
      )
      return
    }

    continuation?.resume(returning: (data, response))
  }
}

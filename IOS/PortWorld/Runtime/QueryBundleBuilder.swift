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
    let (audioData, audioBytes) = try Self.loadFileData(at: audioFileURL, invalidFileError: .invalidAudioFile(audioFileURL))
    let (videoData, videoBytes) = try Self.loadFileData(at: videoFileURL, invalidFileError: .invalidVideoFile(videoFileURL))

    let boundary = "Boundary-\(UUID().uuidString)"
    let body = try makeMultipartBody(
      boundary: boundary,
      metadata: metadata,
      audioData: audioData,
      audioFileName: audioFileURL.lastPathComponent,
      videoData: videoData,
      videoFileName: videoFileURL.lastPathComponent
    )

    var lastError: Error?
    let maxAttemptCount = maxRetryCount + 1

    for attempt in 1...maxAttemptCount {
      do {
        var request = URLRequest(url: endpointURL)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        for (name, value) in defaultHeaders {
          request.setValue(value, forHTTPHeaderField: name)
        }
        request.httpBody = body
        request.timeoutInterval = TimeInterval(requestTimeoutMs) / 1000.0

        let startedAt = Date()
        let (responseData, response) = try await urlSession.data(for: request)
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
          requestBytes: body.count,
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

  private func makeMultipartBody(
    boundary: String,
    metadata: QueryMetadata,
    audioData: Data,
    audioFileName: String,
    videoData: Data,
    videoFileName: String
  ) throws -> Data {
    let metadataJSON: Data
    do {
      metadataJSON = try JSONEncoder().encode(metadata)
    } catch {
      throw QueryBundleBuilderError.metadataEncodingFailed(error)
    }

    var body = Data()

    body.appendUTF8("--\(boundary)\r\n")
    body.appendUTF8("Content-Disposition: form-data; name=\"metadata\"\r\n")
    body.appendUTF8("Content-Type: application/json\r\n\r\n")
    body.append(metadataJSON)
    body.appendUTF8("\r\n")

    body.appendUTF8("--\(boundary)\r\n")
    body.appendUTF8("Content-Disposition: form-data; name=\"audio\"; filename=\"\(audioFileName)\"\r\n")
    body.appendUTF8("Content-Type: audio/wav\r\n\r\n")
    body.append(audioData)
    body.appendUTF8("\r\n")

    body.appendUTF8("--\(boundary)\r\n")
    body.appendUTF8("Content-Disposition: form-data; name=\"video\"; filename=\"\(videoFileName)\"\r\n")
    body.appendUTF8("Content-Type: video/mp4\r\n\r\n")
    body.append(videoData)
    body.appendUTF8("\r\n")

    body.appendUTF8("--\(boundary)--\r\n")
    return body
  }

  private static func loadFileData(at fileURL: URL, invalidFileError: QueryBundleBuilderError) throws -> (Data, Int64) {
    let filePath = fileURL.path
    guard FileManager.default.fileExists(atPath: filePath),
          let attributes = try? FileManager.default.attributesOfItem(atPath: filePath),
          let sizeNumber = attributes[.size] as? NSNumber else {
      throw invalidFileError
    }

    let data = try Data(contentsOf: fileURL)
    return (data, sizeNumber.int64Value)
  }

  private func shouldRetry(error: Error, attempt: Int) -> Bool {
    guard attempt <= maxRetryCount else { return false }

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
    if let queryError = error as? QueryBundleBuilderError {
      return queryError
    }
    if let urlError = error as? URLError {
      if urlError.code == .timedOut {
        return QueryBundleBuilderError.timeout
      }
      return QueryBundleBuilderError.transport(message: urlError.localizedDescription)
    }
    return QueryBundleBuilderError.transport(message: error.localizedDescription)
  }
}

private extension Data {
  mutating func appendUTF8(_ value: String) {
    if let data = value.data(using: .utf8) {
      append(data)
    }
  }
}

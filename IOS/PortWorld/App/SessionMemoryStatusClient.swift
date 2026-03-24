import Foundation

struct SessionMemoryStatusClient {
  struct SessionMemoryStatus: Decodable, Sendable {
    let status: String
    let recentFrames: [RecentFrame]

    private enum CodingKeys: String, CodingKey {
      case status
      case recentFrames = "recent_frames"
    }
  }

  struct RecentFrame: Decodable, Sendable {
    let frameID: String
    let processingStatus: String
    let errorCode: String?
    let errorDetails: ErrorDetails?

    private enum CodingKeys: String, CodingKey {
      case frameID = "frame_id"
      case processingStatus = "processing_status"
      case errorCode = "error_code"
      case errorDetails = "error_details"
    }
  }

  struct ErrorDetails: Decodable, Sendable {
    let providerMessage: String?
    let providerErrorCode: String?

    private enum CodingKeys: String, CodingKey {
      case providerMessage = "provider_message"
      case providerErrorCode = "provider_error_code"
    }
  }

  enum ClientError: LocalizedError {
    case invalidEndpoint
    case requestFailed
    case unexpectedResponse
    case unexpectedStatusCode(Int)
    case invalidPayload

    var errorDescription: String? {
      switch self {
      case .invalidEndpoint:
        return "Unable to resolve the session memory status endpoint."
      case .requestFailed:
        return "The backend session memory status endpoint could not be reached."
      case .unexpectedResponse:
        return "The backend session memory status endpoint returned an unexpected response."
      case .unexpectedStatusCode(let statusCode):
        return "The backend session memory status endpoint returned HTTP \(statusCode)."
      case .invalidPayload:
        return "The backend session memory status payload could not be decoded."
      }
    }
  }

  private let urlSession: URLSession
  private let jsonDecoder = JSONDecoder()

  init(urlSession: URLSession = .shared) {
    self.urlSession = urlSession
  }

  func fetchStatus(
    sessionID: String,
    endpointURL: URL,
    headers: [String: String]
  ) async throws -> SessionMemoryStatus {
    guard let url = BackendEndpoints.sessionMemoryStatusURL(sessionID: sessionID, from: endpointURL) else {
      throw ClientError.invalidEndpoint
    }

    var request = URLRequest(url: url)
    request.httpMethod = "GET"
    request.timeoutInterval = 5
    for (name, value) in headers {
      request.setValue(value, forHTTPHeaderField: name)
    }

    let data: Data
    let response: URLResponse
    do {
      (data, response) = try await urlSession.data(for: request)
    } catch {
      throw ClientError.requestFailed
    }

    guard let httpResponse = response as? HTTPURLResponse else {
      throw ClientError.unexpectedResponse
    }

    guard (200...299).contains(httpResponse.statusCode) else {
      throw ClientError.unexpectedStatusCode(httpResponse.statusCode)
    }

    do {
      return try jsonDecoder.decode(SessionMemoryStatus.self, from: data)
    } catch {
      throw ClientError.invalidPayload
    }
  }
}

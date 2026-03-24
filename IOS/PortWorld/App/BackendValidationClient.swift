import Foundation

struct BackendValidationClient {
  enum ValidationError: LocalizedError {
    case invalidBaseURL
    case unreachable
    case unexpectedResponse
    case endpointNotFound(path: String)
    case livenessCheckFailed(statusCode: Int)
    case unauthorized
    case backendNotReady
    case readinessFailed(statusCode: Int)

    var errorDescription: String? {
      switch self {
      case .invalidBaseURL:
        return "Enter a valid backend URL."
      case .unreachable:
        return "The backend could not be reached."
      case .unexpectedResponse:
        return "The backend returned an unexpected response."
      case .endpointNotFound(let path):
        return "The backend is reachable, but \(path) was not found. Check that the iOS app is using the current backend routes."
      case .livenessCheckFailed(let statusCode):
        return "Liveness check failed with status \(statusCode)."
      case .unauthorized:
        return "The backend requires a valid bearer token."
      case .backendNotReady:
        return "The backend is reachable but not ready."
      case .readinessFailed(let statusCode):
        return "Readiness check failed with status \(statusCode)."
      }
    }
  }

  private let urlSession: URLSession

  init(urlSession: URLSession = .shared) {
    self.urlSession = urlSession
  }

  func validate(baseURLString: String, bearerToken: String) async throws {
    let trimmedBaseURL = baseURLString.trimmingCharacters(in: .whitespacesAndNewlines)
    guard let baseURL = URL(string: trimmedBaseURL),
      let scheme = baseURL.scheme,
      scheme == "http" || scheme == "https"
    else {
      throw ValidationError.invalidBaseURL
    }

    let trimmedToken = bearerToken.trimmingCharacters(in: .whitespacesAndNewlines)
    let authorizationToken = trimmedToken.isEmpty ? nil : trimmedToken

    try await performRequest(
      path: BackendEndpoints.livezPath,
      baseURL: baseURL,
      bearerToken: nil,
      endpoint: .livez
    )
    try await performRequest(
      path: BackendEndpoints.readyzPath,
      baseURL: baseURL,
      bearerToken: authorizationToken,
      endpoint: .readyz
    )
  }

  private func performRequest(
    path: String,
    baseURL: URL,
    bearerToken: String?,
    endpoint: ValidationEndpoint
  ) async throws {
    var request = URLRequest(url: BackendEndpoints.appendPath(path, to: baseURL))
    request.httpMethod = "GET"
    request.timeoutInterval = 10

    if let bearerToken, bearerToken.isEmpty == false {
      request.setValue("Bearer \(bearerToken)", forHTTPHeaderField: "Authorization")
    }

    let response: URLResponse
    do {
      (_, response) = try await urlSession.data(for: request)
    } catch {
      throw ValidationError.unreachable
    }

    guard let httpResponse = response as? HTTPURLResponse else {
      throw ValidationError.unexpectedResponse
    }

    guard (200...299).contains(httpResponse.statusCode) else {
      throw mapValidationError(for: endpoint, path: path, statusCode: httpResponse.statusCode)
    }
  }

  private func mapValidationError(
    for endpoint: ValidationEndpoint,
    path: String,
    statusCode: Int
  ) -> ValidationError {
    switch endpoint {
    case .livez:
      if statusCode == 404 {
        return .endpointNotFound(path: path)
      }
      return .livenessCheckFailed(statusCode: statusCode)
    case .readyz:
      switch statusCode {
      case 401:
        return .unauthorized
      case 404:
        return .endpointNotFound(path: path)
      case 503:
        return .backendNotReady
      default:
        return .readinessFailed(statusCode: statusCode)
      }
    }
  }
}

private extension BackendValidationClient {
  enum ValidationEndpoint {
    case livez
    case readyz
  }
}

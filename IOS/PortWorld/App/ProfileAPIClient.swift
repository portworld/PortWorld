import Foundation

struct ProfileDraft: Equatable {
  var name: String = ""
  var job: String = ""
  var company: String = ""
  var preferencesText: String = ""
  var projectsText: String = ""

  init() {}

  init(profile: ProfileAPIClient.Profile) {
    name = profile.name ?? ""
    job = profile.job ?? ""
    company = profile.company ?? ""
    preferencesText = profile.preferences.joined(separator: ", ")
    projectsText = profile.projects.joined(separator: ", ")
  }

  var payload: ProfileAPIClient.UpdatePayload {
    .init(
      name: normalizedString(name),
      job: normalizedString(job),
      company: normalizedString(company),
      preferences: normalizedList(preferencesText),
      projects: normalizedList(projectsText)
    )
  }

  private func normalizedString(_ value: String) -> String? {
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    return trimmed.isEmpty ? nil : trimmed
  }

  private func normalizedList(_ value: String) -> [String] {
    value
      .split(separator: ",")
      .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
      .filter { $0.isEmpty == false }
  }
}

struct ProfileAPIClient {
  struct Response: Decodable {
    let profile: Profile
    let isOnboarded: Bool
    let missingFields: [String]

    private enum CodingKeys: String, CodingKey {
      case profile
      case isOnboarded = "is_onboarded"
      case missingFields = "missing_fields"
    }
  }

  struct Profile: Codable, Equatable {
    let name: String?
    let job: String?
    let company: String?
    let preferences: [String]
    let projects: [String]

    init(from decoder: Decoder) throws {
      let container = try decoder.container(keyedBy: CodingKeys.self)
      name = try container.decodeIfPresent(String.self, forKey: .name)
      job = try container.decodeIfPresent(String.self, forKey: .job)
      company = try container.decodeIfPresent(String.self, forKey: .company)
      preferences = try container.decodeIfPresent([String].self, forKey: .preferences) ?? []
      projects = try container.decodeIfPresent([String].self, forKey: .projects) ?? []
    }

    init(
      name: String? = nil,
      job: String? = nil,
      company: String? = nil,
      preferences: [String] = [],
      projects: [String] = []
    ) {
      self.name = name
      self.job = job
      self.company = company
      self.preferences = preferences
      self.projects = projects
    }

    private enum CodingKeys: String, CodingKey {
      case name
      case job
      case company
      case preferences
      case projects
    }
  }

  struct UpdatePayload: Encodable {
    let name: String?
    let job: String?
    let company: String?
    let preferences: [String]
    let projects: [String]
  }

  enum ClientError: LocalizedError {
    case invalidBaseURL
    case requestFailed
    case unexpectedResponse
    case serverError(statusCode: Int)
    case decodingFailed

    var errorDescription: String? {
      switch self {
      case .invalidBaseURL:
        return "The saved backend URL is invalid."
      case .requestFailed:
        return "PortWorld could not reach your backend."
      case .unexpectedResponse:
        return "The backend returned an unexpected response."
      case .serverError(let statusCode):
        return "The backend returned status \(statusCode)."
      case .decodingFailed:
        return "The backend profile response could not be read."
      }
    }
  }

  private let urlSession: URLSession
  private let jsonDecoder = JSONDecoder()
  private let jsonEncoder = JSONEncoder()

  init(urlSession: URLSession = .shared) {
    self.urlSession = urlSession
  }

  func getProfile(settings: AppSettingsStore.Settings) async throws -> Response {
    let request = try makeRequest(
      baseURLString: settings.backendBaseURL,
      path: "/profile",
      bearerToken: settings.bearerToken,
      method: "GET"
    )
    let (data, response) = try await perform(request)
    guard let httpResponse = response as? HTTPURLResponse else {
      throw ClientError.unexpectedResponse
    }
    guard (200...299).contains(httpResponse.statusCode) else {
      throw ClientError.serverError(statusCode: httpResponse.statusCode)
    }
    do {
      return try jsonDecoder.decode(Response.self, from: data)
    } catch {
      throw ClientError.decodingFailed
    }
  }

  func putProfile(settings: AppSettingsStore.Settings, draft: ProfileDraft) async throws -> Response {
    var request = try makeRequest(
      baseURLString: settings.backendBaseURL,
      path: "/profile",
      bearerToken: settings.bearerToken,
      method: "PUT"
    )
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.httpBody = try jsonEncoder.encode(draft.payload)
    let (data, response) = try await perform(request)
    guard let httpResponse = response as? HTTPURLResponse else {
      throw ClientError.unexpectedResponse
    }
    guard (200...299).contains(httpResponse.statusCode) else {
      throw ClientError.serverError(statusCode: httpResponse.statusCode)
    }
    do {
      return try jsonDecoder.decode(Response.self, from: data)
    } catch {
      throw ClientError.decodingFailed
    }
  }

  private func perform(_ request: URLRequest) async throws -> (Data, URLResponse) {
    do {
      return try await urlSession.data(for: request)
    } catch {
      throw ClientError.requestFailed
    }
  }

  private func makeRequest(
    baseURLString: String,
    path: String,
    bearerToken: String,
    method: String
  ) throws -> URLRequest {
    let trimmedBaseURL = baseURLString.trimmingCharacters(in: .whitespacesAndNewlines)
    guard let baseURL = URL(string: trimmedBaseURL),
          let scheme = baseURL.scheme,
          scheme == "http" || scheme == "https"
    else {
      throw ClientError.invalidBaseURL
    }

    var request = URLRequest(url: appendPath(path, to: baseURL))
    request.httpMethod = method
    request.timeoutInterval = 15

    let trimmedToken = bearerToken.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmedToken.isEmpty == false {
      request.setValue("Bearer \(trimmedToken)", forHTTPHeaderField: "Authorization")
    }

    return request
  }

  private func appendPath(_ path: String, to baseURL: URL) -> URL {
    guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
      return URL(string: baseURL.absoluteString + path) ?? baseURL
    }

    let basePath = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    let cleanPath = path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))

    if basePath.isEmpty {
      components.path = "/\(cleanPath)"
    } else {
      components.path = "/\(basePath)/\(cleanPath)"
    }

    return components.url ?? baseURL
  }
}

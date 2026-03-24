import Foundation

enum BackendEndpoints {
  static let livezPath = "/livez"
  static let readyzPath = "/readyz"
  static let userMemoryPath = "/memory/user"
  static let sessionMemoryBasePath = "/memory/sessions"

  static func appendPath(_ path: String, to baseURL: URL) -> URL {
    guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
      return URL(string: baseURL.absoluteString + path) ?? baseURL
    }

    let basePath = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    let cleanPath = path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))

    if basePath.isEmpty {
      components.path = cleanPath.isEmpty ? "/" : "/\(cleanPath)"
    } else if cleanPath.isEmpty {
      components.path = "/\(basePath)"
    } else {
      components.path = "/\(basePath)/\(cleanPath)"
    }

    return components.url ?? baseURL
  }

  static func sessionMemoryStatusURL(sessionID: String, from endpointURL: URL) -> URL? {
    guard var components = URLComponents(url: endpointURL, resolvingAgainstBaseURL: false) else {
      return nil
    }

    let cleanPath = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    let pathSegments = cleanPath.split(separator: "/")
    let baseSegments = pathSegments.count >= 2 ? Array(pathSegments.dropLast(2)) : []
    guard let encodedSessionID = sessionID.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) else {
      return nil
    }

    let sessionMemoryPathSegments = sessionMemoryBasePath
      .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
      .split(separator: "/")
    let rebuiltSegments = baseSegments + sessionMemoryPathSegments + [
      Substring(encodedSessionID),
      Substring("status")
    ]
    components.path = "/" + rebuiltSegments.joined(separator: "/")
    return components.url
  }
}

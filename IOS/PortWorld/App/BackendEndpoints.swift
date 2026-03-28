import Foundation

enum BackendEndpoints {
  static let livezPath = "/livez"
  static let readyzPath = "/readyz"

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
}

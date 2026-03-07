// Shared configuration surface for the active assistant runtime.
import Foundation

enum AssistantWakeWordMode: String, Codable {
  case manualOnly = "manual_only"
  case onDevicePreferred = "on_device_preferred"
}

struct AssistantRuntimeConfig {
  private static let apiKeyBootstrapMarkerUserDefaultsKey = "portworld.apiKeyBootstrapSeeded"

  let webSocketURL: URL
  let requestHeaders: [String: String]
  let wakePhrase: String
  let sleepPhrase: String
  let wakeWordMode: AssistantWakeWordMode
  let wakeWordLocaleIdentifier: String
  let wakeWordRequiresOnDeviceRecognition: Bool
  let wakeWordDetectionCooldownMs: Int64
  let sleepWordMinActiveStreamMs: Int64

  static func load(from bundle: Bundle = .main, userDefaults: UserDefaults = .standard) -> AssistantRuntimeConfig {
    let backendBaseURL = resolveURL(
      infoPlistKey: "SON_BACKEND_BASE_URL",
      defaultURLString: "http://127.0.0.1:8080",
      bundle: bundle
    )
    let wsPath = resolvePath(
      infoPlistKey: "SON_WS_PATH",
      defaultPath: "/ws/session",
      bundle: bundle
    )
    let explicitWebSocketURL = resolveOptionalURL(infoPlistKey: "SON_WS_URL", bundle: bundle)

    let apiKey = resolveAPIKey(bundle: bundle, userDefaults: userDefaults)
    let bearerToken = resolveString(infoPlistKey: "SON_BEARER_TOKEN", defaultValue: "", bundle: bundle)

    return AssistantRuntimeConfig(
      webSocketURL: explicitWebSocketURL ?? deriveWebSocketURL(baseURL: backendBaseURL, path: wsPath),
      requestHeaders: makeRequestHeaders(apiKey: apiKey, bearerToken: bearerToken),
      wakePhrase: resolveWakePhrase(bundle: bundle, userDefaults: userDefaults),
      sleepPhrase: resolveString(infoPlistKey: "SON_SLEEP_PHRASE", defaultValue: "goodbye mario", bundle: bundle),
      wakeWordMode: resolveWakeWordMode(bundle: bundle),
      wakeWordLocaleIdentifier: resolveWakeLocale(bundle: bundle),
      wakeWordRequiresOnDeviceRecognition: resolveWakeRequiresOnDevice(bundle: bundle),
      wakeWordDetectionCooldownMs: resolveWakeCooldown(bundle: bundle),
      sleepWordMinActiveStreamMs: resolveSleepWordMinActiveStreamMs(bundle: bundle)
    )
  }

  private static func makeRequestHeaders(apiKey: String, bearerToken: String) -> [String: String] {
    var headers: [String: String] = [:]

    let trimmedApiKey = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
    if !trimmedApiKey.isEmpty {
      headers["X-API-Key"] = trimmedApiKey
    }

    let trimmedBearerToken = bearerToken.trimmingCharacters(in: .whitespacesAndNewlines)
    if !trimmedBearerToken.isEmpty {
      headers["Authorization"] = "Bearer \(trimmedBearerToken)"
    }

    return headers
  }

  private static func resolveAPIKey(bundle: Bundle, userDefaults: UserDefaults) -> String {
    do {
      if let stored = try KeychainCredentialStore.retrieve() {
        let trimmedStored = stored.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmedStored.isEmpty {
          return trimmedStored
        }
      }
    } catch {
      #if DEBUG
        NSLog("AssistantRuntimeConfig: failed to retrieve API key from keychain: \(error)")
      #endif
    }

    if userDefaults.bool(forKey: apiKeyBootstrapMarkerUserDefaultsKey) {
      return ""
    }

    userDefaults.set(true, forKey: apiKeyBootstrapMarkerUserDefaultsKey)

    let plistAPIKey = resolveString(infoPlistKey: "SON_API_KEY", defaultValue: "", bundle: bundle)
    guard !plistAPIKey.isEmpty else {
      return ""
    }

    do {
      try KeychainCredentialStore.store(apiKey: plistAPIKey)
    } catch {
      #if DEBUG
        NSLog("AssistantRuntimeConfig: failed to seed API key into keychain: \(error)")
      #endif
    }

    return plistAPIKey
  }

  private static func resolveWakePhrase(bundle: Bundle, userDefaults: UserDefaults) -> String {
    if let rawOverride = userDefaults.object(forKey: "portworld.wakePhrase") as? String {
      let trimmedOverride = rawOverride.trimmingCharacters(in: .whitespacesAndNewlines)
      if !trimmedOverride.isEmpty {
        return trimmedOverride
      }
    }

    return resolveString(infoPlistKey: "SON_WAKE_PHRASE", defaultValue: "hey mario", bundle: bundle)
  }

  private static func resolveWakeWordMode(bundle: Bundle) -> AssistantWakeWordMode {
    if let raw = bundle.object(forInfoDictionaryKey: "SON_WAKE_MODE") as? String {
      let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
      return AssistantWakeWordMode(rawValue: trimmed) ?? .onDevicePreferred
    }

    return .onDevicePreferred
  }

  private static func resolveWakeLocale(bundle: Bundle) -> String {
    if let raw = bundle.object(forInfoDictionaryKey: "SON_WAKE_LOCALE") as? String {
      let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
      if !trimmed.isEmpty {
        return trimmed
      }
    }

    return "en-US"
  }

  private static func resolveWakeRequiresOnDevice(bundle: Bundle) -> Bool {
    if let raw = bundle.object(forInfoDictionaryKey: "SON_WAKE_REQUIRE_ON_DEVICE") as? NSNumber {
      return raw.boolValue
    }

    if let raw = bundle.object(forInfoDictionaryKey: "SON_WAKE_REQUIRE_ON_DEVICE") as? String {
      switch raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
      case "1", "true", "yes", "y", "on":
        return true
      case "0", "false", "no", "n", "off":
        return false
      default:
        break
      }
    }

    return true
  }

  private static func resolveWakeCooldown(bundle: Bundle) -> Int64 {
    if let raw = bundle.object(forInfoDictionaryKey: "SON_WAKE_DETECTION_COOLDOWN_MS") as? NSNumber {
      return max(250, raw.int64Value)
    }

    if let raw = bundle.object(forInfoDictionaryKey: "SON_WAKE_DETECTION_COOLDOWN_MS") as? String,
       let parsed = Int64(raw.trimmingCharacters(in: .whitespacesAndNewlines))
    {
      return max(250, parsed)
    }

    return 1_500
  }

  private static func resolveSleepWordMinActiveStreamMs(bundle: Bundle) -> Int64 {
    if let raw = bundle.object(forInfoDictionaryKey: "SON_SLEEP_WORD_MIN_ACTIVE_STREAM_MS") as? NSNumber {
      return max(0, raw.int64Value)
    }

    if let raw = bundle.object(forInfoDictionaryKey: "SON_SLEEP_WORD_MIN_ACTIVE_STREAM_MS") as? String,
       let parsed = Int64(raw.trimmingCharacters(in: .whitespacesAndNewlines))
    {
      return max(0, parsed)
    }

    return 1_500
  }

  private static func resolveString(infoPlistKey: String, defaultValue: String, bundle: Bundle) -> String {
    if let raw = bundle.object(forInfoDictionaryKey: infoPlistKey) as? String {
      let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
      if !trimmed.isEmpty {
        return trimmed
      }
    }

    return defaultValue
  }

  private static func resolveURL(infoPlistKey: String, defaultURLString: String, bundle: Bundle) -> URL {
    if let rawValue = bundle.object(forInfoDictionaryKey: infoPlistKey) as? String {
      let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
      if !trimmed.isEmpty, let resolvedURL = URL(string: trimmed) {
        return resolvedURL
      }
    }

    return URL(string: defaultURLString)!
  }

  private static func resolveOptionalURL(infoPlistKey: String, bundle: Bundle) -> URL? {
    if let rawValue = bundle.object(forInfoDictionaryKey: infoPlistKey) as? String {
      let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
      if !trimmed.isEmpty {
        return URL(string: trimmed)
      }
    }

    return nil
  }

  private static func resolvePath(infoPlistKey: String, defaultPath: String, bundle: Bundle) -> String {
    if let raw = bundle.object(forInfoDictionaryKey: infoPlistKey) as? String {
      let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
      if !trimmed.isEmpty {
        return trimmed.hasPrefix("/") ? trimmed : "/\(trimmed)"
      }
    }

    return defaultPath.hasPrefix("/") ? defaultPath : "/\(defaultPath)"
  }

  private static func appendPath(path: String, to baseURL: URL) -> URL {
    guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
      return URL(string: baseURL.absoluteString + path)!
    }

    let basePath = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    let cleanPath = path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))

    if basePath.isEmpty {
      components.path = "/\(cleanPath)"
    } else if cleanPath.isEmpty {
      components.path = "/\(basePath)"
    } else {
      components.path = "/\(basePath)/\(cleanPath)"
    }

    return components.url ?? URL(string: baseURL.absoluteString + path)!
  }

  private static func deriveWebSocketURL(baseURL: URL, path: String) -> URL {
    guard var components = URLComponents(url: appendPath(path: path, to: baseURL), resolvingAgainstBaseURL: false) else {
      return URL(string: "ws://127.0.0.1:8080\(path)")!
    }

    if components.scheme == "https" {
      components.scheme = "wss"
    } else if components.scheme == "http" || components.scheme == nil {
      components.scheme = "ws"
    }

    return components.url ?? URL(string: "ws://127.0.0.1:8080\(path)")!
  }
}

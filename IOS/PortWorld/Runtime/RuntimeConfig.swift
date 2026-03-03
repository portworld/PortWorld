import Foundation

public enum WakeWordMode: String, Codable {
  case manualOnly = "manual_only"
  case onDevicePreferred = "on_device_preferred"
}

public struct RuntimeConfig {
  public let backendBaseURL: URL
  public let webSocketURL: URL
  public let visionFrameURL: URL
  public let queryURL: URL
  public let apiKey: String
  public let bearerToken: String
  public let photoFps: Double
  public let silenceTimeoutMs: Int
  public let preWakeVideoMs: Int
  public let wakePhrase: String
  public let wakeWordMode: WakeWordMode
  public let wakeWordLocaleIdentifier: String
  public let wakeWordRequiresOnDeviceRecognition: Bool
  public let wakeWordDetectionCooldownMs: Int64

  /// Minimum RMS energy to consider a frame as speech (0.0–1.0). Default 0.02.
  public let speechRMSThreshold: Float
  /// Minimum milliseconds between consecutive speech-activity emissions. Default 250.
  public let speechActivityDebounceMs: Int64

  public init(
    backendBaseURL: URL,
    webSocketURL: URL,
    visionFrameURL: URL,
    queryURL: URL,
    apiKey: String = "",
    bearerToken: String = "",
    photoFps: Double = 1.0,
    silenceTimeoutMs: Int = 5_000,
    preWakeVideoMs: Int = 5_000,
    wakePhrase: String = "hey mario",
    wakeWordMode: WakeWordMode = .onDevicePreferred,
    wakeWordLocaleIdentifier: String = "en-US",
    wakeWordRequiresOnDeviceRecognition: Bool = true,
    wakeWordDetectionCooldownMs: Int64 = 1_500,
    speechRMSThreshold: Float = 0.02,
    speechActivityDebounceMs: Int64 = 250
  ) {
    self.backendBaseURL = backendBaseURL
    self.webSocketURL = webSocketURL
    self.visionFrameURL = visionFrameURL
    self.queryURL = queryURL
    self.apiKey = apiKey
    self.bearerToken = bearerToken
    self.photoFps = photoFps
    self.silenceTimeoutMs = silenceTimeoutMs
    self.preWakeVideoMs = preWakeVideoMs
    self.wakePhrase = wakePhrase
    self.wakeWordMode = wakeWordMode
    self.wakeWordLocaleIdentifier = wakeWordLocaleIdentifier
    self.wakeWordRequiresOnDeviceRecognition = wakeWordRequiresOnDeviceRecognition
    self.wakeWordDetectionCooldownMs = wakeWordDetectionCooldownMs
    self.speechRMSThreshold = speechRMSThreshold
    self.speechActivityDebounceMs = speechActivityDebounceMs
  }

  public var requestHeaders: [String: String] {
    var headers: [String: String] = [:]
    let trimmedApiKey = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
    if !trimmedApiKey.isEmpty {
      headers["X-API-Key"] = trimmedApiKey
    }
    let trimmedBearer = bearerToken.trimmingCharacters(in: .whitespacesAndNewlines)
    if !trimmedBearer.isEmpty {
      headers["Authorization"] = "Bearer \(trimmedBearer)"
    }
    return headers
  }

  public var backendSummary: String {
    "base=\(backendBaseURL.absoluteString) ws=\(webSocketURL.absoluteString) vision=\(visionFrameURL.absoluteString) query=\(queryURL.absoluteString)"
  }

  public static func load(from bundle: Bundle = .main) -> RuntimeConfig {
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
    let visionPath = resolvePath(
      infoPlistKey: "SON_VISION_PATH",
      defaultPath: "/vision/frame",
      bundle: bundle
    )
    let queryPath = resolvePath(
      infoPlistKey: "SON_QUERY_PATH",
      defaultPath: "/v1/query",
      bundle: bundle
    )

    let explicitWSURL = resolveOptionalURL(infoPlistKey: "SON_WS_URL", bundle: bundle)
    let explicitVisionURL = resolveOptionalURL(infoPlistKey: "SON_VISION_URL", bundle: bundle)
    let explicitQueryURL = resolveOptionalURL(infoPlistKey: "SON_QUERY_URL", bundle: bundle)

    return RuntimeConfig(
      backendBaseURL: backendBaseURL,
      webSocketURL: explicitWSURL ?? deriveWebSocketURL(baseURL: backendBaseURL, path: wsPath),
      visionFrameURL: explicitVisionURL ?? appendPath(path: visionPath, to: backendBaseURL),
      queryURL: explicitQueryURL ?? appendPath(path: queryPath, to: backendBaseURL),
      apiKey: resolveString(infoPlistKey: "SON_API_KEY", defaultValue: "", bundle: bundle),
      bearerToken: resolveString(infoPlistKey: "SON_BEARER_TOKEN", defaultValue: "", bundle: bundle),
      photoFps: resolvePhotoFPS(bundle: bundle),
      wakeWordMode: resolveWakeWordMode(bundle: bundle),
      wakeWordLocaleIdentifier: resolveWakeLocale(bundle: bundle),
      wakeWordRequiresOnDeviceRecognition: resolveWakeRequiresOnDevice(bundle: bundle),
      wakeWordDetectionCooldownMs: resolveWakeCooldown(bundle: bundle)
    )
  }

  private static func resolvePhotoFPS(bundle: Bundle) -> Double {
    if let raw = bundle.object(forInfoDictionaryKey: "SON_PHOTO_FPS") as? NSNumber {
      return max(0.1, raw.doubleValue)
    }

    if let raw = bundle.object(forInfoDictionaryKey: "SON_PHOTO_FPS") as? String,
       let parsed = Double(raw.trimmingCharacters(in: .whitespacesAndNewlines))
    {
      return max(0.1, parsed)
    }

    return 1.0
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
    if
      let rawValue = bundle.object(forInfoDictionaryKey: infoPlistKey) as? String,
      let resolvedURL = URL(string: rawValue.trimmingCharacters(in: .whitespacesAndNewlines)),
      !rawValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    {
      return resolvedURL
    }

    return URL(string: defaultURLString)!
  }

  private static func resolveOptionalURL(infoPlistKey: String, bundle: Bundle) -> URL? {
    if
      let rawValue = bundle.object(forInfoDictionaryKey: infoPlistKey) as? String
    {
      let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
      if trimmed.isEmpty {
        return nil
      }
      return URL(string: trimmed)
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

  private static func resolveWakeWordMode(bundle: Bundle) -> WakeWordMode {
    if let raw = bundle.object(forInfoDictionaryKey: "SON_WAKE_MODE") as? String {
      return WakeWordMode(rawValue: raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()) ?? .onDevicePreferred
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
}

import Foundation

public enum WakeWordMode: String, Codable {
  case manualOnly = "manual_only"
  case onDevicePreferred = "on_device_preferred"
}

public struct RuntimeConfig {
  static let apiKeyBootstrapMarkerUserDefaultsKey = "portworld.apiKeyBootstrapSeeded"

  public let backendBaseURL: URL
  public let webSocketURL: URL
  public let visionFrameURL: URL
  public let realtimeDiagnosticsEnabled: Bool
  public let realtimeForceTextAudioFallback: Bool
  /// Legacy batch `/query` endpoint retained for compatibility.
  /// In Phase 6 realtime mode this endpoint is inactive on the primary path.
  public let queryURL: URL
  public let apiKey: String
  public let bearerToken: String
  public let photoFps: Double
  public let silenceTimeoutMs: Int
  public let preWakeVideoMs: Int
  public let wakePhrase: String
  public let sleepPhrase: String
  public let wakeWordMode: WakeWordMode
  public let wakeWordLocaleIdentifier: String
  public let wakeWordRequiresOnDeviceRecognition: Bool
  public let wakeWordDetectionCooldownMs: Int64
  public let sleepWordMinActiveStreamMs: Int64
  public let assistantStuckDetectionThresholdMs: Int64

  /// Minimum RMS energy to consider a frame as speech (0.0–1.0). Default 0.02.
  public let speechRMSThreshold: Float
  /// Minimum milliseconds between consecutive speech-activity emissions. Default 250.
  public let speechActivityDebounceMs: Int64

  public init(
    backendBaseURL: URL,
    webSocketURL: URL,
    visionFrameURL: URL,
    realtimeDiagnosticsEnabled: Bool = false,
    realtimeForceTextAudioFallback: Bool = false,
    queryURL: URL,
    apiKey: String = "",
    bearerToken: String = "",
    photoFps: Double = 1.0,
    silenceTimeoutMs: Int = 5_000,
    preWakeVideoMs: Int = 5_000,
    wakePhrase: String = "hey mario",
    sleepPhrase: String = "goodbye mario",
    wakeWordMode: WakeWordMode = .onDevicePreferred,
    wakeWordLocaleIdentifier: String = "en-US",
    wakeWordRequiresOnDeviceRecognition: Bool = true,
    wakeWordDetectionCooldownMs: Int64 = 1_500,
    sleepWordMinActiveStreamMs: Int64 = 1_500,
    assistantStuckDetectionThresholdMs: Int64 = 1_500,
    speechRMSThreshold: Float = 0.02,
    speechActivityDebounceMs: Int64 = 250
  ) {
    self.backendBaseURL = backendBaseURL
    self.webSocketURL = webSocketURL
    self.visionFrameURL = visionFrameURL
    self.realtimeDiagnosticsEnabled = realtimeDiagnosticsEnabled
    self.realtimeForceTextAudioFallback = realtimeForceTextAudioFallback
    self.queryURL = queryURL
    self.apiKey = apiKey
    self.bearerToken = bearerToken
    self.photoFps = photoFps
    self.silenceTimeoutMs = silenceTimeoutMs
    self.preWakeVideoMs = preWakeVideoMs
    self.wakePhrase = wakePhrase
    self.sleepPhrase = sleepPhrase
    self.wakeWordMode = wakeWordMode
    self.wakeWordLocaleIdentifier = wakeWordLocaleIdentifier
    self.wakeWordRequiresOnDeviceRecognition = wakeWordRequiresOnDeviceRecognition
    self.wakeWordDetectionCooldownMs = wakeWordDetectionCooldownMs
    self.sleepWordMinActiveStreamMs = max(0, sleepWordMinActiveStreamMs)
    self.assistantStuckDetectionThresholdMs = max(250, assistantStuckDetectionThresholdMs)
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
    "base=\(backendBaseURL.absoluteString) ws=\(webSocketURL.absoluteString) vision=\(visionFrameURL.absoluteString)"
  }

  public static func load(from bundle: Bundle = .main) -> RuntimeConfig {
    load(from: bundle, userDefaults: .standard)
  }

  public static func load(from bundle: Bundle = .main, userDefaults: UserDefaults) -> RuntimeConfig {
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
      realtimeDiagnosticsEnabled: resolveRealtimeDiagnosticsEnabled(bundle: bundle, userDefaults: userDefaults),
      realtimeForceTextAudioFallback: resolveRealtimeForceTextAudioFallback(bundle: bundle, userDefaults: userDefaults),
      queryURL: explicitQueryURL ?? appendPath(path: queryPath, to: backendBaseURL),
      apiKey: resolveAPIKey(bundle: bundle, userDefaults: userDefaults),
      bearerToken: resolveString(infoPlistKey: "SON_BEARER_TOKEN", defaultValue: "", bundle: bundle),
      photoFps: resolvePhotoFPS(bundle: bundle),
      silenceTimeoutMs: resolveSilenceTimeout(bundle: bundle, userDefaults: userDefaults),
      wakePhrase: resolveWakePhrase(bundle: bundle, userDefaults: userDefaults),
      sleepPhrase: resolveString(infoPlistKey: "SON_SLEEP_PHRASE", defaultValue: "goodbye mario", bundle: bundle),
      wakeWordMode: resolveWakeWordMode(bundle: bundle),
      wakeWordLocaleIdentifier: resolveWakeLocale(bundle: bundle),
      wakeWordRequiresOnDeviceRecognition: resolveWakeRequiresOnDevice(bundle: bundle),
      wakeWordDetectionCooldownMs: resolveWakeCooldown(bundle: bundle),
      sleepWordMinActiveStreamMs: resolveSleepWordMinActiveStreamMs(bundle: bundle),
      assistantStuckDetectionThresholdMs: resolveAssistantStuckDetectionThreshold(bundle: bundle)
    )
  }

  static func clearStoredAPIKey() throws {
    try KeychainCredentialStore.clear()
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
      NSLog("RuntimeConfig: failed to retrieve API key from keychain: \(error)")
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
      NSLog("RuntimeConfig: failed to seed API key into keychain: \(error)")
      #endif
    }

    return plistAPIKey
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

  private static func resolveSilenceTimeout(bundle: Bundle, userDefaults: UserDefaults) -> Int {
    if let override = resolveUserDefaultsInteger(key: "portworld.silenceTimeoutMs", userDefaults: userDefaults), override > 0 {
      return max(250, override)
    }

    if let fromPlist = resolveOptionalInt(infoPlistKey: "SON_SILENCE_TIMEOUT_MS", bundle: bundle), fromPlist > 0 {
      return max(250, fromPlist)
    }

    return 5_000
  }

  private static func resolveRealtimeDiagnosticsEnabled(bundle: Bundle, userDefaults: UserDefaults) -> Bool {
    if let override = resolveUserDefaultsBool(key: "portworld.realtimeDiagnosticsEnabled", userDefaults: userDefaults) {
      return override
    }
    return resolveOptionalBool(infoPlistKey: "SON_REALTIME_DIAGNOSTICS_ENABLED", bundle: bundle) ?? false
  }

  private static func resolveRealtimeForceTextAudioFallback(bundle: Bundle, userDefaults: UserDefaults) -> Bool {
    #if DEBUG
    return true
    #endif
    if let override = resolveUserDefaultsBool(key: "portworld.realtimeForceTextAudioFallback", userDefaults: userDefaults) {
      return override
    }
    if let envValue = ProcessInfo.processInfo.environment["SON_REALTIME_FORCE_TEXT_AUDIO_FALLBACK"] {
      let normalized = envValue.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
      if ["1", "true", "yes", "on"].contains(normalized) {
        return true
      }
      if ["0", "false", "no", "off"].contains(normalized) {
        return false
      }
    }
    return resolveOptionalBool(infoPlistKey: "SON_REALTIME_FORCE_TEXT_AUDIO_FALLBACK", bundle: bundle) ?? false
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

  private static func resolveUserDefaultsInteger(key: String, userDefaults: UserDefaults) -> Int? {
    guard userDefaults.object(forKey: key) != nil else {
      return nil
    }

    if let raw = userDefaults.object(forKey: key) as? NSNumber {
      return raw.intValue
    }

    if let raw = userDefaults.string(forKey: key),
       let parsed = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines))
    {
      return parsed
    }

    return nil
  }

  private static func resolveUserDefaultsBool(key: String, userDefaults: UserDefaults) -> Bool? {
    guard userDefaults.object(forKey: key) != nil else {
      return nil
    }

    if let raw = userDefaults.object(forKey: key) as? NSNumber {
      return raw.boolValue
    }

    if let raw = userDefaults.string(forKey: key) {
      switch raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
      case "1", "true", "yes", "y", "on":
        return true
      case "0", "false", "no", "n", "off":
        return false
      default:
        return nil
      }
    }

    return nil
  }

  private static func resolveOptionalInt(infoPlistKey: String, bundle: Bundle) -> Int? {
    if let raw = bundle.object(forInfoDictionaryKey: infoPlistKey) as? NSNumber {
      return raw.intValue
    }

    if let raw = bundle.object(forInfoDictionaryKey: infoPlistKey) as? String,
       let parsed = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines))
    {
      return parsed
    }

    return nil
  }

  private static func resolveOptionalBool(infoPlistKey: String, bundle: Bundle) -> Bool? {
    if let raw = bundle.object(forInfoDictionaryKey: infoPlistKey) as? NSNumber {
      return raw.boolValue
    }

    if let raw = bundle.object(forInfoDictionaryKey: infoPlistKey) as? String {
      switch raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
      case "1", "true", "yes", "y", "on":
        return true
      case "0", "false", "no", "n", "off":
        return false
      default:
        return nil
      }
    }

    return nil
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

  private static func resolveAssistantStuckDetectionThreshold(bundle: Bundle) -> Int64 {
    if let raw = bundle.object(forInfoDictionaryKey: "SON_ASSISTANT_STUCK_DETECTION_THRESHOLD_MS") as? NSNumber {
      return max(250, raw.int64Value)
    }
    if let raw = bundle.object(forInfoDictionaryKey: "SON_ASSISTANT_STUCK_DETECTION_THRESHOLD_MS") as? String,
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
}

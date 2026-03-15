import Combine
import Foundation

@MainActor
final class AppSettingsStore: ObservableObject {
  enum BackendValidationState: String, Codable {
    case unknown
    case valid
    case invalid
  }

  struct Settings: Codable, Equatable {
    var backendBaseURL: String
    var bearerToken: String
    var validationState: BackendValidationState

    init(
      backendBaseURL: String,
      bearerToken: String,
      validationState: BackendValidationState
    ) {
      self.backendBaseURL = backendBaseURL
      self.bearerToken = bearerToken
      self.validationState = validationState
    }

    init(from decoder: Decoder) throws {
      let container = try decoder.container(keyedBy: CodingKeys.self)
      backendBaseURL = try container.decodeIfPresent(String.self, forKey: .backendBaseURL) ?? ""
      bearerToken = try container.decodeIfPresent(String.self, forKey: .bearerToken) ?? ""
      validationState =
        try container.decodeIfPresent(BackendValidationState.self, forKey: .validationState) ?? .unknown
    }

    func encode(to encoder: Encoder) throws {
      var container = encoder.container(keyedBy: PersistedCodingKeys.self)
      try container.encode(backendBaseURL, forKey: .backendBaseURL)
      try container.encode(validationState, forKey: .validationState)
    }

    private enum CodingKeys: String, CodingKey {
      case backendBaseURL
      case bearerToken
      case validationState
    }

    private enum PersistedCodingKeys: String, CodingKey {
      case backendBaseURL
      case validationState
    }
  }

  private static let settingsKey = "portworld.app.settings"

  @Published private(set) var settings: Settings

  private let userDefaults: UserDefaults
  private let encoder = JSONEncoder()
  private let decoder = JSONDecoder()

  init(userDefaults: UserDefaults = .standard, bundle: Bundle = .main) {
    self.userDefaults = userDefaults

    let bundleBaseURL =
      (bundle.object(forInfoDictionaryKey: "SON_BACKEND_BASE_URL") as? String)?
      .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    let bundleBearerToken =
      (bundle.object(forInfoDictionaryKey: "SON_BEARER_TOKEN") as? String)?
      .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""

    let decodedSettings: Settings?
    if let data = userDefaults.data(forKey: Self.settingsKey),
       let decoded = try? decoder.decode(Settings.self, from: data)
    {
      decodedSettings = decoded
    } else {
      decodedSettings = nil
    }

    let decodedBearerToken = decodedSettings?.bearerToken.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    let keychainBearerToken = Self.readBearerTokenFromKeychain()
    let resolvedBearerToken = Self.resolveBearerToken(
      keychainBearerToken: keychainBearerToken,
      decodedBearerToken: decodedBearerToken,
      bundleBearerToken: bundleBearerToken
    )

    self.settings = Settings(
      backendBaseURL: decodedSettings?.backendBaseURL ?? bundleBaseURL,
      bearerToken: resolvedBearerToken,
      validationState: decodedSettings?.validationState ?? .unknown
    )

    if keychainBearerToken.isEmpty && resolvedBearerToken.isEmpty == false {
      Self.writeBearerTokenToKeychain(resolvedBearerToken)
    }

    if decodedBearerToken.isEmpty == false {
      persist()
    }
  }

  func updateBackendSettings(
    backendBaseURL: String,
    bearerToken: String,
    validationState: BackendValidationState
  ) {
    settings.backendBaseURL = backendBaseURL.trimmingCharacters(in: .whitespacesAndNewlines)
    settings.bearerToken = bearerToken.trimmingCharacters(in: .whitespacesAndNewlines)
    settings.validationState = validationState
    persistBearerToken(settings.bearerToken)
    persist()
  }

  func markValidationState(_ validationState: BackendValidationState) {
    settings.validationState = validationState
    persist()
  }

  private func persist() {
    do {
      let data = try encoder.encode(settings)
      userDefaults.set(data, forKey: Self.settingsKey)
    } catch {
      #if DEBUG
        NSLog("AppSettingsStore: failed to persist settings: \(error)")
      #endif
    }
  }

  private func persistBearerToken(_ bearerToken: String) {
    let trimmedBearerToken = bearerToken.trimmingCharacters(in: .whitespacesAndNewlines)

    if trimmedBearerToken.isEmpty {
      do {
        try KeychainCredentialStore.clearBearerToken()
      } catch {
        #if DEBUG
          NSLog("AppSettingsStore: failed to clear bearer token from keychain: \(error)")
        #endif
      }
      return
    }

    Self.writeBearerTokenToKeychain(trimmedBearerToken)
  }

  private static func resolveBearerToken(
    keychainBearerToken: String,
    decodedBearerToken: String,
    bundleBearerToken: String
  ) -> String {
    if keychainBearerToken.isEmpty == false {
      return keychainBearerToken
    }

    if decodedBearerToken.isEmpty == false {
      return decodedBearerToken
    }

    return bundleBearerToken
  }

  private static func readBearerTokenFromKeychain() -> String {
    do {
      return try KeychainCredentialStore.retrieveBearerToken()?
        .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    } catch {
      #if DEBUG
        NSLog("AppSettingsStore: failed to read bearer token from keychain: \(error)")
      #endif
      return ""
    }
  }

  private static func writeBearerTokenToKeychain(_ bearerToken: String) {
    do {
      try KeychainCredentialStore.storeBearerToken(bearerToken)
    } catch {
      #if DEBUG
        NSLog("AppSettingsStore: failed to store bearer token in keychain: \(error)")
      #endif
    }
  }
}

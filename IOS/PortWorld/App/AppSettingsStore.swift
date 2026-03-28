import Combine
import Foundation
import OSLog

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
  }

  private static let settingsKey = "portworld.app.settings"
  private static let logger = Logger(
    subsystem: Bundle.main.bundleIdentifier ?? "PortWorld",
    category: "AppSettingsStore"
  )

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
    if let data = userDefaults.data(forKey: Self.settingsKey) {
      do {
        decodedSettings = try decoder.decode(Settings.self, from: data)
      } catch {
        Self.logger.error("Failed to decode persisted settings: \(error.localizedDescription, privacy: .public)")
        decodedSettings = nil
      }
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

  private func persist() {
    do {
      let data = try encoder.encode(settings)
      userDefaults.set(data, forKey: Self.settingsKey)
    } catch {
      Self.logger.error("Failed to persist settings: \(error.localizedDescription, privacy: .public)")
    }
  }

  private func persistBearerToken(_ bearerToken: String) {
    let trimmedBearerToken = bearerToken.trimmingCharacters(in: .whitespacesAndNewlines)

    if trimmedBearerToken.isEmpty {
      do {
        try KeychainCredentialStore.clearBearerToken()
      } catch {
        Self.logger.error("Failed to clear bearer token from keychain: \(error.localizedDescription, privacy: .public)")
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
      logger.error("Failed to read bearer token from keychain: \(error.localizedDescription, privacy: .public)")
      return ""
    }
  }

  private static func writeBearerTokenToKeychain(_ bearerToken: String) {
    do {
      try KeychainCredentialStore.storeBearerToken(bearerToken)
    } catch {
      logger.error("Failed to store bearer token in keychain: \(error.localizedDescription, privacy: .public)")
    }
  }
}

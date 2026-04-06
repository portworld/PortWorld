import Foundation

enum AppLaunchCommand: String, Codable, Equatable {
  case startSession = "start_session"
}

enum AppLaunchCommandStore {
  private static let pendingCommandKey = "portworld.app.pending-launch-command"
  private static let onboardingProgressKey = "portworld.onboarding.progress"
  private static let appSettingsKey = "portworld.app.settings"

  static func enqueue(_ command: AppLaunchCommand, userDefaults: UserDefaults = .standard) {
    userDefaults.set(command.rawValue, forKey: pendingCommandKey)
  }

  static func consumePendingCommand(userDefaults: UserDefaults = .standard) -> AppLaunchCommand? {
    guard let rawValue = userDefaults.string(forKey: pendingCommandKey) else { return nil }
    userDefaults.removeObject(forKey: pendingCommandKey)
    return AppLaunchCommand(rawValue: rawValue)
  }

  static func startSessionIntentDialog(userDefaults: UserDefaults = .standard) -> String {
    if hasCompletedInitialOnboarding(userDefaults: userDefaults) == false {
      return "Launching PortWorld. Session start is blocked until onboarding is complete."
    }

    let settings = loadSettingsSnapshot(userDefaults: userDefaults)
    switch settings.validationState {
    case .valid:
      return "Launching PortWorld and starting your assistant session."
    case .invalid:
      return "Launching PortWorld. Session start is blocked because backend validation failed."
    case .unknown:
      if settings.backendBaseURL.isEmpty {
        return "Launching PortWorld. Session start is blocked because backend setup is incomplete."
      }
      return "Launching PortWorld. Session start is blocked until backend readiness is verified."
    }
  }

  static func onboardingBlockedMessage() -> String {
    "Siri opened PortWorld, but session start is blocked until onboarding is complete."
  }
}

private extension AppLaunchCommandStore {
  struct OnboardingProgressSnapshot: Decodable {
    var metaSkipped = false
    var profileCompleted = false
  }

  struct AppSettingsSnapshot: Decodable {
    enum BackendValidationState: String, Decodable {
      case unknown
      case valid
      case invalid
    }

    var backendBaseURL: String = ""
    var validationState: BackendValidationState = .unknown
  }

  static func hasCompletedInitialOnboarding(userDefaults: UserDefaults) -> Bool {
    guard
      let data = userDefaults.data(forKey: onboardingProgressKey),
      let progress = try? JSONDecoder().decode(OnboardingProgressSnapshot.self, from: data)
    else {
      return false
    }

    return progress.metaSkipped || progress.profileCompleted
  }

  static func loadSettingsSnapshot(userDefaults: UserDefaults) -> AppSettingsSnapshot {
    guard
      let data = userDefaults.data(forKey: appSettingsKey),
      let settings = try? JSONDecoder().decode(AppSettingsSnapshot.self, from: data)
    else {
      return AppSettingsSnapshot()
    }

    return settings
  }
}

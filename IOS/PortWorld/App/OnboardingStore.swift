import Combine
import Foundation

@MainActor
final class OnboardingStore: ObservableObject {
  struct Progress: Codable, Equatable {
    var welcomeSeen = false
    var featuresSeen = false
    var backendIntroSeen = false
    var backendValidated = false
    var metaCompleted = false
    var metaSkipped = false
    var profileCompleted = false
    var initialOnboardingCompleted = false
    var isFullyOnboarded = false
  }

  private static let progressKey = "portworld.onboarding.progress"

  @Published private(set) var progress: Progress

  private let userDefaults: UserDefaults
  private let encoder = JSONEncoder()
  private let decoder = JSONDecoder()

  init(userDefaults: UserDefaults = .standard) {
    self.userDefaults = userDefaults
    if let data = userDefaults.data(forKey: Self.progressKey),
       let decoded = try? decoder.decode(Progress.self, from: data)
    {
      self.progress = Self.normalize(decoded)
    } else {
      self.progress = Progress()
    }
  }

  var shouldShowWelcome: Bool {
    progress.welcomeSeen == false
  }

  var shouldOfferProfileSetup: Bool {
    progress.metaCompleted && progress.profileCompleted == false
  }

  var hasCompletedInitialOnboarding: Bool {
    progress.initialOnboardingCompleted
  }

  func markWelcomeSeen() {
    guard progress.welcomeSeen == false else { return }
    progress.welcomeSeen = true
    persist()
  }

  func markFeaturesSeen() {
    guard progress.featuresSeen == false else { return }
    progress.featuresSeen = true
    persist()
  }

  func markBackendIntroSeen() {
    guard progress.backendIntroSeen == false else { return }
    progress.backendIntroSeen = true
    persist()
  }

  func markBackendValidated() {
    guard progress.backendValidated == false else { return }
    progress.backendValidated = true
    persist()
  }

  func markMetaCompleted() {
    guard progress.metaCompleted == false || progress.metaSkipped else { return }
    progress.metaCompleted = true
    progress.metaSkipped = false
    persist()
  }

  func markMetaSkipped() {
    guard progress.metaSkipped == false || progress.initialOnboardingCompleted == false else { return }
    progress.metaSkipped = true
    progress.initialOnboardingCompleted = true
    progress.isFullyOnboarded = true
    persist()
  }

  func markProfileCompleted() {
    guard progress.profileCompleted == false ||
      progress.initialOnboardingCompleted == false ||
      progress.isFullyOnboarded == false else { return }
    progress.profileCompleted = true
    progress.initialOnboardingCompleted = true
    progress.isFullyOnboarded = true
    persist()
  }

  private func persist() {
    guard let data = try? encoder.encode(progress) else { return }
    userDefaults.set(data, forKey: Self.progressKey)
  }

  private static func normalize(_ progress: Progress) -> Progress {
    var normalized = progress

    if normalized.profileCompleted || normalized.isFullyOnboarded || normalized.metaSkipped {
      normalized.initialOnboardingCompleted = true
    }

    if normalized.profileCompleted {
      normalized.isFullyOnboarded = true
    }

    return normalized
  }
}

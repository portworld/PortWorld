import Combine
import Foundation

enum OnboardingSessionSupport {
  static func makeConfig(from settings: AppSettingsStore.Settings) -> AssistantRuntimeConfig {
    AssistantRuntimeConfig.load(
      backendBaseURLOverride: settings.backendBaseURL,
      bearerTokenOverride: settings.bearerToken
    )
  }

  @MainActor
  static func makeAssistantRuntimeViewModel(
    wearablesRuntimeManager: WearablesRuntimeManager,
    settings: AppSettingsStore.Settings
  ) -> AssistantRuntimeViewModel {
    AssistantRuntimeViewModel(
      wearablesRuntimeManager: wearablesRuntimeManager,
      config: makeConfig(from: settings)
    )
  }

  static func formattedPhrase(_ phrase: String) -> String {
    phrase
      .split(separator: " ")
      .map { $0.prefix(1).uppercased() + $0.dropFirst().lowercased() }
      .joined(separator: " ")
  }
}

@MainActor
final class OnboardingGlassesSessionObserver: ObservableObject {
  @Published private(set) var audioRouteDetail =
    "PortWorld will request the glasses audio route when practice starts."
  @Published private(set) var sessionPhase: GlassesSessionPhase = .inactive
  @Published private(set) var sessionErrorMessage: String?

  private var cancellables = Set<AnyCancellable>()

  init(wearablesRuntimeManager: WearablesRuntimeManager) {
    wearablesRuntimeManager.$glassesAudioDetailText
      .receive(on: RunLoop.main)
      .sink { [weak self] in
        self?.audioRouteDetail = $0
      }
      .store(in: &cancellables)

    wearablesRuntimeManager.$glassesSessionPhase
      .receive(on: RunLoop.main)
      .sink { [weak self] in
        self?.sessionPhase = $0
      }
      .store(in: &cancellables)

    wearablesRuntimeManager.$glassesSessionErrorMessage
      .receive(on: RunLoop.main)
      .sink { [weak self] in
        self?.sessionErrorMessage = $0
      }
      .store(in: &cancellables)
  }
}

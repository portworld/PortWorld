import Combine
import SwiftUI

@MainActor
final class ProfileInterviewSessionViewModel: ObservableObject {
  @Published private(set) var status: AssistantRuntimeStatus
  @Published private(set) var isStarting = false
  @Published private(set) var didAttemptStart = false
  @Published private(set) var isProfileReadyForReview = false
  @Published private(set) var startupBlockerMessage: String?

  private let runtimeViewModel: AssistantRuntimeViewModel
  private let settings: AppSettingsStore.Settings
  private var cancellables = Set<AnyCancellable>()

  init(
    wearablesRuntimeManager: WearablesRuntimeManager,
    settings: AppSettingsStore.Settings
  ) {
    self.settings = settings
    let runtimeViewModel = OnboardingSessionSupport.makeAssistantRuntimeViewModel(
      wearablesRuntimeManager: wearablesRuntimeManager,
      settings: settings
    )
    self.runtimeViewModel = runtimeViewModel
    self.status = runtimeViewModel.status

    runtimeViewModel.$status
      .receive(on: RunLoop.main)
      .sink { [weak self] in
        self?.status = $0
      }
      .store(in: &cancellables)

    runtimeViewModel.$isProfileOnboardingReady
      .receive(on: RunLoop.main)
      .sink { [weak self] in
        self?.isProfileReadyForReview = $0
      }
      .store(in: &cancellables)
  }

  var isInterviewRunning: Bool {
    switch status.assistantRuntimeState {
    case .connectingConversation, .activeConversation:
      return true
    case .inactive, .armedListening, .pausedByHardware, .deactivating:
      return false
    }
  }

  var canRetry: Bool {
    didAttemptStart &&
      isInterviewRunning == false &&
      isProfileReadyForReview == false &&
      (startupBlockerMessage != nil || status.errorText.isEmpty == false)
  }

  func startInterviewIfNeeded() async -> AssistantRuntimeViewModel.GuidedConversationStartResult {
    guard didAttemptStart == false else { return .blocked(.runtimeUnavailable("The onboarding interview has already been attempted.")) }
    return await startInterview()
  }

  func retryInterview() async -> AssistantRuntimeViewModel.GuidedConversationStartResult {
    await startInterview()
  }

  func stopInterview() async {
    await runtimeViewModel.stopGuidedConversation()
  }

  func handleScenePhaseChange(_ phase: ScenePhase) {
    runtimeViewModel.handleScenePhaseChange(phase)
  }

  func waitUntilProfileReadyForReview() async {
    guard isProfileReadyForReview == false else { return }

    for await isReady in $isProfileReadyForReview.values {
      if isReady {
        return
      }
    }
  }

  private func startInterview() async -> AssistantRuntimeViewModel.GuidedConversationStartResult {
    guard isStarting == false else { return .blocked(.runtimeUnavailable("The onboarding interview is already starting.")) }
    isStarting = true
    didAttemptStart = true
    isProfileReadyForReview = false
    startupBlockerMessage = nil
    let result = await runtimeViewModel.startGuidedConversation(
      backendValidationState: settings.validationState,
      backendReadinessDetail: settings.backendReadinessDetail
    )
    if case .blocked(let blocker) = result {
      startupBlockerMessage = blocker.message
    }
    isStarting = false
    return result
  }
}

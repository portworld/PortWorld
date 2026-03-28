import Combine
import SwiftUI

@MainActor
final class ProfileInterviewSessionViewModel: ObservableObject {
  @Published private(set) var status: AssistantRuntimeStatus
  @Published private(set) var isStarting = false
  @Published private(set) var didAttemptStart = false
  @Published private(set) var isProfileReadyForReview = false

  private let runtimeViewModel: AssistantRuntimeViewModel
  private var cancellables = Set<AnyCancellable>()

  init(
    wearablesRuntimeManager: WearablesRuntimeManager,
    settings: AppSettingsStore.Settings
  ) {
    let config = AssistantRuntimeConfig.load(
      backendBaseURLOverride: settings.backendBaseURL,
      bearerTokenOverride: settings.bearerToken
    )
    let runtimeViewModel = AssistantRuntimeViewModel(
      wearablesRuntimeManager: wearablesRuntimeManager,
      config: config
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
      status.errorText.isEmpty == false
  }

  func startInterviewIfNeeded() async {
    guard didAttemptStart == false else { return }
    await startInterview()
  }

  func retryInterview() async {
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

  private func startInterview() async {
    guard isStarting == false else { return }
    isStarting = true
    didAttemptStart = true
    isProfileReadyForReview = false
    await runtimeViewModel.startGuidedConversation()
    isStarting = false
  }
}

import Combine
import SwiftUI

@MainActor
final class ProfileInterviewSessionViewModel: ObservableObject {
  @Published private(set) var status: AssistantRuntimeStatus
  @Published private(set) var isStarting = false
  @Published private(set) var hasStartedInterview = false

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
  }

  var isInterviewRunning: Bool {
    switch status.assistantRuntimeState {
    case .connectingConversation, .activeConversation:
      return true
    case .inactive, .armedListening, .pausedByHardware, .deactivating:
      return false
    }
  }

  func startInterview() async {
    guard isStarting == false else { return }
    isStarting = true
    hasStartedInterview = true
    await runtimeViewModel.startGuidedConversation(instructions: Self.instructions)
    isStarting = false
  }

  func stopInterview() async {
    await runtimeViewModel.stopGuidedConversation()
  }

  private static let instructions = """
  You are onboarding a first-time PortWorld user through a live voice conversation.

  Goals:
  - Lead the conversation proactively.
  - Ask one short question at a time.
  - Collect the user's name, job, company, preferences, and current projects.
  - Keep the tone concise, warm, and product-focused.

  Profile rules:
  - Start by calling get_user_profile.
  - Use update_user_profile only after the user has clearly confirmed a fact.
  - Do not guess unknown values.
  - If a field is already known, avoid asking for it again unless clarification is needed.
  - Preferences should be short phrases, not paragraphs.
  - Projects should be a short list of concrete things the user is working on.

  Conversation rules:
  - Do not mention tools, prompts, or internal system behavior.
  - Do not give a long introduction.
  - Ask for only one missing field at a time.
  - Once you have enough information, tell the user their profile is ready to review in the app.
  """
}

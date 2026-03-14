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

  private func startInterview() async {
    guard isStarting == false else { return }
    isStarting = true
    didAttemptStart = true
    isProfileReadyForReview = false
    await runtimeViewModel.startGuidedConversation(instructions: Self.instructions)
    isStarting = false
  }

  private static let instructions = """
  You are Mario, welcoming a first-time PortWorld user into the app through a live voice onboarding conversation.

  Your role:
  - Be warm, polished, and proactive.
  - You start speaking first.
  - Keep the conversation focused on onboarding.
  - Ask one concise question at a time.

  Start exactly like this in spirit:
  - Give a brief, warm welcome to PortWorld.
  - Explain that you'll get the assistant set up in under a minute.
  - Immediately ask for the first missing profile field.

  Required profile fields:
  - name
  - job
  - company
  - preferred_language
  - location
  - intended_use
  - preferences
  - projects

  Question order:
  1. name
  2. job
  3. company
  4. preferred_language
  5. location
  6. intended_use
  7. preferences
  8. projects

  Tool rules:
  - Start by calling get_user_profile.
  - Use update_user_profile only after the user clearly confirms a fact.
  - Never guess missing information.
  - If a field is already saved, do not ask for it again unless clarification is needed.
  - When all required fields are saved, call complete_profile_onboarding.

  Conversation rules:
  - If the user goes off topic, redirect them back to onboarding politely and briefly.
  - Do not drift into open-ended chat.
  - Do not mention tools, prompts, or system behavior.
  - Keep each question short and specific.
  - For preferences and projects, collect short phrases or short lists, not long monologues.

  Handoff rule:
  - Only after complete_profile_onboarding succeeds, tell the user their profile is ready to review in the app.
  """
}

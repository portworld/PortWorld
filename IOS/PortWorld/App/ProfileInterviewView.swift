import SwiftUI

struct ProfileInterviewView: View {
  @Environment(\.scenePhase) private var scenePhase
  let onContinue: () -> Void

  @StateObject private var viewModel: ProfileInterviewSessionViewModel
  @State private var isContinuing = false

  init(
    wearablesRuntimeManager: WearablesRuntimeManager,
    settings: AppSettingsStore.Settings,
    onContinue: @escaping () -> Void
  ) {
    self.onContinue = onContinue
    _viewModel = StateObject(
      wrappedValue: ProfileInterviewSessionViewModel(
        wearablesRuntimeManager: wearablesRuntimeManager,
        settings: settings
      )
    )
  }

  var body: some View {
    PWOnboardingScaffold(
      style: .centeredHero,
      title: "Let Mario get to know you",
      subtitle: "He’ll welcome you, teach the interaction flow, and ask a few setup questions before unlocking the app.",
      content: {
        VStack(spacing: PWSpace.hero) {
          VStack(spacing: PWSpace.md) {
            Text(headline)
              .font(.system(size: 36, weight: .bold, design: .rounded))
              .foregroundStyle(headlineColor)
              .multilineTextAlignment(.center)

            Text(detailText)
              .font(PWTypography.body)
              .foregroundStyle(PWColor.textSecondary)
              .multilineTextAlignment(.center)
              .frame(maxWidth: 320)
          }

          if let startupBlockerMessage = viewModel.startupBlockerMessage {
            PWStatusRow(
              title: "Interview blocked",
              value: startupBlockerMessage,
              tone: .warning,
              systemImage: "pause.circle"
            )
            .frame(maxWidth: 320, alignment: .leading)
          }

          if viewModel.status.errorText.isEmpty == false {
            PWStatusRow(
              title: "Interview issue",
              value: viewModel.status.errorText,
              tone: .error,
              systemImage: "exclamationmark.triangle"
            )
            .frame(maxWidth: 320, alignment: .leading)
          }
        }
      },
      footer: {
        PWOnboardingButton(
          title: primaryButtonTitle,
          isDisabled: isPrimaryDisabled,
          action: primaryAction
        )
      }
    )
    .task {
      await runInterviewFlow()
    }
    .onChange(of: scenePhase) { _, newValue in
      viewModel.handleScenePhaseChange(newValue)
    }
  }
}

private extension ProfileInterviewView {
  var headline: String {
    if viewModel.startupBlockerMessage != nil {
      return "Interview blocked"
    }

    if viewModel.status.errorText.isEmpty == false && viewModel.isProfileReadyForReview == false {
      return "Interview interrupted"
    }

    if viewModel.isProfileReadyForReview {
      return "Profile ready"
    }

    if viewModel.isStarting {
      return "Mario is joining"
    }

    return "Mario is getting to know you"
  }

  var detailText: String {
    if let startupBlockerMessage = viewModel.startupBlockerMessage {
      return startupBlockerMessage
    }

    if viewModel.status.errorText.isEmpty == false && viewModel.isProfileReadyForReview == false {
      return "The onboarding interview stopped before it could finish. Start it again once your glasses audio is ready."
    }

    if viewModel.isProfileReadyForReview {
      return "Mario finished the interview. Wrapping things up now."
    }

    if viewModel.isStarting {
      return "Opening your onboarding session through the glasses now. Mario will start speaking first."
    }

    return "Listen and answer naturally through your glasses. Mario will explain the interaction flow and collect your initial profile details."
  }

  var headlineColor: Color {
    if viewModel.startupBlockerMessage != nil {
      return PWColor.warning
    }

    if viewModel.status.errorText.isEmpty == false && viewModel.isProfileReadyForReview == false {
      return PWColor.error
    }

    return viewModel.isProfileReadyForReview ? PWColor.success : PWColor.textPrimary
  }

  var primaryButtonTitle: String {
    if viewModel.isProfileReadyForReview {
      return "Wrapping up..."
    }

    if viewModel.canRetry {
      return viewModel.isStarting ? "Restarting..." : "Try again"
    }

    return viewModel.isStarting ? "Starting..." : "Listening..."
  }

  var isPrimaryDisabled: Bool {
    if viewModel.isProfileReadyForReview == false && viewModel.canRetry == false {
      return true
    }

    return viewModel.isStarting || isContinuing || viewModel.isProfileReadyForReview
  }

  func primaryAction() {
    guard viewModel.canRetry else { return }
    Task {
      let startResult = await viewModel.retryInterview()
      guard startResult == .started else { return }
      await viewModel.waitUntilProfileReadyForReview()
      guard Task.isCancelled == false else { return }
      await MainActor.run {
        continueIfNeeded()
      }
    }
  }

  func runInterviewFlow() async {
    await withTaskCancellationHandler(operation: {
      let startResult = await viewModel.startInterviewIfNeeded()
      guard startResult == .started else { return }
      await viewModel.waitUntilProfileReadyForReview()
      guard Task.isCancelled == false else { return }
      continueIfNeeded()
    }, onCancel: {
      Task {
        await stopInterviewIfNeeded()
      }
    })
  }

  func continueIfNeeded() {
    guard isContinuing == false else { return }
    isContinuing = true
    Task {
      await stopInterviewIfNeeded()
      await MainActor.run {
        onContinue()
      }
    }
  }

  func stopInterviewIfNeeded() async {
    if viewModel.isInterviewRunning {
      await viewModel.stopInterview()
    }
  }
}

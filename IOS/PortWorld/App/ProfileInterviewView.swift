import SwiftUI

struct ProfileInterviewView: View {
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
      subtitle: "He’ll welcome you, ask a few setup questions, and finish setup when he has enough to get started.",
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
      await viewModel.startInterviewIfNeeded()
    }
    .onChange(of: viewModel.isProfileReadyForReview) { _, isReady in
      guard isReady else { return }
      continueIfNeeded()
    }
    .onDisappear {
      if viewModel.isInterviewRunning {
        Task { await viewModel.stopInterview() }
      }
    }
  }
}

private extension ProfileInterviewView {
  var headline: String {
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
    if viewModel.status.errorText.isEmpty == false && viewModel.isProfileReadyForReview == false {
      return "The onboarding interview stopped before it could finish. Start it again and Mario will pick it up from the beginning."
    }

    if viewModel.isProfileReadyForReview {
      return "Mario finished the interview. Wrapping things up now."
    }

    if viewModel.isStarting {
      return "Opening your onboarding session now. Mario will start speaking first."
    }

    return "Listen and answer naturally. Mario will keep the conversation focused on setting up your profile."
  }

  var headlineColor: Color {
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
    Task { await viewModel.retryInterview() }
  }

  func continueIfNeeded() {
    guard isContinuing == false else { return }
    isContinuing = true
    Task {
      await viewModel.stopInterview()
      await MainActor.run {
        onContinue()
      }
    }
  }
}

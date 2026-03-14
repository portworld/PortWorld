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
      subtitle: "A short voice conversation will set up your profile for the assistant.",
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
    .onDisappear {
      if viewModel.isInterviewRunning {
        Task { await viewModel.stopInterview() }
      }
    }
  }
}

private extension ProfileInterviewView {
  var headline: String {
    if viewModel.isStarting {
      return "Starting interview..."
    }

    if viewModel.isInterviewRunning {
      return "Mario is listening"
    }

    if viewModel.hasStartedInterview {
      return viewModel.status.errorText.isEmpty ? "Profile ready to review" : "Try the interview again"
    }

    return "Ready when you are"
  }

  var detailText: String {
    if viewModel.isInterviewRunning {
      return "Speak naturally. Mario will ask for your name, work, preferences, and projects one question at a time."
    }

    if viewModel.hasStartedInterview {
      return viewModel.status.errorText.isEmpty
        ? "When the conversation feels complete, continue to review and edit what was saved."
        : "The interview stopped before it could finish. Start it again when you're ready."
    }

    return "This first conversation personalizes PortWorld before you enter the app."
  }

  var headlineColor: Color {
    if viewModel.status.errorText.isEmpty == false {
      return PWColor.error
    }

    return viewModel.isInterviewRunning ? PWColor.success : PWColor.textPrimary
  }

  var primaryButtonTitle: String {
    if viewModel.isStarting {
      return "Starting..."
    }

    if viewModel.isInterviewRunning {
      return isContinuing ? "Wrapping up..." : "Continue to review"
    }

    if viewModel.hasStartedInterview {
      return "Start again"
    }

    return "Start interview"
  }

  var isPrimaryDisabled: Bool {
    viewModel.isStarting || isContinuing
  }

  func primaryAction() {
    if viewModel.isInterviewRunning {
      isContinuing = true
      Task {
        await viewModel.stopInterview()
        await MainActor.run {
          onContinue()
        }
      }
      return
    }

    Task { await viewModel.startInterview() }
  }
}

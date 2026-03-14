import SwiftUI

struct WakePracticeView: View {
  let settings: AppSettingsStore.Settings
  let onContinue: () -> Void

  @StateObject private var viewModel: WakePracticeSessionViewModel
  @State private var isContinuing = false

  init(
    wearablesRuntimeManager _: WearablesRuntimeManager,
    settings: AppSettingsStore.Settings,
    onContinue: @escaping () -> Void
  ) {
    self.settings = settings
    self.onContinue = onContinue

    let config = AssistantRuntimeConfig.load(
      backendBaseURLOverride: settings.backendBaseURL,
      bearerTokenOverride: settings.bearerToken
    )
    _viewModel = StateObject(wrappedValue: WakePracticeSessionViewModel(config: config))
  }

  var body: some View {
    PWOnboardingScaffold(
      style: .centeredHero,
      title: stageTitle,
      subtitle: stageSubtitle,
      content: {
        VStack(spacing: PWSpace.hero) {
          VStack(spacing: PWSpace.md) {
            Text(viewModel.feedback.title)
              .font(.system(size: 38, weight: .bold, design: .rounded))
              .foregroundStyle(feedbackColor)
              .multilineTextAlignment(.center)

            Text(viewModel.feedback.detail)
              .font(PWTypography.body)
              .foregroundStyle(PWColor.textSecondary)
              .multilineTextAlignment(.center)
              .frame(maxWidth: 300)
          }
          .id("\(viewModel.feedback.title)|\(viewModel.feedback.detail)")

          PracticeDotRow(
            completedCount: currentCompletedCount,
            total: 3,
            tint: feedbackColor,
            isListening: viewModel.isListening
          )
        }
      },
      footer: {
        VStack(spacing: PWSpace.md) {
          PWOnboardingButton(
            title: primaryButtonTitle,
            isDisabled: primaryButtonDisabled,
            action: primaryAction
          )

          if viewModel.isListening && viewModel.stage != .completed {
            Button("Stop listening") {
              Task { await viewModel.stopListening() }
            }
            .buttonStyle(.plain)
            .font(PWTypography.subbody)
            .foregroundStyle(PWColor.textSecondary)
          }
        }
      }
    )
    .animation(.easeOut(duration: 0.22), value: viewModel.stage)
    .onDisappear {
      Task { await viewModel.stopListening() }
    }
  }
}

private extension WakePracticeView {
  var stageTitle: String {
    switch viewModel.stage {
    case .wake:
      return "Say \"\(formattedPhrase(viewModel.wakePhrase))\""
    case .sleep:
      return "Say \"\(formattedPhrase(viewModel.sleepPhrase))\""
    case .completed:
      return "Voice commands are ready"
    }
  }

  var stageSubtitle: String {
    switch viewModel.stage {
    case .wake:
      return "We’ll listen for your wake phrase three times."
    case .sleep:
      return "Now we’ll listen for your sleep phrase three times."
    case .completed:
      return "Your wake and sleep phrases were both detected correctly."
    }
  }

  var currentCompletedCount: Int {
    switch viewModel.stage {
    case .wake:
      return viewModel.wakeCount
    case .sleep, .completed:
      return viewModel.stage == .completed ? 3 : viewModel.sleepCount
    }
  }

  var feedbackColor: Color {
    switch viewModel.feedback.tone {
    case .neutral:
      return PWColor.textPrimary
    case .success:
      return PWColor.success
    case .error:
      return PWColor.error
    }
  }

  var primaryButtonTitle: String {
    if viewModel.stage == .completed {
      return isContinuing ? "Finishing..." : "Continue"
    }

    if viewModel.isListening {
      return "Listening"
    }

    return "Start listening"
  }

  var primaryButtonDisabled: Bool {
    if viewModel.stage == .completed {
      return isContinuing
    }

    return viewModel.isListening
  }

  func primaryAction() {
    if viewModel.stage == .completed {
      isContinuing = true
      onContinue()
      return
    }

    Task { await viewModel.startListening() }
  }

  func formattedPhrase(_ phrase: String) -> String {
    phrase
      .split(separator: " ")
      .map { $0.prefix(1).uppercased() + $0.dropFirst().lowercased() }
      .joined(separator: " ")
  }
}

private struct PracticeDotRow: View {
  let completedCount: Int
  let total: Int
  let tint: Color
  let isListening: Bool

  var body: some View {
    HStack(spacing: PWSpace.md) {
      ForEach(0..<total, id: \.self) { index in
        Circle()
          .fill(fillColor(for: index))
          .frame(width: 12, height: 12)
          .overlay(
            Circle()
              .stroke(borderColor(for: index), lineWidth: 1)
          )
      }
    }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel("\(completedCount) of \(total) detections completed")
  }

  private func fillColor(for index: Int) -> Color {
    if index < completedCount {
      return tint
    }

    return isListening ? PWColor.surfaceRaised : PWColor.surface
  }

  private func borderColor(for index: Int) -> Color {
    if index < completedCount {
      return tint
    }

    return PWColor.border
  }
}

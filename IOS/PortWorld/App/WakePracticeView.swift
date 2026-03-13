import SwiftUI

struct WakePracticeView: View {
  let wearablesRuntimeManager: WearablesRuntimeManager
  let settings: AppSettingsStore.Settings
  let onContinue: () -> Void

  @StateObject private var viewModel: AssistantRuntimeViewModel
  @State private var wakeSuccessCount = 0
  @State private var sleepSuccessCount = 0
  @State private var isCompleting = false

  init(
    wearablesRuntimeManager: WearablesRuntimeManager,
    settings: AppSettingsStore.Settings,
    onContinue: @escaping () -> Void
  ) {
    self.wearablesRuntimeManager = wearablesRuntimeManager
    self.settings = settings
    self.onContinue = onContinue

    let config = AssistantRuntimeConfig.load(
      backendBaseURLOverride: settings.backendBaseURL,
      bearerTokenOverride: settings.bearerToken
    )
    _viewModel = StateObject(
      wrappedValue: AssistantRuntimeViewModel(
        wearablesRuntimeManager: wearablesRuntimeManager,
        config: config
      )
    )
  }

  var body: some View {
    PWOnboardingScaffold(
      style: .leadingContent,
      title: "Practice your voice commands",
      subtitle: "Say \"\(viewModel.status.wakePhraseText)\" three times, then \"\(viewModel.status.sleepPhraseText)\" three times.",
      content: {
        VStack(alignment: .leading, spacing: PWSpace.section) {
          VStack(alignment: .leading, spacing: PWSpace.lg) {
            PracticePhraseRow(
              title: "Wake assistant",
              phrase: viewModel.status.wakePhraseText,
              count: wakeSuccessCount,
              goal: 3,
              isActive: wakeSuccessCount < 3
            )

            PracticePhraseRow(
              title: "Put assistant to sleep",
              phrase: viewModel.status.sleepPhraseText,
              count: sleepSuccessCount,
              goal: 3,
              isActive: wakeSuccessCount >= 3 && sleepSuccessCount < 3
            )
          }

          PWStatusRow(
            title: currentStatusTitle,
            value: currentStatusDetail,
            tone: currentStatusTone,
            systemImage: currentStatusSymbol
          )

          Text("Practice uses the iPhone microphone during onboarding so you can confirm the wake phrases work before moving on.")
            .font(PWTypography.caption)
            .foregroundStyle(PWColor.textSecondary)
            .fixedSize(horizontal: false, vertical: true)
        }
      },
      footer: {
        VStack(spacing: PWSpace.md) {
          PWOnboardingButton(
            title: footerButtonTitle,
            isDisabled: footerButtonDisabled,
            action: footerAction
          )

          if showSecondaryStopAction {
            Button("Stop listening") {
              Task { await viewModel.deactivateAssistant() }
            }
            .buttonStyle(.plain)
            .font(PWTypography.subbody)
            .foregroundStyle(PWColor.textSecondary)
          }
        }
      }
    )
    .onChange(of: viewModel.wakeDetectionSequence) { _, _ in
      guard wakeSuccessCount < 3 else { return }
      wakeSuccessCount += 1
    }
    .onChange(of: viewModel.sleepDetectionSequence) { _, _ in
      guard wakeSuccessCount >= 3 else { return }
      guard sleepSuccessCount < 3 else { return }
      sleepSuccessCount += 1
      if sleepSuccessCount == 3 {
        Task { await viewModel.deactivateAssistant() }
      }
    }
    .onDisappear {
      Task { await viewModel.deactivateAssistant() }
    }
  }
}

private extension WakePracticeView {
  var practiceCompleted: Bool {
    wakeSuccessCount >= 3 && sleepSuccessCount >= 3
  }

  var isListening: Bool {
    viewModel.status.assistantRuntimeState != .inactive &&
      viewModel.status.assistantRuntimeState != .deactivating
  }

  var footerButtonTitle: String {
    if practiceCompleted { return isCompleting ? "Finishing..." : "Continue" }
    if isListening { return "Listening" }
    return "Start practice"
  }

  var footerButtonDisabled: Bool {
    if practiceCompleted { return isCompleting }
    return isListening
  }

  var showSecondaryStopAction: Bool {
    practiceCompleted == false && isListening
  }

  func footerAction() {
    if practiceCompleted {
      isCompleting = true
      Task {
        await viewModel.deactivateAssistant()
        await MainActor.run {
          onContinue()
          isCompleting = false
        }
      }
      return
    }

    Task {
      viewModel.selectRoute(.phone)
      await viewModel.activateAssistant()
    }
  }

  var currentStatusTitle: String {
    if practiceCompleted {
      return "Voice practice complete"
    }

    if wakeSuccessCount < 3 {
      return "Listening for wake phrase"
    }

    return "Listening for sleep phrase"
  }

  var currentStatusDetail: String {
    if practiceCompleted {
      return "Wake and sleep commands were both detected three times."
    }

    if viewModel.status.errorText.isEmpty == false {
      return viewModel.status.errorText
    }

    if wakeSuccessCount < 3 {
      return "Say \"\(viewModel.status.wakePhraseText)\" clearly to start a conversation."
    }

    return "Say \"\(viewModel.status.sleepPhraseText)\" while the conversation is active."
  }

  var currentStatusTone: PWStatusTone {
    if practiceCompleted { return .success }
    if viewModel.status.errorText.isEmpty == false { return .error }
    return isListening ? .neutral : .warning
  }

  var currentStatusSymbol: String {
    if practiceCompleted { return "checkmark.circle" }
    if viewModel.status.errorText.isEmpty == false { return "exclamationmark.triangle" }
    return isListening ? "waveform" : "mic"
  }
}

private struct PracticePhraseRow: View {
  let title: String
  let phrase: String
  let count: Int
  let goal: Int
  let isActive: Bool

  var body: some View {
    VStack(alignment: .leading, spacing: PWSpace.sm) {
      HStack(alignment: .center, spacing: PWSpace.md) {
        VStack(alignment: .leading, spacing: 2) {
          Text(title)
            .font(PWTypography.headline)
            .foregroundStyle(PWColor.textPrimary)

          Text("\"\(phrase)\"")
            .font(PWTypography.body)
            .foregroundStyle(PWColor.textSecondary)
        }

        Spacer(minLength: 0)

        Text("\(count)/\(goal)")
          .font(PWTypography.headline)
          .foregroundStyle(count >= goal ? PWColor.success : PWColor.textPrimary)
      }

      ProgressView(value: Double(count), total: Double(goal))
        .tint(count >= goal ? PWColor.success : (isActive ? PWColor.textPrimary : PWColor.borderStrong))
    }
    .padding(.vertical, 4)
  }
}

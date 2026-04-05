import SwiftUI

struct WakePracticeView: View {
  @Environment(\.scenePhase) private var scenePhase

  let onContinue: () -> Void

  @StateObject private var viewModel: WakePracticeSessionViewModel
  @State private var isContinuing = false

  init(
    wearablesRuntimeManager: WearablesRuntimeManager,
    settings: AppSettingsStore.Settings,
    onContinue: @escaping () -> Void
  ) {
    self.onContinue = onContinue
    _viewModel = StateObject(
      wrappedValue: WakePracticeSessionViewModel(
        wearablesRuntimeManager: wearablesRuntimeManager,
        settings: settings
      )
    )
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
            completedCount: viewModel.currentCompletedCount,
            total: 3,
            tint: feedbackColor,
            isListening: viewModel.isListening
          )

          VStack(alignment: .leading, spacing: PWSpace.lg) {
            PWStatusRow(
              title: "Glasses session",
              value: glassesSessionDetail,
              tone: glassesSessionTone,
              systemImage: glassesSessionSymbol
            )

            PWStatusRow(
              title: "Glasses audio",
              value: viewModel.audioRouteDetail,
              tone: glassesAudioTone,
              systemImage: glassesAudioSymbol
            )
          }
          .frame(maxWidth: 320, alignment: .leading)

          Text("Practice listens through your connected Meta glasses so you can confirm both voice commands before the interview starts.")
            .font(PWTypography.caption)
            .foregroundStyle(PWColor.textSecondary)
            .multilineTextAlignment(.center)
            .frame(maxWidth: 320)
        }
      },
      footer: {
        VStack(spacing: PWSpace.md) {
          PWOnboardingButton(
            title: primaryButtonTitle,
            isDisabled: primaryButtonDisabled,
            action: primaryAction
          )

          if viewModel.isListening && viewModel.canContinue == false {
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
    .onChange(of: scenePhase) { _, newValue in
      viewModel.handleScenePhaseChange(newValue)
    }
    .onDisappear {
      Task { await viewModel.stopListening() }
    }
  }
}

private extension WakePracticeView {
  var stageTitle: String {
    switch viewModel.stage {
    case .wake:
      return "Say \"\(OnboardingSessionSupport.formattedPhrase(viewModel.wakePhrase))\""
    case .sleep:
      return "Say \"\(OnboardingSessionSupport.formattedPhrase(viewModel.sleepPhrase))\""
    case .completed:
      return "Voice commands are ready"
    }
  }

  var stageSubtitle: String {
    switch viewModel.stage {
    case .wake:
      return "We’ll listen through your glasses for the wake phrase three times."
    case .sleep:
      return "Now we’ll listen through your glasses for the sleep phrase three times."
    case .completed:
      return "Your glasses detected both phrases correctly."
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
    if viewModel.canContinue {
      return isContinuing ? "Finishing..." : "Continue"
    }

    if viewModel.isStarting {
      return "Starting..."
    }

    if viewModel.isListening {
      return "Listening"
    }

    return "Start listening"
  }

  var primaryButtonDisabled: Bool {
    if viewModel.canContinue {
      return isContinuing
    }

    return viewModel.isStarting || viewModel.isListening
  }

  var glassesSessionDetail: String {
    if let startupBlockerMessage = viewModel.startupBlockerMessage,
       viewModel.didAttemptStart
    {
      return startupBlockerMessage
    }

    if let sessionErrorMessage = viewModel.sessionErrorMessage,
       sessionErrorMessage.isEmpty == false
    {
      return sessionErrorMessage
    }

    switch viewModel.sessionPhase {
    case .inactive:
      return "The glasses session will start when practice begins."
    case .starting:
      return "Connecting to the glasses now."
    case .waitingForDevice:
      return "Bring your approved glasses nearby and reconnect."
    case .running:
      return viewModel.isListening
        ? "Practice is listening through the glasses."
        : "The glasses session is ready."
    case .paused:
      return "The glasses session paused. Resume when the hardware route is ready again."
    case .stopping:
      return "Stopping the glasses session."
    case .failed:
      return viewModel.sessionErrorMessage ?? "The glasses session could not start."
    }
  }

  var glassesSessionTone: PWStatusTone {
    if viewModel.canContinue {
      return .success
    }

    if viewModel.startupBlockerMessage != nil || viewModel.sessionErrorMessage != nil {
      return .error
    }

    switch viewModel.sessionPhase {
    case .running:
      return .success
    case .inactive, .starting, .stopping:
      return .neutral
    case .waitingForDevice, .paused:
      return .warning
    case .failed:
      return .error
    }
  }

  var glassesSessionSymbol: String {
    if viewModel.canContinue {
      return "checkmark.circle"
    }

    switch viewModel.sessionPhase {
    case .running:
      return "dot.radiowaves.up.forward"
    case .inactive, .starting, .stopping:
      return "glasses"
    case .waitingForDevice, .paused:
      return "pause.circle"
    case .failed:
      return "exclamationmark.triangle"
    }
  }

  var glassesAudioTone: PWStatusTone {
    if viewModel.canContinue {
      return .success
    }

    if viewModel.isListening {
      return .success
    }

    if viewModel.sessionErrorMessage != nil {
      return .error
    }

    return .neutral
  }

  var glassesAudioSymbol: String {
    if viewModel.canContinue {
      return "waveform.badge.checkmark"
    }

    return viewModel.isListening ? "waveform" : "airpodsmax"
  }

  func primaryAction() {
    if viewModel.canContinue {
      isContinuing = true
      onContinue()
      isContinuing = false
      return
    }

    Task {
      await viewModel.startListening()
    }
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

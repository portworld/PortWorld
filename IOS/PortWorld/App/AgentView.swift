import SwiftUI

struct AgentView: View {
  @ObservedObject private var viewModel: AssistantRuntimeViewModel
  @ObservedObject private var appSettingsStore: AppSettingsStore
  @ObservedObject private var wearablesRuntimeManager: WearablesRuntimeManager

  init(
    viewModel: AssistantRuntimeViewModel,
    appSettingsStore: AppSettingsStore,
    wearablesRuntimeManager: WearablesRuntimeManager
  ) {
    self.viewModel = viewModel
    self.appSettingsStore = appSettingsStore
    self.wearablesRuntimeManager = wearablesRuntimeManager
  }

  var body: some View {
    let readiness = HomeReadinessState(
      settings: appSettingsStore.settings,
      runtimeStatus: viewModel.status,
      wearablesRuntimeManager: wearablesRuntimeManager
    )

    PWScreen(title: "Agent", topPadding: PWSpace.md) {
      VStack(spacing: PWSpace.section) {
        Spacer(minLength: 0)

        AgentPlaceholderView(isAwake: isAwake)

        VStack(spacing: PWSpace.md) {
          Text(statusLine(readiness: readiness))
            .font(PWTypography.title)
            .foregroundStyle(PWColor.textPrimary)
            .multilineTextAlignment(.center)

          Text(isAwake ? "Mario is active." : "Mario is resting.")
            .font(PWTypography.body)
            .foregroundStyle(PWColor.textSecondary)
            .multilineTextAlignment(.center)
        }
        .frame(maxWidth: 320)

        primaryButton(readiness: readiness)
          .frame(maxWidth: .infinity)

        Spacer(minLength: 0)
      }
      .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
    .onAppear {
      viewModel.selectRoute(.glasses)
    }
  }
}

private extension AgentView {
  var isAwake: Bool {
    switch viewModel.status.assistantRuntimeState {
    case .inactive:
      return false
    case .armedListening, .connectingConversation, .activeConversation, .pausedByHardware, .deactivating:
      return true
    }
  }

  @ViewBuilder
  func primaryButton(readiness: HomeReadinessState) -> some View {
    if viewModel.status.canDeactivate {
      PWPrimaryButton(title: "Deactivate Assistant") {
        Task {
          await viewModel.deactivateAssistant()
        }
      }
    } else {
      PWPrimaryButton(
        title: buttonTitle,
        isDisabled: readiness.canActivateAssistant == false || isStopping
      ) {
        viewModel.selectRoute(.glasses)
        Task {
          await viewModel.activateAssistant()
        }
      }
    }
  }

  var isStopping: Bool {
    viewModel.status.assistantRuntimeState == .deactivating
  }

  var buttonTitle: String {
    isStopping ? "Stopping…" : "Activate Assistant"
  }

  func statusLine(readiness: HomeReadinessState) -> String {
    let runtimeState = viewModel.status.assistantRuntimeState

    switch runtimeState {
    case .inactive:
      if readiness.canActivateAssistant == false {
        if readiness.backendStatus.action == .openBackendSettings {
          return "Backend needs attention"
        }

        return "Glasses aren’t ready"
      }

      return "Ready to wake Mario"

    case .connectingConversation:
      return "Mario is joining"

    case .armedListening:
      return "Listening for \"\(viewModel.status.wakePhraseText)\""

    case .activeConversation:
      return "Mario is awake"

    case .pausedByHardware:
      return "Mario is waiting for your glasses"

    case .deactivating:
      return "Mario is going back to sleep"
    }
  }
}

private struct AgentPlaceholderView: View {
  let isAwake: Bool

  var body: some View {
    ZStack {
      Circle()
        .fill(PWColor.surfaceRaised)
        .frame(width: 220, height: 220)
        .overlay(
          Circle()
            .stroke(isAwake ? PWColor.borderStrong : PWColor.border, lineWidth: 1)
        )

      Image(systemName: isAwake ? "sparkles" : "moon.zzz.fill")
        .font(.system(size: 72, weight: .medium))
        .foregroundStyle(isAwake ? PWColor.textPrimary : PWColor.textSecondary)
    }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(isAwake ? "Mario is awake" : "Mario is asleep")
  }
}

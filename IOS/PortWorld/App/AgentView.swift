import SwiftUI

struct AgentView: View {
  let readiness: HomeReadinessState
  let runtimeStatus: AssistantRuntimeStatus
  let onActivateAssistant: () -> Void
  let onDeactivateAssistant: () -> Void

  var body: some View {
    PWScreen(title: "Agent", titleAlignment: .center, topPadding: PWSpace.md) {
      VStack(spacing: PWSpace.section) {
        Spacer(minLength: 0)

        AgentPlaceholderView(isAwake: isAwake)

        VStack(spacing: PWSpace.md) {
          Text(statusLine(readiness: readiness))
            .font(PWTypography.title)
            .foregroundStyle(PWColor.textPrimary)
            .multilineTextAlignment(.center)

          Text(detailLine(readiness: readiness))
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
  }
}

private extension AgentView {
  var isAwake: Bool {
    switch runtimeStatus.assistantRuntimeState {
    case .inactive:
      return false
    case .armedListening, .connectingConversation, .activeConversation, .pausedByHardware, .deactivating:
      return true
    }
  }

  @ViewBuilder
  func primaryButton(readiness: HomeReadinessState) -> some View {
    if runtimeStatus.canDeactivate {
      PWPrimaryButton(title: "Deactivate Assistant") {
        onDeactivateAssistant()
      }
    } else {
      PWPrimaryButton(
        title: runtimeStatus.activationButtonTitle,
        isDisabled: readiness.canActivateAssistant == false || isStopping
      ) {
        onActivateAssistant()
      }
    }
  }

  var isStopping: Bool {
    runtimeStatus.assistantRuntimeState == .deactivating
  }

  func statusLine(readiness: HomeReadinessState) -> String {
    let runtimeState = runtimeStatus.assistantRuntimeState

    switch runtimeState {
    case .inactive:
      if readiness.backendStatus.action == .openBackendSettings {
        return "Backend needs attention"
      }
      if readiness.canActivateAssistant == false {
        return "Glasses aren’t ready"
      }
      return "Ready to wake Mario"

    case .connectingConversation:
      return "Mario is joining"

    case .armedListening:
      return "Listening for \"\(runtimeStatus.wakePhraseText)\""

    case .activeConversation:
      return "Mario is awake"

    case .pausedByHardware:
      return "Mario is waiting for your glasses"

    case .deactivating:
      return "Mario is going back to sleep"
    }
  }

  func detailLine(readiness: HomeReadinessState) -> String {
    let runtimeState = runtimeStatus.assistantRuntimeState

    switch runtimeState {
    case .inactive:
      if readiness.backendStatus.action == .openBackendSettings {
        return readiness.backendStatus.detail
      }
      if readiness.canActivateAssistant == false {
        return readiness.glassesStatus.detail
      }
      return "Mario will start through your connected glasses."

    case .connectingConversation:
      return "Glasses route is opening a live backend session."

    case .armedListening:
      return "Mario is listening through your glasses."

    case .activeConversation:
      return "Glasses route is active."

    case .pausedByHardware:
      return "Reconnect your glasses or deactivate the assistant."

    case .deactivating:
      return "Mario is closing the current session."
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

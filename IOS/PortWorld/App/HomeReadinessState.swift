import Foundation
import MWDATCore

struct HomeStatusRowState {
  enum Action {
    case openBackendSettings
    case openGlassesSettings

    var title: String {
      switch self {
      case .openBackendSettings:
        return "Open Settings"
      case .openGlassesSettings:
        return "Connect Glasses"
      }
    }
  }

  let title: String
  let label: String
  let detail: String
  let tone: PWStatusTone
  let systemImage: String
  let action: Action?
}

struct HomeReadinessState {
  let assistantSummary: String
  let assistantDetail: String
  let canActivateAssistant: Bool
  let backendStatus: HomeStatusRowState
  let glassesStatus: HomeStatusRowState

  init(
    settings: AppSettingsStore.Settings,
    runtimeStatus: AssistantRuntimeStatus,
    wearablesRuntimeManager: WearablesRuntimeManager
  ) {
    let backendStatus = HomeReadinessState.makeBackendStatus(
      settings: settings,
      runtimeStatus: runtimeStatus
    )
    let glassesStatus = HomeReadinessState.makeGlassesStatus(
      runtimeStatus: runtimeStatus,
      wearablesRuntimeManager: wearablesRuntimeManager
    )

    self.backendStatus = backendStatus
    self.glassesStatus = glassesStatus

    self.canActivateAssistant = HomeReadinessState.canActivateSelectedRoute(
      settings: settings,
      runtimeStatus: runtimeStatus,
      wearablesRuntimeManager: wearablesRuntimeManager
    )

    let hero = HomeReadinessState.makeHeroState(
      runtimeStatus: runtimeStatus,
      canActivateAssistant: self.canActivateAssistant,
      backendStatus: backendStatus,
      glassesStatus: glassesStatus
    )
    self.assistantSummary = hero.summary
    self.assistantDetail = hero.detail
  }
}

private extension HomeReadinessState {
  static func canActivateSelectedRoute(
    settings: AppSettingsStore.Settings,
    runtimeStatus: AssistantRuntimeStatus,
    wearablesRuntimeManager: WearablesRuntimeManager
  ) -> Bool {
    guard settings.validationState == .valid else { return false }

    switch runtimeStatus.selectedRoute {
    case .phone:
      return true
    case .glasses:
      return areGlassesReady(wearablesRuntimeManager: wearablesRuntimeManager)
    }
  }

  static func areGlassesReady(
    wearablesRuntimeManager: WearablesRuntimeManager
  ) -> Bool {
    guard wearablesRuntimeManager.configurationState == .ready else { return false }
    guard wearablesRuntimeManager.registrationState == .registered else { return false }
    guard wearablesRuntimeManager.devices.isEmpty == false else { return false }
    guard wearablesRuntimeManager.activeCompatibilityMessage == nil else { return false }
    guard wearablesRuntimeManager.glassesSessionPhase != .failed else { return false }
    return true
  }

  static func makeBackendStatus(
    settings: AppSettingsStore.Settings,
    runtimeStatus: AssistantRuntimeStatus
  ) -> HomeStatusRowState {
    switch runtimeStatus.assistantRuntimeState {
    case .connectingConversation:
      return HomeStatusRowState(
        title: "Backend",
        label: "Connecting",
        detail: "PortWorld is opening a live backend session now.",
        tone: .neutral,
        systemImage: "network",
        action: nil
      )

    case .activeConversation:
      return HomeStatusRowState(
        title: "Backend",
        label: "Ready",
        detail: "Your backend session is active.",
        tone: .success,
        systemImage: "checkmark.circle",
        action: nil
      )

    case .inactive, .armedListening, .pausedByHardware, .deactivating:
      break
    }

    switch settings.validationState {
    case .valid:
      return HomeStatusRowState(
        title: "Backend",
        label: "Ready",
        detail: "Your backend was verified and is ready to use.",
        tone: .success,
        systemImage: "checkmark.circle",
        action: nil
      )

    case .invalid:
      return HomeStatusRowState(
        title: "Backend",
        label: "Needs attention",
        detail: "Backend validation failed. Check your URL or token.",
        tone: .error,
        systemImage: "exclamationmark.triangle",
        action: .openBackendSettings
      )

    case .unknown:
      let detail: String
      if settings.backendBaseURL.isEmpty {
        detail = "Add your self-hosted PortWorld backend to continue."
      } else {
        detail = "Re-check your backend connection before starting the assistant."
      }

      return HomeStatusRowState(
        title: "Backend",
        label: "Needs setup",
        detail: detail,
        tone: .warning,
        systemImage: "gearshape",
        action: .openBackendSettings
      )
    }
  }

  static func makeGlassesStatus(
    runtimeStatus: AssistantRuntimeStatus,
    wearablesRuntimeManager: WearablesRuntimeManager
  ) -> HomeStatusRowState {
    if let compatibilityMessage = wearablesRuntimeManager.activeCompatibilityMessage {
      return HomeStatusRowState(
        title: "Glasses",
        label: "Needs attention",
        detail: compatibilityMessage,
        tone: .warning,
        systemImage: "exclamationmark.triangle",
        action: .openGlassesSettings
      )
    }

    switch wearablesRuntimeManager.configurationState {
    case .idle, .configuring:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Connecting",
        detail: "Preparing Meta wearables support for the app.",
        tone: .neutral,
        systemImage: "gearshape",
        action: .openGlassesSettings
      )

    case .failed:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Needs attention",
        detail: wearablesRuntimeManager.configurationErrorMessage ?? "Meta wearables support failed to initialize.",
        tone: .error,
        systemImage: "xmark.octagon",
        action: .openGlassesSettings
      )

    case .ready:
      break
    }

    if runtimeStatus.assistantRuntimeState == .pausedByHardware {
      return HomeStatusRowState(
        title: "Glasses",
        label: "Needs attention",
        detail: "Your glasses session paused. Reconnect your glasses or deactivate the assistant.",
        tone: .warning,
        systemImage: "pause.circle",
        action: .openGlassesSettings
      )
    }

    if wearablesRuntimeManager.registrationState != .registered {
      return HomeStatusRowState(
        title: "Glasses",
        label: "Not connected",
        detail: "Authorize PortWorld in the Meta app before starting the assistant.",
        tone: .warning,
        systemImage: "eyeglasses",
        action: .openGlassesSettings
      )
    }

    if wearablesRuntimeManager.devices.isEmpty {
      return HomeStatusRowState(
        title: "Glasses",
        label: "Not connected",
        detail: "Bring your paired glasses nearby and reconnect.",
        tone: .warning,
        systemImage: "antenna.radiowaves.left.and.right",
        action: .openGlassesSettings
      )
    }

    switch wearablesRuntimeManager.glassesSessionPhase {
    case .starting:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Connecting",
        detail: "Starting a live glasses session now.",
        tone: .neutral,
        systemImage: "dot.radiowaves.left.and.right",
        action: nil
      )

    case .running:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Ready",
        detail: "Your glasses session is active.",
        tone: .success,
        systemImage: "checkmark.circle",
        action: nil
      )

    case .failed:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Needs attention",
        detail: wearablesRuntimeManager.glassesSessionErrorMessage ?? "The glasses session could not start.",
        tone: .error,
        systemImage: "xmark.octagon",
        action: .openGlassesSettings
      )

    case .paused:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Needs attention",
        detail: "Your glasses session is paused right now.",
        tone: .warning,
        systemImage: "pause.circle",
        action: .openGlassesSettings
      )

    case .waitingForDevice:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Connecting",
        detail: "Waiting for your glasses to become available nearby.",
        tone: .warning,
        systemImage: "antenna.radiowaves.left.and.right",
        action: .openGlassesSettings
      )

    case .inactive, .stopping:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Ready",
        detail: "Your glasses are connected and available.",
        tone: .success,
        systemImage: "checkmark.circle",
        action: nil
      )
    }
  }

  static func makeHeroState(
    runtimeStatus: AssistantRuntimeStatus,
    canActivateAssistant: Bool,
    backendStatus: HomeStatusRowState,
    glassesStatus: HomeStatusRowState
  ) -> (summary: String, detail: String) {
    switch runtimeStatus.assistantRuntimeState {
    case .inactive:
      if backendStatus.label != "Ready" {
        return ("Backend needs attention", backendStatus.detail)
      }

      if runtimeStatus.selectedRoute == .glasses && canActivateAssistant == false {
        return ("Glasses aren’t ready", glassesStatus.detail)
      }

      switch runtimeStatus.selectedRoute {
      case .phone:
        return (
          "Ready on your phone",
          "Your backend is ready. Activate the assistant to begin testing from your iPhone."
        )
      case .glasses:
        return (
          "Ready when you are",
          "Your backend and glasses are ready. Activate the assistant to begin."
        )
      }

    case .armedListening:
      return (
        "Listening for \"\(runtimeStatus.wakePhraseText)\"",
        "Say your wake phrase to start, or \"\(runtimeStatus.sleepPhraseText)\" to stop later."
      )

    case .connectingConversation:
      if runtimeStatus.selectedRoute == .phone {
        return (
          "Mario is joining",
          "Opening your live assistant session on your phone now."
        )
      }
      return (
        "Mario is joining",
        "Opening your live assistant session through your glasses now."
      )

    case .activeConversation:
      if runtimeStatus.selectedRoute == .phone {
        return (
          "Conversation active",
          "Mario is connected on your phone right now."
        )
      }
      return (
        "Conversation active",
        "Mario is connected through your glasses right now."
      )

    case .pausedByHardware:
      return (
        "Needs attention",
        "Your glasses session paused. Reconnect your glasses or deactivate the assistant."
      )

    case .deactivating:
      return (
        "Needs attention",
        "PortWorld is closing the current assistant session."
      )
    }
  }
}

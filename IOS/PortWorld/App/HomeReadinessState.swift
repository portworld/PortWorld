import Foundation
import MWDATCore

struct HomeStatusRowState {
  let title: String
  let label: String
  let detail: String
  let tone: PWStatusTone
  let systemImage: String
}

struct HomeReadinessState {
  enum RecoveryAction {
    case openBackendSetup
    case connectGlasses

    var title: String {
      switch self {
      case .openBackendSetup:
        return "Open Backend Setup"
      case .connectGlasses:
        return "Connect Glasses"
      }
    }
  }

  let assistantSummary: String
  let assistantDetail: String
  let canActivateAssistant: Bool
  let recoveryAction: RecoveryAction?
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

    let isBackendReady = settings.validationState == .valid
    let areGlassesReady = HomeReadinessState.areGlassesReady(
      wearablesRuntimeManager: wearablesRuntimeManager
    )
    self.canActivateAssistant = isBackendReady && areGlassesReady
    self.recoveryAction = HomeReadinessState.makeRecoveryAction(
      isBackendReady: isBackendReady,
      areGlassesReady: areGlassesReady
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
  static func makeRecoveryAction(
    isBackendReady: Bool,
    areGlassesReady: Bool
  ) -> RecoveryAction? {
    if isBackendReady == false {
      return .openBackendSetup
    }

    if areGlassesReady == false {
      return .connectGlasses
    }

    return nil
  }

  static func areGlassesReady(
    wearablesRuntimeManager: WearablesRuntimeManager
  ) -> Bool {
    guard wearablesRuntimeManager.configurationState == .ready else { return false }
    guard wearablesRuntimeManager.registrationState == .registered else { return false }
    guard wearablesRuntimeManager.devices.isEmpty == false else { return false }
    guard wearablesRuntimeManager.activeCompatibilityMessage == nil else { return false }
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
        systemImage: "network"
      )

    case .activeConversation:
      return HomeStatusRowState(
        title: "Backend",
        label: "Ready",
        detail: "Your backend session is active.",
        tone: .success,
        systemImage: "checkmark.circle"
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
        systemImage: "checkmark.circle"
      )

    case .invalid:
      return HomeStatusRowState(
        title: "Backend",
        label: "Needs attention",
        detail: "Backend validation failed. Check your URL or token.",
        tone: .error,
        systemImage: "exclamationmark.triangle"
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
        systemImage: "gearshape"
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
        systemImage: "exclamationmark.triangle"
      )
    }

    switch wearablesRuntimeManager.configurationState {
    case .idle, .configuring:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Connecting",
        detail: "Preparing Meta wearables support for the app.",
        tone: .neutral,
        systemImage: "gearshape"
      )

    case .failed:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Needs attention",
        detail: wearablesRuntimeManager.configurationErrorMessage ?? "Meta wearables support failed to initialize.",
        tone: .error,
        systemImage: "xmark.octagon"
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
        systemImage: "pause.circle"
      )
    }

    if wearablesRuntimeManager.registrationState != .registered {
      return HomeStatusRowState(
        title: "Glasses",
        label: "Not connected",
        detail: "Authorize PortWorld in the Meta app before starting the assistant.",
        tone: .warning,
        systemImage: "eyeglasses"
      )
    }

    if wearablesRuntimeManager.devices.isEmpty {
      return HomeStatusRowState(
        title: "Glasses",
        label: "Not connected",
        detail: "Bring your paired glasses nearby and reconnect.",
        tone: .warning,
        systemImage: "antenna.radiowaves.left.and.right"
      )
    }

    switch wearablesRuntimeManager.glassesSessionPhase {
    case .starting:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Connecting",
        detail: "Starting a live glasses session now.",
        tone: .neutral,
        systemImage: "dot.radiowaves.left.and.right"
      )

    case .running:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Ready",
        detail: "Your glasses session is active.",
        tone: .success,
        systemImage: "checkmark.circle"
      )

    case .failed:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Needs attention",
        detail: wearablesRuntimeManager.glassesSessionErrorMessage ?? "The glasses session could not start.",
        tone: .error,
        systemImage: "xmark.octagon"
      )

    case .paused:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Needs attention",
        detail: "Your glasses session is paused right now.",
        tone: .warning,
        systemImage: "pause.circle"
      )

    case .waitingForDevice:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Connecting",
        detail: "Waiting for your glasses to become available nearby.",
        tone: .warning,
        systemImage: "antenna.radiowaves.left.and.right"
      )

    case .inactive, .stopping:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Ready",
        detail: "Your glasses are connected and available.",
        tone: .success,
        systemImage: "checkmark.circle"
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
      if canActivateAssistant == false {
        if backendStatus.label != "Ready" {
          return ("Backend needs attention", backendStatus.detail)
        }

        return ("Glasses aren’t ready", glassesStatus.detail)
      }

      return (
        "Ready when you are",
        "Your backend and glasses are ready. Activate the assistant to begin."
      )

    case .armedListening:
      return (
        "Listening for \"\(runtimeStatus.wakePhraseText)\"",
        "Say your wake phrase to start, or \"\(runtimeStatus.sleepPhraseText)\" to stop later."
      )

    case .connectingConversation:
      return (
        "Mario is joining",
        "Opening your live assistant session now."
      )

    case .activeConversation:
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

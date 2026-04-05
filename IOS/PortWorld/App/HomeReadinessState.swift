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
    self.canActivateAssistant = HomeReadinessState.canActivateAssistant(
      runtimeStatus: runtimeStatus,
      backendStatus: backendStatus,
      glassesStatus: glassesStatus
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
  static func canActivateAssistant(
    runtimeStatus: AssistantRuntimeStatus,
    backendStatus: HomeStatusRowState,
    glassesStatus: HomeStatusRowState
  ) -> Bool {
    guard runtimeStatus.assistantRuntimeState != .deactivating else { return false }
    return isActivationReady(backendStatus) && isActivationReady(glassesStatus)
  }

  static func isActivationReady(_ status: HomeStatusRowState) -> Bool {
    status.tone == .success && status.action == nil
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
        detail: settings.backendReadinessDetail,
        tone: .success,
        systemImage: "checkmark.circle",
        action: nil
      )

    case .invalid:
      return HomeStatusRowState(
        title: "Backend",
        label: "Needs attention",
        detail: settings.backendReadinessDetail,
        tone: .error,
        systemImage: "exclamationmark.triangle",
        action: .openBackendSettings
      )

    case .unknown:
      return HomeStatusRowState(
        title: "Backend",
        label: "Needs setup",
        detail: settings.backendReadinessDetail,
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
    if let visionStatus = makeVisionStatusIfNeeded(
      runtimeStatus: runtimeStatus,
      wearablesRuntimeManager: wearablesRuntimeManager
    ) {
      return visionStatus
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

    if let activationBlocker = wearablesRuntimeManager.activationBlocker {
      return HomeStatusRowState(
        title: "Glasses",
        label: glassesStatusLabel(for: activationBlocker),
        detail: activationBlocker.message,
        tone: glassesStatusTone(for: activationBlocker),
        systemImage: glassesStatusSymbol(for: activationBlocker),
        action: .openGlassesSettings
      )
    }

    return HomeStatusRowState(
      title: "Glasses",
      label: "Ready",
      detail: "Your glasses are connected and available.",
      tone: .success,
      systemImage: "checkmark.circle",
      action: nil
    )
  }

  static func makeVisionStatusIfNeeded(
    runtimeStatus: AssistantRuntimeStatus,
    wearablesRuntimeManager: WearablesRuntimeManager
  ) -> HomeStatusRowState? {
    guard runtimeStatus.assistantRuntimeState == .activeConversation ||
      wearablesRuntimeManager.isVisionCaptureRequested else {
      return nil
    }

    switch wearablesRuntimeManager.visionStreamPhase {
    case .requestingPermission, .starting:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Connecting",
        detail: "Starting the DAT camera stream from your glasses.",
        tone: .neutral,
        systemImage: "camera.viewfinder",
        action: nil
      )

    case .waitingForDevice:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Connecting",
        detail: "Waiting for the glasses camera stream to become available.",
        tone: .warning,
        systemImage: "antenna.radiowaves.left.and.right",
        action: .openGlassesSettings
      )

    case .capturing:
      if wearablesRuntimeManager.visionUploadFailureCount > 0,
        !wearablesRuntimeManager.visionLastErrorText.isEmpty {
        return HomeStatusRowState(
          title: "Glasses",
          label: "Needs attention",
          detail: "Vision stream is live, but backend upload is failing: \(wearablesRuntimeManager.visionLastErrorText)",
          tone: .warning,
          systemImage: "exclamationmark.triangle",
          action: .openGlassesSettings
        )
      }

      let detail: String
      if wearablesRuntimeManager.visionUploadCount > 0 {
        detail = "Your glasses camera stream is live and frames are reaching the backend."
      } else {
        detail = "Your glasses camera stream is live. Waiting for the first backend vision upload."
      }

      return HomeStatusRowState(
        title: "Glasses",
        label: "Ready",
        detail: detail,
        tone: .success,
        systemImage: "checkmark.circle",
        action: nil
      )

    case .paused:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Needs attention",
        detail: "The glasses camera stream is paused right now.",
        tone: .warning,
        systemImage: "pause.circle",
        action: .openGlassesSettings
      )

    case .failed:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Needs attention",
        detail: wearablesRuntimeManager.visionLastErrorText.isEmpty
          ? "The DAT camera stream failed."
          : wearablesRuntimeManager.visionLastErrorText,
        tone: .error,
        systemImage: "xmark.octagon",
        action: .openGlassesSettings
      )

    case .inactive, .stopping:
      return HomeStatusRowState(
        title: "Glasses",
        label: "Connecting",
        detail: "Preparing the glasses camera stream now.",
        tone: .neutral,
        systemImage: "camera.viewfinder",
        action: nil
      )
    }
  }

  static func glassesStatusLabel(
    for blocker: WearablesRuntimeManager.ActivationBlocker
  ) -> String {
    switch blocker {
    case .initializing:
      return "Connecting"
    case .registrationRequired, .glassesNotDiscovered:
      return "Not connected"
    case .hfpAudioUnavailable:
      return "Audio unavailable"
    case .configurationFailed, .cameraPermissionFailed, .sessionFailed, .compatibilityIssue:
      return "Needs attention"
    case .cameraPermissionRequired:
      return "Needs permission"
    }
  }

  static func glassesStatusTone(
    for blocker: WearablesRuntimeManager.ActivationBlocker
  ) -> PWStatusTone {
    switch blocker {
    case .initializing:
      return .neutral
    case .configurationFailed, .cameraPermissionFailed, .sessionFailed:
      return .error
    case .registrationRequired, .cameraPermissionRequired, .glassesNotDiscovered, .compatibilityIssue, .hfpAudioUnavailable:
      return .warning
    }
  }

  static func glassesStatusSymbol(
    for blocker: WearablesRuntimeManager.ActivationBlocker
  ) -> String {
    switch blocker {
    case .initializing:
      return "gearshape"
    case .registrationRequired:
      return "eyeglasses"
    case .cameraPermissionRequired:
      return "camera"
    case .cameraPermissionFailed, .configurationFailed, .sessionFailed:
      return "xmark.octagon"
    case .glassesNotDiscovered:
      return "antenna.radiowaves.left.and.right"
    case .compatibilityIssue:
      return "exclamationmark.triangle"
    case .hfpAudioUnavailable:
      return "waveform.badge.exclamationmark"
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
      if isActivationReady(backendStatus) == false {
        return ("Backend needs attention", backendStatus.detail)
      }

      if canActivateAssistant == false {
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
        "Opening your live assistant session through your glasses now."
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

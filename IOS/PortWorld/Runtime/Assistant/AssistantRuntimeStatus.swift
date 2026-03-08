// Shared UI-facing status model for the assistant runtime across phone and glasses routes.
import Foundation

enum AssistantRoute: String {
  case phone
  case glasses
}

enum GlassesReadinessKind {
  case neutral
  case success
  case warning
  case error
}

struct AssistantRuntimeStatus {
  var assistantRuntimeState: AssistantRuntimeState = .inactive
  var selectedRoute: AssistantRoute = .phone
  var activeRouteText: String = "none"
  var glassesReadinessTitle: String = "Glasses setup required"
  var glassesReadinessDetail: String = "Open Glasses Setup to connect Meta glasses and review DAT readiness."
  var glassesReadinessKind: GlassesReadinessKind = .neutral
  var glassesSessionText: String = "inactive"
  var activeGlassesDeviceText: String = "-"
  var glassesAudioModeText: String = "inactive"
  var hfpRouteText: String = "not_ready"
  var glassesAudioDetailText: String = "No glasses audio path is active."
  var mockWorkflowText: String = "disabled"
  var glassesDevelopmentDetailText: String = "Complete DAT setup before validating the glasses runtime."
  var canChangeRoute: Bool = true
  var canActivateSelectedRoute: Bool = true
  var activationButtonTitle: String = "Activate Assistant"
  var audioModeText: String = "inactive"
  var audioStatusText: String = "idle"
  var backendStatusText: String = "idle"
  var wakeStatusText: String = "idle"
  var wakePhraseText: String = ""
  var sleepPhraseText: String = ""
  var sessionID: String = "-"
  var transportStatusText: String = "disconnected"
  var uplinkStatusText: String = "idle"
  var playbackStatusText: String = "idle"
  var playbackRouteText: String = "-"
  var visionCaptureStateText: String = "inactive"
  var visionUploadCount: Int = 0
  var visionUploadFailureCount: Int = 0
  var visionLastErrorText: String = ""
  var debugPhoneVisionModeText: String = "disabled"
  var debugPhoneVisionDetailText: String = ""
  var debugPhoneVisionToggleTitle: String = "Enable Phone Camera Vision Test"
  var canToggleDebugPhoneVision: Bool = false
  var infoText: String = ""
  var errorText: String = ""

  var canActivate: Bool {
    assistantRuntimeState == .inactive
  }

  var canDeactivate: Bool {
    switch assistantRuntimeState {
    case .armedListening, .connectingConversation, .activeConversation, .pausedByHardware:
      return true
    case .inactive, .deactivating:
      return false
    }
  }

  var canEndConversation: Bool {
    switch assistantRuntimeState {
    case .connectingConversation, .activeConversation:
      return true
    case .inactive, .armedListening, .pausedByHardware, .deactivating:
      return false
    }
  }
}

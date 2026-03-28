// Shared UI-facing status model for the glasses-first assistant runtime.
import Foundation

struct AssistantRuntimeStatus {
  var assistantRuntimeState: AssistantRuntimeState = .inactive
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
}

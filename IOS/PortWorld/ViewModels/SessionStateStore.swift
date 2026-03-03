import Observation
import SwiftUI

enum StreamingStatus {
  case streaming
  case waiting
  case stopped
}

enum AssistantRuntimeState {
  case inactive
  case activating
  case active
  case deactivating
  case failed
}

@MainActor
@Observable
final class SessionStateStore {
  var currentVideoFrame: UIImage?
  var hasReceivedFirstFrame: Bool = false
  var streamingStatus: StreamingStatus = .stopped
  var showError: Bool = false
  var errorMessage: String = ""
  var hasActiveDevice: Bool = false

  var assistantRuntimeState: AssistantRuntimeState = .inactive
  var runtimeSessionStateText: String = "inactive"
  var runtimeWakeStateText: String = "idle"
  var runtimeQueryStateText: String = "idle"
  var runtimePhotoStateText: String = "idle"
  var runtimePlaybackStateText: String = "idle"
  var runtimeWakeEngineText: String = "manual"
  var runtimeWakeRuntimeText: String = "idle"
  var runtimeSpeechAuthorizationText: String = "not_required"
  var runtimeManualWakeFallbackText: String = "enabled"
  var runtimeBackendText: String = "-"
  var runtimeErrorText: String = ""
  var runtimeSessionIdText: String = "-"
  var runtimeQueryIdText: String = "-"
  var runtimeWakeCount: Int = 0
  var runtimeQueryCount: Int = 0
  var runtimeVideoFrameCount: Int = 0
  var runtimePhotoUploadCount: Int = 0
  var runtimePlaybackChunkCount: Int = 0
  var runtimePendingPlaybackBufferCount: Int = 0

  var audioStateText: String = "idle"
  var audioStatsText: String = "Chunks: 0  Bytes: 0"
  var isAudioReady: Bool = false
  var isAudioRecording: Bool = false
  var audioSessionPath: String = "No audio session directory"
  var audioLastError: String = ""
  var audioChunkCount: Int = 0
  var audioByteCount: Int64 = 0

  var capturedPhoto: UIImage?
  var showPhotoPreview: Bool = false

  var isStreaming: Bool {
    switch assistantRuntimeState {
    case .activating, .active, .deactivating:
      return true
    case .inactive, .failed:
      return false
    }
  }

  var canActivateAssistantRuntime: Bool {
    hasActiveDevice && (assistantRuntimeState == .inactive || assistantRuntimeState == .failed)
  }

  var canDeactivateAssistantRuntime: Bool {
    switch assistantRuntimeState {
    case .activating, .active, .failed:
      return true
    case .inactive, .deactivating:
      return false
    }
  }
}

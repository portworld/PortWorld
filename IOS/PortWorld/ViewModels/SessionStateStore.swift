import Observation
import SwiftUI

enum StreamingStatus {
  case streaming
  case waiting
  case stopped
}

enum InternetReachabilityState {
  case unknown
  case connected
  case disconnected
}

@MainActor
@Observable
final class SessionStateStore {
  private static let firstFrameTimestampFormatter: DateFormatter = {
    let formatter = DateFormatter()
    formatter.dateStyle = .none
    formatter.timeStyle = .medium
    return formatter
  }()

  var currentVideoFrame: UIImage?
  var hasReceivedFirstFrame: Bool = false
  var firstFrameWaitStatusText: String = "idle"
  var firstFrameWaitTimestampText: String = "-"
  private var firstFrameWaitUpdatedAt: Date?
  var streamingStatus: StreamingStatus = .stopped
  var showError: Bool = false
  var errorMessage: String = ""
  var hasActiveDevice: Bool = false

  var assistantRuntimeState: AssistantRuntimeState = .inactive
  var runtimeSessionStateText: String = "inactive" {
    didSet {
      updateRealtimePresentationState()
    }
  }
  var runtimeWakeStateText: String = "idle"
  var runtimeQueryStateText: String = "idle"
  var runtimePhotoStateText: String = "idle"
  var runtimePlaybackStateText: String = "idle" {
    didSet {
      updateRealtimePresentationState()
    }
  }
  var internetReachabilityState: InternetReachabilityState = .unknown {
    didSet {
      updateRealtimePresentationState()
    }
  }
  var isInternetReachable: Bool {
    get { internetReachabilityState != .disconnected }
    set { internetReachabilityState = newValue ? .connected : .disconnected }
  }
  var transportStatusText: String = "Disconnected"
  var streamDurationSeconds: Int = 0
  var runtimeWakeEngineText: String = "manual"
  var runtimeWakeRuntimeText: String = "idle"
  var runtimeSpeechAuthorizationText: String = "not_required"
  var runtimeManualWakeFallbackText: String = "enabled"
  var runtimeBackendText: String = "-"
  var runtimeErrorText: String = ""
  var runtimeInfoText: String = ""
  var runtimeWakePhraseText: String = ""
  var runtimeSleepPhraseText: String = ""
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
  private var streamStartedAt: Date?

  var shouldPresentStreamView: Bool {
    assistantRuntimeState != .inactive
  }

  var isStreaming: Bool {
    switch assistantRuntimeState {
    case .armedListening, .connectingConversation, .activeConversation, .deactivating:
      return true
    case .inactive:
      return false
    }
  }

  func markWaitingForFirstFrame(now: Date = Date()) {
    guard !hasReceivedFirstFrame else { return }
    guard firstFrameWaitStatusText != "waiting_for_first_frame" else { return }
    updateFirstFrameDiagnostics(status: "waiting_for_first_frame", at: now)
  }

  func markFirstFrameReceived(now: Date = Date()) {
    hasReceivedFirstFrame = true
    updateFirstFrameDiagnostics(status: "first_frame_received", at: now)
  }

  func resetFirstFrameState(status: String, now: Date = Date()) {
    guard hasReceivedFirstFrame || currentVideoFrame != nil || firstFrameWaitStatusText != status else { return }
    currentVideoFrame = nil
    hasReceivedFirstFrame = false
    updateFirstFrameDiagnostics(status: status, at: now)
  }

  var canActivateAssistantRuntime: Bool {
    hasActiveDevice && assistantRuntimeState == .inactive
  }

  var canDeactivateAssistantRuntime: Bool {
    switch assistantRuntimeState {
    case .armedListening, .connectingConversation, .activeConversation:
      return true
    case .inactive, .deactivating:
      return false
    }
  }

  private func updateRealtimePresentationState(now: Date = Date()) {
    let sessionState = runtimeSessionStateText.lowercased()
    let playbackState = runtimePlaybackStateText.lowercased()
    let isTransportConnecting =
      sessionState == "connecting" ||
      sessionState == "activating" ||
      sessionState == "waiting" ||
      playbackState.contains("streaming_connecting")
    let isTransportReconnecting =
      sessionState == "reconnecting" ||
      playbackState.contains("streaming_reconnecting")
    let isTransportReady =
      playbackState == "streaming_ready" ||
      playbackState.contains("streaming.ready") ||
      playbackState.contains("streaming.active")

    if internetReachabilityState == .unknown {
      transportStatusText = "Checking internet"
    } else if !isInternetReachable {
      transportStatusText = "No internet"
    } else if sessionState == "failed" {
      transportStatusText = "Connection failed"
    } else if sessionState == "disconnecting" {
      transportStatusText = "Disconnecting"
    } else if isTransportReconnecting {
      transportStatusText = "Reconnecting"
    } else if isTransportConnecting {
      transportStatusText = "Connecting"
    } else if sessionState == "active" {
      transportStatusText = "Session active | Waiting for transport"
    } else if sessionState == "streaming" {
      if playbackState == "playing" {
        transportStatusText = "Connected | Playing response"
      } else if playbackState.contains("buffer") || playbackState.contains("thinking") {
        transportStatusText = "Connected | Waiting for response"
      } else if playbackState.contains("waiting_ready") || !isTransportReady {
        transportStatusText = "Connected | Waiting for session ready"
      } else {
        transportStatusText = "Connected | Ready"
      }
    } else {
      transportStatusText = "Disconnected"
    }

    if sessionState == "active" || sessionState == "streaming" {
      if streamStartedAt == nil {
        streamStartedAt = now
      }
      if let streamStartedAt {
        streamDurationSeconds = max(0, Int(now.timeIntervalSince(streamStartedAt)))
      } else {
        streamDurationSeconds = 0
      }
    } else {
      streamStartedAt = nil
      streamDurationSeconds = 0
    }
  }

  private func updateFirstFrameDiagnostics(status: String, at timestamp: Date) {
    firstFrameWaitStatusText = status
    firstFrameWaitUpdatedAt = timestamp
    firstFrameWaitTimestampText = Self.firstFrameTimestampFormatter.string(from: timestamp)
  }
}

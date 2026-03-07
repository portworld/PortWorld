// Main-actor coordinator that owns phone-only assistant runtime state and service bindings.
import Foundation

@MainActor
final class AssistantRuntimeController {
  struct PendingRealtimeFrame {
    let payload: Data
    let timestampMs: Int64
  }

  let config: PhoneOnlyRuntimeConfig
  let phoneAudioIO: PhoneAudioIO
  let backendSessionClient: BackendSessionClient
  let wakePhraseDetector: WakePhraseDetector

  var wakeWarmupTask: Task<Void, Never>?
  var wakeListeningGeneration: Int = 0
  var activeSessionID: String?
  var backendReady = false
  var firstUplinkAckReceived = false
  var hasLoggedUplinkDuringPlayback = false
  var awaitingFirstWakePCMFrame = false
  var activeConversationStartedAtMs: Int64?
  var isResettingConversationToArmedState = false
  var isLocallyInterruptingAssistantPlayback = false
  var consecutiveLocalBargeInFrames = 0
  var pendingRealtimeFrames: [PendingRealtimeFrame] = []
  let maxPendingRealtimeFrames = 24
  let localBargeInRMSFloor: Double = 0.012
  let localBargeInFrameThreshold = 3

  var status: PhoneAssistantRuntimeStatus
  var onStatusUpdated: ((PhoneAssistantRuntimeStatus) -> Void)?

  init(
    config: PhoneOnlyRuntimeConfig,
    phoneAudioIO: PhoneAudioIO? = nil,
    backendSessionClient: BackendSessionClient? = nil,
    wakePhraseDetector: WakePhraseDetector? = nil
  ) {
    self.config = config
    self.phoneAudioIO = phoneAudioIO ?? PhoneAudioIO()
    self.backendSessionClient = backendSessionClient ?? BackendSessionClient(
      webSocketURL: config.webSocketURL,
      requestHeaders: config.requestHeaders
    )
    self.wakePhraseDetector = wakePhraseDetector ?? WakePhraseDetector(config: config)
    self.status = PhoneAssistantRuntimeStatus(
      wakePhraseText: config.wakePhrase,
      sleepPhraseText: config.sleepPhrase,
      infoText: "Phone-only assistant ready."
    )

    bindPhoneAudio()
    bindWakePhraseDetector()
    bindBackendEvents()
  }

  deinit {
    wakeWarmupTask?.cancel()
    let backendSessionClient = self.backendSessionClient
    Task {
      await backendSessionClient.setEventHandler(nil)
    }
  }

  func bindPhoneAudio() {
    phoneAudioIO.onWakePCMFrame = { [weak self] frame in
      guard let self else { return }
      if self.awaitingFirstWakePCMFrame, self.status.assistantRuntimeState == .armedListening {
        self.awaitingFirstWakePCMFrame = false
        self.status.infoText = "Say \"\(self.config.wakePhrase)\" to start a conversation."
        self.debugLog("Received first wake PCM frame after arming")
        self.publishStatus()
      }
      self.wakePhraseDetector.processPCMFrame(frame)
    }
    phoneAudioIO.onRealtimePCMFrame = { [weak self] payload, timestampMs in
      Task { @MainActor [weak self] in
        await self?.handleRealtimePCMFrame(payload, timestampMs: timestampMs)
      }
    }
  }

  func bindWakePhraseDetector() {
    wakePhraseDetector.onWakeDetected = { [weak self] event in
      Task { @MainActor [weak self] in
        await self?.startConversation(from: event)
      }
    }
    wakePhraseDetector.onSleepDetected = { [weak self] event in
      Task { @MainActor [weak self] in
        await self?.handleSleepDetected(event)
      }
    }
    wakePhraseDetector.onError = { [weak self] message in
      self?.status.errorText = message
      self?.publishStatus()
    }
  }

  func refreshSubsystemStatus() async {
    let wakeStatus = wakePhraseDetector.statusSnapshot()
    let diagnostics = await backendSessionClient.diagnosticsSnapshot()
    status.audioStatusText = phoneAudioIO.stateDescription()
    status.backendStatusText = await backendSessionClient.connectionStateText()
    status.wakeStatusText = wakeStatus.runtime
    status.playbackRouteText = phoneAudioIO.playbackRouteDescription()
    if status.assistantRuntimeState == .inactive {
      status.playbackStatusText = "idle"
    } else if status.playbackStatusText == "idle" {
      let inboundFrames = diagnostics.inboundServerAudioFrameCount
      if inboundFrames > 0 {
        status.playbackStatusText = "received frames=\(inboundFrames) bytes=\(diagnostics.inboundServerAudioBytes)"
      } else if diagnostics.lastPlaybackControlCommand != "none" {
        status.playbackStatusText = diagnostics.lastPlaybackControlCommand
      }
    }
    if !firstUplinkAckReceived && (status.transportStatusText == "ready" || status.transportStatusText == "connected") {
      status.uplinkStatusText = "binary_completed=\(diagnostics.binarySendSuccessCount)"
    }
  }

  func publishStatus() {
    onStatusUpdated?(status)
  }

  func debugLog(_ message: String) {
    #if DEBUG
      print("[AssistantRuntimeController] \(message)")
    #endif
  }
}

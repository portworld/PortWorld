// Lifecycle operations for activation, deactivation, and scene-phase handling.
import SwiftUI

extension AssistantRuntimeController {
  func activate() async {
    guard status.assistantRuntimeState == .inactive else { return }
    status.errorText = ""
    status.infoText = "Preparing glasses audio routing and wake detection."
    publishStatus()

    let authorization = await wakePhraseDetector.requestAuthorizationIfNeeded()
    if authorization != .authorized && authorization != .notRequired {
      status.assistantRuntimeState = .inactive
      status.errorText = "Wake phrase authorization unavailable: \(authorization.rawValue)"
      status.infoText = ""
      await refreshSubsystemStatus()
      publishStatus()
      return
    }

    do {
      try await activeAudioIO.prepareForArmedListening()
    } catch {
      status.assistantRuntimeState = .inactive
      status.errorText = error.localizedDescription
      status.infoText = ""
      await refreshSubsystemStatus()
      publishStatus()
      return
    }

    backendReady = false
    firstUplinkAckReceived = false
    hasLoggedUplinkDuringPlayback = false
    awaitingFirstWakePCMFrame = false
    activeConversationStartedAtMs = nil
    wakeListeningGeneration += 1
    status.assistantRuntimeState = .armedListening
    status.transportStatusText = "idle"
    status.uplinkStatusText = "armed_waiting_for_wake"
    status.playbackStatusText = "armed_waiting_for_response"
    status.infoText = "Warming up wake detection."
    await refreshSubsystemStatus()
    publishStatus()
    scheduleWakeListeningStart(generation: wakeListeningGeneration)
  }

  func deactivate() async {
    guard status.assistantRuntimeState != .inactive else { return }
    let previousState = status.assistantRuntimeState
    let previousSessionID = activeSessionID ?? "-"
    debugLog("Deactivate requested from state=\(previousState.rawValue) session=\(previousSessionID)")
    status.assistantRuntimeState = .deactivating
    status.infoText = "Stopping assistant runtime."
    publishStatus()

    debugLog("Stopping wake recognizer and cancelling warmup tasks")
    wakePhraseDetector.stop()
    wakeWarmupTask?.cancel()
    wakeWarmupTask = nil
    wakeListeningGeneration += 1
    debugLog("Disconnecting backend session session=\(previousSessionID)")
    await backendSessionClient.disconnect()
    debugLog("Stopping active audio I/O")
    await activeAudioIO.stop()

    activeSessionID = nil
    backendReady = false
    firstUplinkAckReceived = false
    hasLoggedUplinkDuringPlayback = false
    awaitingFirstWakePCMFrame = false
    activeConversationStartedAtMs = nil
    isResettingConversationToArmedState = false
    conversationMode = .wakeTriggered
    status.assistantRuntimeState = .inactive
    status.sessionID = "-"
    status.transportStatusText = "disconnected"
    status.uplinkStatusText = "idle"
    status.playbackStatusText = "idle"
    status.infoText = "Assistant inactive."
    await refreshSubsystemStatus()
    publishStatus()
    debugLog("Deactivate completed; runtime state=\(status.assistantRuntimeState.rawValue)")
  }

  func handleScenePhaseChange(_ phase: ScenePhase) {
    switch phase {
    case .background:
      guard status.assistantRuntimeState != .inactive else { return }
      activeAudioIO.prepareForBackground()
      if status.assistantRuntimeState == .activeConversation {
        status.infoText = "Active conversation continues while app is backgrounded if audio session remains available."
        status.playbackRouteText = activeAudioIO.playbackRouteDescription()
        publishStatus()
      }
    case .active:
      guard status.assistantRuntimeState != .inactive else { return }
      activeAudioIO.restoreFromForeground()
      status.playbackRouteText = activeAudioIO.playbackRouteDescription()
      publishStatus()
    case .inactive:
      break
    @unknown default:
      break
    }
  }

  func suspendForExternalRoutePause() async {
    switch status.assistantRuntimeState {
    case .inactive, .pausedByHardware, .deactivating:
      return
    case .armedListening, .connectingConversation, .activeConversation:
      await transitionToPausedByHardware(infoText: "Glasses session paused. Waiting for resume.")
    }
  }

  func resumeFromExternalRoutePause() async {
    guard status.assistantRuntimeState == .pausedByHardware else { return }

    backendReady = false
    firstUplinkAckReceived = false
    hasLoggedUplinkDuringPlayback = false
    awaitingFirstWakePCMFrame = false
    activeSessionID = nil
    activeConversationStartedAtMs = nil
    pendingRealtimeFrames.removeAll(keepingCapacity: false)
    wakeListeningGeneration += 1
    status.assistantRuntimeState = .armedListening
    status.sessionID = "-"
    status.transportStatusText = "idle"
    status.uplinkStatusText = "armed_waiting_for_wake"
    status.playbackStatusText = "armed_waiting_for_response"
    status.infoText = "Glasses session running. Returning to armed listening."
    await refreshSubsystemStatus()
    publishStatus()
    scheduleWakeListeningStart(generation: wakeListeningGeneration)
  }

  func transitionToPausedByHardware(infoText: String) async {
    wakePhraseDetector.stop()
    wakeWarmupTask?.cancel()
    wakeWarmupTask = nil
    wakeListeningGeneration += 1
    awaitingFirstWakePCMFrame = false
    activeSessionID = nil
    backendReady = false
    firstUplinkAckReceived = false
    hasLoggedUplinkDuringPlayback = false
    activeConversationStartedAtMs = nil
    pendingRealtimeFrames.removeAll(keepingCapacity: false)
    activeAudioIO.cancelPlayback()
    await backendSessionClient.disconnect(sendDeactivate: false)
    status.assistantRuntimeState = .pausedByHardware
    status.sessionID = "-"
    status.transportStatusText = "paused"
    status.uplinkStatusText = "paused_by_hardware"
    status.playbackStatusText = "paused"
    status.infoText = infoText
    await refreshSubsystemStatus()
    publishStatus()
  }

  func scheduleWakeListeningStart(generation: Int, readyMessage: String? = nil) {
    wakeWarmupTask?.cancel()
    wakeWarmupTask = Task { @MainActor [weak self] in
      guard let self else { return }
      guard wakeListeningGeneration == generation, status.assistantRuntimeState == .armedListening else { return }
      if wakePhraseDetector.isListening == false {
        awaitingFirstWakePCMFrame = true
        status.infoText = "Starting wake detection."
        publishStatus()
        debugLog("Starting wake recognizer for generation \(generation)")
        wakePhraseDetector.startArmedListening()
        status.infoText = readyMessage ?? "Listening for microphone frames."
      } else {
        awaitingFirstWakePCMFrame = false
        status.infoText = readyMessage ?? "Say \"\(config.wakePhrase)\" to start a conversation."
      }
      await refreshSubsystemStatus()
      publishStatus()
    }
  }
}

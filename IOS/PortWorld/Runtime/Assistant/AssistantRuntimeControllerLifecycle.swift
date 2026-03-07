// Lifecycle operations for activation, deactivation, and scene-phase handling.
import SwiftUI

extension AssistantRuntimeController {
  func activate() async {
    guard status.assistantRuntimeState == .inactive else { return }
    status.errorText = ""
    status.infoText = "Preparing phone microphone, speaker playback, and wake detection."
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
      try await phoneAudioIO.prepareForArmedListening()
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
    status.infoText = "Stopping phone-only assistant."
    publishStatus()

    debugLog("Stopping wake recognizer and cancelling warmup tasks")
    wakePhraseDetector.stop()
    wakeWarmupTask?.cancel()
    wakeWarmupTask = nil
    wakeListeningGeneration += 1
    debugLog("Disconnecting backend session session=\(previousSessionID)")
    await backendSessionClient.disconnect()
    debugLog("Stopping phone audio I/O")
    await phoneAudioIO.stop()

    activeSessionID = nil
    backendReady = false
    firstUplinkAckReceived = false
    hasLoggedUplinkDuringPlayback = false
    awaitingFirstWakePCMFrame = false
    activeConversationStartedAtMs = nil
    isResettingConversationToArmedState = false
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
      phoneAudioIO.prepareForBackground()
      if status.assistantRuntimeState == .activeConversation {
        status.infoText = "Active conversation continues while app is backgrounded if audio session remains available."
        status.playbackRouteText = phoneAudioIO.playbackRouteDescription()
        publishStatus()
      }
    case .active:
      guard status.assistantRuntimeState != .inactive else { return }
      phoneAudioIO.restoreFromForeground()
      status.playbackRouteText = phoneAudioIO.playbackRouteDescription()
      publishStatus()
    case .inactive:
      break
    @unknown default:
      break
    }
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

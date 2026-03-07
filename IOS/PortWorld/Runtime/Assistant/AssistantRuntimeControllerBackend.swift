// Backend event binding and status updates for the phone-only assistant controller.
import Foundation

extension AssistantRuntimeController {
  func bindBackendEvents() {
    debugLog("Binding backend event handler")
    Task { [weak self] in
      await self?.backendSessionClient.setEventHandler { [weak self] envelope in
        Task { @MainActor [weak self] in
          guard let self else { return }
          await self.handleBackendEvent(envelope)
        }
      }
    }
  }

  func handleBackendEvent(_ envelope: BackendSessionClient.EventEnvelope) async {
    switch envelope.event {
    case .stateChanged(let state):
      status.backendStatusText = state.rawValue
      status.transportStatusText = state.rawValue

    case .sessionReady:
      debugLog("Received backend session.state active event#\(envelope.id)")
      status.transportStatusText = "ready"
      markConversationReady(source: "session_state_active")

    case .uplinkAcknowledged(let payload):
      firstUplinkAckReceived = true
      status.uplinkStatusText = "ack frames=\(payload.framesReceived) bytes=\(payload.bytesReceived)"

    case .serverAudio(let data):
      if isLocallyInterruptingAssistantPlayback {
        break
      }
      do {
        try phoneAudioIO.appendAssistantPCMData(data)
        let diagnostics = await backendSessionClient.diagnosticsSnapshot()
        status.playbackStatusText = "scheduled frames=\(diagnostics.inboundServerAudioFrameCount) bytes=\(diagnostics.inboundServerAudioBytes)"
        status.playbackRouteText = phoneAudioIO.playbackRouteDescription()
      } catch {
        status.playbackStatusText = "playback_failed"
        status.errorText = "Failed to play assistant audio: \(error.localizedDescription)"
      }

    case .playbackControl(let payload):
      debugLog("Received playback control event#\(envelope.id) command=\(payload.command.rawValue)")
      status.playbackStatusText = payload.command.rawValue
      if payload.command == .cancelResponse {
        isLocallyInterruptingAssistantPlayback = false
        consecutiveLocalBargeInFrames = 0
        status.infoText = "Assistant interrupted. Listening to user speech."
        status.uplinkStatusText = "streaming_during_playback"
        debugLog("Assistant playback canceled by backend; continuing live uplink")
      } else if payload.command == .startResponse {
        isLocallyInterruptingAssistantPlayback = false
        consecutiveLocalBargeInFrames = 0
      }
      phoneAudioIO.handlePlaybackControl(payload)

    case .closed:
      if isResettingConversationToArmedState {
        break
      }
      if status.assistantRuntimeState == .activeConversation || status.assistantRuntimeState == .connectingConversation {
        await resetConversationToArmedState(reason: "Connection closed. Listening for wake phrase again.")
      }

    case .error(let message):
      if isResettingConversationToArmedState, isExpectedDisconnectError(message) {
        debugLog("Ignoring expected backend disconnect error during reset: \(message)")
        break
      }
      if isExpectedInterruptRaceError(message) {
        debugLog("Ignoring expected interrupt race backend error: \(message)")
        status.infoText = "Assistant interrupted. Listening to user speech."
        break
      }
      status.errorText = message
      if status.assistantRuntimeState == .connectingConversation || status.assistantRuntimeState == .activeConversation {
        await resetConversationToArmedState(reason: "Conversation failed. Listening for wake phrase again.")
      }
    }

    await refreshSubsystemStatus()
    publishStatus()
  }

  func isExpectedDisconnectError(_ message: String) -> Bool {
    let normalized = message.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    return normalized.contains("socket is not connected")
  }

  func isExpectedInterruptRaceError(_ message: String) -> Bool {
    let normalized = message.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    return normalized.contains("cancellation failed") && normalized.contains("no active response found")
  }

  func describeBackendEvent(_ event: BackendSessionClient.Event) -> String {
    switch event {
    case .stateChanged(let state):
      return "state_changed=\(state.rawValue)"
    case .sessionReady:
      return "session_ready"
    case .uplinkAcknowledged(let payload):
      return "uplink_ack frames=\(payload.framesReceived) bytes=\(payload.bytesReceived)"
    case .serverAudio(let data):
      return "server_audio bytes=\(data.count)"
    case .playbackControl(let payload):
      return "playback_control command=\(payload.command.rawValue)"
    case .closed:
      return "closed"
    case .error(let message):
      return "error=\(message)"
    }
  }
}

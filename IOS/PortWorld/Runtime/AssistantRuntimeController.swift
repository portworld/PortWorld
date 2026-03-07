import Foundation
import SwiftUI

@MainActor
final class AssistantRuntimeController {
  private struct PendingRealtimeFrame {
    let payload: Data
    let timestampMs: Int64
  }

  struct StatusSnapshot {
    var assistantRuntimeState: AssistantRuntimeState = .inactive
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
  }

  private let config: RuntimeConfig
  private let phoneAudioIO: PhoneAudioIO
  private let backendSessionClient: BackendSessionClient
  private let wakePhraseDetector: WakePhraseDetector

  private var wakeWarmupTask: Task<Void, Never>?
  private var wakeListeningGeneration: Int = 0
  private var activeSessionID: String?
  private var backendReady = false
  private var firstUplinkAckReceived = false
  private var isSuppressingRealtimeUplinkForPlayback = false
  private var awaitingFirstWakePCMFrame = false
  private var activeConversationStartedAtMs: Int64?
  private var isResettingConversationToArmedState = false
  private var pendingRealtimeFrames: [PendingRealtimeFrame] = []
  private let maxPendingRealtimeFrames = 24

  private(set) var snapshot: StatusSnapshot
  var onStatusUpdated: ((StatusSnapshot) -> Void)?

  init(
    config: RuntimeConfig,
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
    self.snapshot = StatusSnapshot(
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

  func activate() async {
    guard snapshot.assistantRuntimeState == .inactive else { return }
    snapshot.errorText = ""
    snapshot.infoText = "Preparing phone microphone, speaker playback, and wake detection."
    publishSnapshot()

    let authorization = await wakePhraseDetector.requestAuthorizationIfNeeded()
    if authorization != .authorized && authorization != .notRequired {
      snapshot.assistantRuntimeState = .inactive
      snapshot.errorText = "Wake phrase authorization unavailable: \(authorization.rawValue)"
      snapshot.infoText = ""
      await refreshSubsystemStatus()
      publishSnapshot()
      return
    }

    do {
      try await phoneAudioIO.prepareForArmedListening()
    } catch {
      snapshot.assistantRuntimeState = .inactive
      snapshot.errorText = error.localizedDescription
      snapshot.infoText = ""
      await refreshSubsystemStatus()
      publishSnapshot()
      return
    }

    backendReady = false
    firstUplinkAckReceived = false
    isSuppressingRealtimeUplinkForPlayback = false
    awaitingFirstWakePCMFrame = false
    activeConversationStartedAtMs = nil
    wakeListeningGeneration += 1
    snapshot.assistantRuntimeState = .armedListening
    snapshot.transportStatusText = "idle"
    snapshot.uplinkStatusText = "armed_waiting_for_wake"
    snapshot.playbackStatusText = "armed_waiting_for_response"
    snapshot.infoText = "Warming up wake detection."
    await refreshSubsystemStatus()
    publishSnapshot()
    scheduleWakeListeningStart(generation: wakeListeningGeneration)
  }

  func deactivate() async {
    guard snapshot.assistantRuntimeState != .inactive else { return }
    snapshot.assistantRuntimeState = .deactivating
    snapshot.infoText = "Stopping phone-only assistant."
    publishSnapshot()

    wakePhraseDetector.stop()
    wakeWarmupTask?.cancel()
    wakeWarmupTask = nil
    wakeListeningGeneration += 1
    await backendSessionClient.disconnect()
    await phoneAudioIO.stop()

    activeSessionID = nil
    backendReady = false
    firstUplinkAckReceived = false
    isSuppressingRealtimeUplinkForPlayback = false
    awaitingFirstWakePCMFrame = false
    activeConversationStartedAtMs = nil
    isResettingConversationToArmedState = false
    snapshot.assistantRuntimeState = .inactive
    snapshot.sessionID = "-"
    snapshot.transportStatusText = "disconnected"
    snapshot.uplinkStatusText = "idle"
    snapshot.playbackStatusText = "idle"
    snapshot.infoText = "Assistant inactive."
    await refreshSubsystemStatus()
    publishSnapshot()
  }

  func endConversation() async {
    guard snapshot.assistantRuntimeState == .activeConversation || snapshot.assistantRuntimeState == .connectingConversation else { return }
    do {
      try await backendSessionClient.sendEndTurn()
    } catch {
      snapshot.errorText = "Failed to send end-turn: \(error.localizedDescription)"
    }
    await resetConversationToArmedState(reason: "Conversation ended. Listening for wake phrase again.")
  }

  func handleScenePhaseChange(_ phase: ScenePhase) {
    switch phase {
    case .background:
      guard snapshot.assistantRuntimeState != .inactive else { return }
      phoneAudioIO.prepareForBackground()
      if snapshot.assistantRuntimeState == .activeConversation {
        snapshot.infoText = "Active conversation continues while app is backgrounded if audio session remains available."
        snapshot.playbackRouteText = phoneAudioIO.playbackRouteDescription()
        publishSnapshot()
      }
    case .active:
      guard snapshot.assistantRuntimeState != .inactive else { return }
      phoneAudioIO.restoreFromForeground()
      snapshot.playbackRouteText = phoneAudioIO.playbackRouteDescription()
      publishSnapshot()
    case .inactive:
      break
    @unknown default:
      break
    }
  }

  private func bindPhoneAudio() {
    phoneAudioIO.onWakePCMFrame = { [weak self] frame in
      guard let self else { return }
      if self.awaitingFirstWakePCMFrame, self.snapshot.assistantRuntimeState == .armedListening {
        self.awaitingFirstWakePCMFrame = false
        self.snapshot.infoText = "Say \"\(self.config.wakePhrase)\" to start a conversation."
        self.debugLog("Received first wake PCM frame after arming")
        self.publishSnapshot()
      }
      self.wakePhraseDetector.processPCMFrame(frame)
    }
    phoneAudioIO.onRealtimePCMFrame = { [weak self] payload, timestampMs in
      Task { @MainActor [weak self] in
        await self?.handleRealtimePCMFrame(payload, timestampMs: timestampMs)
      }
    }
  }

  private func bindWakePhraseDetector() {
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
      self?.snapshot.errorText = message
      self?.publishSnapshot()
    }
  }

  private func bindBackendEvents() {
    debugLog("Binding backend event handler")
    Task { [weak self] in
      await self?.backendSessionClient.setEventHandler { [weak self] envelope in
        Task { @MainActor [weak self] in
          guard let self else { return }
          self.debugLog("Consuming backend event#\(envelope.id) \(self.describeBackendEvent(envelope.event))")
          await self.handleBackendEvent(envelope)
        }
      }
    }
  }

  private func startConversation(from event: WakeWordDetectionEvent) async {
    guard snapshot.assistantRuntimeState == .armedListening else { return }

    wakeWarmupTask?.cancel()
    wakeWarmupTask = nil
    wakeListeningGeneration += 1
    activeSessionID = "sess_\(UUID().uuidString)"
    backendReady = false
    firstUplinkAckReceived = false
    isSuppressingRealtimeUplinkForPlayback = false
    awaitingFirstWakePCMFrame = false
    activeConversationStartedAtMs = nil
    snapshot.errorText = ""
    snapshot.assistantRuntimeState = .connectingConversation
    snapshot.sessionID = activeSessionID ?? "-"
    snapshot.transportStatusText = "connecting"
    snapshot.uplinkStatusText = "waiting_for_backend_ready"
    snapshot.playbackStatusText = "waiting_for_server_response"
    snapshot.infoText = "Wake detected. Opening backend conversation."
    publishSnapshot()

    guard let activeSessionID else { return }
    await backendSessionClient.connect(sessionID: activeSessionID)

    do {
      try await backendSessionClient.sendSessionActivate()
      try await backendSessionClient.sendWakewordDetected(event)
      debugLog("Conversation control messages sent; enabling realtime uplink for session \(activeSessionID)")
      markConversationReady(source: "control_messages_sent")
    } catch {
      snapshot.errorText = "Failed to start backend conversation: \(error.localizedDescription)"
      await resetConversationToArmedState(reason: "Listening for wake phrase again.")
      return
    }
  }

  private func handleRealtimePCMFrame(_ payload: Data, timestampMs: Int64) async {
    switch snapshot.assistantRuntimeState {
    case .connectingConversation:
      bufferRealtimeFrame(payload, timestampMs: timestampMs)
      return
    case .activeConversation:
      break
    case .inactive, .armedListening, .deactivating:
      return
    }

    guard backendReady else {
      bufferRealtimeFrame(payload, timestampMs: timestampMs)
      return
    }

    if phoneAudioIO.shouldSuppressRealtimeUplink() {
      if isSuppressingRealtimeUplinkForPlayback == false {
        isSuppressingRealtimeUplinkForPlayback = true
        snapshot.uplinkStatusText = "suppressed_during_playback"
        debugLog("Suppressing realtime uplink while assistant playback is active")
        publishSnapshot()
      }
      return
    }

    if isSuppressingRealtimeUplinkForPlayback {
      isSuppressingRealtimeUplinkForPlayback = false
      debugLog("Resuming realtime uplink after assistant playback")
    }

    do {
      if pendingRealtimeFrames.isEmpty == false {
        await flushPendingRealtimeFrames()
      }
      if firstUplinkAckReceived == false, snapshot.uplinkStatusText == "streaming_live_audio" {
        snapshot.uplinkStatusText = "sending_first_live_audio"
        debugLog("Sending first live client audio frame timestamp=\(timestampMs)")
      }
      try await backendSessionClient.sendAudioFrame(payload, timestampMs: timestampMs)
      let diagnostics = await backendSessionClient.diagnosticsSnapshot()
      snapshot.uplinkStatusText = "binary_sent=\(diagnostics.binarySendSuccessCount) last=\(diagnostics.lastBinaryFirstByteHex)"
      if diagnostics.binarySendSuccessCount == 1 {
        debugLog("First binary client audio send completed bytes=\(diagnostics.lastOutboundBytes)")
      }
    } catch {
      snapshot.errorText = "Failed to send client audio: \(error.localizedDescription)"
    }
    publishSnapshot()
  }

  private func handleBackendEvent(_ envelope: BackendSessionClient.EventEnvelope) async {
    switch envelope.event {
    case .stateChanged(let state):
      snapshot.backendStatusText = state.rawValue
      snapshot.transportStatusText = state.rawValue

    case .sessionReady:
      debugLog("Received backend session.state active event#\(envelope.id)")
      snapshot.transportStatusText = "ready"
      markConversationReady(source: "session_state_active")

    case .uplinkAcknowledged(let payload):
      firstUplinkAckReceived = true
      snapshot.uplinkStatusText = "ack frames=\(payload.framesReceived) bytes=\(payload.bytesReceived)"
      debugLog("Received uplink ack event#\(envelope.id) frames=\(payload.framesReceived) bytes=\(payload.bytesReceived)")

    case .serverAudio(let data):
      do {
        debugLog("Received server audio event#\(envelope.id) bytes=\(data.count)")
        debugLog("Calling appendAssistantPCMData for event#\(envelope.id)")
        try phoneAudioIO.appendAssistantPCMData(data)
        debugLog("appendAssistantPCMData completed for event#\(envelope.id) route=\(phoneAudioIO.playbackRouteDescription())")
        let diagnostics = await backendSessionClient.diagnosticsSnapshot()
        snapshot.playbackStatusText = "scheduled frames=\(diagnostics.inboundServerAudioFrameCount) bytes=\(diagnostics.inboundServerAudioBytes)"
        snapshot.playbackRouteText = phoneAudioIO.playbackRouteDescription()
      } catch {
        snapshot.playbackStatusText = "playback_failed"
        snapshot.errorText = "Failed to play assistant audio: \(error.localizedDescription)"
      }

    case .playbackControl(let payload):
      debugLog("Received playback control event#\(envelope.id) command=\(payload.command.rawValue)")
      snapshot.playbackStatusText = payload.command.rawValue
      phoneAudioIO.handlePlaybackControl(payload)

    case .closed:
      if isResettingConversationToArmedState {
        break
      }
      if snapshot.assistantRuntimeState == .activeConversation || snapshot.assistantRuntimeState == .connectingConversation {
        await resetConversationToArmedState(reason: "Connection closed. Listening for wake phrase again.")
      }

    case .error(let message):
      if isResettingConversationToArmedState, isExpectedDisconnectError(message) {
        debugLog("Ignoring expected backend disconnect error during reset: \(message)")
        break
      }
      snapshot.errorText = message
      if snapshot.assistantRuntimeState == .connectingConversation || snapshot.assistantRuntimeState == .activeConversation {
        await resetConversationToArmedState(reason: "Conversation failed. Listening for wake phrase again.")
      }
    }

    await refreshSubsystemStatus()
    publishSnapshot()
  }

  private func handleSleepDetected(_ event: WakeWordDetectionEvent) async {
    guard snapshot.assistantRuntimeState == .activeConversation else {
      return
    }

    guard let activeConversationStartedAtMs else {
      debugLog("Ignoring sleep phrase because active conversation start time is unavailable")
      return
    }

    let activeDurationMs = max(0, event.timestampMs - activeConversationStartedAtMs)
    guard activeDurationMs >= config.sleepWordMinActiveStreamMs else {
      debugLog(
        "Ignoring sleep phrase because active conversation duration \(activeDurationMs)ms is below threshold \(config.sleepWordMinActiveStreamMs)ms"
      )
      return
    }

    debugLog("Accepting sleep phrase after active duration \(activeDurationMs)ms")
    await endConversation()
  }

  private func resetConversationToArmedState(reason: String) async {
    guard isResettingConversationToArmedState == false else {
      debugLog("Reset to armed state already in progress")
      return
    }

    isResettingConversationToArmedState = true
    phoneAudioIO.cancelPlayback()
    activeSessionID = nil
    backendReady = false
    firstUplinkAckReceived = false
    isSuppressingRealtimeUplinkForPlayback = false
    activeConversationStartedAtMs = nil
    awaitingFirstWakePCMFrame = true
    pendingRealtimeFrames.removeAll(keepingCapacity: false)
    wakeListeningGeneration += 1
    let generation = wakeListeningGeneration
    snapshot.assistantRuntimeState = .armedListening
    snapshot.sessionID = "-"
    snapshot.transportStatusText = "idle"
    snapshot.uplinkStatusText = "armed_waiting_for_wake"
    snapshot.playbackStatusText = "armed_waiting_for_response"
    snapshot.infoText = "Warming up wake detection."
    await backendSessionClient.disconnect(sendDeactivate: false)
    await refreshSubsystemStatus()
    publishSnapshot()
    scheduleWakeListeningStart(generation: generation, readyMessage: reason)
    isResettingConversationToArmedState = false
  }

  private func refreshSubsystemStatus() async {
    let wakeStatus = wakePhraseDetector.statusSnapshot()
    let diagnostics = await backendSessionClient.diagnosticsSnapshot()
    snapshot.audioStatusText = phoneAudioIO.stateDescription()
    snapshot.backendStatusText = await backendSessionClient.connectionStateText()
    snapshot.wakeStatusText = wakeStatus.runtime
    snapshot.playbackRouteText = phoneAudioIO.playbackRouteDescription()
    if snapshot.assistantRuntimeState == .inactive {
      snapshot.playbackStatusText = "idle"
    } else if snapshot.playbackStatusText == "idle" {
      let inboundFrames = diagnostics.inboundServerAudioFrameCount
      if inboundFrames > 0 {
        snapshot.playbackStatusText = "received frames=\(inboundFrames) bytes=\(diagnostics.inboundServerAudioBytes)"
      } else if diagnostics.lastPlaybackControlCommand != "none" {
        snapshot.playbackStatusText = diagnostics.lastPlaybackControlCommand
      }
    }
    if !firstUplinkAckReceived && (snapshot.transportStatusText == "ready" || snapshot.transportStatusText == "connected") {
      snapshot.uplinkStatusText = "binary_completed=\(diagnostics.binarySendSuccessCount)"
    }
  }

  private func publishSnapshot() {
    onStatusUpdated?(snapshot)
  }

  private func scheduleWakeListeningStart(generation: Int, readyMessage: String? = nil) {
    wakeWarmupTask?.cancel()
    wakeWarmupTask = Task { @MainActor [weak self] in
      guard let self else { return }
      guard wakeListeningGeneration == generation, snapshot.assistantRuntimeState == .armedListening else { return }
      if wakePhraseDetector.isListening == false {
        awaitingFirstWakePCMFrame = true
        snapshot.infoText = "Starting wake detection."
        publishSnapshot()
        debugLog("Starting wake recognizer for generation \(generation)")
        wakePhraseDetector.startArmedListening()
        snapshot.infoText = readyMessage ?? "Listening for microphone frames."
      } else {
        awaitingFirstWakePCMFrame = false
        snapshot.infoText = readyMessage ?? "Say \"\(config.wakePhrase)\" to start a conversation."
      }
      await refreshSubsystemStatus()
      publishSnapshot()
    }
  }

  private func markConversationReady(source: String) {
    backendReady = true
    activeConversationStartedAtMs = Clocks.nowMs()
    awaitingFirstWakePCMFrame = false
    snapshot.assistantRuntimeState = .activeConversation
    snapshot.uplinkStatusText = firstUplinkAckReceived ? snapshot.uplinkStatusText : "streaming_live_audio"
    snapshot.infoText = "Conversation active."
    debugLog("Conversation active via \(source); pendingFrames=\(pendingRealtimeFrames.count)")
  }

  private func bufferRealtimeFrame(_ payload: Data, timestampMs: Int64) {
    pendingRealtimeFrames.append(PendingRealtimeFrame(payload: payload, timestampMs: timestampMs))
    if pendingRealtimeFrames.count > maxPendingRealtimeFrames {
      pendingRealtimeFrames.removeFirst(pendingRealtimeFrames.count - maxPendingRealtimeFrames)
    }
  }

  private func flushPendingRealtimeFrames() async {
    guard backendReady, pendingRealtimeFrames.isEmpty == false else { return }
    let frames = pendingRealtimeFrames
    pendingRealtimeFrames.removeAll(keepingCapacity: true)
    debugLog("Flushing \(frames.count) buffered realtime frames")
    for frame in frames {
      do {
        try await backendSessionClient.sendAudioFrame(frame.payload, timestampMs: frame.timestampMs)
      } catch {
        snapshot.errorText = "Failed to flush client audio: \(error.localizedDescription)"
        return
      }
    }
    let diagnostics = await backendSessionClient.diagnosticsSnapshot()
    snapshot.uplinkStatusText = "binary_sent=\(diagnostics.binarySendSuccessCount) last=\(diagnostics.lastBinaryFirstByteHex)"
  }

  private func debugLog(_ message: String) {
    #if DEBUG
      print("[AssistantRuntimeController] \(message)")
    #endif
  }

  private func isExpectedDisconnectError(_ message: String) -> Bool {
    let normalized = message.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    return normalized.contains("socket is not connected")
  }

  private func describeBackendEvent(_ event: BackendSessionClient.Event) -> String {
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

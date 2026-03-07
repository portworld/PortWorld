// Bridges phone microphone capture and assistant playback for the active phone-only runtime.

import AVFAudio
import Foundation

@MainActor
final class PhoneAudioIO {
  enum Error: LocalizedError {
    case sessionPreparationFailed(String)
    case startFailed(String)

    var errorDescription: String? {
      switch self {
      case .sessionPreparationFailed(let message):
        return message
      case .startFailed(let message):
        return message
      }
    }
  }

  var onWakePCMFrame: ((WakeWordPCMFrame) -> Void)? {
    didSet {
      audioManager.onWakePCMFrame = onWakePCMFrame
    }
  }

  var onRealtimePCMFrame: (@Sendable (Data, Int64) -> Void)? {
    didSet {
      audioManager.onRealtimePCMFrame = onRealtimePCMFrame
    }
  }

  private let audioManager: AudioCollectionManager
  private let audioSession: AVAudioSession
  private let playbackEngine: PhoneOnlyAssistantPlaybackControlling
  private let audioSessionLeaseManager: AudioSessionLeaseManager
  private var isResponseStreaming = false

  init(preferSpeakerOutput: Bool = true) {
    let manager = AudioCollectionManager(
      preferSpeakerOutput: preferSpeakerOutput,
      allowBuiltInMicInput: true
    )
    self.audioManager = manager
    self.audioSession = .sharedInstance()
    self.playbackEngine = AssistantPlaybackEngine(audioEngine: manager.sharedAudioEngine)
    self.audioSessionLeaseManager = AudioSessionLeaseManager(arbiter: AudioSessionArbiter())
    self.audioManager.isPlaybackPendingProvider = { [playbackEngine] in
      playbackEngine.hasActivePendingPlayback()
    }

    playbackEngine.onRouteChanged = { [weak self] route in
      guard let self else { return }
      self.debugLog("Playback route changed: \(route)")
      do {
        try self.ensurePhoneSpeakerRouteIfNeeded(context: "route_changed")
      } catch {
        self.debugLog("Failed to enforce speaker route after route change: \(error.localizedDescription)")
      }
    }

    playbackEngine.onRouteIssue = { [weak self] _ in
      guard let self else { return }
      do {
        try self.ensurePhoneSpeakerRouteIfNeeded(context: "route_issue")
      } catch {
        self.debugLog("Failed to enforce speaker route after route issue: \(error.localizedDescription)")
      }
    }
  }

  func prepareForArmedListening() async throws {
    try await audioSessionLeaseManager.acquire(configuration: .playAndRecordPhone)
    await audioManager.prepareAudioSession()
    guard audioManager.isAudioSessionReady else {
      throw Error.sessionPreparationFailed(
        audioManager.stats.lastError ?? "Phone audio session did not become ready."
      )
    }

    await audioManager.start()
    switch audioManager.state {
    case .recording:
      try ensurePhoneSpeakerRouteIfNeeded(context: "prepare_for_armed_listening")
      return
    case .failed(let message):
      throw Error.startFailed(message)
    default:
      throw Error.startFailed("Audio manager entered unexpected state: \(stateDescription())")
    }
  }

  func appendAssistantPCMData(_ pcmData: Data) throws {
    try ensurePhoneSpeakerRouteIfNeeded(context: "append_assistant_pcm")
    let format = AssistantAudioFormat(codec: "pcm_s16le", sampleRate: 24_000, channels: 1)
    try playbackEngine.appendPCMData(pcmData, format: format)
  }

  func handlePlaybackControl(_ payload: PhoneOnlyPlaybackControlPayload) {
    switch payload.command {
    case .startResponse:
      isResponseStreaming = true
      do {
        try ensurePhoneSpeakerRouteIfNeeded(context: "start_response")
      } catch {
        debugLog("Failed to enforce speaker route on start_response: \(error.localizedDescription)")
      }
    case .stopResponse:
      isResponseStreaming = false
    case .cancelResponse:
      isResponseStreaming = false
      debugLog("Received cancel_response; flushing assistant playback immediately")
    }
    playbackEngine.handlePlaybackControl(payload)
  }

  func cancelPlayback() {
    guard isResponseStreaming || playbackEngine.hasActivePendingPlayback() else { return }
    isResponseStreaming = false
    playbackEngine.cancelResponse()
  }

  func isAssistantPlaybackActive() -> Bool {
    isResponseStreaming || playbackEngine.hasActivePendingPlayback()
  }

  func prepareForBackground() {
    playbackEngine.prepareForBackground()
  }

  func restoreFromForeground() {
    playbackEngine.restoreFromBackground()
    do {
      try ensurePhoneSpeakerRouteIfNeeded(context: "restore_from_foreground")
    } catch {
      debugLog("Failed to enforce speaker route after foreground restore: \(error.localizedDescription)")
    }
  }

  func ensurePhoneSpeakerRouteIfNeeded() throws {
    try ensurePhoneSpeakerRouteIfNeeded(context: "manual_check")
  }

  func stop() async {
    debugLog(
      "Stopping phone audio I/O responseStreaming=\(isResponseStreaming) playbackPending=\(playbackEngine.hasActivePendingPlayback()) state=\(stateDescription())"
    )
    cancelPlayback()
    playbackEngine.shutdown()
    await audioManager.stop()
    try? await audioSessionLeaseManager.releaseIfNeeded()
    debugLog("Phone audio I/O stopped state=\(stateDescription()) route=\(playbackRouteDescription())")
  }

  func stateDescription() -> String {
    switch audioManager.state {
    case .idle:
      return "idle"
    case .preparingAudioSession:
      return "preparing_audio_session"
    case .waitingForDevice:
      return "waiting_for_device"
    case .recording:
      return "recording"
    case .stopping:
      return "stopping"
    case .failed(let message):
      return "failed: \(message)"
    }
  }

  func playbackRouteDescription() -> String {
    playbackEngine.currentRouteDescription()
  }

  private func ensurePhoneSpeakerRouteIfNeeded(context: String) throws {
    let outputs = audioSession.currentRoute.outputs
    guard outputs.isEmpty == false else { return }

    if outputs.contains(where: { $0.portType == .builtInSpeaker }) {
      return
    }

    let hasReceiverOnlyRoute = outputs.allSatisfy { $0.portType == .builtInReceiver }
    guard hasReceiverOnlyRoute else { return }

    debugLog("Overriding receiver route to speaker (\(context))")
    try audioSession.overrideOutputAudioPort(.speaker)
  }

  private func debugLog(_ message: String) {
    #if DEBUG
      print("[PhoneAudioIO] \(message)")
    #endif
  }
}

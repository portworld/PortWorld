import AVFAudio
import Foundation

@MainActor
final class GlassesAudioIO: AssistantAudioIOControlling {
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
      hfpAudioManager.onWakePCMFrame = onWakePCMFrame
      fallbackPhoneAudioIO.onWakePCMFrame = onWakePCMFrame
    }
  }

  var onRealtimePCMFrame: (@Sendable (Data, Int64) -> Void)? {
    didSet {
      hfpAudioManager.onRealtimePCMFrame = onRealtimePCMFrame
      fallbackPhoneAudioIO.onRealtimePCMFrame = onRealtimePCMFrame
    }
  }

  var onAudioModeChanged: ((AssistantAudioMode, Bool) -> Void)?

  private enum ActivePipeline {
    case none
    case hfp
    case mockFallback
  }

  private let hfpAudioManager: AudioCollectionManager
  private let audioSession: AVAudioSession
  private let hfpPlaybackEngine: AssistantPlaybackEngine
  private let hfpAudioSessionLeaseManager: AudioSessionLeaseManager
  private let fallbackPhoneAudioIO: PhoneAudioIO
  private var activePipeline: ActivePipeline = .none
  private var isResponseStreaming = false

  init() {
    let manager = AudioCollectionManager(
      preferSpeakerOutput: false,
      allowBuiltInMicInput: false
    )
    self.hfpAudioManager = manager
    self.audioSession = .sharedInstance()
    self.hfpPlaybackEngine = AssistantPlaybackEngine(audioEngine: manager.sharedAudioEngine)
    self.hfpAudioSessionLeaseManager = AudioSessionLeaseManager(arbiter: AudioSessionArbiter())
    self.fallbackPhoneAudioIO = PhoneAudioIO()

    hfpAudioManager.isPlaybackPendingProvider = { [hfpPlaybackEngine] in
      hfpPlaybackEngine.hasActivePendingPlayback()
    }

    hfpPlaybackEngine.onRouteChanged = { [weak self] _ in
      self?.publishAudioModeChange()
    }

    hfpPlaybackEngine.onRouteIssue = { [weak self] _ in
      self?.publishAudioModeChange()
    }
  }

  var currentAudioMode: AssistantAudioMode {
    switch activePipeline {
    case .none:
      return .inactive
    case .hfp:
      return .glassesHFP
    case .mockFallback:
      return .glassesMockFallback
    }
  }

  var isHFPRouteReady: Bool {
    let currentRoute = audioSession.currentRoute
    let inputReady = currentRoute.inputs.contains { $0.portType == .bluetoothHFP }
    let outputReady = currentRoute.outputs.contains { $0.portType == .bluetoothHFP }
    return inputReady || outputReady
  }

  func prepareForArmedListening() async throws {
    await stopActivePipelineIfNeeded(resetMode: false)

    if try await prepareHFPPipelineIfPossible() {
      return
    }

    try await fallbackPhoneAudioIO.prepareForArmedListening()
    activePipeline = .mockFallback
    publishAudioModeChange()
  }

  func appendAssistantPCMData(_ pcmData: Data) throws {
    switch activePipeline {
    case .hfp:
      let format = AssistantAudioFormat(codec: "pcm_s16le", sampleRate: 24_000, channels: 1)
      try hfpPlaybackEngine.appendPCMData(pcmData, format: format)
    case .mockFallback:
      try fallbackPhoneAudioIO.appendAssistantPCMData(pcmData)
    case .none:
      throw Error.startFailed("Glasses audio is not active.")
    }
  }

  func handlePlaybackControl(_ payload: AssistantPlaybackControlPayload) {
    switch payload.command {
    case .startResponse:
      isResponseStreaming = true
    case .stopResponse, .cancelResponse:
      isResponseStreaming = false
    }

    switch activePipeline {
    case .hfp:
      hfpPlaybackEngine.handlePlaybackControl(payload)
    case .mockFallback:
      fallbackPhoneAudioIO.handlePlaybackControl(payload)
    case .none:
      break
    }
  }

  func cancelPlayback() {
    switch activePipeline {
    case .hfp:
      guard isResponseStreaming || hfpPlaybackEngine.hasActivePendingPlayback() else { return }
      isResponseStreaming = false
      hfpPlaybackEngine.cancelResponse()
    case .mockFallback:
      fallbackPhoneAudioIO.cancelPlayback()
    case .none:
      break
    }
  }

  func isAssistantPlaybackActive() -> Bool {
    switch activePipeline {
    case .hfp:
      return isResponseStreaming || hfpPlaybackEngine.hasActivePendingPlayback()
    case .mockFallback:
      return fallbackPhoneAudioIO.isAssistantPlaybackActive()
    case .none:
      return false
    }
  }

  func prepareForBackground() {
    switch activePipeline {
    case .hfp:
      hfpPlaybackEngine.prepareForBackground()
    case .mockFallback:
      fallbackPhoneAudioIO.prepareForBackground()
    case .none:
      break
    }
  }

  func restoreFromForeground() {
    switch activePipeline {
    case .hfp:
      hfpPlaybackEngine.restoreFromBackground()
      publishAudioModeChange()
    case .mockFallback:
      fallbackPhoneAudioIO.restoreFromForeground()
      publishAudioModeChange()
    case .none:
      break
    }
  }

  func stop() async {
    await stopActivePipelineIfNeeded(resetMode: true)
  }

  func stateDescription() -> String {
    switch activePipeline {
    case .hfp:
      return "glasses_hfp/\(hfpStateDescription())"
    case .mockFallback:
      return "glasses_mock_fallback/\(fallbackPhoneAudioIO.stateDescription())"
    case .none:
      return "inactive"
    }
  }

  func playbackRouteDescription() -> String {
    switch activePipeline {
    case .hfp:
      return hfpPlaybackEngine.currentRouteDescription()
    case .mockFallback:
      return fallbackPhoneAudioIO.playbackRouteDescription()
    case .none:
      return audioSession.currentRoute.outputs.map(\.portType.rawValue).joined(separator: ",")
    }
  }
}

private extension GlassesAudioIO {
  func prepareHFPPipelineIfPossible() async throws -> Bool {
    try await hfpAudioSessionLeaseManager.acquire(configuration: .playAndRecordHFP)
    await hfpAudioManager.prepareAudioSession()

    guard hfpAudioManager.isAudioSessionReady else {
      try? await hfpAudioSessionLeaseManager.releaseIfNeeded()
      throw Error.sessionPreparationFailed(
        hfpAudioManager.stats.lastError ?? "Glasses audio session did not become ready."
      )
    }

    guard isHFPRouteReady else {
      await hfpAudioManager.stop()
      try? await hfpAudioSessionLeaseManager.releaseIfNeeded()
      return false
    }

    do {
      try hfpPlaybackEngine.configureBluetoothHFPRoute()
    } catch {
      await hfpAudioManager.stop()
      try? await hfpAudioSessionLeaseManager.releaseIfNeeded()
      throw error
    }

    await hfpAudioManager.start()
    switch hfpAudioManager.state {
    case .recording:
      activePipeline = .hfp
      publishAudioModeChange()
      return true

    case .waitingForDevice:
      await hfpAudioManager.stop()
      try? await hfpAudioSessionLeaseManager.releaseIfNeeded()
      return false

    case .failed(let message):
      await hfpAudioManager.stop()
      try? await hfpAudioSessionLeaseManager.releaseIfNeeded()
      throw Error.startFailed(message)

    default:
      await hfpAudioManager.stop()
      try? await hfpAudioSessionLeaseManager.releaseIfNeeded()
      throw Error.startFailed("Audio manager entered unexpected state: \(hfpStateDescription())")
    }
  }

  func stopActivePipelineIfNeeded(resetMode: Bool) async {
    switch activePipeline {
    case .hfp:
      cancelPlayback()
      hfpPlaybackEngine.shutdown()
      await hfpAudioManager.stop()
      try? await hfpAudioSessionLeaseManager.releaseIfNeeded()
    case .mockFallback:
      await fallbackPhoneAudioIO.stop()
    case .none:
      break
    }

    isResponseStreaming = false
    activePipeline = .none
    if resetMode {
      publishAudioModeChange()
    }
  }

  func hfpStateDescription() -> String {
    switch hfpAudioManager.state {
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

  func publishAudioModeChange() {
    onAudioModeChanged?(currentAudioMode, isHFPRouteReady)
  }
}

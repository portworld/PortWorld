import AVFAudio
import Foundation

@MainActor
final class GlassesAudioIO: AssistantAudioIOControlling {
  private static let hfpRouteSelectionTimeoutNs: UInt64 = 2_000_000_000
  private static let hfpRouteSelectionPollIntervalNs: UInt64 = 100_000_000

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
    }
  }

  var onRealtimePCMFrame: (@Sendable (Data, Int64) -> Void)? {
    didSet {
      hfpAudioManager.onRealtimePCMFrame = onRealtimePCMFrame
    }
  }

  var onAudioModeChanged: ((AssistantAudioMode, Bool) -> Void)?

  private enum ActivePipeline {
    case none
    case hfp
  }

  private let hfpAudioManager: AudioCollectionManager
  private let audioSession: AVAudioSession
  private let hfpPlaybackEngine: AssistantPlaybackEngine
  private let hfpAudioSessionLeaseManager: AudioSessionLeaseManager
  private var audioRouteObserver: NSObjectProtocol?
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

    hfpAudioManager.isPlaybackPendingProvider = { [hfpPlaybackEngine] in
      hfpPlaybackEngine.hasActivePendingPlayback()
    }

    hfpPlaybackEngine.onRouteChanged = { [weak self] _ in
      self?.publishAudioModeChange()
    }

    hfpPlaybackEngine.onRouteIssue = { [weak self] _ in
      self?.publishAudioModeChange()
    }

    audioRouteObserver = NotificationCenter.default.addObserver(
      forName: AVAudioSession.routeChangeNotification,
      object: audioSession,
      queue: .main
    ) { [weak self] _ in
      Task { @MainActor [weak self] in
        await self?.handleAudioRouteChange()
      }
    }
  }

  deinit {
    if let audioRouteObserver {
      NotificationCenter.default.removeObserver(audioRouteObserver)
    }
  }

  var currentAudioMode: AssistantAudioMode {
    switch activePipeline {
    case .none:
      return .inactive
    case .hfp:
      return .glassesHFP
    }
  }

  var isHFPRouteReady: Bool {
    hfpAudioManager.hfpRouteAvailability().isActive
  }

  var isHFPRouteSelectable: Bool {
    let routeAvailability = hfpAudioManager.hfpRouteAvailability()
    return routeAvailability.isSelectable || routeAvailability.isActive
  }

  func prepareForArmedListening() async throws {
    await stopActivePipelineIfNeeded(resetMode: false)
    try await prepareHFPPipeline()
  }

  func appendAssistantPCMData(_ pcmData: Data) throws {
    switch activePipeline {
    case .hfp:
      let format = AssistantAudioFormat(codec: "pcm_s16le", sampleRate: 24_000, channels: 1)
      try hfpPlaybackEngine.appendPCMData(pcmData, format: format)
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
    case .none:
      break
    }
  }

  func isAssistantPlaybackActive() -> Bool {
    switch activePipeline {
    case .hfp:
      return isResponseStreaming || hfpPlaybackEngine.hasActivePendingPlayback()
    case .none:
      return false
    }
  }

  func prepareForBackground() {
    switch activePipeline {
    case .hfp:
      hfpPlaybackEngine.prepareForBackground()
    case .none:
      break
    }
  }

  func restoreFromForeground() {
    switch activePipeline {
    case .hfp:
      hfpPlaybackEngine.restoreFromBackground()
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
    case .none:
      return "inactive"
    }
  }

  func playbackRouteDescription() -> String {
    switch activePipeline {
    case .hfp:
      return hfpPlaybackEngine.currentRouteDescription()
    case .none:
      return audioSession.currentRoute.outputs.map(\.portType.rawValue).joined(separator: ",")
    }
  }
}

private extension GlassesAudioIO {
  func releaseHFPAudioSessionLease(context: String) async {
    do {
      try await hfpAudioSessionLeaseManager.releaseIfNeeded()
    } catch {
      NSLog("GlassesAudioIO: failed to release HFP audio session lease (\(context)): \(error)")
    }
  }

  func handleAudioRouteChange() async {
    publishAudioModeChange()

    guard activePipeline == .hfp else { return }
    guard isHFPRouteReady == false else { return }

    await stopActivePipelineIfNeeded(resetMode: true)
  }

  func prepareHFPPipeline() async throws {
    try await hfpAudioSessionLeaseManager.acquire(configuration: .playAndRecordHFP)
    await hfpAudioManager.prepareAudioSession()

    guard hfpAudioManager.isAudioSessionReady else {
      await releaseHFPAudioSessionLease(context: "session_not_ready")
      throw Error.sessionPreparationFailed(
        hfpAudioManager.stats.lastError ?? "Glasses audio session did not become ready."
      )
    }

    hfpAudioManager.logRouteDiagnostics(context: "after_audio_session_prepare")

    guard isHFPRouteSelectable else {
      hfpAudioManager.logRouteDiagnostics(context: "no_selectable_hfp_input")
      await hfpAudioManager.stop()
      await releaseHFPAudioSessionLease(context: "no_selectable_hfp_input")
      throw Error.startFailed("No Bluetooth HFP glasses audio route is available on this phone right now.")
    }

    do {
      _ = try hfpAudioManager.selectBluetoothHFPInputIfAvailable()
    } catch {
      hfpAudioManager.logRouteDiagnostics(context: "preferred_input_selection_failed")
      await hfpAudioManager.stop()
      await releaseHFPAudioSessionLease(context: "preferred_input_selection_failed")
      throw error
    }

    hfpAudioManager.logRouteDiagnostics(context: "after_preferred_input_selection")

    if try await waitForHFPRouteActivation() == false {
      hfpAudioManager.logRouteDiagnostics(context: "hfp_route_activation_timeout")
      await hfpAudioManager.stop()
      await releaseHFPAudioSessionLease(context: "hfp_route_activation_timeout")
      throw Error.startFailed("Glasses audio route did not become active. Make sure your glasses are connected for calls and audio on iPhone.")
    }

    await hfpAudioManager.start()
    switch hfpAudioManager.state {
    case .recording:
      activePipeline = .hfp
      publishAudioModeChange()
      return

    case .waitingForDevice:
      hfpAudioManager.logRouteDiagnostics(context: "audio_manager_waiting_for_device")
      await hfpAudioManager.stop()
      await releaseHFPAudioSessionLease(context: "waiting_for_device")
      throw Error.startFailed("Glasses audio route is selectable, but iOS did not activate it in time.")

    case .failed(let message):
      hfpAudioManager.logRouteDiagnostics(context: "audio_manager_failed")
      await hfpAudioManager.stop()
      await releaseHFPAudioSessionLease(context: "audio_manager_failed")
      throw Error.startFailed(message)

    default:
      hfpAudioManager.logRouteDiagnostics(context: "unexpected_audio_manager_state")
      await hfpAudioManager.stop()
      await releaseHFPAudioSessionLease(context: "unexpected_audio_manager_state")
      throw Error.startFailed("Audio manager entered unexpected state: \(hfpStateDescription())")
    }
  }

  func waitForHFPRouteActivation() async throws -> Bool {
    if isHFPRouteReady {
      return true
    }

    let clock = ContinuousClock()
    let deadline = clock.now + .nanoseconds(Int64(Self.hfpRouteSelectionTimeoutNs))
    while clock.now < deadline {
      try await Task.sleep(nanoseconds: Self.hfpRouteSelectionPollIntervalNs)
      if isHFPRouteReady {
        return true
      }
    }

    return isHFPRouteReady
  }

  func stopActivePipelineIfNeeded(resetMode: Bool) async {
    switch activePipeline {
    case .hfp:
      cancelPlayback()
      hfpPlaybackEngine.shutdown()
      await hfpAudioManager.stop()
      await releaseHFPAudioSessionLease(context: "stop_active_pipeline")
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

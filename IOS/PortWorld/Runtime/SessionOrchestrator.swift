import AVFAudio
import Darwin
import Foundation
import OSLog
import UIKit

private final class RealtimePCMUplinkWorker {
  struct Frame {
    let payload: Data
    let timestampMs: Int64
  }

  private let capacity: Int
  private let sendFrame: @Sendable (Frame) async throws -> Void
  private let onSendError: @Sendable (Error) -> Void
  private let stateQueue = DispatchQueue(label: "com.portworld.session_orchestrator.realtime_pcm_uplink")
  private let drainSignal: AsyncStream<Void>
  private let drainSignalContinuation: AsyncStream<Void>.Continuation
  private var drainTask: Task<Void, Never>?
  private var pendingFrames: [Frame] = []
  private var droppedFrameCountSinceLastRead = 0
  private var hasPendingDrainSignal = false
  private var isActive = true

  init(
    capacity: Int,
    sendFrame: @escaping @Sendable (Frame) async throws -> Void,
    onSendError: @escaping @Sendable (Error) -> Void
  ) {
    self.capacity = max(1, capacity)
    self.sendFrame = sendFrame
    self.onSendError = onSendError
    let stream = AsyncStream.makeStream(of: Void.self)
    self.drainSignal = stream.stream
    self.drainSignalContinuation = stream.continuation
    self.drainTask = Task { [weak self] in
      await self?.runDrainLoop()
    }
  }

  func enqueue(payload: Data, timestampMs: Int64) {
    stateQueue.async { [weak self] in
      guard let self, self.isActive else { return }
      if self.pendingFrames.count >= self.capacity {
        self.pendingFrames.removeFirst()
        self.droppedFrameCountSinceLastRead += 1
      }
      self.pendingFrames.append(Frame(payload: payload, timestampMs: timestampMs))
      if !self.hasPendingDrainSignal {
        self.hasPendingDrainSignal = true
        self.drainSignalContinuation.yield(())
      }
    }
  }

  func consumeDroppedFrameCount() -> Int {
    stateQueue.sync {
      let count = droppedFrameCountSinceLastRead
      droppedFrameCountSinceLastRead = 0
      return count
    }
  }

  func stopAndReset() {
    stateQueue.sync {
      isActive = false
      pendingFrames.removeAll(keepingCapacity: false)
      hasPendingDrainSignal = false
      droppedFrameCountSinceLastRead = 0
    }
    drainSignalContinuation.finish()
    drainTask?.cancel()
    drainTask = nil
  }

  func clearPendingFrames() {
    stateQueue.async { [weak self] in
      self?.pendingFrames.removeAll(keepingCapacity: false)
      self?.hasPendingDrainSignal = false
    }
  }

  private func runDrainLoop() async {
    for await _ in drainSignal {
      while let frame = popNextFrameForDrain() {
        do {
          try await sendFrame(frame)
        } catch {
          onSendError(error)
        }
      }
      if !isWorkerActive() {
        return
      }
    }
  }

  private func popNextFrameForDrain() -> Frame? {
    stateQueue.sync {
      guard isActive else {
        pendingFrames.removeAll(keepingCapacity: false)
        hasPendingDrainSignal = false
        return nil
      }
      guard !pendingFrames.isEmpty else {
        hasPendingDrainSignal = false
        return nil
      }
      return pendingFrames.removeFirst()
    }
  }

  private func isWorkerActive() -> Bool {
    stateQueue.sync { isActive }
  }
}

@MainActor
final class SessionOrchestrator {
  struct Dependencies {
    typealias MakeRealtimeTransport = (_ config: RuntimeConfig) -> RealtimeTransport
    typealias MakeVisionFrameUploader = (_ config: RuntimeConfig) -> VisionFrameUploaderProtocol
    typealias MakeRollingVideoBuffer = (_ config: RuntimeConfig) -> RollingVideoBufferProtocol
    typealias MakePlaybackEngine = (
      _ sharedAudioEngine: AVAudioEngine?,
      _ stuckDetectionThresholdMs: Int64
    ) -> AssistantPlaybackEngineProtocol

    let startStream: () async -> Void
    let stopStream: () async -> Void
    let exportAudioClip: (AudioClipExportWindow) throws -> URL
    let flushPendingAudioChunks: () -> Void
    let audioBufferDurationProvider: () -> Int
    /// Shared AVAudioEngine from AudioCollectionManager for playback.
    /// Pass nil to create an internal engine (not recommended for HFP).
    let sharedAudioEngine: AVAudioEngine?
    let clock: () -> Int64
    let makeRealtimeTransport: MakeRealtimeTransport
    let makeVisionFrameUploader: MakeVisionFrameUploader
    let makeRollingVideoBuffer: MakeRollingVideoBuffer
    let makePlaybackEngine: MakePlaybackEngine
    let eventLogger: EventLoggerProtocol

    init(
      startStream: @escaping () async -> Void,
      stopStream: @escaping () async -> Void,
      exportAudioClip: @escaping (AudioClipExportWindow) throws -> URL,
      flushPendingAudioChunks: @escaping () -> Void,
      audioBufferDurationProvider: @escaping () -> Int,
      sharedAudioEngine: AVAudioEngine?,
      clock: @escaping () -> Int64,
      makeRealtimeTransport: @escaping MakeRealtimeTransport,
      makeVisionFrameUploader: @escaping MakeVisionFrameUploader,
      makeRollingVideoBuffer: @escaping MakeRollingVideoBuffer,
      makePlaybackEngine: @escaping MakePlaybackEngine,
      eventLogger: EventLoggerProtocol
    ) {
      self.startStream = startStream
      self.stopStream = stopStream
      self.exportAudioClip = exportAudioClip
      self.flushPendingAudioChunks = flushPendingAudioChunks
      self.audioBufferDurationProvider = audioBufferDurationProvider
      self.sharedAudioEngine = sharedAudioEngine
      self.clock = clock
      self.makeRealtimeTransport = makeRealtimeTransport
      self.makeVisionFrameUploader = makeVisionFrameUploader
      self.makeRollingVideoBuffer = makeRollingVideoBuffer
      self.makePlaybackEngine = makePlaybackEngine
      self.eventLogger = eventLogger
    }

    init(
      startStream: @escaping () async -> Void,
      stopStream: @escaping () async -> Void,
      exportAudioClip: @escaping (AudioClipExportWindow) throws -> URL,
      flushPendingAudioChunks: @escaping () -> Void,
      audioBufferDurationProvider: @escaping () -> Int,
      sharedAudioEngine: AVAudioEngine?,
      clock: @escaping () -> Int64,
      makeVisionFrameUploader: @escaping MakeVisionFrameUploader,
      makeRollingVideoBuffer: @escaping MakeRollingVideoBuffer,
      makePlaybackEngine: @escaping MakePlaybackEngine,
      eventLogger: EventLoggerProtocol
    ) {
      self.init(
        startStream: startStream,
        stopStream: stopStream,
        exportAudioClip: exportAudioClip,
        flushPendingAudioChunks: flushPendingAudioChunks,
        audioBufferDurationProvider: audioBufferDurationProvider,
        sharedAudioEngine: sharedAudioEngine,
        clock: clock,
        makeRealtimeTransport: SessionOrchestrator.Dependencies.live.makeRealtimeTransport,
        makeVisionFrameUploader: makeVisionFrameUploader,
        makeRollingVideoBuffer: makeRollingVideoBuffer,
        makePlaybackEngine: makePlaybackEngine,
        eventLogger: eventLogger
      )
    }

    static var live: Dependencies {
      Dependencies(
        startStream: {},
        stopStream: {},
        exportAudioClip: { _ in throw AudioClipExportError.sessionDirectoryUnavailable },
        flushPendingAudioChunks: {},
        audioBufferDurationProvider: { 0 },
        sharedAudioEngine: nil,
        clock: { Clocks.nowMs() },
        makeRealtimeTransport: { config in
          GatewayTransport(runtimeConfig: config)
        },
        makeVisionFrameUploader: { config in
          VisionFrameUploader(
            endpointURL: config.visionFrameURL,
            defaultHeaders: config.requestHeaders,
            sessionIDProvider: { nil },
            uploadIntervalMs: SessionOrchestrator.photoUploadIntervalMs(photoFps: config.photoFps)
          )
        },
        makeRollingVideoBuffer: { config in
          RollingVideoBuffer(maxDurationMs: Int64(max(config.preWakeVideoMs * 6, 30_000)))
        },
        makePlaybackEngine: { sharedAudioEngine, stuckDetectionThresholdMs in
          AssistantPlaybackEngine(
            audioEngine: sharedAudioEngine,
            stuckDetectionThresholdMs: stuckDetectionThresholdMs
          )
        },
        eventLogger: EventLogger()
      )
    }
  }

  struct StatusSnapshot {
    var sessionState: SessionState = .idle
    var wakeState: WakeState = .listening
    var queryState: QueryState = .idle
    var photoState: PhotoUploadState = .idle
    var playbackState: String = "idle"
    var sessionID: String = "-"
    var queryID: String = "-"
    var wakeCount: Int = 0
    var queryCount: Int = 0
    var photoUploadCount: Int = 0
    var playbackChunkCount: Int = 0
    /// Number of audio buffers currently pending playback (queue depth).
    var pendingPlaybackBufferCount: Int = 0
    /// Estimated pending audio duration in milliseconds.
    var pendingPlaybackDurationMs: Int = 0
    /// Whether playback queue is under backpressure.
    var playbackBackpressured: Bool = false
    var videoFrameCount: Int = 0
    var wakeEngine: String = WakeWordEngineKind.manual.rawValue
    var wakeRuntimeStatus: String = WakeWordRuntimeStatus.idle.rawValue
    var speechAuthorization: String = WakeWordAuthorizationState.notRequired.rawValue
    var manualWakeFallbackEnabled: Bool = true
    var backendSummary: String = "-"
    var lastError: String = ""
  }

  private struct BufferedOutboundMessage {
    let type: WSOutboundType
    let sessionID: String
    let payload: JSONValue
    let enqueuedAtMs: Int64
  }

  var onStatusUpdated: ((StatusSnapshot) -> Void)?

  private let config: RuntimeConfig
  private let dependencies: Dependencies
  private let eventLogger: EventLoggerProtocol
  private let healthIntervalMs: UInt64 = 10_000
  private static let realtimePCMUplinkQueueLimit = 32
  private let appVersion: String?
  private let deviceModel: String?
  private let osVersion: String?

  private let manualWakeEngine: ManualWakeWordEngine
  private let primaryWakeEngine: WakeWordEngineProtocol

  private func configureWakeEngine(_ engine: WakeWordEngineProtocol) {
    engine.onWakeDetected = { [weak self] event in
      Task { @MainActor in
        self?.handleWakeDetected(event)
      }
    }
    engine.onSleepDetected = { [weak self] event in
      Task { @MainActor in
        await self?.handleSleepDetected(event)
      }
    }
    engine.onError = { [weak self] error in
      Task { @MainActor in
        self?.setError(error.localizedDescription)
      }
    }
    engine.onStatusChanged = { [weak self] status in
      Task { @MainActor in
        self?.snapshot.wakeEngine = status.engine.rawValue
        self?.snapshot.wakeRuntimeStatus = status.runtime.rawValue
        self?.snapshot.speechAuthorization = status.authorization.rawValue
        self?.publishSnapshot()
      }
    }
  }

  private var visionFrameUploader: VisionFrameUploaderProtocol?
  private var rollingVideoBuffer: RollingVideoBufferProtocol?
  private var playbackEngine: AssistantPlaybackEngineProtocol?
  private var realtimeTransport: RealtimeTransport?

  private var snapshot = StatusSnapshot()
  private var activeSessionID: String?
  private var isActivated = false
  private var runtimeState: RuntimeState = .foregroundActive
  private var photosFailed = 0
  private var wsReconnectAttempts = 0
  private var isNetworkAvailable = true
  private var reconnectOnNetworkRestore = false
  private var pendingNetworkAvailability: Bool?
  private var networkTransitionTask: Task<Void, Never>?
  /// Counts full session restarts (deactivate+activate cycles), persists across activations.
  /// Distinguishes from wsReconnectAttempts which tracks transport-level reconnects within a session.
  private var sessionRestartCount = 0
  private var sessionActivatedAtMs: Int64 = 0
  private var lastHealthEmissionTsMs: Int64?
  private var lastHealthPingSentAtMs: Int64?
  private var wsRoundTripLatencyMs = 0
  private var lastKnownPlaybackRoute = "unknown"
  private var healthTask: Task<Void, Never>?
  private var transportEventsTask: Task<Void, Never>?
  private var transportState: TransportState = .disconnected
  private var wantsRealtimeStreaming = false
  private var isTransportDisconnecting = false
  private var realtimePCMUplinkWorker: RealtimePCMUplinkWorker?
  private var outboundMessageBuffer: [BufferedOutboundMessage] = []
  private let outboundMessageBufferLimit = 20
  private let outboundMessageTTLms: Int64 = 60_000
  private let logger = Logger(subsystem: "PortWorld", category: "SessionOrchestrator")

  init(config: RuntimeConfig, dependencies: Dependencies) {
    self.config = config
    self.dependencies = dependencies
    self.eventLogger = dependencies.eventLogger

    let manual = ManualWakeWordEngine(defaultPhrase: config.wakePhrase)
    self.manualWakeEngine = manual

    if config.wakeWordMode == .onDevicePreferred {
      self.primaryWakeEngine = SFSpeechWakeWordEngine(
        wakePhrase: config.wakePhrase,
        sleepPhrase: config.sleepPhrase,
        localeIdentifier: config.wakeWordLocaleIdentifier,
        requiresOnDeviceRecognition: config.wakeWordRequiresOnDeviceRecognition,
        detectionCooldownMs: config.wakeWordDetectionCooldownMs
      )
      self.snapshot.manualWakeFallbackEnabled = true
    } else {
      self.primaryWakeEngine = manual
      self.snapshot.manualWakeFallbackEnabled = true
    }

    self.appVersion = Self.resolveAppVersion()
    self.deviceModel = Self.resolveDeviceModel()
    self.osVersion = Self.resolveOSVersion()

    configureWakeEngine(manualWakeEngine)
    if primaryWakeEngine !== manualWakeEngine {
      configureWakeEngine(primaryWakeEngine)
    }
    self.snapshot.backendSummary = config.backendSummary
  }

  convenience init(config: RuntimeConfig) {
    self.init(config: config, dependencies: .live)
  }

  func hasPendingPlayback() -> Bool {
    playbackEngine?.hasActivePendingPlayback() ?? false
  }

  private func configureInjectedServices() async {
    self.realtimeTransport = dependencies.makeRealtimeTransport(config)
    self.realtimePCMUplinkWorker = makeRealtimePCMUplinkWorker()

    let visionFrameUploader = dependencies.makeVisionFrameUploader(config)
    await visionFrameUploader.bindHandlers(
      sessionIDProvider: { [weak self] in self?.activeSessionID },
      onUploadResult: { [weak self] result in
        Task { @MainActor in
          self?.handleVisionUploadResult(result)
        }
      }
    )
    self.visionFrameUploader = visionFrameUploader
    self.rollingVideoBuffer = dependencies.makeRollingVideoBuffer(config)
    self.playbackEngine = dependencies.makePlaybackEngine(
      dependencies.sharedAudioEngine,
      config.assistantStuckDetectionThresholdMs
    )
    configurePlaybackEngine()
    startTransportEventsLoop()
  }

  private func makeRealtimePCMUplinkWorker() -> RealtimePCMUplinkWorker? {
    guard let realtimeTransport else { return nil }
    return RealtimePCMUplinkWorker(
      capacity: Self.realtimePCMUplinkQueueLimit,
      sendFrame: { frame in
        try await realtimeTransport.sendAudio(frame.payload, timestampMs: frame.timestampMs)
      },
      onSendError: { [weak self] error in
        Task { @MainActor [weak self] in
          self?.setError("Failed to send realtime audio: \(error.localizedDescription)")
        }
      }
    )
  }

  func preflightWakeAuthorization() async {
    let status = await primaryWakeEngine.requestAuthorizationIfNeeded()
    snapshot.speechAuthorization = status.rawValue
    if status != .authorized, primaryWakeEngine !== manualWakeEngine {
      snapshot.wakeRuntimeStatus = WakeWordRuntimeStatus.fallbackManual.rawValue
    }
    publishSnapshot()
  }

  func activate() async {
    guard !isActivated else { return }

    isActivated = true
    await configureInjectedServices()
    let sessionID = "sess_\(UUID().uuidString)"
    activeSessionID = sessionID

    snapshot.sessionState = .idle
    snapshot.sessionID = sessionID
    snapshot.wakeState = .listening
    snapshot.queryState = .idle
    snapshot.wakeEngine = primaryWakeEngine.engineKind.rawValue
    snapshot.speechAuthorization = primaryWakeEngine.currentAuthorizationStatus().rawValue
    publishSnapshot()
    runtimeState = .foregroundActive
    wantsRealtimeStreaming = false
    isTransportDisconnecting = false
    transportState = .disconnected
    photosFailed = 0
    wsReconnectAttempts = 0
    reconnectOnNetworkRestore = false
    lastHealthEmissionTsMs = nil
    lastHealthPingSentAtMs = nil
    wsRoundTripLatencyMs = 0
    sessionActivatedAtMs = dependencies.clock()

    await dependencies.startStream()
    if let visionFrameUploader {
      await visionFrameUploader.start()
    }
    manualWakeEngine.startListening()
    if primaryWakeEngine !== manualWakeEngine {
      _ = await primaryWakeEngine.requestAuthorizationIfNeeded()
      primaryWakeEngine.startListening()
    }

    startHealthLoop()

    await logEvent(name: "session.activate")
    await sendOutbound(type: .sessionActivate, payload: EmptyPayload())
    await emitHealth(reason: "activate")
  }

  func deactivate() async {
    guard isActivated else { return }

    await sendOutbound(type: .sessionDeactivate, payload: EmptyPayload())
    wantsRealtimeStreaming = false
    isTransportDisconnecting = true
    snapshot.sessionState = .disconnecting
    snapshot.playbackState = "disconnecting"
    publishSnapshot()
    transportEventsTask?.cancel()
    transportEventsTask = nil
    realtimePCMUplinkWorker?.stopAndReset()
    realtimePCMUplinkWorker = nil
    if let realtimeTransport {
      await realtimeTransport.disconnect()
    }
    transportState = .disconnected
    manualWakeEngine.stopListening()
    if primaryWakeEngine !== manualWakeEngine {
      primaryWakeEngine.stopListening()
    }
    if let visionFrameUploader {
      await visionFrameUploader.stop()
    }
    if let rollingVideoBuffer {
      await rollingVideoBuffer.clear()
    }
    playbackEngine?.shutdown()
    stopHealthLoop()
    await dependencies.stopStream()

    activeSessionID = nil
    outboundMessageBuffer.removeAll(keepingCapacity: false)
    isActivated = false
    sessionRestartCount += 1
    reconnectOnNetworkRestore = false
    lastHealthEmissionTsMs = nil
    lastHealthPingSentAtMs = nil
    wsRoundTripLatencyMs = 0
    realtimeTransport = nil
    visionFrameUploader = nil
    rollingVideoBuffer = nil
    playbackEngine = nil

    snapshot.sessionState = .ended
    snapshot.queryState = .idle
    snapshot.wakeState = .listening
    snapshot.photoState = .idle
    snapshot.playbackState = "idle"
    snapshot.queryID = "-"
    publishSnapshot()
  }

  func handleAppDidEnterBackground() {
    runtimeState = .backgroundBestEffort
    playbackEngine?.prepareForBackground()
    Task {
      await logEvent(name: "runtime.background")
      await emitHealth(reason: "background")
    }
  }

  func handleAppWillResignActive() {
    runtimeState = .suspended
    Task {
      await logEvent(name: "runtime.suspended")
    }
  }

  func handleAppDidBecomeActive() {
    runtimeState = .resumed
    playbackEngine?.restoreFromBackground()
    Task {
      await logEvent(name: "runtime.resumed")
      if isActivated, wantsRealtimeStreaming, transportState == .disconnected {
        await self.connectRealtimeTransport(reason: "foreground_resume")
      }
      runtimeState = .foregroundActive
      await emitHealth(reason: "foreground")
    }
  }

  func setNetworkAvailable(_ isAvailable: Bool) {
    let hasPendingTransition = pendingNetworkAvailability != nil
    guard isNetworkAvailable != isAvailable || hasPendingTransition else { return }
    pendingNetworkAvailability = isAvailable
    guard networkTransitionTask == nil else { return }

    networkTransitionTask = Task { [weak self] in
      await self?.processPendingNetworkTransitions()
    }
  }

  private func processPendingNetworkTransitions() async {
    while let nextAvailability = pendingNetworkAvailability {
      pendingNetworkAvailability = nil
      await applyNetworkAvailabilityTransition(nextAvailability)
    }
    networkTransitionTask = nil
  }

  private func applyNetworkAvailabilityTransition(_ isAvailable: Bool) async {
    guard isNetworkAvailable != isAvailable else { return }
    isNetworkAvailable = isAvailable

    if isAvailable {
      guard reconnectOnNetworkRestore, isActivated else { return }
      reconnectOnNetworkRestore = false
      await connectRealtimeTransport(reason: "network_restored")
      return
    }

    guard isActivated, wantsRealtimeStreaming else { return }
    reconnectOnNetworkRestore = true
    await disconnectRealtimeTransport(reason: "network_unavailable")
  }

  func pushVideoFrame(_ image: UIImage, timestampMs: Int64) {
    guard isActivated else { return }
    guard let rollingVideoBuffer, let visionFrameUploader else { return }

    Task {
      await rollingVideoBuffer.append(frame: image, timestampMs: timestampMs)
      await visionFrameUploader.submitLatestFrame(image, captureTimestampMs: timestampMs)
    }

    snapshot.videoFrameCount += 1
    publishSnapshot()
  }

  func submitCapturedPhoto(_ image: UIImage, timestampMs: Int64) {
    guard isActivated else { return }
    guard let visionFrameUploader else { return }
    Task {
      await visionFrameUploader.submitLatestFrame(image, captureTimestampMs: timestampMs)
    }
  }

  func processWakePCMFrame(_ frame: WakeWordPCMFrame) {
    guard isActivated else { return }
    if primaryWakeEngine !== manualWakeEngine {
      primaryWakeEngine.processPCMFrame(frame)
    }
  }

  func recordSpeechActivity(at timestampMs: Int64) {
    guard isActivated, wantsRealtimeStreaming else { return }
    _ = timestampMs
  }

  func triggerWakeForTesting() {
    manualWakeEngine.triggerManualWake(timestampMs: dependencies.clock())
  }

  var wakeEngineType: String {
    primaryWakeEngine.engineKind.rawValue
  }

  private func handleWakeDetected(_ event: WakeWordDetectionEvent) {
    guard isActivated else { return }
    guard !wantsRealtimeStreaming else { return }

    // Cancel any in-flight playback from previous response to avoid queue buildup
    playbackEngine?.cancelResponse()

    // Immediate audio feedback: single beep so the user knows wake word was heard
    playWakeChime()

    snapshot.wakeState = .triggered
    snapshot.wakeCount += 1
    snapshot.queryID = "-"
    snapshot.queryState = .recording
    snapshot.playbackState = "streaming_connecting"
    publishSnapshot()

    Task {
      await logEvent(name: "wakeword.detected")
      await sendOutbound(
        type: .wakewordDetected,
        payload: WakewordDetectedPayload(
          wakePhrase: event.wakePhrase,
          engine: event.engine,
          confidence: event.confidence.map(Double.init)
        )
      )
      await connectRealtimeTransport(reason: "wake")
    }
  }

  private func handleSleepDetected(_ event: WakeWordDetectionEvent) async {
    guard isActivated else { return }
    guard wantsRealtimeStreaming else { return }

    snapshot.wakeState = .triggered
    snapshot.playbackState = "disconnecting"
    publishSnapshot()
    await logEvent(name: "sleepword.detected", fields: ["phrase": .string(event.wakePhrase)])
    await disconnectRealtimeTransport(reason: "sleep")
  }

  private func startTransportEventsLoop() {
    transportEventsTask?.cancel()
    guard let realtimeTransport else { return }

    transportEventsTask = Task { [weak self, realtimeTransport] in
      for await event in realtimeTransport.events {
        guard let self else { return }
        await self.handleTransportEvent(event)
      }
    }
  }

  private func connectRealtimeTransport(reason: String) async {
    guard let activeSessionID else { return }
    guard let realtimeTransport else { return }
    if wantsRealtimeStreaming && (transportState == .connected || transportState == .connecting || transportState == .reconnecting) {
      return
    }
    guard isNetworkAvailable else {
      reconnectOnNetworkRestore = true
      snapshot.sessionState = .reconnecting
      snapshot.playbackState = "waiting_for_network"
      publishSnapshot()
      return
    }

    wantsRealtimeStreaming = true
    isTransportDisconnecting = false
    reconnectOnNetworkRestore = false
    transportState = .connecting
    snapshot.sessionState = .connecting
    snapshot.queryState = .recording
    snapshot.wakeState = .triggered
    snapshot.playbackState = "streaming_connecting"
    publishSnapshot()

    let transportConfig = TransportConfig(
      endpoint: config.webSocketURL,
      sessionId: activeSessionID,
      audioFormat: AudioStreamFormat(sampleRate: 8_000, channels: 1, encoding: "pcm_s16le"),
      headers: config.requestHeaders
    )

    do {
      try await realtimeTransport.connect(config: transportConfig)
      await logEvent(name: "transport.connect", fields: ["reason": .string(reason)])
    } catch {
      wantsRealtimeStreaming = false
      snapshot.sessionState = .failed
      snapshot.queryState = .failed
      snapshot.playbackState = "idle"
      publishSnapshot()
      setError("Realtime transport connect failed: \(error.localizedDescription)")
    }
  }

  private func disconnectRealtimeTransport(reason: String) async {
    guard let realtimeTransport else { return }
    guard wantsRealtimeStreaming || transportState != .disconnected else { return }

    wantsRealtimeStreaming = false
    isTransportDisconnecting = true
    realtimePCMUplinkWorker?.clearPendingFrames()
    snapshot.playbackState = "disconnecting"
    if
      snapshot.sessionState == .active ||
      snapshot.sessionState == .streaming ||
      snapshot.sessionState == .reconnecting ||
      snapshot.sessionState == .connecting
    {
      snapshot.sessionState = .disconnecting
    }
    publishSnapshot()

    await realtimeTransport.disconnect()
    transportState = .disconnected
    isTransportDisconnecting = false
    snapshot.sessionState = .idle
    snapshot.queryState = .idle
    snapshot.wakeState = .listening
    snapshot.playbackState = "idle"
    publishSnapshot()
    await logEvent(name: "transport.disconnect", fields: ["reason": .string(reason)])
  }

  private func handleTransportEvent(_ event: TransportEvent) async {
    guard isActivated else { return }
    switch event {
    case .audioReceived(let data, _):
      guard let playbackEngine else { return }
      do {
        try playbackEngine.appendPCMData(
          data,
          format: AssistantAudioFormat(codec: "pcm_s16le", sampleRate: 16_000, channels: 1)
        )
        snapshot.playbackChunkCount += 1
        snapshot.pendingPlaybackBufferCount = playbackEngine.pendingBufferCount
        snapshot.pendingPlaybackDurationMs = Int(playbackEngine.pendingBufferDurationMs)
        snapshot.playbackBackpressured = playbackEngine.isBackpressured
        snapshot.playbackState = "playing"
        publishSnapshot()
      } catch {
        setError(error.localizedDescription)
      }
    case .controlReceived(let control):
      await logEvent(
        name: "transport.control.received",
        fields: ["type": .string(control.type)]
      )
      await handleTransportControl(control)
    case .stateChanged(let state):
      transportState = state
      await logEvent(
        name: "transport.state.changed",
        fields: ["state": .string(transportStateLabel(state))]
      )
      switch state {
      case .disconnected:
        if isTransportDisconnecting || !wantsRealtimeStreaming {
          snapshot.sessionState = .idle
          snapshot.queryState = .idle
          snapshot.wakeState = .listening
          snapshot.playbackState = "idle"
        } else {
          snapshot.sessionState = .reconnecting
          wsReconnectAttempts += 1
          snapshot.playbackState = "streaming_reconnecting"
        }
      case .connecting:
        snapshot.sessionState = .connecting
        snapshot.queryState = .recording
        snapshot.playbackState = "streaming_connecting"
      case .connected:
        snapshot.sessionState = .streaming
        snapshot.queryState = .recording
        snapshot.wakeState = .triggered
        snapshot.playbackState = "streaming"
        await replayBufferedOutboundMessages()
      case .reconnecting:
        snapshot.sessionState = .reconnecting
        wsReconnectAttempts += 1
        snapshot.playbackState = "streaming_reconnecting"
      }
      publishSnapshot()
    case .error(let error):
      await logEvent(
        name: "transport.error",
        fields: ["kind": .string(transportErrorLabel(error))]
      )
      setError("Transport error: \(String(describing: error))")
      if !isTransportDisconnecting {
        snapshot.sessionState = .reconnecting
        snapshot.playbackState = "streaming_reconnecting"
        publishSnapshot()
      }
    }
  }

  private func handleTransportControl(_ control: TransportControlMessage) async {
    let type = control.type
    switch type {
    case "assistant.playback.control":
      guard let playbackEngine else { return }
      do {
        let payload = try decodeTransportPayload(control.payload, as: PlaybackControlPayload.self)
        playbackEngine.handlePlaybackControl(payload)
        snapshot.playbackState = payload.command.rawValue
        snapshot.pendingPlaybackBufferCount = playbackEngine.pendingBufferCount
        snapshot.pendingPlaybackDurationMs = Int(playbackEngine.pendingBufferDurationMs)
        snapshot.playbackBackpressured = playbackEngine.isBackpressured
        publishSnapshot()
      } catch {
        setError("Failed to decode playback control payload: \(error.localizedDescription)")
      }
    case "assistant.thinking":
      snapshot.playbackState = "thinking"
      publishSnapshot()
      triggerThinkingHaptic()
    case "session.state":
      if let state = extractString(control.payload["state"]) {
        snapshot.playbackState = "streaming.\(state)"
      }
      publishSnapshot()
    case "error":
      if let message = extractString(control.payload["message"]) {
        setError(message)
      } else {
        setError("Transport control error")
      }
    case "health.pong":
      if let pingSentAtMs = lastHealthPingSentAtMs {
        let latencyMs = max(0, dependencies.clock() - pingSentAtMs)
        wsRoundTripLatencyMs = Int(min(latencyMs, Int64(Int.max)))
      }
    default:
      if let detail = extractString(control.payload["detail"]) {
        snapshot.playbackState = "\(type):\(detail)"
      } else {
        snapshot.playbackState = type
      }
      publishSnapshot()
    }
  }

  private func extractString(_ value: TransportJSONValue?) -> String? {
    guard let value else { return nil }
    switch value {
    case .string(let string):
      return string
    case .number(let number):
      return String(number)
    case .bool(let bool):
      return String(bool)
    default:
      return nil
    }
  }

  private func decodeTransportPayload<Payload: Decodable>(
    _ payload: [String: TransportJSONValue],
    as type: Payload.Type
  ) throws -> Payload {
    let data = try JSONEncoder().encode(payload)
    return try JSONDecoder().decode(type, from: data)
  }

  func processRealtimePCMFrame(_ payload: Data, timestampMs: Int64) {
    guard isActivated else { return }
    guard wantsRealtimeStreaming else { return }
    guard transportState == .connected else { return }
    guard let realtimePCMUplinkWorker else { return }
    realtimePCMUplinkWorker.enqueue(payload: payload, timestampMs: timestampMs)
  }

  private func handleVisionUploadResult(_ result: VisionFrameUploadResult) {
    snapshot.photoState = result.success ? .idle : .failed
    if result.success {
      snapshot.photoUploadCount += 1
    } else {
      photosFailed += 1
      if let description = result.errorDescription {
        setError("[\(result.errorCode ?? "PHOTO_UPLOAD_FAILED")] \(description)")
      } else {
        setError(result.errorCode ?? "PHOTO_UPLOAD_FAILED")
      }
    }
    publishSnapshot()
  }

  private func setError(_ message: String) {
    snapshot.lastError = message
    publishSnapshot()
    Task {
      await logEvent(name: "error", fields: ["message": .string(message)])
    }
  }

  private func publishSnapshot() {
    onStatusUpdated?(snapshot)
  }

  private func triggerThinkingHaptic() {
    let generator = UIImpactFeedbackGenerator(style: .light)
    generator.impactOccurred()
  }

  /// Pre-computed single beep for wake word recognition (660 Hz, 120 ms).
  private static let wakeChimePCM: Data = {
    let sampleRate = 16000.0
    let frequency  = 660.0   // E5 — distinct from thinking chime (880 Hz)
    let duration   = 0.12    // 120 ms
    let amplitude  = 0.22
    let count = Int(sampleRate * duration)
    let fade  = max(1, Int(sampleRate * 0.008))

    var pcm = Data()
    for i in 0..<count {
      let t = Double(i) / sampleRate
      var env: Double = 1.0
      if i < fade {
        env = Double(i) / Double(fade)
      } else if i > count - fade {
        env = Double(count - i) / Double(fade)
      }
      let value = sin(2.0 * .pi * frequency * t) * amplitude * env
      var sample = Int16(clamping: Int(value * Double(Int16.max)))
      withUnsafeBytes(of: &sample) { pcm.append(contentsOf: $0) }
    }
    return pcm
  }()

  /// Play a single beep when wake word is detected.
  private func playWakeChime() {
    let format = AssistantAudioFormat(codec: "pcm_s16le", sampleRate: 16_000, channels: 1)
    do {
      try playbackEngine?.appendPCMData(Self.wakeChimePCM, format: format)
      debugLog("Wake chime scheduled")
    } catch {
      debugLog("Wake chime failed: \(error.localizedDescription)")
    }
  }

  private func configurePlaybackEngine() {
    guard let playbackEngine else { return }
    lastKnownPlaybackRoute = playbackEngine.currentRouteDescription()
    playbackEngine.onRouteChanged = { [weak self] route in
      self?.lastKnownPlaybackRoute = route
    }
    playbackEngine.onRouteIssue = { [weak self] message in
      guard let self else { return }
      self.setError(message)
      Task {
        await self.sendOutbound(
          type: .error,
          payload: RuntimeErrorPayload(
            code: "AUDIO_PLAYBACK_ROUTE_ERROR",
            retriable: true,
            message: message
          )
        )
      }
    }
  }

  private func startHealthLoop() {
    healthTask?.cancel()
    healthTask = Task { [weak self] in
      guard let self else { return }
      while !Task.isCancelled {
        await self.emitHealth(reason: "interval")
        do {
          try await Task.sleep(nanoseconds: healthIntervalMs * 1_000_000)
        } catch is CancellationError {
          return
        } catch {
          self.debugLog("Health loop terminated after sleep error: \(error.localizedDescription)")
          return
        }
      }
    }
  }

  private func stopHealthLoop() {
    healthTask?.cancel()
    healthTask = nil
  }

  private func emitHealth(reason: String) async {
    guard isActivated, activeSessionID != nil else { return }

    let pingSentAtMs = dependencies.clock()
    if await sendOutbound(type: .healthPing, payload: EmptyPayload()) {
      lastHealthPingSentAtMs = pingSentAtMs
    }

    let photoRate = effectivePhotoUploadRate()
    let reconnectAttempts = wsReconnectAttempts
    // Legacy batch metrics are retained for schema compatibility in strict
    // Phase 6 streaming mode; batch uploads are removed so both stay zero.
    let queryBundlesUploaded = 0
    let queryBundlesFailed = 0
    let pendingDurationMs = Int(playbackEngine?.pendingBufferDurationMs ?? 0)
    let backpressured = playbackEngine?.isBackpressured ?? false
    let videoBufferDurationMs = if let rollingVideoBuffer {
      Int(await rollingVideoBuffer.bufferedDurationMs)
    } else {
      0
    }
    let visionFrameDropCount = if let visionFrameUploader {
      await visionFrameUploader.consumeFrameDropCount()
    } else {
      0
    }
    let realtimeAudioFrameDropCount = realtimePCMUplinkWorker?.consumeDroppedFrameCount() ?? 0
    let frameDropCount = visionFrameDropCount + realtimeAudioFrameDropCount
    let nowMs = dependencies.clock()
    let elapsedMs = max(1, (lastHealthEmissionTsMs.map { nowMs - $0 } ?? Int64(healthIntervalMs)))
    lastHealthEmissionTsMs = nowMs
    let frameDropRate = (Double(frameDropCount) * 1000.0) / Double(elapsedMs)

    let statsPayload = HealthStatsPayload(
      wakeState: snapshot.wakeState,
      queryState: snapshot.queryState,
      queriesCompleted: snapshot.queryCount,
      queryBundlesUploaded: queryBundlesUploaded,
      queryBundlesFailed: queryBundlesFailed,
      photoUploadRateEffective: photoRate,
      photosUploaded: snapshot.photoUploadCount,
      photosFailed: photosFailed,
      videoBufferDurationMs: videoBufferDurationMs,
      audioBufferDurationMs: dependencies.audioBufferDurationProvider(),
      wsReconnectAttempts: max(wsReconnectAttempts, reconnectAttempts),
      wsRoundTripLatencyMs: wsRoundTripLatencyMs,
      frameDropCount: frameDropCount,
      frameDropRate: frameDropRate,
      sessionRestartCount: sessionRestartCount,
      pendingPlaybackDurationMs: pendingDurationMs,
      playbackBackpressured: backpressured,
      playbackRoute: lastKnownPlaybackRoute,
      appVersion: appVersion,
      deviceModel: deviceModel,
      osVersion: osVersion
    )
    await sendOutbound(type: .healthStats, payload: statsPayload)
    var healthFields: [String: JSONValue] = [
      "reason": .string(reason),
      "runtime_state": .string(runtimeState.rawValue),
      "photo_upload_rate_effective": .number(photoRate),
      "photos_uploaded": .number(Double(snapshot.photoUploadCount)),
      "photos_failed": .number(Double(photosFailed)),
      "queries_completed": .number(Double(snapshot.queryCount)),
      "query_bundles_uploaded": .number(Double(queryBundlesUploaded)),
      "query_bundles_failed": .number(Double(queryBundlesFailed)),
      "video_buffer_duration_ms": .number(Double(videoBufferDurationMs)),
      "audio_buffer_duration_ms": .number(Double(dependencies.audioBufferDurationProvider())),
      "ws_reconnect_attempts": .number(Double(max(wsReconnectAttempts, reconnectAttempts))),
      "ws_round_trip_latency_ms": .number(Double(wsRoundTripLatencyMs)),
      "frame_drop_count": .number(Double(frameDropCount)),
      "frame_drop_rate": .number(frameDropRate),
      "session_restart_count": .number(Double(sessionRestartCount)),
      "pending_playback_duration_ms": .number(Double(pendingDurationMs)),
      "playback_backpressured": .bool(backpressured),
      "playback_route": .string(lastKnownPlaybackRoute)
    ]
    if let appVersion {
      healthFields["app_version"] = .string(appVersion)
    }
    if let deviceModel {
      healthFields["device_model"] = .string(deviceModel)
    }
    if let osVersion {
      healthFields["os_version"] = .string(osVersion)
    }
    await logEvent(name: "health.stats", fields: healthFields)
  }

  private func effectivePhotoUploadRate() -> Double {
    let elapsedMs = max(1, dependencies.clock() - sessionActivatedAtMs)
    return (Double(snapshot.photoUploadCount) * 1000.0) / Double(elapsedMs)
  }

  private static func photoUploadIntervalMs(photoFps: Double) -> Int64 {
    let clamped = max(0.1, photoFps)
    return Int64(max(100, (1000.0 / clamped).rounded()))
  }

  private func transportStateLabel(_ state: TransportState) -> String {
    switch state {
    case .disconnected:
      return "disconnected"
    case .connecting:
      return "connecting"
    case .connected:
      return "connected"
    case .reconnecting:
      return "reconnecting"
    }
  }

  private func transportErrorLabel(_ error: TransportError) -> String {
    switch error {
    case .connectionFailed:
      return "connection_failed"
    case .authError:
      return "auth_error"
    case .timeout:
      return "timeout"
    case .protocolError:
      return "protocol_error"
    case .disconnected:
      return "disconnected"
    case .unknown:
      return "unknown"
    }
  }

  @discardableResult
  private func sendOutbound<Payload: Codable>(type: WSOutboundType, payload: Payload) async -> Bool {
    guard let sessionID = activeSessionID else { return false }
    guard let realtimeTransport else { return false }

    let payloadJSON: JSONValue
    do {
      payloadJSON = try encodePayloadAsJSONValue(payload)
    } catch {
      setError(error.localizedDescription)
      return false
    }

    guard let transportPayload = transportPayload(from: payloadJSON) else {
      setError("Outbound payload for \(type.rawValue) must encode to an object")
      return false
    }

    let controlMessage = TransportControlMessage(type: type.rawValue, payload: transportPayload)

    do {
      try await realtimeTransport.sendControl(controlMessage)
      return true
    } catch let transportError as TransportError {
      // Expected while socket is transitioning/disconnected; avoid spamming
      // app-level error state with redundant transport noise.
      if case .disconnected = transportError {
        enqueueOutboundMessage(type: type, sessionID: sessionID, payload: payloadJSON)
        return true
      }
      setError("Transport send failed: \(String(describing: transportError))")
      return false
    } catch {
      setError(error.localizedDescription)
      return false
    }
  }

  private func encodePayloadAsJSONValue<Payload: Codable>(_ payload: Payload) throws -> JSONValue {
    let data = try JSONEncoder().encode(payload)
    return try JSONDecoder().decode(JSONValue.self, from: data)
  }

  private func transportPayload(from payload: JSONValue) -> [String: TransportJSONValue]? {
    guard case .object(let object) = payload else { return nil }
    return object.mapValues(convertToTransportJSON)
  }

  private func convertToTransportJSON(_ value: JSONValue) -> TransportJSONValue {
    switch value {
    case .string(let string):
      return .string(string)
    case .number(let number):
      return .number(number)
    case .bool(let bool):
      return .bool(bool)
    case .object(let object):
      return .object(object.mapValues(convertToTransportJSON))
    case .array(let array):
      return .array(array.map(convertToTransportJSON))
    case .null:
      return .null
    }
  }

  private func enqueueOutboundMessage(type: WSOutboundType, sessionID: String, payload: JSONValue) {
    pruneOutboundMessageBuffer()
    outboundMessageBuffer.append(
      BufferedOutboundMessage(
        type: type,
        sessionID: sessionID,
        payload: payload,
        enqueuedAtMs: dependencies.clock()
      )
    )
    if outboundMessageBuffer.count > outboundMessageBufferLimit {
      let dropCount = outboundMessageBuffer.count - outboundMessageBufferLimit
      outboundMessageBuffer.removeFirst(dropCount)
      debugLog("Dropped \(dropCount) buffered outbound message(s) due to capacity")
    }
  }

  private func pruneOutboundMessageBuffer() {
    let nowMs = dependencies.clock()
    outboundMessageBuffer.removeAll { nowMs - $0.enqueuedAtMs > outboundMessageTTLms }
  }

  private func replayBufferedOutboundMessages() async {
    guard !outboundMessageBuffer.isEmpty else { return }
    guard case .connected = transportState else { return }
    guard let realtimeTransport else { return }

    pruneOutboundMessageBuffer()
    while !outboundMessageBuffer.isEmpty {
      let nextMessage = outboundMessageBuffer[0]
      guard let transportPayload = transportPayload(from: nextMessage.payload) else {
        outboundMessageBuffer.removeFirst()
        setError("Buffered outbound payload for \(nextMessage.type.rawValue) must encode to an object")
        continue
      }

      let controlMessage = TransportControlMessage(type: nextMessage.type.rawValue, payload: transportPayload)
      do {
        try await realtimeTransport.sendControl(controlMessage)
        outboundMessageBuffer.removeFirst()
      } catch let transportError as TransportError {
        if case .disconnected = transportError {
          return
        }
        outboundMessageBuffer.removeFirst()
        setError("Transport send failed: \(String(describing: transportError))")
      } catch {
        outboundMessageBuffer.removeFirst()
        setError(error.localizedDescription)
      }
    }
  }

  private func logEvent(name: String, queryID: String? = nil, fields: [String: JSONValue] = [:]) async {
    eventLogger.log(
      name: name,
      sessionID: activeSessionID ?? "unknown",
      queryID: queryID,
      fields: fields
    )
  }

  private func debugLog(_ message: String) {
#if DEBUG
    logger.debug("[SessionOrchestrator] \(message, privacy: .public)")
#endif
  }

  private static func resolveAppVersion() -> String? {
    let info = Bundle.main.infoDictionary
    let shortVersion = (info?["CFBundleShortVersionString"] as? String)?
      .trimmingCharacters(in: .whitespacesAndNewlines)
    guard let shortVersion, !shortVersion.isEmpty else { return nil }
    return shortVersion
  }

  private static func resolveDeviceModel() -> String? {
    var systemInfo = utsname()
    uname(&systemInfo)
    let machineMirror = Mirror(reflecting: systemInfo.machine)
    let identifier = machineMirror.children.reduce(into: "") { partialResult, element in
      guard let value = element.value as? Int8, value != 0 else { return }
      partialResult.append(Character(UnicodeScalar(UInt8(value))))
    }
    return identifier.isEmpty ? nil : identifier
  }

  private static func resolveOSVersion() -> String? {
    let version = UIDevice.current.systemVersion.trimmingCharacters(in: .whitespacesAndNewlines)
    return version.isEmpty ? nil : version
  }
}

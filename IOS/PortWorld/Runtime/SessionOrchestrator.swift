import AVFAudio
import Darwin
import Foundation
import OSLog
import UIKit

private final class RealtimePCMUplinkWorker: @unchecked Sendable {
  struct Frame {
    let payload: Data
    let timestampMs: Int64
  }

  struct MetricsSnapshot {
    let enqueuedFrames: Int
    let sendAttempts: Int
    let sentFrames: Int
    let sendFailures: Int
    let lastSendError: String?
  }

  private let capacity: Int
  private let sendFrame: @Sendable (Frame) async throws -> Void
  private let onSendAttempt: @Sendable (Frame) -> Void
  private let onSendSuccess: @Sendable (Frame) -> Void
  private let onSendError: @Sendable (Error) -> Void
  private let lock = NSLock()
  private let drainSignal: AsyncStream<Void>
  private let drainSignalContinuation: AsyncStream<Void>.Continuation
  private var drainTask: Task<Void, Never>?
  private var pendingFrames: [Frame] = []
  private var droppedFrameCountSinceLastRead = 0
  private var hasPendingDrainSignal = false
  private var isActive = true
  private var enqueuedFrames = 0
  private var sendAttempts = 0
  private var sentFrames = 0
  private var sendFailures = 0
  private var lastSendError: String?

  init(
    capacity: Int,
    sendFrame: @escaping @Sendable (Frame) async throws -> Void,
    onSendAttempt: @escaping @Sendable (Frame) -> Void,
    onSendSuccess: @escaping @Sendable (Frame) -> Void,
    onSendError: @escaping @Sendable (Error) -> Void
  ) {
    self.capacity = max(1, capacity)
    self.sendFrame = sendFrame
    self.onSendAttempt = onSendAttempt
    self.onSendSuccess = onSendSuccess
    self.onSendError = onSendError
    let stream = AsyncStream.makeStream(of: Void.self)
    self.drainSignal = stream.stream
    self.drainSignalContinuation = stream.continuation
    self.drainTask = Task.detached { [weak self] in
      await self?.runDrainLoop()
    }
  }

  func enqueue(payload: Data, timestampMs: Int64) {
    lock.lock()
    guard isActive else { lock.unlock(); return }
    enqueuedFrames += 1
    if pendingFrames.count >= capacity {
      pendingFrames.removeFirst()
      droppedFrameCountSinceLastRead += 1
    }
    pendingFrames.append(Frame(payload: payload, timestampMs: timestampMs))
    let shouldSignal = !hasPendingDrainSignal
    if shouldSignal { hasPendingDrainSignal = true }
    lock.unlock()
    if shouldSignal {
      drainSignalContinuation.yield(())
    }
  }

  func consumeDroppedFrameCount() -> Int {
    lock.lock()
    let count = droppedFrameCountSinceLastRead
    droppedFrameCountSinceLastRead = 0
    lock.unlock()
    return count
  }

  func metricsSnapshot() -> MetricsSnapshot {
    lock.lock()
    let snapshot = MetricsSnapshot(
      enqueuedFrames: enqueuedFrames,
      sendAttempts: sendAttempts,
      sentFrames: sentFrames,
      sendFailures: sendFailures,
      lastSendError: lastSendError
    )
    lock.unlock()
    return snapshot
  }

  func stopAndReset() {
    lock.lock()
    isActive = false
    pendingFrames.removeAll(keepingCapacity: false)
    hasPendingDrainSignal = false
    droppedFrameCountSinceLastRead = 0
    enqueuedFrames = 0
    sendAttempts = 0
    sentFrames = 0
    sendFailures = 0
    lastSendError = nil
    lock.unlock()
    drainSignalContinuation.finish()
    drainTask?.cancel()
    drainTask = nil
  }

  func clearPendingFrames() {
    lock.lock()
    pendingFrames.removeAll(keepingCapacity: false)
    hasPendingDrainSignal = false
    lock.unlock()
  }

  private func runDrainLoop() async {
    for await _ in drainSignal {
      while let frame = popNextFrameForDrain() {
        recordSendAttempt()
        onSendAttempt(frame)
        do {
          try await sendFrame(frame)
          recordSendSuccess()
          onSendSuccess(frame)
        } catch {
          recordSendFailure(error)
          onSendError(error)
        }
      }
      if !isWorkerActive() {
        return
      }
    }
  }

  private func popNextFrameForDrain() -> Frame? {
    lock.lock()
    guard isActive else {
      pendingFrames.removeAll(keepingCapacity: false)
      hasPendingDrainSignal = false
      lock.unlock()
      return nil
    }
    guard !pendingFrames.isEmpty else {
      hasPendingDrainSignal = false
      lock.unlock()
      return nil
    }
    let frame = pendingFrames.removeFirst()
    lock.unlock()
    return frame
  }

  private func isWorkerActive() -> Bool {
    lock.lock()
    let active = isActive
    lock.unlock()
    return active
  }

  private func recordSendAttempt() {
    lock.lock()
    sendAttempts += 1
    lock.unlock()
  }

  private func recordSendSuccess() {
    lock.lock()
    sentFrames += 1
    lock.unlock()
  }

  private func recordSendFailure(_ error: Error) {
    lock.lock()
    sendFailures += 1
    lastSendError = String(describing: error)
    lock.unlock()
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
    let suppressSpeakerRouteErrors: Bool

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
      eventLogger: EventLoggerProtocol,
      suppressSpeakerRouteErrors: Bool = false
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
      self.suppressSpeakerRouteErrors = suppressSpeakerRouteErrors
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
      eventLogger: EventLoggerProtocol,
      suppressSpeakerRouteErrors: Bool = false
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
        eventLogger: eventLogger,
        suppressSpeakerRouteErrors: suppressSpeakerRouteErrors
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
        eventLogger: EventLogger(),
        suppressSpeakerRouteErrors: false
      )
    }
  }

  struct StatusSnapshot {
    var assistantRuntimeState: AssistantRuntimeState = .inactive
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

  private struct BufferedRealtimePCMFrame {
    let payload: Data
    let timestampMs: Int64
  }

  var onStatusUpdated: ((StatusSnapshot) -> Void)?

  private let config: RuntimeConfig
  private let dependencies: Dependencies
  private let eventLogger: EventLoggerProtocol
  private let healthIntervalMs: UInt64 = 10_000
  private static let realtimePCMUplinkQueueLimit = 32
  private static let realtimePCMUplinkPrerollDurationMs: Int64 = 2_000
  private static let realtimePCMUplinkPrerollFrameLimit = 48
  private static let realtimeUplinkAckTimeoutMs: UInt64 = 4_000
  private static let realtimeSessionType = "realtime"
  private static let realtimeUplinkEncoding = "pcm_s16le"
  private static let realtimeUplinkChannels = 1
  private static let realtimeUplinkSampleRate = 24_000
  private static let realtimeDebugBinarySweepSizes = [16, 64, 256, 512, 1_024, 2_048, 3_072, 4_080]
  private static let realtimeUplinkAckTimeoutReconnectEnabled: Bool = {
#if DEBUG
    false
#else
    true
#endif
  }()
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
  private var pendingNetworkTransitions: [Bool] = []
  private var networkTransitionTask: Task<Void, Never>?
  /// Counts full session restarts (deactivate+activate cycles), persists across activations.
  /// Distinguishes from wsReconnectAttempts which tracks transport-level reconnects within a session.
  private var sessionRestartCount = 0
  private var sessionActivatedAtMs: Int64 = 0
  private var lastHealthEmissionTsMs: Int64?
  private var lastHealthPingSentAtMs: Int64?
  private var wsRoundTripLatencyMs = 0
  private var lastRealtimeConnectedAtMs: Int64?
  private var lastKnownPlaybackRoute = "unknown"
  private var healthTask: Task<Void, Never>?
  private var transportEventsTask: Task<Void, Never>?
  private var transportState: TransportState = .disconnected
  private var wantsRealtimeStreaming = false
  private var isRealtimeUplinkActive = false
  private var isTransportDisconnecting = false
  private var realtimeSessionReady = false
  private var realtimeServerSessionReady = false
  private var realtimeUplinkProbePending = false
  private var realtimeUplinkProbeAcknowledged = false
  private var realtimeDebugPayloadSweepSent = false
  private var pendingSessionActivateForConnection = false
  private var didLogRealtimeUplinkFirstFrame = false
  private var realtimePCMUplinkWorker: RealtimePCMUplinkWorker?
  private var realtimePrerollFrames: [BufferedRealtimePCMFrame] = []
  private var realtimePrerollDroppedFrameCount = 0
  private var realtimeBackendConfirmedFrames = 0
  private var realtimeBackendConfirmedBytes = 0
  private var realtimeUplinkConfirmed = false
  private var realtimeFirstAudioSendAttemptAtMs: Int64?
  private var realtimeLastUplinkAckAtMs: Int64?
  private var realtimeUplinkAckWatchdogTask: Task<Void, Never>?
  private var realtimeUplinkAckRecoveryAttempted = false
  private var realtimeUplinkTerminalFailureReported = false
  private var realtimeTransportSendSuccessLogCount = 0
  private var didEmitRealtimeWorkerPathMarker = false
  private var outboundMessageBuffer: [BufferedOutboundMessage] = []
  private let outboundMessageBufferLimit = 20
  private let outboundMessageTTLms: Int64 = 60_000
  private let repeatedErrorLogCooldownMs: Int64 = 1_000
  private var lastLoggedErrorMessage = ""
  private var lastLoggedErrorTsMs: Int64 = 0
  private var lastRealtimePCMDeferralLogKey = ""
  private let logger = Logger(subsystem: "PortWorld", category: "SessionOrchestrator")
  private var realtimeDiagnosticsEnabled: Bool {
    config.realtimeDiagnosticsEnabled
  }

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
    let logger = self.logger
    let sendLiveFrame: @Sendable (RealtimePCMUplinkWorker.Frame) async throws -> Void

    if let gatewayTransport = realtimeTransport as? GatewayTransport {
      sendLiveFrame = { frame in
        logger.warning(
          "worker_send_live_audio dispatch=gateway_transport payload_bytes=\(frame.payload.count, privacy: .public) timestamp_ms=\(frame.timestampMs, privacy: .public)"
        )
        try await gatewayTransport.sendLiveAudio(frame.payload, timestampMs: frame.timestampMs)
      }
    } else {
      sendLiveFrame = { frame in
        logger.warning(
          "worker_send_live_audio dispatch=protocol_fallback payload_bytes=\(frame.payload.count, privacy: .public) timestamp_ms=\(frame.timestampMs, privacy: .public)"
        )
        try await realtimeTransport.sendLiveAudio(frame.payload, timestampMs: frame.timestampMs)
      }
    }

    return RealtimePCMUplinkWorker(
      capacity: Self.realtimePCMUplinkQueueLimit,
      sendFrame: { [weak self] frame in
        guard !frame.payload.isEmpty else { return }
        if let self {
          try await self.emitRealtimeWorkerPathMarkerIfNeeded(
            using: realtimeTransport,
            frame: frame
          )
        }
        try await sendLiveFrame(frame)
      },
      onSendAttempt: { [weak self] _ in
        Task { @MainActor [weak self] in
          self?.handleRealtimeAudioSendAttempt()
        }
      },
      onSendSuccess: { [weak self] frame in
        Task { @MainActor [weak self] in
          await self?.handleRealtimeAudioSendSuccess(frame)
        }
      },
      onSendError: { [weak self] error in
        Task { @MainActor [weak self] in
          self?.setError("Failed to send realtime audio: \(String(describing: error))")
        }
      }
    )
  }

  private func emitRealtimeWorkerPathMarkerIfNeeded(
    using realtimeTransport: RealtimeTransport,
    frame: RealtimePCMUplinkWorker.Frame
  ) async throws {
    guard !didEmitRealtimeWorkerPathMarker else { return }
    didEmitRealtimeWorkerPathMarker = true
    try await realtimeTransport.sendControl(
      TransportControlMessage(
        type: "debug.worker_live_audio_path",
        payload: [
          "mode": .string("worker_send_frame"),
          "payload_bytes": .number(Double(frame.payload.count)),
          "timestamp_ms": .number(Double(frame.timestampMs))
        ]
      )
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
    activeSessionID = nil

    snapshot.sessionState = .idle
    snapshot.sessionID = "-"
    snapshot.wakeState = .listening
    snapshot.queryState = .idle
    snapshot.playbackState = "starting"
    snapshot.assistantRuntimeState = .armedListening
    snapshot.wakeEngine = primaryWakeEngine.engineKind.rawValue
    snapshot.speechAuthorization = primaryWakeEngine.currentAuthorizationStatus().rawValue
    publishSnapshot()
    runtimeState = .foregroundActive
    wantsRealtimeStreaming = false
    isRealtimeUplinkActive = false
    isTransportDisconnecting = false
    transportState = .disconnected
    realtimeSessionReady = false
    pendingSessionActivateForConnection = false
    photosFailed = 0
    wsReconnectAttempts = 0
    reconnectOnNetworkRestore = false
    lastHealthEmissionTsMs = nil
    lastHealthPingSentAtMs = nil
    wsRoundTripLatencyMs = 0
    lastRealtimeConnectedAtMs = nil
    sessionActivatedAtMs = dependencies.clock()
    didLogRealtimeUplinkFirstFrame = false
    didEmitRealtimeWorkerPathMarker = false
    lastRealtimePCMDeferralLogKey = ""
    resetRealtimeUplinkState()
    clearRealtimePrerollBuffer()

    await dependencies.startStream()
    if let visionFrameUploader {
      await visionFrameUploader.start()
    }
    manualWakeEngine.startListening()
    if primaryWakeEngine !== manualWakeEngine {
      _ = await primaryWakeEngine.requestAuthorizationIfNeeded()
      primaryWakeEngine.startListening()
    }

    snapshot.sessionState = .idle
    snapshot.playbackState = "idle"
    publishSnapshot()

    startHealthLoop()

    await logEvent(name: "session.activate")
    await emitHealth(reason: "activate")
  }

  func deactivate() async {
    guard isActivated else { return }

    snapshot.assistantRuntimeState = .deactivating
    await sendOutbound(type: .sessionDeactivate, payload: EmptyPayload())
    wantsRealtimeStreaming = false
    isRealtimeUplinkActive = false
    isTransportDisconnecting = true
    realtimeSessionReady = false
    pendingSessionActivateForConnection = false
    cancelRealtimeUplinkAckWatchdog()
    clearRealtimePrerollBuffer()
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
    lastRealtimeConnectedAtMs = nil
    realtimeSessionReady = false
    pendingSessionActivateForConnection = false
    didLogRealtimeUplinkFirstFrame = false
    didEmitRealtimeWorkerPathMarker = false
    lastRealtimePCMDeferralLogKey = ""
    resetRealtimeUplinkState()
    clearRealtimePrerollBuffer()
    realtimeTransport = nil
    visionFrameUploader = nil
    rollingVideoBuffer = nil
    playbackEngine = nil

    snapshot.sessionState = .ended
    snapshot.assistantRuntimeState = .inactive
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
    let lastKnownAvailability = pendingNetworkTransitions.last ?? isNetworkAvailable
    guard lastKnownAvailability != isAvailable else { return }
    pendingNetworkTransitions.append(isAvailable)
    guard networkTransitionTask == nil else { return }

    networkTransitionTask = Task { @MainActor [weak self] in
      guard let self else { return }
      defer { self.networkTransitionTask = nil }
      await self.processPendingNetworkTransitions()
    }
  }

  private func processPendingNetworkTransitions() async {
    while !pendingNetworkTransitions.isEmpty {
      let nextAvailability = pendingNetworkTransitions.removeFirst()
      await applyNetworkAvailabilityTransition(nextAvailability)
    }
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
    guard isActivated, isRealtimeUplinkActive else { return }
    _ = timestampMs
  }

  func triggerWakeForTesting() {
    manualWakeEngine.triggerManualWake(timestampMs: dependencies.clock())
  }

  func endConversation(reason: String = "manual_end_conversation") async {
    guard isActivated else { return }
    guard isRealtimeUplinkActive || transportState != .disconnected || wantsRealtimeStreaming else { return }

    _ = await sendOutbound(type: .sessionEndTurn, payload: EmptyPayload())
    isRealtimeUplinkActive = false
    resetRealtimeUplinkTurnState()
    clearRealtimePrerollBuffer()
    playbackEngine?.cancelResponse()
    snapshot.queryState = .idle
    snapshot.wakeState = .listening
    snapshot.playbackState = "standby_connecting"
    snapshot.assistantRuntimeState = .connectingConversation
    publishSnapshot()
    await disconnectRealtimeTransport(reason: reason)
    snapshot.assistantRuntimeState = .armedListening
    publishSnapshot()
  }

#if DEBUG
  func triggerSleepForTesting(phrase: String? = nil, timestampMs: Int64? = nil) async {
    await handleSleepDetected(
      WakeWordDetectionEvent(
        wakePhrase: phrase ?? config.sleepPhrase,
        timestampMs: timestampMs ?? dependencies.clock(),
        engine: "test",
        confidence: nil
      )
    )
  }
#endif

  var wakeEngineType: String {
    primaryWakeEngine.engineKind.rawValue
  }

  private func handleWakeDetected(_ event: WakeWordDetectionEvent) {
    guard isActivated else { return }
    guard !isRealtimeUplinkActive else { return }

    let sessionID = "sess_\(UUID().uuidString)"
    activeSessionID = sessionID

    // Cancel any in-flight playback from previous response to avoid queue buildup
    playbackEngine?.cancelResponse()
    resetRealtimeUplinkTurnState()
    clearRealtimePrerollBuffer()
    lastRealtimePCMDeferralLogKey = ""
    isRealtimeUplinkActive = true

    // Immediate audio feedback: single beep so the user knows wake word was heard
    playWakeChime()

    snapshot.wakeState = .triggered
    snapshot.wakeCount += 1
    snapshot.sessionID = sessionID
    snapshot.queryID = "-"
    snapshot.queryState = .recording
    snapshot.assistantRuntimeState = .connectingConversation
    snapshot.playbackState = transportState == .connected ? "streaming_waiting_ready" : "streaming_connecting"
    publishSnapshot()

    Task { [weak self] in
      await self?.connectRealtimeTransport(reason: "wake_detected")
    }

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
    }
  }

  private func handleSleepDetected(_ event: WakeWordDetectionEvent) async {
    guard isActivated else { return }
    guard isRealtimeUplinkActive else { return }
    guard let lastRealtimeConnectedAtMs else {
      await logEvent(
        name: "sleepword.ignored",
        fields: [
          "phrase": .string(event.wakePhrase),
          "reason": .string("not_connected")
        ]
      )
      return
    }

    let elapsedActiveStreamMs = max(0, dependencies.clock() - lastRealtimeConnectedAtMs)
    if elapsedActiveStreamMs < config.sleepWordMinActiveStreamMs {
      await logEvent(
        name: "sleepword.ignored",
        fields: [
          "phrase": .string(event.wakePhrase),
          "reason": .string("min_stream_duration"),
          "elapsed_active_stream_ms": .number(Double(elapsedActiveStreamMs)),
          "minimum_required_ms": .number(Double(config.sleepWordMinActiveStreamMs))
        ]
      )
      return
    }

    await endConversation(reason: "sleepword_detected")
    await logEvent(
      name: "sleepword.detected",
      fields: [
        "phrase": .string(event.wakePhrase),
        "did_end_turn": .bool(true)
      ]
    )
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
      snapshot.assistantRuntimeState = .connectingConversation
      snapshot.playbackState = "waiting_for_network"
      publishSnapshot()
      return
    }

    wantsRealtimeStreaming = true
    isTransportDisconnecting = false
    realtimeSessionReady = false
    pendingSessionActivateForConnection = true
    reconnectOnNetworkRestore = false
    lastRealtimeConnectedAtMs = nil
    resetRealtimeUplinkState(keepRecoveryAttempt: realtimeUplinkAckRecoveryAttempted)
    clearRealtimePrerollBufferIfStale()
    transportState = .connecting
    snapshot.sessionState = .connecting
    snapshot.assistantRuntimeState = .connectingConversation
    snapshot.queryState = isRealtimeUplinkActive ? .recording : .idle
    snapshot.wakeState = isRealtimeUplinkActive ? .triggered : .listening
    snapshot.playbackState = isRealtimeUplinkActive ? "streaming_connecting" : "standby_connecting"
    publishSnapshot()

    let transportConfig = TransportConfig(
      endpoint: config.webSocketURL,
      sessionId: activeSessionID,
      audioFormat: AudioStreamFormat(
        sampleRate: Self.realtimeUplinkSampleRate,
        channels: Self.realtimeUplinkChannels,
        encoding: Self.realtimeUplinkEncoding
      ),
      headers: config.requestHeaders
    )

    do {
      try await realtimeTransport.connect(config: transportConfig)
      await logEvent(name: "transport.connect", fields: ["reason": .string(reason)])
    } catch {
      isRealtimeUplinkActive = false
      transportState = .disconnected
      isTransportDisconnecting = false
      realtimeSessionReady = false
      pendingSessionActivateForConnection = false
      lastRealtimeConnectedAtMs = nil
      resetRealtimeUplinkState()
      clearRealtimePrerollBuffer()
      snapshot.sessionState = .idle
      snapshot.assistantRuntimeState = .armedListening
      snapshot.queryState = .idle
      snapshot.playbackState = "idle"
      publishSnapshot()
      setError("Realtime transport connect failed: \(error.localizedDescription)")
    }
  }

  private func disconnectRealtimeTransport(reason: String) async {
    guard let realtimeTransport else { return }
    guard wantsRealtimeStreaming || transportState != .disconnected else { return }
    let shouldPreservePrerollFrames =
      reason == "network_unavailable" || reason == "uplink_ack_timeout"

    wantsRealtimeStreaming = false
    isTransportDisconnecting = true
    realtimeSessionReady = false
    pendingSessionActivateForConnection = false
    realtimePCMUplinkWorker?.clearPendingFrames()
    cancelRealtimeUplinkAckWatchdog()
    if !shouldPreservePrerollFrames {
      clearRealtimePrerollBuffer()
    }
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
    realtimeSessionReady = false
    pendingSessionActivateForConnection = false
    lastRealtimeConnectedAtMs = nil
    resetRealtimeUplinkState(keepRecoveryAttempt: realtimeUplinkAckRecoveryAttempted)
    snapshot.sessionState = .idle
    snapshot.assistantRuntimeState = isActivated ? .armedListening : .inactive
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
          format: AssistantAudioFormat(codec: "pcm_s16le", sampleRate: 24_000, channels: 1)
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
    case .closed(let closeInfo):
      var fields: [String: JSONValue] = [
        "connection_id": .number(Double(closeInfo.connectionID))
      ]
      if let code = closeInfo.code {
        fields["code"] = .number(Double(code))
      }
      if let reason = closeInfo.reason, !reason.isEmpty {
        fields["reason"] = .string(reason)
      }
      await logEvent(name: "transport.socket.closed", fields: fields)
    case .stateChanged(let state):
      transportState = state
      await logEvent(
        name: "transport.state.changed",
        fields: ["state": .string(transportStateLabel(state))]
      )
      switch state {
      case .disconnected:
        realtimeSessionReady = false
        lastRealtimeConnectedAtMs = nil
        cancelRealtimeUplinkAckWatchdog()
        resetRealtimeUplinkState(keepRecoveryAttempt: realtimeUplinkAckRecoveryAttempted)
        if isTransportDisconnecting || !wantsRealtimeStreaming {
          pendingSessionActivateForConnection = false
          snapshot.sessionState = .idle
          snapshot.assistantRuntimeState = isActivated ? .armedListening : .inactive
          snapshot.queryState = isRealtimeUplinkActive ? .recording : .idle
          snapshot.wakeState = isRealtimeUplinkActive ? .triggered : .listening
          snapshot.playbackState = isRealtimeUplinkActive ? "streaming_connecting" : "idle"
        } else {
          pendingSessionActivateForConnection = true
          snapshot.sessionState = .reconnecting
          snapshot.assistantRuntimeState = .connectingConversation
          wsReconnectAttempts += 1
          snapshot.playbackState = isRealtimeUplinkActive ? "streaming_reconnecting" : "standby_reconnecting"
        }
      case .connecting:
        realtimeSessionReady = false
        pendingSessionActivateForConnection = true
        cancelRealtimeUplinkAckWatchdog()
        resetRealtimeUplinkState(keepRecoveryAttempt: realtimeUplinkAckRecoveryAttempted)
        snapshot.sessionState = .connecting
        snapshot.assistantRuntimeState = .connectingConversation
        snapshot.queryState = isRealtimeUplinkActive ? .recording : .idle
        snapshot.wakeState = isRealtimeUplinkActive ? .triggered : .listening
        snapshot.playbackState = isRealtimeUplinkActive ? "streaming_connecting" : "standby_connecting"
      case .connected:
        realtimeSessionReady = false
        lastRealtimeConnectedAtMs = dependencies.clock()
        cancelRealtimeUplinkAckWatchdog()
        resetRealtimeUplinkState(keepRecoveryAttempt: realtimeUplinkAckRecoveryAttempted)
        snapshot.sessionState = isRealtimeUplinkActive ? .streaming : .active
        snapshot.assistantRuntimeState = isRealtimeUplinkActive ? .connectingConversation : .armedListening
        snapshot.queryState = isRealtimeUplinkActive ? .recording : .idle
        snapshot.wakeState = isRealtimeUplinkActive ? .triggered : .listening
        snapshot.playbackState = isRealtimeUplinkActive ? "streaming_waiting_ready" : "standby_ready"
        let didSendActivate = await sendSessionActivateForCurrentConnection()
        if didSendActivate {
          await replayBufferedOutboundMessages()
        }
      case .reconnecting:
        realtimeSessionReady = false
        pendingSessionActivateForConnection = true
        cancelRealtimeUplinkAckWatchdog()
        resetRealtimeUplinkState(keepRecoveryAttempt: realtimeUplinkAckRecoveryAttempted)
        snapshot.sessionState = .reconnecting
        snapshot.assistantRuntimeState = .connectingConversation
        wsReconnectAttempts += 1
        snapshot.queryState = isRealtimeUplinkActive ? .recording : .idle
        snapshot.wakeState = isRealtimeUplinkActive ? .triggered : .listening
        snapshot.playbackState = isRealtimeUplinkActive ? "streaming_reconnecting" : "standby_reconnecting"
      }
      publishSnapshot()
    case .error(let error):
      await logEvent(
        name: "transport.error",
        fields: ["kind": .string(transportErrorLabel(error))]
      )
      setError("Transport error: \(String(describing: error))")
      if !isTransportDisconnecting, wantsRealtimeStreaming {
        snapshot.sessionState = .reconnecting
        snapshot.assistantRuntimeState = .connectingConversation
        snapshot.playbackState = isRealtimeUplinkActive ? "streaming_reconnecting" : "standby_reconnecting"
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
        let wasWaitingForReady =
          snapshot.playbackState == "streaming_waiting_ready" ||
          snapshot.playbackState == "standby_ready"
        snapshot.playbackState = isRealtimeUplinkActive ? "streaming.\(state)" : "standby.\(state)"
        let isReadyState = isRealtimeSessionReadyState(state)
        realtimeServerSessionReady = isReadyState
        realtimeSessionReady = false
        if !isReadyState {
          debugLog("Realtime session reported non-ready state: \(state)")
          realtimeUplinkProbePending = false
        }
        if isReadyState {
          if await sendRealtimeUplinkProbeIfNeeded() {
            snapshot.playbackState = "streaming_probing_uplink"
          } else {
            markRealtimeSessionReadyAfterServerReady(wasWaitingForReady: wasWaitingForReady)
          }
        } else if transportState == .connected {
          snapshot.playbackState = isRealtimeUplinkActive ? "streaming_waiting_ready" : "standby_ready"
        }
      } else {
        debugLog("Realtime session state payload missing string 'state' field")
      }
      publishSnapshot()
    case "transport.uplink.ack":
      do {
        let payload = try decodeTransportPayload(control.payload, as: RealtimeUplinkAckPayload.self)
        let wasConfirmed = realtimeUplinkConfirmed
        if payload.probeAcknowledged == true {
          realtimeUplinkProbePending = false
          realtimeUplinkProbeAcknowledged = true
          await logEvent(name: "realtime.uplink.probe_acknowledged")
          markRealtimeSessionReadyAfterServerReady(
            wasWaitingForReady: true,
            flushBufferedFrames: false
          )
          await sendDebugPayloadSweepIfNeeded()
          flushBufferedRealtimePCMFramesIfReady()
        }
        if payload.framesReceived > 0 {
          realtimeBackendConfirmedFrames = max(realtimeBackendConfirmedFrames, payload.framesReceived)
          realtimeBackendConfirmedBytes = max(realtimeBackendConfirmedBytes, payload.bytesReceived)
          realtimeUplinkConfirmed = true
          realtimeLastUplinkAckAtMs = dependencies.clock()
          cancelRealtimeUplinkAckWatchdog()
        }
        if realtimeUplinkConfirmed, !wasConfirmed {
          let ackLatencyMs = realtimeUplinkAckLatencyMs()
          Task { [weak self] in
            await self?.logEvent(
              name: "realtime.uplink.ack_received",
              fields: [
                "frames_received": .number(Double(payload.framesReceived)),
                "bytes_received": .number(Double(payload.bytesReceived)),
                "ack_latency_ms": .number(Double(ackLatencyMs ?? 0))
              ]
            )
          }
        }
        if
          snapshot.playbackState == "streaming_waiting_ready" ||
          snapshot.playbackState == "streaming_ready" ||
          snapshot.playbackState == "streaming_probing_uplink"
        {
          snapshot.playbackState = "streaming_uplink_confirmed"
          publishSnapshot()
        }
      } catch {
        setError("Failed to decode realtime uplink ack payload: \(error.localizedDescription)")
      }
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
    guard isActivated else {
      logRealtimePCMDeferralOnce("session_not_activated")
      return
    }
    guard isRealtimeUplinkActive else {
      logRealtimePCMDeferralOnce("streaming_not_requested")
      return
    }
    guard !payload.isEmpty else {
      logRealtimePCMDeferralOnce("empty_payload")
      return
    }
    guard let realtimePCMUplinkWorker else {
      logRealtimePCMDeferralOnce("uplink_worker_unavailable")
      return
    }

    if transportState != .connected {
      bufferRealtimePCMFrame(payload, timestampMs: timestampMs)
      logRealtimePCMDeferralOnce("transport_\(transportStateLabel(transportState))")
      return
    }

    if !realtimeSessionReady {
      bufferRealtimePCMFrame(payload, timestampMs: timestampMs)
      logRealtimePCMDeferralOnce("session_not_ready")
      return
    }

    lastRealtimePCMDeferralLogKey = ""
    flushBufferedRealtimePCMFramesIfReady()
    enqueueRealtimePCMFrameForSend(payload, timestampMs: timestampMs, worker: realtimePCMUplinkWorker)
  }

  private func enqueueRealtimePCMFrameForSend(
    _ payload: Data,
    timestampMs: Int64,
    worker: RealtimePCMUplinkWorker
  ) {
    guard !payload.isEmpty else { return }
    if !didLogRealtimeUplinkFirstFrame {
      didLogRealtimeUplinkFirstFrame = true
      let bytesPerSample = 2
      let chunkBytes = payload.count
      let chunkSamplesPerChannel = chunkBytes / (bytesPerSample * Self.realtimeUplinkChannels)
      let chunkDurationMs = Int(
        (Double(chunkSamplesPerChannel) / Double(Self.realtimeUplinkSampleRate)) * 1000.0
      )
      Task { [weak self] in
        await self?.logEvent(
          name: "realtime.uplink.first_frame",
          fields: [
            "encoding": .string(Self.realtimeUplinkEncoding),
            "channels": .number(Double(Self.realtimeUplinkChannels)),
            "sample_rate": .number(Double(Self.realtimeUplinkSampleRate)),
            "chunk_bytes": .number(Double(chunkBytes)),
            "chunk_samples_per_channel": .number(Double(chunkSamplesPerChannel)),
            "chunk_duration_ms": .number(Double(chunkDurationMs)),
            "timestamp_ms": .number(Double(timestampMs))
          ]
        )
      }
    }
    worker.enqueue(payload: payload, timestampMs: timestampMs)
  }

  private func bufferRealtimePCMFrame(_ payload: Data, timestampMs: Int64) {
    realtimePrerollFrames.append(
      BufferedRealtimePCMFrame(payload: payload, timestampMs: timestampMs)
    )
    pruneBufferedRealtimePCMFrames(relativeTo: timestampMs)
  }

  private func pruneBufferedRealtimePCMFrames(relativeTo timestampMs: Int64) {
    let minimumTimestampMs = timestampMs - Self.realtimePCMUplinkPrerollDurationMs
    let previousCount = realtimePrerollFrames.count
    realtimePrerollFrames.removeAll { $0.timestampMs < minimumTimestampMs }
    realtimePrerollDroppedFrameCount += max(0, previousCount - realtimePrerollFrames.count)

    if realtimePrerollFrames.count > Self.realtimePCMUplinkPrerollFrameLimit {
      let overflow = realtimePrerollFrames.count - Self.realtimePCMUplinkPrerollFrameLimit
      realtimePrerollFrames.removeFirst(overflow)
      realtimePrerollDroppedFrameCount += overflow
    }
  }

  private func flushBufferedRealtimePCMFramesIfReady() {
    guard transportState == .connected, realtimeSessionReady else { return }
    guard let realtimePCMUplinkWorker else { return }
    guard !realtimePrerollFrames.isEmpty else { return }

    let bufferedFrames = realtimePrerollFrames
    realtimePrerollFrames.removeAll(keepingCapacity: false)
    for frame in bufferedFrames {
      enqueueRealtimePCMFrameForSend(
        frame.payload,
        timestampMs: frame.timestampMs,
        worker: realtimePCMUplinkWorker
      )
    }
  }

  private func sendRealtimeUplinkProbeIfNeeded() async -> Bool {
    guard realtimeDiagnosticsEnabled else { return false }
    guard realtimeServerSessionReady else { return false }
    guard !realtimeSessionReady else { return false }
    guard !realtimeUplinkProbeAcknowledged else { return false }
    guard !realtimeUplinkProbePending else { return true }
    guard let realtimeTransport else { return false }

    let timestampMs = dependencies.clock()
    do {
      try await realtimeTransport.sendProbe(timestampMs: timestampMs)
      realtimeUplinkProbePending = true
      await logEvent(
        name: "realtime.uplink.probe_sent",
        fields: ["timestamp_ms": .number(Double(timestampMs))]
      )
      return true
    } catch {
      setError("Failed to send realtime uplink probe: \(error.localizedDescription)")
      return false
    }
  }

  private func markRealtimeSessionReadyAfterServerReady(
    wasWaitingForReady: Bool,
    flushBufferedFrames: Bool = true
  ) {
    guard realtimeServerSessionReady else { return }
    if realtimeDiagnosticsEnabled && !realtimeUplinkProbeAcknowledged {
      return
    }
    realtimeSessionReady = true
    snapshot.assistantRuntimeState = isRealtimeUplinkActive ? .activeConversation : .armedListening
    if wasWaitingForReady || snapshot.playbackState == "streaming_probing_uplink" {
      snapshot.playbackState = isRealtimeUplinkActive ? "streaming_ready" : "standby_ready"
    }
    if flushBufferedFrames {
      flushBufferedRealtimePCMFramesIfReady()
    }
  }

  private func sendDebugPayloadSweepIfNeeded() async {
    guard realtimeDiagnosticsEnabled else { return }
    guard realtimeSessionReady else { return }
    guard !realtimeDebugPayloadSweepSent else { return }
    guard let realtimeTransport else { return }

    realtimeDebugPayloadSweepSent = true

    for (index, size) in Self.realtimeDebugBinarySweepSizes.enumerated() {
      let timestampMs = dependencies.clock() + Int64(index)
      let payload = Data(repeating: 0, count: size)
      await logEvent(
        name: "realtime.uplink.binary_sweep_sent",
        fields: [
          "index": .number(Double(index)),
          "payload_bytes": .number(Double(size)),
          "timestamp_ms": .number(Double(timestampMs))
        ]
      )
      do {
        try await realtimeTransport.sendAudio(payload, timestampMs: timestampMs)
      } catch {
        setError("Failed to send realtime binary sweep frame: \(error.localizedDescription)")
        break
      }
    }
  }

  private func clearRealtimePrerollBuffer() {
    realtimePrerollFrames.removeAll(keepingCapacity: false)
    realtimePrerollDroppedFrameCount = 0
  }

  private func clearRealtimePrerollBufferIfStale() {
    pruneBufferedRealtimePCMFrames(relativeTo: dependencies.clock())
  }

  private func handleRealtimeAudioSendAttempt() {
    guard isRealtimeUplinkActive, transportState == .connected, realtimeSessionReady else { return }
    let nowMs = dependencies.clock()
    if realtimeFirstAudioSendAttemptAtMs == nil {
      realtimeFirstAudioSendAttemptAtMs = nowMs
      Task { [weak self] in
        await self?.logEvent(
          name: "realtime.uplink.first_send_attempt",
          fields: ["timestamp_ms": .number(Double(nowMs))]
        )
      }
    }
    scheduleRealtimeUplinkAckWatchdogIfNeeded()
  }

  private func scheduleRealtimeUplinkAckWatchdogIfNeeded() {
    guard !realtimeUplinkConfirmed else { return }
    guard !realtimeUplinkTerminalFailureReported else { return }
    guard realtimeFirstAudioSendAttemptAtMs != nil else { return }
    guard realtimeUplinkAckWatchdogTask == nil else { return }

    realtimeUplinkAckWatchdogTask = Task { @MainActor [weak self] in
      do {
        try await Task.sleep(nanoseconds: Self.realtimeUplinkAckTimeoutMs * 1_000_000)
      } catch is CancellationError {
        return
      } catch {
        return
      }
      await self?.handleRealtimeUplinkAckTimeoutIfNeeded()
    }
  }

  private func cancelRealtimeUplinkAckWatchdog() {
    realtimeUplinkAckWatchdogTask?.cancel()
    realtimeUplinkAckWatchdogTask = nil
  }

  private func handleRealtimeAudioSendSuccess(_ frame: RealtimePCMUplinkWorker.Frame) async {
    realtimeTransportSendSuccessLogCount += 1
    guard
      realtimeTransportSendSuccessLogCount == 1 ||
      realtimeTransportSendSuccessLogCount % 100 == 0
    else { return }

    await logEvent(
      name: "realtime.uplink.transport_send_succeeded",
      fields: [
        "count": .number(Double(realtimeTransportSendSuccessLogCount)),
        "payload_bytes": .number(Double(frame.payload.count)),
        "timestamp_ms": .number(Double(frame.timestampMs))
      ]
    )
  }

  private func handleRealtimeUplinkAckTimeoutIfNeeded() async {
    realtimeUplinkAckWatchdogTask = nil
    guard isRealtimeUplinkActive, transportState == .connected, realtimeSessionReady else { return }
    guard !realtimeUplinkConfirmed else { return }
    guard let firstAttemptAtMs = realtimeFirstAudioSendAttemptAtMs else { return }

    let elapsedMs = max(0, dependencies.clock() - firstAttemptAtMs)
    guard elapsedMs >= Int64(Self.realtimeUplinkAckTimeoutMs) else { return }

    await logEvent(
      name: "realtime.uplink.ack_timeout",
      fields: [
        "elapsed_ms": .number(Double(elapsedMs)),
        "recovery_attempted": .bool(realtimeUplinkAckRecoveryAttempted)
      ]
    )

    if !Self.realtimeUplinkAckTimeoutReconnectEnabled {
      guard !realtimeUplinkTerminalFailureReported else { return }
      realtimeUplinkTerminalFailureReported = true
      cancelRealtimeUplinkAckWatchdog()
      snapshot.playbackState = "streaming_audio_ack_missing_debug"
      publishSnapshot()
      await logEvent(
        name: "realtime.uplink.ack_timeout_debug_hold",
        fields: ["elapsed_ms": .number(Double(elapsedMs))]
      )
      setError("Realtime uplink was not acknowledged by backend. Debug mode retained the current transport connection.")
      return
    }

    if realtimeUplinkAckRecoveryAttempted {
      guard !realtimeUplinkTerminalFailureReported else { return }
      realtimeUplinkTerminalFailureReported = true
      cancelRealtimeUplinkAckWatchdog()
      setError("Realtime uplink is not acknowledged by backend.")
      return
    }

    realtimeUplinkAckRecoveryAttempted = true
    setError("Realtime uplink was not acknowledged by backend. Reconnecting transport.")
    await recoverFromMissingRealtimeUplinkAck()
  }

  private func recoverFromMissingRealtimeUplinkAck() async {
    await disconnectRealtimeTransport(reason: "uplink_ack_timeout")
    guard isActivated else { return }
    await connectRealtimeTransport(reason: "uplink_ack_timeout")
  }

  private func resetRealtimeUplinkTurnState() {
    cancelRealtimeUplinkAckWatchdog()
    realtimeBackendConfirmedFrames = 0
    realtimeBackendConfirmedBytes = 0
    realtimeUplinkConfirmed = false
    realtimeFirstAudioSendAttemptAtMs = nil
    realtimeLastUplinkAckAtMs = nil
    realtimeUplinkTerminalFailureReported = false
    realtimeTransportSendSuccessLogCount = 0
    didEmitRealtimeWorkerPathMarker = false
  }

  private func resetRealtimeUplinkState(keepRecoveryAttempt: Bool = false) {
    resetRealtimeUplinkTurnState()
    realtimeServerSessionReady = false
    realtimeUplinkProbePending = false
    realtimeUplinkProbeAcknowledged = false
    realtimeDebugPayloadSweepSent = false
    realtimeSessionReady = false
    if !keepRecoveryAttempt {
      realtimeUplinkAckRecoveryAttempted = false
    }
  }

  private func realtimeUplinkAckLatencyMs() -> Int? {
    guard
      let firstAudioSendAttemptAtMs = realtimeFirstAudioSendAttemptAtMs,
      let lastUplinkAckAtMs = realtimeLastUplinkAckAtMs
    else {
      return nil
    }
    let latencyMs = max(0, lastUplinkAckAtMs - firstAudioSendAttemptAtMs)
    return Int(min(latencyMs, Int64(Int.max)))
  }

  private func logRealtimePCMDeferralOnce(_ key: String) {
    guard lastRealtimePCMDeferralLogKey != key else { return }
    lastRealtimePCMDeferralLogKey = key
    debugLog("Realtime PCM frame deferred: \(key)")
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
    if snapshot.lastError != message {
      snapshot.lastError = message
      publishSnapshot()
    }
    let nowMs = dependencies.clock()
    let shouldSuppressLog = message == lastLoggedErrorMessage && (nowMs - lastLoggedErrorTsMs) < repeatedErrorLogCooldownMs
    if shouldSuppressLog { return }
    lastLoggedErrorMessage = message
    lastLoggedErrorTsMs = nowMs
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
    let sampleRate = 24000.0
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
    let format = AssistantAudioFormat(codec: "pcm_s16le", sampleRate: 24_000, channels: 1)
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
    playbackEngine.onRouteIssue = { [weak self, weak playbackEngine] message in
      guard let self else { return }
      if
        dependencies.suppressSpeakerRouteErrors,
        let playbackEngine,
        Self.isSpeakerRoute(playbackEngine.currentRouteDescription())
      {
        debugLog("Route issue ignored in speaker-preferred mode: \(message)")
        return
      }
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

  private static func isSpeakerRoute(_ routeDescription: String) -> Bool {
    routeDescription
      .split(separator: ",")
      .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
      .contains("speaker")
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

    if wantsRealtimeStreaming, case .connected = transportState, pendingSessionActivateForConnection {
      _ = await sendSessionActivateForCurrentConnection()
    }

    if wantsRealtimeStreaming, case .connected = transportState {
      let pingSentAtMs = dependencies.clock()
      if await sendOutbound(type: .healthPing, payload: EmptyPayload()) {
        lastHealthPingSentAtMs = pingSentAtMs
      } else {
        lastHealthPingSentAtMs = nil
      }
    } else {
      lastHealthPingSentAtMs = nil
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
    let realtimeUplinkMetrics = realtimePCMUplinkWorker?.metricsSnapshot()
    let socketDiagnostics: SessionWebSocketDiagnosticsSnapshot? = if let gatewayTransport = realtimeTransport as? GatewayTransport {
      await gatewayTransport.diagnosticsSnapshot()
    } else {
      nil
    }
    let realtimeAudioFrameDropCount = realtimePCMUplinkWorker?.consumeDroppedFrameCount() ?? 0
    let prerollFrameDropCount = realtimePrerollDroppedFrameCount
    realtimePrerollDroppedFrameCount = 0
    let frameDropCount = visionFrameDropCount + realtimeAudioFrameDropCount + prerollFrameDropCount
    let nowMs = dependencies.clock()
    let elapsedMs = max(1, (lastHealthEmissionTsMs.map { nowMs - $0 } ?? Int64(healthIntervalMs)))
    lastHealthEmissionTsMs = nowMs
    let frameDropRate = (Double(frameDropCount) * 1000.0) / Double(elapsedMs)
    let realtimeUplinkAckLatencyMs = realtimeUplinkAckLatencyMs()

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
      realtimeAudioFramesEnqueued: realtimeUplinkMetrics?.enqueuedFrames ?? 0,
      realtimeAudioFramesSendAttempted: realtimeUplinkMetrics?.sendAttempts ?? 0,
      realtimeAudioFramesSent: realtimeUplinkMetrics?.sentFrames ?? 0,
      realtimeAudioBackendConfirmedFrames: realtimeBackendConfirmedFrames,
      realtimeAudioBackendConfirmedBytes: realtimeBackendConfirmedBytes,
      realtimeAudioSendFailures: realtimeUplinkMetrics?.sendFailures ?? 0,
      realtimeAudioLastSendError: realtimeUplinkMetrics?.lastSendError,
      realtimeUplinkConfirmed: realtimeUplinkConfirmed,
      realtimeUplinkAckLatencyMs: realtimeUplinkAckLatencyMs,
      realtimeForceTextAudioFallback: config.realtimeForceTextAudioFallback,
      realtimeSocketConnectionID: socketDiagnostics?.connectionID,
      realtimeSocketLastOutboundKind: socketDiagnostics?.lastOutboundKind,
      realtimeSocketLastOutboundBytes: socketDiagnostics?.lastOutboundBytes,
      realtimeSocketBinarySendAttempted: socketDiagnostics?.binarySendAttemptCount,
      realtimeSocketBinarySendCompleted: socketDiagnostics?.binarySendSuccessCount,
      realtimeSocketLastBinaryFirstByte: socketDiagnostics?.lastBinaryFirstByteHex,
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
      "realtime_audio_frames_enqueued": .number(Double(realtimeUplinkMetrics?.enqueuedFrames ?? 0)),
      "realtime_audio_frames_send_attempted": .number(Double(realtimeUplinkMetrics?.sendAttempts ?? 0)),
      "realtime_audio_frames_sent": .number(Double(realtimeUplinkMetrics?.sentFrames ?? 0)),
      "realtime_audio_backend_confirmed_frames": .number(Double(realtimeBackendConfirmedFrames)),
      "realtime_audio_backend_confirmed_bytes": .number(Double(realtimeBackendConfirmedBytes)),
      "realtime_audio_send_failures": .number(Double(realtimeUplinkMetrics?.sendFailures ?? 0)),
      "realtime_uplink_confirmed": .bool(realtimeUplinkConfirmed),
      "realtime_force_text_audio_fallback": .bool(config.realtimeForceTextAudioFallback),
      "realtime_socket_connection_id": .number(Double(socketDiagnostics?.connectionID ?? 0)),
      "realtime_socket_last_outbound_bytes": .number(Double(socketDiagnostics?.lastOutboundBytes ?? 0)),
      "realtime_socket_last_outbound_kind": .string(socketDiagnostics?.lastOutboundKind ?? "none"),
      "realtime_socket_binary_send_attempted": .number(Double(socketDiagnostics?.binarySendAttemptCount ?? 0)),
      "realtime_socket_binary_send_completed": .number(Double(socketDiagnostics?.binarySendSuccessCount ?? 0)),
      "realtime_socket_last_binary_first_byte": .string(socketDiagnostics?.lastBinaryFirstByteHex ?? "none"),
      "session_restart_count": .number(Double(sessionRestartCount)),
      "pending_playback_duration_ms": .number(Double(pendingDurationMs)),
      "playback_backpressured": .bool(backpressured),
      "playback_route": .string(lastKnownPlaybackRoute)
    ]
    if let realtimeUplinkAckLatencyMs {
      healthFields["realtime_uplink_ack_latency_ms"] = .number(Double(realtimeUplinkAckLatencyMs))
    }
    if let lastRealtimeSendError = realtimeUplinkMetrics?.lastSendError, !lastRealtimeSendError.isEmpty {
      healthFields["realtime_audio_last_send_error"] = .string(lastRealtimeSendError)
    }
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
        if type == .sessionActivate {
          return false
        }
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

  @discardableResult
  private func sendSessionActivateForCurrentConnection() async -> Bool {
    guard pendingSessionActivateForConnection else { return true }
    let didSend = await sendOutbound(
      type: .sessionActivate,
      payload: SessionActivatePayload(
        session: SessionActivatePayload.SessionInfo(type: Self.realtimeSessionType),
        audioFormat: SessionActivatePayload.ClientAudioFormat(
          encoding: Self.realtimeUplinkEncoding,
          channels: Self.realtimeUplinkChannels,
          sampleRate: Self.realtimeUplinkSampleRate
        )
      )
    )
    if didSend {
      pendingSessionActivateForConnection = false
    }
    return didSend
  }

  private func isRealtimeSessionReadyState(_ state: String) -> Bool {
    let normalizedState = state.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    return normalizedState == "active" || normalizedState == "ready"
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

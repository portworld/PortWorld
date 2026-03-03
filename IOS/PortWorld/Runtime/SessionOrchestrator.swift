import AVFAudio
import Foundation
import OSLog
import UIKit

@MainActor
final class SessionOrchestrator {
  struct Dependencies {
    typealias MakeWebSocketClient = (_ config: RuntimeConfig) -> SessionWebSocketClientProtocol
    typealias MakeVisionFrameUploader = (_ config: RuntimeConfig) -> VisionFrameUploaderProtocol
    typealias MakeRollingVideoBuffer = (_ config: RuntimeConfig) -> RollingVideoBufferProtocol
    typealias MakeQueryBundleBuilder = (_ config: RuntimeConfig) -> QueryBundleBuilderProtocol
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
    let makeWebSocketClient: MakeWebSocketClient
    let makeVisionFrameUploader: MakeVisionFrameUploader
    let makeRollingVideoBuffer: MakeRollingVideoBuffer
    let makeQueryBundleBuilder: MakeQueryBundleBuilder
    let makePlaybackEngine: MakePlaybackEngine
    let eventLogger: EventLoggerProtocol

    static var live: Dependencies {
      Dependencies(
        startStream: {},
        stopStream: {},
        exportAudioClip: { _ in throw AudioClipExportError.sessionDirectoryUnavailable },
        flushPendingAudioChunks: {},
        audioBufferDurationProvider: { 0 },
        sharedAudioEngine: nil,
        clock: { Clocks.nowMs() },
        makeWebSocketClient: { config in
          SessionWebSocketClient(
            url: config.webSocketURL,
            requestHeaders: config.requestHeaders,
            onStateChange: nil,
            onMessage: nil,
            onError: nil,
            eventLogger: nil
          )
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
        makeQueryBundleBuilder: { config in
          QueryBundleBuilder(endpointURL: config.queryURL, defaultHeaders: config.requestHeaders)
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

  private struct ActiveQueryContext {
    let queryID: String
    let wakeTsMs: Int64
    var startTsMs: Int64
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

  private let manualWakeEngine: ManualWakeWordEngine
  private let primaryWakeEngine: WakeWordEngineProtocol

  private func configureWakeEngine(_ engine: WakeWordEngineProtocol) {
    engine.onWakeDetected = { [weak self] event in
      Task { @MainActor in
        self?.handleWakeDetected(event)
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

  private lazy var queryEndpointDetector: QueryEndpointDetector = {
    let detector = QueryEndpointDetector(silenceTimeoutMs: Int64(config.silenceTimeoutMs))
    detector.onQueryStarted = { [weak self] event in
      Task { @MainActor in
        self?.handleQueryStarted(event)
      }
    }
    detector.onQueryEnded = { [weak self] event in
      Task { @MainActor in
        await self?.handleQueryEnded(event)
      }
    }
    return detector
  }()

  private var visionFrameUploader: VisionFrameUploaderProtocol?
  private var rollingVideoBuffer: RollingVideoBufferProtocol?
  private var queryBundleBuilder: QueryBundleBuilderProtocol?
  private var playbackEngine: AssistantPlaybackEngineProtocol?
  private var webSocketClient: SessionWebSocketClientProtocol?

  private var snapshot = StatusSnapshot()
  private var activeSessionID: String?
  private var activeQueryContext: ActiveQueryContext?
  private var isActivated = false
  private var runtimeState: RuntimeState = .foregroundActive
  private var photosFailed = 0
  private var queryBundlesUploaded = 0
  private var queryBundlesFailed = 0
  private var wsReconnectAttempts = 0
  /// Counts full session restarts (deactivate+activate cycles), persists across activations.
  /// Distinguishes from wsReconnectAttempts which tracks transport-level reconnects within a session.
  private var sessionRestartCount = 0
  private var sessionActivatedAtMs: Int64 = 0
  private var lastKnownPlaybackRoute = "unknown"
  private var healthTask: Task<Void, Never>?
  private var currentUploadTask: Task<QueryBundleUploadResult, Error>?
  private var webSocketConnectionState: SessionWebSocketConnectionState = .idle
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
        localeIdentifier: config.wakeWordLocaleIdentifier,
        requiresOnDeviceRecognition: config.wakeWordRequiresOnDeviceRecognition,
        detectionCooldownMs: config.wakeWordDetectionCooldownMs
      )
      self.snapshot.manualWakeFallbackEnabled = true
    } else {
      self.primaryWakeEngine = manual
      self.snapshot.manualWakeFallbackEnabled = true
    }

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
    let webSocketClient = dependencies.makeWebSocketClient(config)
    await webSocketClient.bindHandlers(
      onStateChange: { [weak self] state in
        Task { @MainActor in
          self?.handleWebSocketState(state)
        }
      },
      onMessage: { [weak self] message in
        Task { @MainActor in
          await self?.handleInboundWebSocketMessage(message)
        }
      },
      onError: { [weak self] error in
        Task { @MainActor in
          self?.setError(error.localizedDescription)
        }
      },
      eventLogger: eventLogger
    )
    self.webSocketClient = webSocketClient

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
    self.queryBundleBuilder = dependencies.makeQueryBundleBuilder(config)
    self.playbackEngine = dependencies.makePlaybackEngine(
      dependencies.sharedAudioEngine,
      config.assistantStuckDetectionThresholdMs
    )
    configurePlaybackEngine()
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

    snapshot.sessionState = .connecting
    snapshot.sessionID = sessionID
    snapshot.wakeState = .listening
    snapshot.queryState = .idle
    snapshot.wakeEngine = primaryWakeEngine.engineKind.rawValue
    snapshot.speechAuthorization = primaryWakeEngine.currentAuthorizationStatus().rawValue
    publishSnapshot()
    runtimeState = .foregroundActive
    photosFailed = 0
    queryBundlesUploaded = 0
    queryBundlesFailed = 0
    wsReconnectAttempts = 0
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

    if let webSocketClient {
      await webSocketClient.connect()
    }
    startHealthLoop()

    await logEvent(name: "session.activate")
    await sendOutbound(type: .sessionActivate, payload: EmptyPayload())
    await emitHealth(reason: "activate")
  }

  func deactivate() async {
    guard isActivated else { return }

    currentUploadTask?.cancel()
    currentUploadTask = nil
    queryEndpointDetector.reset()
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

    await sendOutbound(type: .sessionDeactivate, payload: EmptyPayload())
    if let webSocketClient {
      await webSocketClient.disconnect(closeCode: .normalClosure)
    }
    await dependencies.stopStream()

    activeQueryContext = nil
    activeSessionID = nil
    outboundMessageBuffer.removeAll(keepingCapacity: false)
    isActivated = false
    sessionRestartCount += 1
    webSocketClient = nil
    visionFrameUploader = nil
    rollingVideoBuffer = nil
    queryBundleBuilder = nil
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
      if isActivated, let webSocketClient {
        await webSocketClient.ensureConnected()
      }
      runtimeState = .foregroundActive
      await emitHealth(reason: "foreground")
    }
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
    guard activeQueryContext != nil else { return }
    queryEndpointDetector.recordSpeechActivity(at: timestampMs)
  }

  func triggerWakeForTesting() {
    manualWakeEngine.triggerManualWake(timestampMs: dependencies.clock())
  }

  var wakeEngineType: String {
    primaryWakeEngine.engineKind.rawValue
  }

  private func handleWakeDetected(_ event: WakeWordDetectionEvent) {
    guard isActivated else { return }
    guard activeQueryContext == nil else { return }

    // Cancel any in-flight playback from previous response to avoid queue buildup
    playbackEngine?.cancelResponse()

    // Immediate audio feedback: single beep so the user knows wake word was heard
    playWakeChime()

    let queryID = "query_\(UUID().uuidString)"
    activeQueryContext = ActiveQueryContext(
      queryID: queryID,
      wakeTsMs: event.timestampMs,
      startTsMs: event.timestampMs
    )

    snapshot.wakeState = .triggered
    snapshot.wakeCount += 1
    snapshot.queryID = queryID
    publishSnapshot()

    let payload = WakewordDetectedPayload(
      wakePhrase: event.wakePhrase,
      engine: event.engine,
      confidence: event.confidence.map(Double.init)
    )

    Task {
      await logEvent(name: "wakeword.detected", queryID: queryID)
      await sendOutbound(type: .wakewordDetected, payload: payload)
    }

    queryEndpointDetector.beginQuery(queryId: queryID, startedAtMs: event.timestampMs)
  }

  private func handleQueryStarted(_ event: QueryEndpointStartedEvent) {
    guard var context = activeQueryContext, context.queryID == event.queryId else { return }

    context.startTsMs = event.startedAtMs
    activeQueryContext = context
    snapshot.queryState = .recording
    snapshot.wakeState = .listening
    publishSnapshot()

    Task {
      await logEvent(name: "query.started", queryID: context.queryID)
      await sendOutbound(type: .queryStarted, payload: QueryStartedPayload(queryID: context.queryID))
    }
  }

  private func handleQueryEnded(_ event: QueryEndpointEndedEvent) async {
    guard let context = activeQueryContext, context.queryID == event.queryId else { return }
    guard let rollingVideoBuffer, let queryBundleBuilder else { return }

    snapshot.queryState = .processingBundle
    snapshot.queryCount += 1
    snapshot.playbackState = "thinking"
    publishSnapshot()

    // Layer 0: Play chime immediately after capture stops, through the
    // glasses audio route.  Cleared automatically when start_response arrives.
    playThinkingChime()

    await logEvent(name: "query.ended", queryID: context.queryID)
    await sendOutbound(
      type: .queryEnded,
      payload: QueryEndedPayload(
        queryID: context.queryID,
        reason: event.reason.rawValue,
        silenceTimeoutMs: config.silenceTimeoutMs,
        durationMs: Int(event.durationMs)
      )
    )

    do {
      // Flush any buffered audio to ensure partial chunks are written before export
      dependencies.flushPendingAudioChunks()
      
      // Extend window backwards by 2 seconds to account for potential timing drift
      // between wake detection timestamp and audio chunk timestamps
      let adjustedStartTs = max(0, context.startTsMs - 2000)
      let clipWindow = AudioClipExportWindow(startTimestampMs: adjustedStartTs, endTimestampMs: event.endedAtMs)
      debugLog("Export clip window: [\(adjustedStartTs)-\(event.endedAtMs)] (original start: \(context.startTsMs))")
      let audioURL = try dependencies.exportAudioClip(clipWindow)

      let videoStartMs = max(0, context.wakeTsMs - Int64(config.preWakeVideoMs))
      let videoResult = try await rollingVideoBuffer.exportInterval(
        startTimestampMs: videoStartMs,
        endTimestampMs: event.endedAtMs
      )

      let metadata = QueryMetadata(
        sessionID: activeSessionID ?? "unknown",
        queryID: context.queryID,
        wakeTsMs: context.wakeTsMs,
        queryStartTsMs: context.startTsMs,
        queryEndTsMs: event.endedAtMs,
        videoStartTsMs: videoStartMs,
        videoEndTsMs: event.endedAtMs
      )

      snapshot.queryState = .uploading
      publishSnapshot()

      let uploadTask = Task {
        try await queryBundleBuilder.uploadQueryBundle(
          metadata: metadata,
          audioFileURL: audioURL,
          videoFileURL: videoResult.outputURL
        )
      }
      currentUploadTask = uploadTask
      let uploadResult = try await uploadTask.value
      currentUploadTask = nil
      queryBundlesUploaded += 1

      let didEnqueueUploadedMessage = await sendOutbound(
        type: .queryBundleUploaded,
        payload: QueryBundleUploadedPayload(
          queryID: context.queryID,
          uploadStatus: uploadResult.success ? "ok" : "failed",
          audioBytes: uploadResult.audioBytes,
          videoBytes: uploadResult.videoBytes
        )
      )

      snapshot.queryState = .idle
      snapshot.queryID = "-"
      activeQueryContext = nil
      if !didEnqueueUploadedMessage {
        debugLog("Failed to notify backend of uploaded bundle for \(context.queryID); context cleared anyway")
      }
      publishSnapshot()
      await emitHealth(reason: "query_uploaded")
    } catch is CancellationError {
      currentUploadTask = nil
      debugLog("Query bundle upload cancelled for query \(context.queryID)")
    } catch {
      currentUploadTask = nil
      queryBundlesFailed += 1
      setError(error.localizedDescription)
      snapshot.queryState = .failed
      publishSnapshot()
      activeQueryContext = nil
      snapshot.queryID = "-"
      await emitHealth(reason: "query_failed")
    }
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

  private func handleWebSocketState(_ state: SessionWebSocketConnectionState) {
    webSocketConnectionState = state
    switch state {
    case .idle:
      snapshot.sessionState = .idle
    case .connecting:
      snapshot.sessionState = .connecting
    case .connected:
      snapshot.sessionState = .active
    case .reconnecting(let attempt, _):
      snapshot.sessionState = .reconnecting
      wsReconnectAttempts = max(wsReconnectAttempts, attempt)
    case .disconnected:
      if isActivated {
        snapshot.sessionState = .reconnecting
      } else {
        snapshot.sessionState = .ended
      }
    }
    publishSnapshot()
    Task {
      if case .connected = state {
        await replayBufferedOutboundMessages()
      }
      await emitHealth(reason: "ws_state")
    }
  }

  private func handleInboundWebSocketMessage(_ message: WSInboundMessage) async {
    guard let playbackEngine else { return }
    switch message {
    case .assistantAudioChunk(let envelope):
      debugLog("Received audio chunk: \(envelope.payload.chunkID), bytes: \(envelope.payload.bytesB64.count), sampleRate: \(envelope.payload.sampleRate), isLast: \(envelope.payload.isLast)")
      do {
        try playbackEngine.appendChunk(envelope.payload)
        snapshot.playbackChunkCount += 1
        snapshot.pendingPlaybackBufferCount = playbackEngine.pendingBufferCount
        snapshot.pendingPlaybackDurationMs = Int(playbackEngine.pendingBufferDurationMs)
        snapshot.playbackBackpressured = playbackEngine.isBackpressured
        snapshot.playbackState = envelope.payload.isLast ? "idle" : "playing"
        publishSnapshot()
        debugLog("Audio chunk processed successfully, playbackChunkCount: \(snapshot.playbackChunkCount), pendingBuffers: \(snapshot.pendingPlaybackBufferCount), pendingDurationMs: \(snapshot.pendingPlaybackDurationMs), backpressured: \(snapshot.playbackBackpressured)")
      } catch {
        debugLog("Audio chunk error: \(error.localizedDescription)")
        setError(error.localizedDescription)
      }

    case .assistantPlaybackControl(let envelope):
      debugLog("Received playback control: \(envelope.payload.command.rawValue)")
      playbackEngine.handlePlaybackControl(envelope.payload)
      snapshot.playbackState = envelope.payload.command.rawValue
      publishSnapshot()

    case .assistantThinking(let envelope):
      debugLog("Thinking received: query=\(envelope.payload.queryID ?? "?")")
      snapshot.playbackState = "thinking"
      publishSnapshot()
      triggerThinkingHaptic()

    case .error(let envelope):
      setError(envelope.payload.message)

    case .sessionState(let envelope):
      snapshot.sessionState = envelope.payload.state
      publishSnapshot()

    case .healthPong, .unknown:
      break
    }
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

  // MARK: - Thinking Chime

  /// Pre-computed single beep for end-of-recording (880 Hz, 120 ms).
  /// Higher pitch than wake chime (660 Hz) so the two are distinct.
  private static let thinkingChimePCM: Data = {
    let sampleRate = 16000.0
    let frequency  = 880.0   // A5
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

  /// Play the thinking chime through the playback engine (routes to glasses).
  /// This schedules a short chime on the existing playback route; it will play
  /// to completion unless stopped or interrupted by other playback controls.
  private func playThinkingChime() {
    let format = AssistantAudioFormat(codec: "pcm_s16le", sampleRate: 16_000, channels: 1)
    do {
      // No startResponse() here — the wake chime has long finished by the time
      // the query ends, and startResponse()'s route update can discard the buffer.
      try playbackEngine?.appendPCMData(Self.thinkingChimePCM, format: format)
      debugLog("Thinking chime scheduled")
    } catch {
      debugLog("Thinking chime failed: \(error.localizedDescription)")
    }
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
    guard let webSocketClient, let rollingVideoBuffer, let playbackEngine else { return }

    await sendOutbound(type: .healthPing, payload: EmptyPayload())

    let photoRate = effectivePhotoUploadRate()
    let reconnectAttempts = await webSocketClient.reconnectAttemptCount()
    let pendingDurationMs = Int(playbackEngine.pendingBufferDurationMs)
    let backpressured = playbackEngine.isBackpressured
    let statsPayload = HealthStatsPayload(
      wakeState: snapshot.wakeState,
      queryState: snapshot.queryState,
      queriesCompleted: snapshot.queryCount,
      queryBundlesUploaded: queryBundlesUploaded,
      queryBundlesFailed: queryBundlesFailed,
      photoUploadRateEffective: photoRate,
      photosUploaded: snapshot.photoUploadCount,
      photosFailed: photosFailed,
      videoBufferDurationMs: Int(await rollingVideoBuffer.bufferedDurationMs),
      audioBufferDurationMs: dependencies.audioBufferDurationProvider(),
      wsReconnectAttempts: max(wsReconnectAttempts, reconnectAttempts),
      sessionRestartCount: sessionRestartCount,
      pendingPlaybackDurationMs: pendingDurationMs,
      playbackBackpressured: backpressured,
      playbackRoute: lastKnownPlaybackRoute
    )
    await sendOutbound(type: .healthStats, payload: statsPayload)
    await logEvent(
      name: "health.stats",
      fields: [
        "reason": .string(reason),
        "runtime_state": .string(runtimeState.rawValue),
        "photo_upload_rate_effective": .number(photoRate),
        "photos_uploaded": .number(Double(snapshot.photoUploadCount)),
        "photos_failed": .number(Double(photosFailed)),
        "queries_completed": .number(Double(snapshot.queryCount)),
        "query_bundles_uploaded": .number(Double(queryBundlesUploaded)),
        "query_bundles_failed": .number(Double(queryBundlesFailed)),
        "video_buffer_duration_ms": .number(Double(await rollingVideoBuffer.bufferedDurationMs)),
        "audio_buffer_duration_ms": .number(Double(dependencies.audioBufferDurationProvider())),
        "ws_reconnect_attempts": .number(Double(max(wsReconnectAttempts, reconnectAttempts))),
        "session_restart_count": .number(Double(sessionRestartCount)),
        "pending_playback_duration_ms": .number(Double(pendingDurationMs)),
        "playback_backpressured": .bool(backpressured),
        "playback_route": .string(lastKnownPlaybackRoute)
      ]
    )
  }

  private func effectivePhotoUploadRate() -> Double {
    let elapsedMs = max(1, dependencies.clock() - sessionActivatedAtMs)
    return (Double(snapshot.photoUploadCount) * 1000.0) / Double(elapsedMs)
  }

  private static func photoUploadIntervalMs(photoFps: Double) -> Int64 {
    let clamped = max(0.1, photoFps)
    return Int64(max(100, (1000.0 / clamped).rounded()))
  }

  @discardableResult
  private func sendOutbound<Payload: Codable>(type: WSOutboundType, payload: Payload) async -> Bool {
    guard let sessionID = activeSessionID else { return false }
    guard let webSocketClient else { return false }

    let payloadJSON: JSONValue
    do {
      payloadJSON = try encodePayloadAsJSONValue(payload)
    } catch {
      setError(error.localizedDescription)
      return false
    }

    do {
      try await webSocketClient.send(type: type, sessionID: sessionID, payload: payloadJSON)
      return true
    } catch let wsError as SessionWebSocketClientError {
      // Expected while socket is transitioning/disconnected; avoid spamming
      // app-level error state with redundant transport noise.
      if case .notConnected = wsError {
        enqueueOutboundMessage(type: type, sessionID: sessionID, payload: payloadJSON)
        return true
      }
      setError(wsError.localizedDescription)
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
    guard case .connected = webSocketConnectionState else { return }
    guard let webSocketClient else { return }

    pruneOutboundMessageBuffer()
    while !outboundMessageBuffer.isEmpty {
      let nextMessage = outboundMessageBuffer[0]
      do {
        try await webSocketClient.send(
          type: nextMessage.type,
          sessionID: nextMessage.sessionID,
          payload: nextMessage.payload
        )
        outboundMessageBuffer.removeFirst()
      } catch let wsError as SessionWebSocketClientError {
        if case .notConnected = wsError {
          return
        }
        outboundMessageBuffer.removeFirst()
        setError(wsError.localizedDescription)
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
}

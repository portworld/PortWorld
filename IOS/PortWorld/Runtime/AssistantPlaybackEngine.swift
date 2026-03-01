import AVFAudio
import Foundation

public enum AssistantPlaybackError: Error, LocalizedError {
  case invalidBase64Chunk
  case unsupportedCodec(String)
  case unsupportedSampleRate(Int)
  case unsupportedChannelCount(Int)
  case invalidPCMByteCount(Int)
  case formatMismatch(expected: AssistantAudioFormat, received: AssistantAudioFormat)
  case unableToBuildAudioFormat
  case unableToAllocateBuffer
  case outputChannelMismatch(expected: Int, actual: Int)
  case engineStartFailed(String)

  public var errorDescription: String? {
    switch self {
    case .invalidBase64Chunk:
      return "Audio chunk payload is not valid base64."
    case .unsupportedCodec(let codec):
      return "Unsupported audio codec '\(codec)'. Expected pcm_s16le."
    case .unsupportedSampleRate(let sampleRate):
      return "Unsupported sample rate '\(sampleRate)'. Expected 16000 Hz."
    case .unsupportedChannelCount(let channels):
      return "Unsupported channel count '\(channels)'. Only mono is supported."
    case .invalidPCMByteCount(let count):
      return "PCM payload byte count \(count) is not aligned to 16-bit mono samples."
    case .formatMismatch(let expected, let received):
      return "Audio format mismatch. Expected \(expected.description), received \(received.description)."
    case .unableToBuildAudioFormat:
      return "Unable to build AVAudioFormat for assistant playback."
    case .unableToAllocateBuffer:
      return "Unable to allocate playback audio buffer."
    case .outputChannelMismatch(let expected, let actual):
      return "Playback output channel mismatch. Expected \(expected), got \(actual)."
    case .engineStartFailed(let message):
      return "Failed to start playback engine: \(message)"
    }
  }
}

public struct AssistantAudioFormat: Equatable {
  public let codec: String
  public let sampleRate: Int
  public let channels: Int

  public init(codec: String, sampleRate: Int, channels: Int) {
    self.codec = codec
    self.sampleRate = sampleRate
    self.channels = channels
  }

  fileprivate var description: String {
    "\(codec)@\(sampleRate)Hz/\(channels)ch"
  }
}

@MainActor
public final class AssistantPlaybackEngine {
  public var onRouteChanged: ((String) -> Void)?
  public var onRouteIssue: ((String) -> Void)?

  private let audioSession: AVAudioSession
  private let audioEngine: AVAudioEngine
  private let playerNode: AVAudioPlayerNode
  private let ownsEngine: Bool
  private var currentFormat: AssistantAudioFormat?
  private var routeObserver: NSObjectProtocol?
  private var interruptionObserver: NSObjectProtocol?
  private var isPlayerNodeAttached = false
  private var isPlayerNodeConnected = false
  private static let graphFormat = AssistantAudioFormat(codec: "pcm_s16le", sampleRate: 16_000, channels: 1)

  /// Number of buffers currently scheduled in the player node awaiting playback.
  /// Used for backpressure and observability.
  private(set) var pendingBufferCount: Int = 0

  /// Estimated duration of pending audio in milliseconds.
  /// Provides a more accurate backpressure signal than buffer count alone.
  private(set) var pendingBufferDurationMs: Double = 0

  /// Timestamp (ms) when the last buffer completion callback fired.
  /// Used to detect stuck playback where buffers are scheduled but never drain.
  private var lastBufferDrainedAtMs: Int64 = 0

  /// Timestamp (ms) when the last buffer was scheduled.
  private var lastBufferScheduledAtMs: Int64 = 0

  /// Number of consecutive stuck-state detections. Used to trigger recovery.
  private var consecutiveStuckChecks: Int = 0

  /// Threshold (ms) for detecting stuck playback. If buffers were scheduled
  /// this recently but no drain callback fired, we may be stuck.
  private static let stuckDetectionThresholdMs: Int64 = 1500

  /// Max consecutive stuck checks before attempting recovery.
  private static let maxStuckChecksBeforeRecovery: Int = 3

  /// Maximum pending audio duration (ms) before backpressure kicks in.
  /// 3 seconds balances latency vs. resilience to Bluetooth HFP drain variability.
  private static let maxPendingDurationMs: Double = 3000

  /// High water mark (ms) at which we signal backpressure to callers.
  /// Set lower than maxPendingDurationMs to allow proactive throttling.
  private static let backpressureHighWaterMs: Double = 1500

  /// Whether the playback queue is under backpressure (pending audio exceeds high water mark).
  /// Callers can use this to throttle upstream chunk generation.
  public var isBackpressured: Bool {
    pendingBufferDurationMs > Self.backpressureHighWaterMs
  }

  /// Maximum number of pending buffers before backpressure kicks in.
  /// At ~100ms per buffer chunk, 50 buffers ≈ 5 seconds of queued audio.
  private static let maxPendingBuffers = 50

  /// Creates a playback engine.
  /// - Parameters:
  ///   - audioSession: The AVAudioSession to use for route information.
  ///   - audioEngine: The AVAudioEngine to attach the player node to. If nil, creates a new engine internally.
  ///   - playerNode: The player node for audio playback.
  public init(
    audioSession: AVAudioSession = .sharedInstance(),
    audioEngine: AVAudioEngine? = nil,
    playerNode: AVAudioPlayerNode = AVAudioPlayerNode()
  ) {
    self.audioSession = audioSession
    if let audioEngine {
      self.audioEngine = audioEngine
      self.ownsEngine = false
    } else {
      self.audioEngine = AVAudioEngine()
      self.ownsEngine = true
    }
    self.playerNode = playerNode

    // Attach once, then connect lazily from the first inbound chunk format.
    // Avoid disconnect/reconnect churn on a shared engine.
    ensurePlayerNodeAttached()
    do {
      try connectPlayerNodeIfNeeded(for: Self.graphFormat)
      currentFormat = Self.graphFormat
    } catch {
      print("[AssistantPlaybackEngine] Failed to connect playback graph at init: \(error.localizedDescription)")
    }

    routeObserver = NotificationCenter.default.addObserver(
      forName: AVAudioSession.routeChangeNotification,
      object: audioSession,
      queue: .main
    ) { [weak self] notification in
      MainActor.assumeIsolated {
        self?.publishRouteUpdate(notification: notification)
      }
    }

    interruptionObserver = NotificationCenter.default.addObserver(
      forName: AVAudioSession.interruptionNotification,
      object: audioSession,
      queue: .main
    ) { [weak self] notification in
      let interruptionType = Self.interruptionType(from: notification)
      MainActor.assumeIsolated {
        self?.handleInterruption(interruptionType)
      }
    }
  }

  deinit {
    if let routeObserver {
      NotificationCenter.default.removeObserver(routeObserver)
    }
    if let interruptionObserver {
      NotificationCenter.default.removeObserver(interruptionObserver)
    }
  }

  public func configureBluetoothHFPRoute() throws {
    // AudioCollectionManager owns AVAudioSession lifecycle/category for capture+playback.
    // Playback intentionally avoids mutating shared AVAudioSession state.
    // Log current routing state for diagnostics.
    logCurrentRouteState(context: "configureBluetoothHFPRoute")
  }

  /// Logs detailed audio session routing state for diagnostics.
  private func logCurrentRouteState(context: String) {
    let route = audioSession.currentRoute
    let inputPorts = route.inputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ", ")
    let outputPorts = route.outputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ", ")
    let category = audioSession.category.rawValue
    let mode = audioSession.mode.rawValue
    print("[AssistantPlaybackEngine] Route state (\(context)): category=\(category), mode=\(mode), inputs=[\(inputPorts)], outputs=[\(outputPorts)]")
  }

  public func appendChunk(_ payload: AssistantAudioChunkPayload) throws {
    guard payload.codec.lowercased() == "pcm_s16le" else {
      throw AssistantPlaybackError.unsupportedCodec(payload.codec)
    }
    guard payload.channels == 1 else {
      throw AssistantPlaybackError.unsupportedChannelCount(payload.channels)
    }
    guard let pcmData = Data(base64Encoded: payload.bytesB64) else {
      throw AssistantPlaybackError.invalidBase64Chunk
    }

    try appendPCMData(
      pcmData,
      format: AssistantAudioFormat(
        codec: payload.codec.lowercased(),
        sampleRate: payload.sampleRate,
        channels: payload.channels
      )
    )
  }

  public func appendPCMData(_ pcmData: Data, format incomingFormat: AssistantAudioFormat) throws {
    print("[AssistantPlaybackEngine] appendPCMData: \(pcmData.count) bytes, format: \(incomingFormat.description)")
    print("[AssistantPlaybackEngine] Current route: \(currentRouteDescription())")
    print("[AssistantPlaybackEngine] Engine running: \(audioEngine.isRunning), ownsEngine: \(ownsEngine)")
    
    guard incomingFormat.codec == "pcm_s16le" else {
      throw AssistantPlaybackError.unsupportedCodec(incomingFormat.codec)
    }
    guard incomingFormat.sampleRate == Self.graphFormat.sampleRate else {
      throw AssistantPlaybackError.unsupportedSampleRate(incomingFormat.sampleRate)
    }
    guard incomingFormat.channels == 1 else {
      throw AssistantPlaybackError.unsupportedChannelCount(incomingFormat.channels)
    }
    guard pcmData.count % MemoryLayout<Int16>.size == 0 else {
      throw AssistantPlaybackError.invalidPCMByteCount(pcmData.count)
    }

    if let currentFormat, currentFormat != incomingFormat {
      throw AssistantPlaybackError.formatMismatch(expected: currentFormat, received: incomingFormat)
    }

    // Start the engine if needed before scheduling playback.
    try startEngineIfNeeded()

    let sampleCount = pcmData.count / MemoryLayout<Int16>.size
    let frameCount = AVAudioFrameCount(sampleCount)

    guard
      let audioFormat = avAudioFormat(for: incomingFormat),
      let buffer = AVAudioPCMBuffer(pcmFormat: audioFormat, frameCapacity: frameCount),
      let channelData = buffer.int16ChannelData
    else {
      throw AssistantPlaybackError.unableToAllocateBuffer
    }

    buffer.frameLength = frameCount
    pcmData.withUnsafeBytes { rawBuffer in
      guard let source = rawBuffer.baseAddress else { return }
      memcpy(channelData.pointee, source, pcmData.count)
    }

    let outputChannels = Int(playerNode.outputFormat(forBus: 0).channelCount)
    if outputChannels != Int(audioFormat.channelCount) {
      throw AssistantPlaybackError.outputChannelMismatch(
        expected: Int(audioFormat.channelCount),
        actual: outputChannels
      )
    }

    // Calculate buffer duration in milliseconds
    let bufferDurationMs = Double(frameCount) / Double(incomingFormat.sampleRate) * 1000.0
    let nowMs = Self.nowMs()

    // Check for stuck playback: buffers scheduled but never draining
    if pendingBufferCount > 0 && lastBufferScheduledAtMs > 0 {
      let timeSinceLastDrain = nowMs - lastBufferDrainedAtMs
      let timeSinceLastSchedule = nowMs - lastBufferScheduledAtMs
      
      // If we've been scheduling buffers but nothing has drained for a while, we're stuck
      if timeSinceLastSchedule < 500 && timeSinceLastDrain > Self.stuckDetectionThresholdMs {
        consecutiveStuckChecks += 1
        print("[AssistantPlaybackEngine] Stuck playback detected: pendingCount=\(pendingBufferCount), timeSinceLastDrain=\(timeSinceLastDrain)ms, consecutiveChecks=\(consecutiveStuckChecks)")
        
        if consecutiveStuckChecks >= Self.maxStuckChecksBeforeRecovery {
          print("[AssistantPlaybackEngine] Attempting stuck playback recovery")
          attemptPlaybackRecovery()
        }
      } else if timeSinceLastDrain < Self.stuckDetectionThresholdMs {
        consecutiveStuckChecks = 0
      }
    }

    // Log backpressure state for observability but DO NOT drop audio chunks.
    // Dropping chunks causes truncated/missing audio on Bluetooth HFP where the hardware
    // drain rate is much slower than the server streaming rate.
    if pendingBufferDurationMs >= Self.maxPendingDurationMs || pendingBufferCount >= Self.maxPendingBuffers {
      print("[AssistantPlaybackEngine] Backpressure: high queue depth, pendingBufferCount=\(pendingBufferCount), pendingDurationMs=\(Int(pendingBufferDurationMs)), chunkDurationMs=\(Int(bufferDurationMs))")
    }

    // IMPORTANT: Ensure engine is running and player node is connected BEFORE incrementing
    // pendingBufferCount and calling scheduleBuffer. If reconnection is needed after
    // scheduleBuffer is called, AVAudioEngine.connect() resets the player node and discards
    // the already-queued buffer without firing its completion callback — leaking the count.
    try ensureEngineRunning(context: "pre_play")

    // Check if player node is actually connected and attempt reconnection if needed
    if audioEngine.outputConnectionPoints(for: playerNode, outputBus: 0).isEmpty {
      print("[AssistantPlaybackEngine] Player node disconnected, attempting reconnection")
      reconnectPlayerNode()

      // Verify reconnection succeeded
      guard !audioEngine.outputConnectionPoints(for: playerNode, outputBus: 0).isEmpty else {
        throw AssistantPlaybackError.engineStartFailed("Player node is disconnected from output graph.")
      }
      print("[AssistantPlaybackEngine] Reconnection successful, continuing playback")
    }

    if !playerNode.isPlaying {
      print("[AssistantPlaybackEngine] Starting player node")
      playerNode.play()
    }

    // Now it is safe to count and schedule: the graph is stable.
    pendingBufferCount += 1
    pendingBufferDurationMs += bufferDurationMs
    lastBufferScheduledAtMs = nowMs
    playerNode.scheduleBuffer(buffer) { [weak self, bufferDurationMs] in
      Task { @MainActor [weak self] in
        guard let self else { return }
        self.pendingBufferCount = max(0, self.pendingBufferCount - 1)
        self.pendingBufferDurationMs = max(0, self.pendingBufferDurationMs - bufferDurationMs)
        self.lastBufferDrainedAtMs = Self.nowMs()
      }
    }
    print("[AssistantPlaybackEngine] Buffer scheduled, playerNode.isPlaying: \(playerNode.isPlaying)")
  }

  public func handlePlaybackControl(_ payload: PlaybackControlPayload) {
    switch payload.command {
    case .startResponse:
      startResponse()
    case .stopResponse:
      stopResponse()
    case .cancelResponse:
      cancelResponse()
    }
  }

  public func startResponse() {
    // Reset cancels all scheduled buffers without stopping the render thread.
    // Using stop() here would trigger a spurious route-change notification cycle that
    // temporarily disconnects the player node, causing the first scheduled buffer to
    // be lost and inflate pendingBufferCount by 1 on every response.
    playerNode.reset()
    pendingBufferCount = 0
    pendingBufferDurationMs = 0
    consecutiveStuckChecks = 0
    lastBufferScheduledAtMs = 0
    lastBufferDrainedAtMs = Self.nowMs()  // Reset to avoid false stuck detection
    print("[AssistantPlaybackEngine] startResponse: flushed playback queue")
    logAudioPipelineState(context: "startResponse")
    publishRouteUpdate()
  }

  public func stopResponse() {
    // `stop_response` indicates the server has finished streaming chunks.
    // Do not hard-stop here; stopping immediately can truncate queued audio
    // before it reaches the Bluetooth route.
  }

  public func cancelResponse() {
    playerNode.stop()
    playerNode.reset()
    pendingBufferCount = 0
    pendingBufferDurationMs = 0
    consecutiveStuckChecks = 0
    print("[AssistantPlaybackEngine] cancelResponse: flushed playback queue")
  }

  public func shutdown() {
    playerNode.stop()
    pendingBufferCount = 0
    pendingBufferDurationMs = 0
    if isPlayerNodeAttached {
      audioEngine.detach(playerNode)
      isPlayerNodeAttached = false
      isPlayerNodeConnected = false
    }
    // Only stop the engine if we own it (created it internally)
    if ownsEngine {
      audioEngine.stop()
    }
    currentFormat = nil
  }

  public func currentRouteDescription() -> String {
    let outputs = audioSession.currentRoute.outputs.map(\.portType.rawValue)
    return outputs.joined(separator: ",")
  }

  private func connectPlayerNodeIfNeeded(for format: AssistantAudioFormat) throws {
    guard let avFormat = avAudioFormat(for: format) else {
      throw AssistantPlaybackError.unableToBuildAudioFormat
    }

    ensurePlayerNodeAttached()
    
    // Use flag as fast-path hint, but verify against actual engine state.
    // The flag can become stale after graph disruptions.
    let actuallyConnected = !audioEngine.outputConnectionPoints(for: playerNode, outputBus: 0).isEmpty
    if isPlayerNodeConnected && actuallyConnected {
      return
    }
    
    // If flag says connected but engine says not, correct the stale flag
    if isPlayerNodeConnected && !actuallyConnected {
      print("[AssistantPlaybackEngine] Correcting stale isPlayerNodeConnected flag")
      isPlayerNodeConnected = false
    }
    
    audioEngine.connect(playerNode, to: audioEngine.mainMixerNode, format: avFormat)
    isPlayerNodeConnected = true
  }

  /// Attempts to reconnect the player node to the audio graph.
  /// Call this when the connection has been invalidated (e.g., background transitions, route changes).
  private func reconnectPlayerNode() {
    guard isPlayerNodeAttached else {
      print("[AssistantPlaybackEngine] Cannot reconnect: player node not attached")
      return
    }

    print("[AssistantPlaybackEngine] Reconnecting player node to output graph")

    // Mark as disconnected so connectPlayerNodeIfNeeded will re-establish the connection
    isPlayerNodeConnected = false

    // Try to reconnect with the current format
    let format = currentFormat ?? Self.graphFormat
    do {
      try connectPlayerNodeIfNeeded(for: format)
      print("[AssistantPlaybackEngine] Player node reconnected successfully")
    } catch {
      print("[AssistantPlaybackEngine] Failed to reconnect player node: \(error.localizedDescription)")
    }
  }

  /// Checks if the player node is actually connected to the output graph.
  /// The `isPlayerNodeConnected` flag can become stale after graph disruptions.
  private func isPlayerNodeActuallyConnected() -> Bool {
    guard isPlayerNodeAttached else { return false }
    return !audioEngine.outputConnectionPoints(for: playerNode, outputBus: 0).isEmpty
  }

  /// Call this when the app enters background to prepare for graph disruption.
  public func prepareForBackground() {
    print("[AssistantPlaybackEngine] Preparing for background")
    // Mark connection as potentially stale - will be verified on next playback attempt
    isPlayerNodeConnected = false
  }

  /// Call this when the app returns to foreground to restore the audio graph.
  public func restoreFromBackground() {
    print("[AssistantPlaybackEngine] Restoring from background")
    if !isPlayerNodeActuallyConnected() {
      reconnectPlayerNode()
    }
  }

  private func startEngineIfNeeded() throws {
    ensurePlayerNodeAttached()
    
    // Start the engine if it's not running, regardless of ownership.
    // When using a shared engine, the greeting audio may arrive before AudioCollectionManager.start()
    // completes. It's safe to start the shared engine from here since AVAudioEngine supports
    // concurrent input tap and output playback.
    if !audioEngine.isRunning {
      print("[AssistantPlaybackEngine] Engine not running, starting it now (ownsEngine: \(ownsEngine))")
      do {
        audioEngine.prepare()
        try audioEngine.start()
        print("[AssistantPlaybackEngine] Engine started successfully")
      } catch {
        print("[AssistantPlaybackEngine] Failed to start engine: \(error.localizedDescription)")
        throw AssistantPlaybackError.engineStartFailed(error.localizedDescription)
      }
    }
  }

  private func ensureEngineRunning(context: String) throws {
    if !audioEngine.isRunning {
      print("[AssistantPlaybackEngine] Engine not running (\(context)); preparing/start")
      audioEngine.prepare()
      do {
        try audioEngine.start()
      } catch {
        print("[AssistantPlaybackEngine] Failed engine start (\(context)): \(error.localizedDescription)")
        throw AssistantPlaybackError.engineStartFailed(error.localizedDescription)
      }
    } else {
      // Graph changes on a shared engine can still leave the engine effectively not
      // render-ready; issue a start attempt as a no-op/recovery.
      do {
        try audioEngine.start()
      } catch {
        print("[AssistantPlaybackEngine] Start reassert failed (\(context)): \(error.localizedDescription)")
      }
    }

    if !audioEngine.isRunning {
      throw AssistantPlaybackError.engineStartFailed("Engine is not running (\(context))")
    }
  }

  private func avAudioFormat(for format: AssistantAudioFormat) -> AVAudioFormat? {
    AVAudioFormat(
      commonFormat: .pcmFormatInt16,
      sampleRate: Double(format.sampleRate),
      channels: AVAudioChannelCount(format.channels),
      interleaved: false
    )
  }

  private func ensurePlayerNodeAttached() {
    guard !isPlayerNodeAttached else { return }
    audioEngine.attach(playerNode)
    isPlayerNodeAttached = true
  }

  private func publishRouteUpdate(notification: Notification? = nil) {
    let route = currentRouteDescription()
    onRouteChanged?(route)

    // Log detailed route change information for diagnostics
    logRouteChange(notification: notification)

    // Route changes can invalidate the audio graph connection
    // Check and reconnect if needed
    if isPlayerNodeAttached && !isPlayerNodeActuallyConnected() {
      print("[AssistantPlaybackEngine] Route change invalidated player node connection, reconnecting")
      reconnectPlayerNode()
    }

    if !isBluetoothRouteActive(route: audioSession.currentRoute) {
      onRouteIssue?("Assistant playback route is not on glasses/Bluetooth output (\(route))")
    }
  }

  /// Logs detailed route change information for diagnostics.
  private func logRouteChange(notification: Notification?) {
    let route = audioSession.currentRoute
    let inputPorts = route.inputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ", ")
    let outputPorts = route.outputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ", ")
    let category = audioSession.category.rawValue
    let mode = audioSession.mode.rawValue

    var reasonStr = "unknown"
    if let userInfo = notification?.userInfo,
       let reasonRaw = userInfo[AVAudioSessionRouteChangeReasonKey] as? UInt,
       let reason = AVAudioSession.RouteChangeReason(rawValue: reasonRaw) {
      reasonStr = routeChangeReasonDescription(reason)
    }

    print("[AssistantPlaybackEngine] audio.route_change: reason=\(reasonStr), category=\(category), mode=\(mode), inputs=[\(inputPorts)], outputs=[\(outputPorts)], pendingBufferCount=\(pendingBufferCount), pendingDurationMs=\(Int(pendingBufferDurationMs))")
  }

  private func routeChangeReasonDescription(_ reason: AVAudioSession.RouteChangeReason) -> String {
    switch reason {
    case .unknown: return "unknown"
    case .newDeviceAvailable: return "newDeviceAvailable"
    case .oldDeviceUnavailable: return "oldDeviceUnavailable"
    case .categoryChange: return "categoryChange"
    case .override: return "override"
    case .wakeFromSleep: return "wakeFromSleep"
    case .noSuitableRouteForCategory: return "noSuitableRouteForCategory"
    case .routeConfigurationChange: return "routeConfigurationChange"
    @unknown default: return "unknown(\(reason.rawValue))"
    }
  }

  private func isBluetoothRouteActive(route: AVAudioSessionRouteDescription) -> Bool {
    route.outputs.contains { output in
      output.portType == .bluetoothA2DP || output.portType == .bluetoothHFP || output.portType == .bluetoothLE
    }
  }

  private func handleInterruption(_ type: AVAudioSession.InterruptionType?) {
    guard let type else { return }
    switch type {
    case .began:
      // Interruption started - mark connection as potentially stale
      print("[AssistantPlaybackEngine] Audio interruption began")
      isPlayerNodeConnected = false
    case .ended:
      // Interruption ended - attempt to restore the graph
      print("[AssistantPlaybackEngine] Audio interruption ended, restoring graph")
      if !isPlayerNodeActuallyConnected() {
        reconnectPlayerNode()
      }
      publishRouteUpdate()
    @unknown default:
      break
    }
  }

  private nonisolated static func interruptionType(from notification: Notification) -> AVAudioSession.InterruptionType? {
    guard
      let rawType = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
      let type = AVAudioSession.InterruptionType(rawValue: rawType)
    else {
      return nil
    }
    return type
  }

  /// Returns current time in milliseconds since 1970.
  private static func nowMs() -> Int64 {
    Int64(Date().timeIntervalSince1970 * 1000)
  }

  /// Logs detailed audio pipeline state for diagnostics.
  private func logAudioPipelineState(context: String) {
    let engineRunning = audioEngine.isRunning
    let playerPlaying = playerNode.isPlaying
    let playerNodeConnected = isPlayerNodeActuallyConnected()
    
    // Get format info from the audio chain
    let playerOutputFormat = playerNode.outputFormat(forBus: 0)
    let mixerInputFormat = audioEngine.mainMixerNode.inputFormat(forBus: 0)
    let outputNodeFormat = audioEngine.outputNode.outputFormat(forBus: 0)
    
    let route = audioSession.currentRoute
    let outputPorts = route.outputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ", ")
    
    print("[AssistantPlaybackEngine] Pipeline state (\(context)): engineRunning=\(engineRunning), playerPlaying=\(playerPlaying), playerConnected=\(playerNodeConnected)")
    print("[AssistantPlaybackEngine] Formats: playerOutput=\(playerOutputFormat.sampleRate)Hz/\(playerOutputFormat.channelCount)ch, mixerInput=\(mixerInputFormat.sampleRate)Hz/\(mixerInputFormat.channelCount)ch, outputNode=\(outputNodeFormat.sampleRate)Hz/\(outputNodeFormat.channelCount)ch")
    print("[AssistantPlaybackEngine] Route outputs: [\(outputPorts)]")
  }

  /// Attempts to recover from stuck playback by restarting the audio graph.
  private func attemptPlaybackRecovery() {
    logAudioPipelineState(context: "pre_recovery")
    
    // Stop and reset the player node
    playerNode.stop()
    playerNode.reset()
    
    // Clear tracking state
    let previousPendingCount = pendingBufferCount
    let previousPendingDuration = pendingBufferDurationMs
    pendingBufferCount = 0
    pendingBufferDurationMs = 0
    consecutiveStuckChecks = 0
    lastBufferDrainedAtMs = Self.nowMs()
    
    print("[AssistantPlaybackEngine] Recovery: cleared \(previousPendingCount) stuck buffers (~\(Int(previousPendingDuration))ms)")
    
    // Force reconnect the player node
    isPlayerNodeConnected = false
    do {
      try connectPlayerNodeIfNeeded(for: currentFormat ?? Self.graphFormat)
    } catch {
      print("[AssistantPlaybackEngine] Recovery: failed to reconnect player node: \(error.localizedDescription)")
    }
    
    // Restart the engine if needed
    if !audioEngine.isRunning {
      print("[AssistantPlaybackEngine] Recovery: engine stopped, attempting restart")
      audioEngine.prepare()
      do {
        try audioEngine.start()
        print("[AssistantPlaybackEngine] Recovery: engine restarted successfully")
      } catch {
        print("[AssistantPlaybackEngine] Recovery: failed to restart engine: \(error.localizedDescription)")
      }
    }
    
    logAudioPipelineState(context: "post_recovery")
  }
}

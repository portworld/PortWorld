// Core assistant playback engine types and shared state for streamed assistant audio responses.

import AVFAudio
import Foundation
import OSLog

public enum AssistantPlaybackError: Error, LocalizedError {
  case invalidBase64Chunk
  case unsupportedCodec(String)
  case unsupportedSampleRate(Int)
  case unsupportedChannelCount(Int)
  case invalidPCMByteCount(Int)
  case formatMismatch(expected: AssistantAudioFormat, received: AssistantAudioFormat)
  case unableToBuildAudioFormat
  case unableToAllocateBuffer
  case engineStartFailed(String)
  case invalidAudioSessionCategory(expected: String, actual: String)

  public var errorDescription: String? {
    switch self {
    case .invalidBase64Chunk:
      return "Audio chunk payload is not valid base64."
    case .unsupportedCodec(let codec):
      return "Unsupported audio codec '\(codec)'. Expected pcm_s16le."
    case .unsupportedSampleRate(let sampleRate):
      return "Unsupported sample rate '\(sampleRate)'. Expected 24000 Hz."
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
    case .engineStartFailed(let message):
      return "Failed to start playback engine: \(message)"
    case .invalidAudioSessionCategory(let expected, let actual):
      return "Invalid audio session category '\(actual)'. Expected '\(expected)' before assistant playback."
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

  var description: String {
    "\(codec)@\(sampleRate)Hz/\(channels)ch"
  }
}

struct AssistantPlaybackQueueState {
  private(set) var pendingBufferCount: Int = 0
  private(set) var pendingBufferDurationMs: Double = 0
  private(set) var lastBufferDrainedAtMs: Int64 = 0
  private(set) var lastBufferScheduledAtMs: Int64 = 0
  private(set) var consecutiveStuckChecks: Int = 0

  mutating func recordScheduledBuffer(durationMs: Double, nowMs: Int64) {
    pendingBufferCount += 1
    pendingBufferDurationMs += durationMs
    lastBufferScheduledAtMs = nowMs
  }

  mutating func recordBufferDrained(durationMs: Double, nowMs: Int64) {
    if pendingBufferCount > 0 {
      pendingBufferCount -= 1
    } else {
      pendingBufferCount = 0
    }

    if pendingBufferDurationMs >= durationMs {
      pendingBufferDurationMs -= durationMs
    } else {
      pendingBufferDurationMs = 0
    }

    lastBufferDrainedAtMs = nowMs
  }

  mutating func shouldAttemptRecovery(
    nowMs: Int64,
    thresholdMs: Int64,
    maxConsecutiveChecks: Int
  ) -> Bool {
    guard pendingBufferCount > 0, lastBufferScheduledAtMs > 0 else {
      return false
    }

    let timeSinceLastDrain = nowMs - lastBufferDrainedAtMs
    let timeSinceLastSchedule = nowMs - lastBufferScheduledAtMs

    if timeSinceLastSchedule < 500 && timeSinceLastDrain > thresholdMs {
      consecutiveStuckChecks += 1
      return consecutiveStuckChecks >= maxConsecutiveChecks
    }

    if timeSinceLastDrain < thresholdMs {
      consecutiveStuckChecks = 0
    }
    return false
  }

  mutating func resetForStartResponse(nowMs: Int64) {
    pendingBufferCount = 0
    pendingBufferDurationMs = 0
    consecutiveStuckChecks = 0
    lastBufferScheduledAtMs = 0
    lastBufferDrainedAtMs = nowMs
  }

  mutating func resetForCancelResponse() {
    pendingBufferCount = 0
    pendingBufferDurationMs = 0
    consecutiveStuckChecks = 0
  }

  mutating func resetForRecovery(nowMs: Int64) {
    pendingBufferCount = 0
    pendingBufferDurationMs = 0
    consecutiveStuckChecks = 0
    lastBufferDrainedAtMs = nowMs
  }
}

@MainActor
public final class AssistantPlaybackEngine: AssistantPlaybackControlling {
  public var onRouteChanged: ((String) -> Void)?
  public var onRouteIssue: ((String) -> Void)?

  let audioSession: AVAudioSession
  let audioEngine: AVAudioEngine
  let playerNode: AVAudioPlayerNode
  let ownsEngine: Bool
  var currentFormat: AssistantAudioFormat?
  var routeObserver: NSObjectProtocol?
  var interruptionObserver: NSObjectProtocol?
  var configurationObserver: NSObjectProtocol?
  var isPlayerNodeAttached = false
  var isPlayerNodeConnected = false
  static let graphFormat = AssistantAudioFormat(codec: "pcm_s16le", sampleRate: 24_000, channels: 1)
  static let logger = Logger(
    subsystem: Bundle.main.bundleIdentifier ?? "PortWorld",
    category: "AssistantPlaybackEngine"
  )
  var queueState = AssistantPlaybackQueueState()
  var hasLoggedFirstAppend = false
  var hasLoggedFirstSchedule = false
  var hasLoggedFirstDrain = false
  var hasLoggedFirstStartResponse = false
  var hasLoggedFirstFailureState = false
  var hasLoggedBackpressureHighWater = false
  var hasLoggedBackpressureCritical = false

  /// Threshold (ms) for detecting stuck playback. If buffers were scheduled
  /// this recently but no drain callback fired, we may be stuck.
  let stuckDetectionThresholdMs: Int64
  let nowMsProvider: () -> Int64

  /// Max consecutive stuck checks before attempting recovery.
  static let maxStuckChecksBeforeRecovery: Int = 3

  /// Maximum pending audio duration (ms) before backpressure kicks in.
  /// 1 second keeps playback responsive enough for barge-in without overreacting to small bursts.
  static let maxPendingDurationMs: Double = 1000

  /// High water mark (ms) at which we signal backpressure to callers.
  /// Set lower than maxPendingDurationMs to surface queue growth before it becomes user-visible.
  static let backpressureHighWaterMs: Double = 500

  /// Recovery mark below which the queue is considered healthy again.
  static let backpressureRecoveryMs: Double = 250

  /// Whether the playback queue is under backpressure (pending audio exceeds high water mark).
  /// Callers can use this to throttle upstream chunk generation.
  public var pendingBufferCount: Int { queueState.pendingBufferCount }
  public var pendingBufferDurationMs: Double { queueState.pendingBufferDurationMs }

  public var isBackpressured: Bool {
    pendingBufferDurationMs > Self.backpressureHighWaterMs
  }

  public func hasActivePendingPlayback() -> Bool {
    pendingBufferCount > 0
  }

  /// Maximum number of pending buffers before backpressure kicks in.
  /// With backend output rechunked to 40ms frames, 25 buffers ≈ 1 second of queued audio.
  static let maxPendingBuffers = 25

  /// Creates a playback engine.
  /// - Parameters:
  ///   - audioSession: The AVAudioSession to use for route information.
  ///   - audioEngine: The AVAudioEngine to attach the player node to. If nil, creates a new engine internally.
  ///   - playerNode: The player node for audio playback.
  public init(
    audioSession: AVAudioSession = .sharedInstance(),
    audioEngine: AVAudioEngine? = nil,
    playerNode: AVAudioPlayerNode = AVAudioPlayerNode(),
    stuckDetectionThresholdMs: Int64 = 1_500
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
    self.stuckDetectionThresholdMs = max(250, stuckDetectionThresholdMs)
    self.nowMsProvider = { Clocks.nowMs() }

    // Attach once, then connect lazily from the first inbound chunk format.
    // Avoid disconnect/reconnect churn on a shared engine.
    ensurePlayerNodeAttached()
    do {
      try connectPlayerNodeIfNeeded(for: Self.graphFormat)
      currentFormat = Self.graphFormat
    } catch {
      debugLog("[AssistantPlaybackEngine] Failed to connect playback graph at init: \(error.localizedDescription)")
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

    configurationObserver = NotificationCenter.default.addObserver(
      forName: Notification.Name.AVAudioEngineConfigurationChange,
      object: audioEngine,
      queue: .main
    ) { [weak self] _ in
      MainActor.assumeIsolated {
        self?.handleEngineConfigurationChange()
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
    if let configurationObserver {
      NotificationCenter.default.removeObserver(configurationObserver)
    }
  }

  public func configureBluetoothHFPRoute() throws {
    // AudioCollectionManager owns AVAudioSession lifecycle/category for capture+playback.
    // Playback intentionally avoids mutating shared AVAudioSession state.
    // Log current routing state for diagnostics.
    logCurrentRouteState(context: "configureBluetoothHFPRoute")
  }

  public func currentRouteDescription() -> String {
    let outputs = audioSession.currentRoute.outputs.map(\.portType.rawValue)
    return outputs.joined(separator: ",")
  }

  func debugLog(_ message: String) {
#if DEBUG
    Self.logger.debug("\(message, privacy: .public)")
#endif
  }
}

// Playback queue handling and response-control commands for the assistant playback engine.

import AVFAudio
import Foundation

extension AssistantPlaybackEngine {
  public func appendPCMData(_ pcmData: Data, format incomingFormat: AssistantAudioFormat) throws {
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

    hasLoggedFirstAppend = true

    guard audioSession.category == .playAndRecord else {
      let expectedCategory = AVAudioSession.Category.playAndRecord.rawValue
      let actualCategory = audioSession.category.rawValue
      logFailureStateOnce(context: "invalid_audio_session_category")
      throw AssistantPlaybackError.invalidAudioSessionCategory(expected: expectedCategory, actual: actualCategory)
    }

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

    let bufferDurationMs = Double(frameCount) / Double(incomingFormat.sampleRate) * 1000.0
    let nowMs = nowMsProvider()

    if queueState.shouldAttemptRecovery(
      nowMs: nowMs,
      thresholdMs: stuckDetectionThresholdMs,
      maxConsecutiveChecks: Self.maxStuckChecksBeforeRecovery
    ) {
      let timeSinceLastDrain = nowMs - queueState.lastBufferDrainedAtMs
      debugLog("[AssistantPlaybackEngine] Stuck playback detected: pendingCount=\(pendingBufferCount), timeSinceLastDrain=\(timeSinceLastDrain)ms, consecutiveChecks=\(queueState.consecutiveStuckChecks)")
      debugLog("[AssistantPlaybackEngine] Attempting stuck playback recovery")
      attemptPlaybackRecovery()
    }

    try recoverAudioGraphIfNeeded(context: "pre_play")

    if !playerNode.isPlaying {
      playerNode.play()
    }

    queueState.recordScheduledBuffer(durationMs: bufferDurationMs, nowMs: nowMs)
    logQueuePressureTransitionIfNeeded(context: "append", chunkDurationMs: bufferDurationMs)
    playerNode.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self, bufferDurationMs] callbackType in
      Task { @MainActor [weak self] in
        guard let self else { return }
        self.queueState.recordBufferDrained(durationMs: bufferDurationMs, nowMs: self.nowMsProvider())
        self.logQueuePressureTransitionIfNeeded(context: "drain", chunkDurationMs: bufferDurationMs)
        if self.hasLoggedFirstDrain == false {
          self.hasLoggedFirstDrain = true
        }
      }
    }
    if hasLoggedFirstSchedule == false {
      hasLoggedFirstSchedule = true
    }
  }

  func handlePlaybackControl(_ payload: AssistantPlaybackControlPayload) {
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
    if pendingBufferCount > 0 {
      debugLog(
        "[AssistantPlaybackEngine] startResponse: flushing stale pending audio pendingCount=\(pendingBufferCount) pendingDurationMs=\(Int(pendingBufferDurationMs))"
      )
      playerNode.stop()
      playerNode.reset()
      queueState.resetForCancelResponse()
    }

    queueState.resetForStartResponse(nowMs: nowMsProvider())
    hasLoggedFirstAppend = false
    hasLoggedFirstSchedule = false
    hasLoggedFirstDrain = false
    hasLoggedFirstFailureState = false
    hasLoggedBackpressureHighWater = false
    hasLoggedBackpressureCritical = false
    hasLoggedFirstStartResponse = true
  }

  public func stopResponse() {
    // `stop_response` indicates the server has finished streaming chunks.
    // Do not hard-stop here; stopping immediately can truncate queued audio
    // before it reaches the Bluetooth route.
  }

  public func cancelResponse() {
    playerNode.stop()
    playerNode.reset()
    queueState.resetForCancelResponse()
    hasLoggedFirstAppend = false
    hasLoggedFirstSchedule = false
    hasLoggedFirstDrain = false
    hasLoggedFirstFailureState = false
    hasLoggedBackpressureHighWater = false
    hasLoggedBackpressureCritical = false
    debugLog("[AssistantPlaybackEngine] cancelResponse: flushed playback queue for interruption")
  }

  public func shutdown() {
    debugLog(
      "[AssistantPlaybackEngine] shutdown: pendingBufferCount=\(pendingBufferCount) pendingDurationMs=\(Int(pendingBufferDurationMs)) engineRunning=\(audioEngine.isRunning)"
    )
    playerNode.stop()
    queueState.resetForCancelResponse()
    hasLoggedFirstAppend = false
    hasLoggedFirstSchedule = false
    hasLoggedFirstDrain = false
    hasLoggedFirstFailureState = false
    hasLoggedBackpressureHighWater = false
    hasLoggedBackpressureCritical = false
    if isPlayerNodeAttached {
      audioEngine.detach(playerNode)
      isPlayerNodeAttached = false
      isPlayerNodeConnected = false
    }
    if ownsEngine {
      audioEngine.stop()
    }
    currentFormat = nil
    debugLog("[AssistantPlaybackEngine] shutdown complete")
  }

  func logQueuePressureTransitionIfNeeded(context: String, chunkDurationMs: Double) {
    if pendingBufferDurationMs >= Self.backpressureHighWaterMs, hasLoggedBackpressureHighWater == false {
      hasLoggedBackpressureHighWater = true
      debugLog(
        "[AssistantPlaybackEngine] Queue high-water crossed (\(context)): pendingBufferCount=\(pendingBufferCount) pendingDurationMs=\(Int(pendingBufferDurationMs)) chunkDurationMs=\(Int(chunkDurationMs))"
      )
    }

    if pendingBufferDurationMs >= Self.maxPendingDurationMs, hasLoggedBackpressureCritical == false {
      hasLoggedBackpressureCritical = true
      debugLog(
        "[AssistantPlaybackEngine] Backpressure: high queue depth, pendingBufferCount=\(pendingBufferCount), pendingDurationMs=\(Int(pendingBufferDurationMs)), chunkDurationMs=\(Int(chunkDurationMs))"
      )
    }

    if pendingBufferDurationMs <= Self.backpressureRecoveryMs {
      if hasLoggedBackpressureHighWater || hasLoggedBackpressureCritical {
        debugLog(
          "[AssistantPlaybackEngine] Queue recovered (\(context)): pendingBufferCount=\(pendingBufferCount) pendingDurationMs=\(Int(pendingBufferDurationMs))"
        )
      }
      hasLoggedBackpressureHighWater = false
      hasLoggedBackpressureCritical = false
    }
  }
}

// Audio-graph connection and recovery helpers for the assistant playback engine.

import AVFAudio
import Foundation

extension AssistantPlaybackEngine {
  func connectPlayerNodeIfNeeded(for format: AssistantAudioFormat) throws {
    guard let avFormat = avAudioFormat(for: format) else {
      throw AssistantPlaybackError.unableToBuildAudioFormat
    }

    ensurePlayerNodeAttached()

    let actuallyConnected = !audioEngine.outputConnectionPoints(for: playerNode, outputBus: 0).isEmpty
    if isPlayerNodeConnected && actuallyConnected {
      return
    }

    if isPlayerNodeConnected && !actuallyConnected {
      debugLog("[AssistantPlaybackEngine] Correcting stale isPlayerNodeConnected flag")
      isPlayerNodeConnected = false
    }

    audioEngine.connect(playerNode, to: audioEngine.mainMixerNode, format: avFormat)
    isPlayerNodeConnected = true
  }

  func reconnectPlayerNode() {
    guard isPlayerNodeAttached else {
      debugLog("[AssistantPlaybackEngine] Cannot reconnect: player node not attached")
      return
    }

    debugLog("[AssistantPlaybackEngine] Reconnecting player node to output graph")
    isPlayerNodeConnected = false

    let format = currentFormat ?? Self.graphFormat
    do {
      try connectPlayerNodeIfNeeded(for: format)
      debugLog("[AssistantPlaybackEngine] Player node reconnected successfully")
    } catch {
      debugLog("[AssistantPlaybackEngine] Failed to reconnect player node: \(error.localizedDescription)")
    }
  }

  func isPlayerNodeActuallyConnected() -> Bool {
    guard isPlayerNodeAttached else { return false }
    return !audioEngine.outputConnectionPoints(for: playerNode, outputBus: 0).isEmpty
  }

  func ensureEngineRunning(context: String) throws {
    if !audioEngine.isRunning {
      debugLog("[AssistantPlaybackEngine] Engine not running (\(context)); preparing/start")
      audioEngine.prepare()
      do {
        try audioEngine.start()
      } catch {
        debugLog("[AssistantPlaybackEngine] Failed engine start (\(context)): \(error.localizedDescription)")
        logFailureStateOnce(context: "ensure_engine_running_failed_\(context)")
        throw AssistantPlaybackError.engineStartFailed(error.localizedDescription)
      }
    } else {
      do {
        try audioEngine.start()
      } catch {
        debugLog("[AssistantPlaybackEngine] Start reassert failed (\(context)): \(error.localizedDescription)")
      }
    }

    if !audioEngine.isRunning {
      throw AssistantPlaybackError.engineStartFailed("Engine is not running (\(context))")
    }
  }

  func avAudioFormat(for format: AssistantAudioFormat) -> AVAudioFormat? {
    AVAudioFormat(
      commonFormat: .pcmFormatInt16,
      sampleRate: Double(format.sampleRate),
      channels: AVAudioChannelCount(format.channels),
      interleaved: false
    )
  }

  func ensurePlayerNodeAttached() {
    guard !isPlayerNodeAttached else { return }
    audioEngine.attach(playerNode)
    isPlayerNodeAttached = true
  }

  func recoverAudioGraphIfNeeded(context: String) throws {
    ensurePlayerNodeAttached()
    let format = currentFormat ?? Self.graphFormat

    if !isPlayerNodeActuallyConnected() {
      debugLog("[AssistantPlaybackEngine] Recovering player node connection (\(context))")
      try connectPlayerNodeIfNeeded(for: format)
    }

    try ensureEngineRunning(context: context)

    if !isPlayerNodeActuallyConnected() {
      logFailureStateOnce(context: "player_node_disconnected_\(context)")
      throw AssistantPlaybackError.engineStartFailed("Player node is disconnected from output graph.")
    }
  }

  func attemptPlaybackRecovery() {
    logAudioPipelineState(context: "pre_recovery")

    playerNode.stop()
    playerNode.reset()

    let previousPendingCount = pendingBufferCount
    let previousPendingDuration = pendingBufferDurationMs
    queueState.resetForRecovery(nowMs: nowMsProvider())

    debugLog("[AssistantPlaybackEngine] Recovery: cleared \(previousPendingCount) stuck buffers (~\(Int(previousPendingDuration))ms)")

    isPlayerNodeConnected = false
    do {
      try recoverAudioGraphIfNeeded(context: "stuck_playback_recovery")
    } catch {
      debugLog("[AssistantPlaybackEngine] Recovery: failed to reconnect player node: \(error.localizedDescription)")
    }

    logAudioPipelineState(context: "post_recovery")
  }
}

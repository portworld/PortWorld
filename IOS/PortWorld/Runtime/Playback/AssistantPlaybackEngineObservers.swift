// Route, interruption, and engine-configuration observers for the assistant playback engine.

import AVFAudio
import Foundation

extension AssistantPlaybackEngine {
  func logCurrentRouteState(context: String) {
    let route = audioSession.currentRoute
    let inputPorts = route.inputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ", ")
    let outputPorts = route.outputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ", ")
    let category = audioSession.category.rawValue
    let mode = audioSession.mode.rawValue
    debugLog("[AssistantPlaybackEngine] Route state (\(context)): category=\(category), mode=\(mode), inputs=[\(inputPorts)], outputs=[\(outputPorts)]")
  }

  public func prepareForBackground() {
    debugLog("[AssistantPlaybackEngine] Preparing for background")
    isPlayerNodeConnected = false
  }

  public func restoreFromBackground() {
    debugLog("[AssistantPlaybackEngine] Restoring from background")
    do {
      try recoverAudioGraphIfNeeded(context: "foreground_restore")
    } catch {
      debugLog("[AssistantPlaybackEngine] Foreground restore failed: \(error.localizedDescription)")
    }
  }

  func publishRouteUpdate(notification: Notification? = nil) {
    let route = currentRouteDescription()
    onRouteChanged?(route)

    if routeIssueDescription(for: audioSession.currentRoute) != nil || pendingBufferCount > 0 {
      logRouteChange(notification: notification)
    }

    if isPlayerNodeAttached && !isPlayerNodeActuallyConnected() {
      debugLog("[AssistantPlaybackEngine] Route change invalidated player node connection, reconnecting")
      do {
        try recoverAudioGraphIfNeeded(context: "route_change")
      } catch {
        debugLog("[AssistantPlaybackEngine] Route change recovery failed: \(error.localizedDescription)")
      }
    }

    if let routeIssue = routeIssueDescription(for: audioSession.currentRoute) {
      onRouteIssue?(routeIssue)
    }
  }

  func logRouteChange(notification: Notification?) {
    let route = audioSession.currentRoute
    let inputPorts = route.inputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ", ")
    let outputPorts = route.outputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ", ")
    let category = audioSession.category.rawValue
    let mode = audioSession.mode.rawValue

    var reasonStr = "unknown"
    if let userInfo = notification?.userInfo,
       let reasonRaw = userInfo[AVAudioSessionRouteChangeReasonKey] as? UInt,
       let reason = AVAudioSession.RouteChangeReason(rawValue: reasonRaw)
    {
      reasonStr = routeChangeReasonDescription(reason)
    }

    debugLog("[AssistantPlaybackEngine] audio.route_change: reason=\(reasonStr), category=\(category), mode=\(mode), inputs=[\(inputPorts)], outputs=[\(outputPorts)], pendingBufferCount=\(pendingBufferCount), pendingDurationMs=\(Int(pendingBufferDurationMs))")
  }

  func routeChangeReasonDescription(_ reason: AVAudioSession.RouteChangeReason) -> String {
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

  func routeIssueDescription(for route: AVAudioSessionRouteDescription) -> String? {
    guard route.outputs.isEmpty == false else {
      return "Assistant playback route is unavailable."
    }

    if route.outputs.contains(where: { $0.portType == .builtInReceiver }) {
      return "Assistant playback route resolved to receiver (\(currentRouteDescription()))"
    }

    return nil
  }

  func handleInterruption(_ type: AVAudioSession.InterruptionType?) {
    guard let type else { return }
    switch type {
    case .began:
      debugLog("[AssistantPlaybackEngine] Audio interruption began")
      isPlayerNodeConnected = false
    case .ended:
      debugLog("[AssistantPlaybackEngine] Audio interruption ended, restoring graph")
      do {
        try recoverAudioGraphIfNeeded(context: "interruption_ended")
      } catch {
        debugLog("[AssistantPlaybackEngine] Interruption recovery failed: \(error.localizedDescription)")
      }
      publishRouteUpdate()
    @unknown default:
      break
    }
  }

  nonisolated static func interruptionType(from notification: Notification) -> AVAudioSession.InterruptionType? {
    guard
      let rawType = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
      let type = AVAudioSession.InterruptionType(rawValue: rawType)
    else {
      return nil
    }
    return type
  }

  func handleEngineConfigurationChange() {
    debugLog("[AssistantPlaybackEngine] Audio engine configuration changed")
    isPlayerNodeConnected = false
    do {
      try recoverAudioGraphIfNeeded(context: "engine_configuration_change")
    } catch {
      debugLog("[AssistantPlaybackEngine] Engine configuration recovery failed: \(error.localizedDescription)")
    }
    publishRouteUpdate()
  }

  func logAudioPipelineState(context: String) {
    let engineRunning = audioEngine.isRunning
    let playerPlaying = playerNode.isPlaying
    let playerNodeConnected = isPlayerNodeActuallyConnected()

    let playerOutputFormat = playerNode.outputFormat(forBus: 0)
    let mixerInputFormat = audioEngine.mainMixerNode.inputFormat(forBus: 0)
    let outputNodeFormat = audioEngine.outputNode.outputFormat(forBus: 0)

    let route = audioSession.currentRoute
    let outputPorts = route.outputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ", ")

    debugLog("[AssistantPlaybackEngine] Pipeline state (\(context)): engineRunning=\(engineRunning), playerPlaying=\(playerPlaying), playerConnected=\(playerNodeConnected)")
    debugLog("[AssistantPlaybackEngine] Formats: playerOutput=\(playerOutputFormat.sampleRate)Hz/\(playerOutputFormat.channelCount)ch, mixerInput=\(mixerInputFormat.sampleRate)Hz/\(mixerInputFormat.channelCount)ch, outputNode=\(outputNodeFormat.sampleRate)Hz/\(outputNodeFormat.channelCount)ch")
    debugLog("[AssistantPlaybackEngine] Route outputs: [\(outputPorts)]")
  }

  func logFailureStateOnce(context: String) {
    guard hasLoggedFirstFailureState == false else { return }
    hasLoggedFirstFailureState = true
    logAudioPipelineState(context: context)
  }
}

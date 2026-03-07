// Inbound receive loop and message decoding for the assistant backend session client.
import Foundation

extension BackendSessionClient {
  func runReceiveLoop() async {
    while !Task.isCancelled {
      guard let webSocketTask else { return }

      do {
        let message = try await webSocketTask.receive()
        switch message {
        case .string(let text):
          guard let data = text.data(using: .utf8) else { continue }
          try await handleControlMessage(data)
        case .data(let data):
          try await handleBinaryMessage(data)
        @unknown default:
          yieldEvent(.error("Unsupported websocket message kind."))
        }
      } catch is CancellationError {
        return
      } catch {
        if shouldIgnoreReceiveLoopError(error) {
          return
        }
        yieldEvent(.error(error.localizedDescription))
        return
      }
    }
  }

  func shouldIgnoreReceiveLoopError(_ error: Error) -> Bool {
    guard isLocallyDisconnecting else { return false }
    let normalized = error.localizedDescription.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    return normalized.contains("socket is not connected")
  }

  func handleControlMessage(_ data: Data) async throws {
    let rawEnvelope = try AssistantWSMessageCodec.decodeRawEnvelopeType(from: data)

    switch rawEnvelope {
    case AssistantWSInboundType.sessionState.rawValue:
      let envelope = try AssistantWSMessageCodec.decodeEnvelope(AssistantSessionStatePayload.self, from: data)
      if envelope.payload.state == .active {
        yieldEvent(.sessionReady)
      }
    case AssistantWSInboundType.transportUplinkAcknowledged.rawValue:
      let envelope = try AssistantWSMessageCodec.decodeEnvelope(AssistantRealtimeUplinkAckPayload.self, from: data)
      yieldEvent(.uplinkAcknowledged(envelope.payload))
    case AssistantWSInboundType.assistantPlaybackControl.rawValue:
      let envelope = try AssistantWSMessageCodec.decodeEnvelope(AssistantPlaybackControlPayload.self, from: data)
      lastPlaybackControlCommand = envelope.payload.command.rawValue
      yieldEvent(.playbackControl(envelope.payload))
    case AssistantWSInboundType.error.rawValue:
      let envelope = try AssistantWSMessageCodec.decodeEnvelope(AssistantRuntimeErrorPayload.self, from: data)
      debugLog("Inbound error code=\(envelope.payload.code) message=\(envelope.payload.message)")
      yieldEvent(.error(envelope.payload.message))
    default:
      break
    }
  }

  func handleBinaryMessage(_ data: Data) async throws {
    let frame = try AssistantBinaryFrameCodec.decode(data)
    guard frame.frameType == .serverAudio else { return }
    inboundServerAudioFrameCount += 1
    inboundServerAudioBytes += frame.payload.count
    lastInboundServerAudioBytes = frame.payload.count
    loggedFirstServerAudioFrame = true
    yieldEvent(.serverAudio(frame.payload))
  }
}

import Foundation

protocol RealtimeTransport: Sendable {
  var events: AsyncStream<TransportEvent> { get }

  func connect(config: TransportConfig) async throws
  func disconnect() async
  func sendAudio(_ buffer: Data, timestampMs: Int64) async throws
  func sendControl(_ message: TransportControlMessage) async throws
}

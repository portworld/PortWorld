import Combine
import Foundation
import Network

@MainActor
final class NWReachability: ObservableObject {
  enum ConnectivityState: Equatable {
    case unknown
    case connected
    case disconnected

    var isConnected: Bool? {
      switch self {
      case .unknown:
        return nil
      case .connected:
        return true
      case .disconnected:
        return false
      }
    }
  }

  @Published private(set) var connectivityState: ConnectivityState = .unknown
  var isConnected: Bool {
    connectivityState == .connected
  }

  // Optional hook for coordinator wiring before a concrete consumer is introduced.
  var onConnectivityStateChanged: ((ConnectivityState) -> Void)?
  var onConnectivityChanged: ((Bool) -> Void)?

  private var monitor: NWPathMonitor?
  private let monitorQueue = DispatchQueue(label: "com.portworld.reachability.monitor", qos: .utility)
  private var streamContinuations: [UUID: AsyncStream<ConnectivityState>.Continuation] = [:]

  func startMonitoring() {
    guard monitor == nil else { return }

    let monitor = NWPathMonitor()
    monitor.pathUpdateHandler = { [weak self] path in
      Task { @MainActor [weak self] in
        self?.handlePathUpdate(path)
      }
    }

    self.monitor = monitor
    monitor.start(queue: monitorQueue)
  }

  func stopMonitoring() {
    guard let monitor else { return }

    monitor.pathUpdateHandler = nil
    monitor.cancel()
    self.monitor = nil
  }

  func updatesStream() -> AsyncStream<ConnectivityState> {
    AsyncStream { continuation in
      let id = UUID()
      streamContinuations[id] = continuation

      continuation.yield(connectivityState)

      continuation.onTermination = { [weak self] _ in
        Task { @MainActor [weak self] in
          self?.streamContinuations.removeValue(forKey: id)
        }
      }
    }
  }

  deinit {
    monitor?.cancel()
  }

  private func handlePathUpdate(_ path: NWPath) {
    let nextState: ConnectivityState = path.status == .satisfied ? .connected : .disconnected
    guard nextState != connectivityState else { return }

    connectivityState = nextState
    onConnectivityStateChanged?(nextState)
    if let connected = nextState.isConnected {
      onConnectivityChanged?(connected)
    }

    for continuation in streamContinuations.values {
      continuation.yield(nextState)
    }
  }
}

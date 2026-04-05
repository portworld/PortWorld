import Foundation
import MWDATCore

enum GlassesSessionPhase: String {
  case inactive
  case starting
  case waitingForDevice = "waiting_for_device"
  case running
  case paused
  case stopping
  case failed
}

@MainActor
final class GlassesSessionCoordinator {
  struct Snapshot {
    var phase: GlassesSessionPhase = .inactive
    var sessionState: SessionState? = nil
    var activeDeviceID: DeviceIdentifier? = nil
    var activeDeviceName: String = "-"
    var errorMessage: String? = nil
  }

  var onSnapshotUpdated: ((Snapshot) -> Void)?

  private let wearables: WearablesInterface
  private let deviceSelector: AutoDeviceSelector
  private var snapshot = Snapshot()
  private var isStarted = false
  private var activeDeviceTask: Task<Void, Never>?
  private var linkStateListenerToken: AnyListenerToken?

  init(wearables: WearablesInterface) {
    self.wearables = wearables
    self.deviceSelector = AutoDeviceSelector(wearables: wearables)
    startObservingActiveDevice()
    publishSnapshot()
  }

  deinit {
    activeDeviceTask?.cancel()
    let linkStateListenerToken = self.linkStateListenerToken
    Task {
      await linkStateListenerToken?.cancel()
    }
  }

  func start() async throws {
    guard isStarted == false else {
      publishSnapshot()
      return
    }

    isStarted = true
    snapshot.errorMessage = nil
    if snapshot.activeDeviceID == nil {
      snapshot.phase = .waitingForDevice
      snapshot.sessionState = nil
    } else {
      snapshot.phase = .running
      snapshot.sessionState = .running
    }
    publishSnapshot()
  }

  func stop() async {
    guard isStarted else {
      snapshot.phase = .inactive
      snapshot.sessionState = .stopped
      publishSnapshot()
      return
    }

    snapshot.phase = .stopping
    publishSnapshot()

    isStarted = false
    snapshot.phase = .inactive
    snapshot.sessionState = .stopped
    publishSnapshot()
  }

  private func startObservingActiveDevice() {
    activeDeviceTask = Task { @MainActor [weak self] in
      guard let self else { return }
      for await activeDeviceID in deviceSelector.activeDeviceStream() {
        await handleActiveDeviceChange(activeDeviceID)
      }
    }
  }

  private func handleActiveDeviceChange(_ activeDeviceID: DeviceIdentifier?) async {
    await cancelActiveDeviceListener()

    snapshot.activeDeviceID = activeDeviceID
    if let activeDeviceID, let device = wearables.deviceForIdentifier(activeDeviceID) {
      snapshot.activeDeviceName = device.nameOrId()
      attachLinkStateListener(to: device)
      if isStarted {
        snapshot.phase = .running
        snapshot.sessionState = .running
        snapshot.errorMessage = nil
      }
    } else {
      snapshot.activeDeviceName = "-"
      if isStarted, snapshot.phase != .stopping {
        snapshot.phase = .waitingForDevice
        snapshot.sessionState = nil
      }
    }

    publishSnapshot()
  }

  private func attachLinkStateListener(to device: Device) {
    linkStateListenerToken = device.addLinkStateListener { [weak self] linkState in
      Task { @MainActor [weak self] in
        self?.applyLinkState(linkState)
      }
    }
  }

  private func applyLinkState(_ linkState: LinkState) {
    guard isStarted, snapshot.phase != .stopping else { return }

    switch linkState {
    case .connected:
      snapshot.phase = .running
      snapshot.sessionState = .running
      snapshot.errorMessage = nil
      publishSnapshot()
    case .connecting, .disconnected:
      snapshot.phase = .waitingForDevice
      snapshot.sessionState = nil
      publishSnapshot()
    }
  }

  private func cancelActiveDeviceListener() async {
    await linkStateListenerToken?.cancel()
    linkStateListenerToken = nil
  }

  private func publishSnapshot() {
    onSnapshotUpdated?(snapshot)
  }
}

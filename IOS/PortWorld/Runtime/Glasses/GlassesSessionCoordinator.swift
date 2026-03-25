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
  private let deviceStateSession: DeviceStateSession

  private var snapshot = Snapshot()
  private var isStarted = false
  private var activeDeviceTask: Task<Void, Never>?
  private var sessionStateListenerToken: AnyListenerToken?
  private var linkStateListenerToken: AnyListenerToken?

  init(wearables: WearablesInterface) {
    self.wearables = wearables
    self.deviceSelector = AutoDeviceSelector(wearables: wearables)
    self.deviceStateSession = DeviceStateSession(deviceSelector: deviceSelector)
    startObservingActiveDevice()
    publishSnapshot()
  }

  deinit {
    activeDeviceTask?.cancel()
    let sessionStateListenerToken = self.sessionStateListenerToken
    let linkStateListenerToken = self.linkStateListenerToken
    Task {
      await sessionStateListenerToken?.cancel()
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
    snapshot.phase = snapshot.activeDeviceID == nil ? .waitingForDevice : .starting
    publishSnapshot()

    do {
      try await deviceStateSession.start()
      applySessionState(deviceStateSession.state)
    } catch {
      isStarted = false
      snapshot.phase = .failed
      snapshot.errorMessage = error.localizedDescription
      publishSnapshot()
      throw error
    }
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

    do {
      try await deviceStateSession.stop()
    } catch {
      snapshot.errorMessage = error.localizedDescription
    }

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
    await cancelActiveDeviceListeners()

    snapshot.activeDeviceID = activeDeviceID
    if let activeDeviceID, let device = wearables.deviceForIdentifier(activeDeviceID) {
      snapshot.activeDeviceName = device.nameOrId()
      attachLinkStateListener(to: device)
      await attachSessionStateListener(for: activeDeviceID)
    } else {
      snapshot.activeDeviceName = "-"
      if isStarted, snapshot.phase != .stopping {
        snapshot.phase = .waitingForDevice
      }
    }

    publishSnapshot()
  }

  private func attachSessionStateListener(for deviceID: DeviceIdentifier) async {
    sessionStateListenerToken = await wearables.addDeviceSessionStateListener(forDeviceId: deviceID) { [weak self] state in
      Task { @MainActor [weak self] in
        self?.applySessionState(state)
      }
    }
  }

  private func attachLinkStateListener(to device: Device) {
    linkStateListenerToken = device.addLinkStateListener { [weak self] linkState in
      Task { @MainActor [weak self] in
        self?.applyLinkState(linkState)
      }
    }
  }

  private func applySessionState(_ state: SessionState) {
    snapshot.sessionState = state

    guard isStarted else {
      publishSnapshot()
      return
    }

    switch state {
    case .running:
      snapshot.phase = .running
      snapshot.errorMessage = nil
    case .paused:
      snapshot.phase = .paused
    case .waitingForDevice, .unknown:
      snapshot.phase = .waitingForDevice
    case .stopped:
      snapshot.phase = .inactive
    }

    publishSnapshot()
  }

  private func applyLinkState(_ linkState: LinkState) {
    guard isStarted, snapshot.phase != .stopping else { return }

    switch linkState {
    case .connected:
      break
    case .connecting, .disconnected:
      snapshot.phase = .waitingForDevice
      publishSnapshot()
    }
  }

  private func cancelActiveDeviceListeners() async {
    await sessionStateListenerToken?.cancel()
    await linkStateListenerToken?.cancel()
    sessionStateListenerToken = nil
    linkStateListenerToken = nil
  }

  private func publishSnapshot() {
    onSnapshotUpdated?(snapshot)
  }
}

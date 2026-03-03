import MWDATCore
import SwiftUI

@MainActor
final class SessionViewModel {
  let store: SessionStateStore

  private let deviceSessionCoordinator: DeviceSessionCoordinator
  private let runtimeCoordinator: RuntimeCoordinator

  init(wearables: WearablesInterface, store: SessionStateStore? = nil) {
    let stateStore = store ?? SessionStateStore()
    let runtimeConfig = RuntimeConfig.load()
    self.store = stateStore
    self.deviceSessionCoordinator = DeviceSessionCoordinator(wearables: wearables)
    self.runtimeCoordinator = RuntimeCoordinator(
      store: stateStore,
      deviceSessionCoordinator: deviceSessionCoordinator,
      runtimeConfig: runtimeConfig
    )
  }

  func preflightWakeAuthorization() async {
    await runtimeCoordinator.preflightWakeAuthorization()
  }

  func activateAssistantRuntime() async {
    guard store.canActivateAssistantRuntime else { return }

    store.assistantRuntimeState = .activating
    store.runtimeSessionStateText = "activating"
    store.runtimeErrorText = ""

    do {
      try await deviceSessionCoordinator.ensureCameraPermissionIfNeeded()
      await runtimeCoordinator.preflightWakeAuthorization()
      await runtimeCoordinator.activate()
    } catch {
      store.errorMessage = "Permission error: \(error.localizedDescription)"
      store.showError = true
      store.runtimeErrorText = "Permission error: \(error.localizedDescription)"
      store.assistantRuntimeState = .failed
      store.runtimeSessionStateText = "failed"
    }
  }

  func deactivateAssistantRuntime() async {
    guard store.canDeactivateAssistantRuntime else { return }

    store.assistantRuntimeState = .deactivating
    store.runtimeSessionStateText = "deactivating"
    await runtimeCoordinator.deactivate()
    store.assistantRuntimeState = .inactive
    store.runtimeSessionStateText = "inactive"
  }

  func dismissError() {
    store.showError = false
    store.errorMessage = ""
  }

  func capturePhoto() {
    store.runtimePhotoStateText = "capturing"
    deviceSessionCoordinator.capturePhoto()
  }

  func triggerWakeForTesting() {
    runtimeCoordinator.triggerWakeForTesting()
  }

  func dismissPhotoPreview() {
    store.showPhotoPreview = false
    store.capturedPhoto = nil
  }

  func handleScenePhaseChange(_ phase: ScenePhase) {
    runtimeCoordinator.handleScenePhaseChange(phase)
  }
}

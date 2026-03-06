import MWDATCore
import SwiftUI

@MainActor
final class SessionViewModel {
  let store: SessionStateStore

  private let preferSpeakerOutput: Bool
  private let deviceSessionCoordinator: DeviceSessionCoordinator
  private var runtimeCoordinator: RuntimeCoordinator
  private var isActivationInProgress = false
  private var runtimeCoordinatorIsActivated = false

  init(
    wearables: WearablesInterface,
    store: SessionStateStore? = nil,
    preferSpeakerOutput: Bool = false
  ) {
    let stateStore = store ?? SessionStateStore()
    let runtimeConfig = RuntimeConfig.load()
    self.preferSpeakerOutput = preferSpeakerOutput
    self.store = stateStore
    self.deviceSessionCoordinator = DeviceSessionCoordinator(wearables: wearables)
    self.runtimeCoordinator = RuntimeCoordinator(
      store: stateStore,
      deviceSessionCoordinator: deviceSessionCoordinator,
      runtimeConfig: runtimeConfig,
      preferSpeakerOutput: preferSpeakerOutput
    )
    stateStore.runtimeWakePhraseText = runtimeConfig.wakePhrase
    stateStore.runtimeSleepPhraseText = runtimeConfig.sleepPhrase
  }

  func preflightWakeAuthorization() async {
    await runtimeCoordinator.preflightWakeAuthorization()
  }

  func activateAssistantRuntime() async {
    guard store.canActivateAssistantRuntime else { return }
    guard !isActivationInProgress else { return }
    isActivationInProgress = true
    defer { isActivationInProgress = false }

    if runtimeCoordinatorIsActivated {
      await runtimeCoordinator.deactivate()
      runtimeCoordinatorIsActivated = false
    }

    if !runtimeCoordinatorIsActivated && !store.isStreaming {
      let runtimeConfig = RuntimeConfig.load()
      runtimeCoordinator = RuntimeCoordinator(
        store: store,
        deviceSessionCoordinator: deviceSessionCoordinator,
        runtimeConfig: runtimeConfig,
        preferSpeakerOutput: preferSpeakerOutput
      )
      store.runtimeWakePhraseText = runtimeConfig.wakePhrase
      store.runtimeSleepPhraseText = runtimeConfig.sleepPhrase
    }

    store.runtimeErrorText = ""
    store.runtimeInfoText = ""

    do {
      if !preferSpeakerOutput {
        try await deviceSessionCoordinator.ensureCameraPermissionIfNeeded()
      }
      await runtimeCoordinator.preflightWakeAuthorization()
      await runtimeCoordinator.activate()
      runtimeCoordinatorIsActivated = true
    } catch {
      store.errorMessage = "Permission error: \(error.localizedDescription)"
      store.showError = true
      store.runtimeErrorText = "Permission error: \(error.localizedDescription)"
      store.assistantRuntimeState = .inactive
      store.runtimeSessionStateText = "idle"
    }
  }

  func deactivateAssistantRuntime() async {
    guard store.canDeactivateAssistantRuntime else { return }
    isActivationInProgress = false

    store.assistantRuntimeState = .deactivating
    store.runtimeSessionStateText = "deactivating"
    await runtimeCoordinator.deactivate()
    runtimeCoordinatorIsActivated = false
    store.assistantRuntimeState = .inactive
    store.runtimeSessionStateText = "inactive"
  }

  func endConversation() async {
    await runtimeCoordinator.endConversation()
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

  func resetTemporaryCredentials() {
    do {
      try RuntimeConfig.clearStoredAPIKey()
      store.runtimeInfoText = "Temporary credentials reset. Reactivate runtime to reload config."
      store.runtimeErrorText = ""
    } catch {
      store.runtimeInfoText = ""
      store.runtimeErrorText = "Failed to reset credentials: \(error.localizedDescription)"
    }
  }
}

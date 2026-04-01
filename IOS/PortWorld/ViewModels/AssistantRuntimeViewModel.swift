// View model that bridges assistant runtime actions and published UI status.
import Combine
import MWDATCore
import SwiftUI

@MainActor
final class AssistantRuntimeViewModel: ObservableObject {
  enum GuidedConversationStartBlocker: Equatable {
    case backendNotReady(String)
    case glassesNotReady(String)
    case runtimeUnavailable(String)

    var message: String {
      switch self {
      case .backendNotReady(let message), .glassesNotReady(let message), .runtimeUnavailable(let message):
        return message
      }
    }
  }

  enum GuidedConversationStartResult: Equatable {
    case started
    case blocked(GuidedConversationStartBlocker)
  }

  @Published private(set) var status: AssistantRuntimeStatus
  @Published private(set) var isProfileOnboardingReady = false

  private let controller: AssistantRuntimeController
  private let wearablesRuntimeManager: WearablesRuntimeManager
  private var controllerStatus: AssistantRuntimeStatus
  private var pendingActivationMode: ActivationMode = .standard
  private var pendingGlassesActivation = false
  private var isStartingGlassesRuntime = false
  private var isStoppingGlassesRoute = false
  private var cancellables = Set<AnyCancellable>()

  private enum ActivationMode {
    case standard
    case guidedOnboarding
  }

  init(
    wearablesRuntimeManager: WearablesRuntimeManager,
    config: AssistantRuntimeConfig
  ) {
    self.wearablesRuntimeManager = wearablesRuntimeManager
    self.controller = AssistantRuntimeController(config: config)
    self.controllerStatus = controller.status
    self.status = controller.status
    bindController()
    bindWearablesRuntimeManager()
    publishMergedStatus()
  }

  func activateAssistant() async {
    guard controllerStatus.assistantRuntimeState == .inactive else { return }
    guard canActivateGlassesRoute else {
      publishMergedStatus()
      return
    }

    pendingGlassesActivation = true
    publishMergedStatus()
    await wearablesRuntimeManager.startGlassesSession()
    await synchronizeGlassesRouteIfNeeded()
    publishMergedStatus()
  }

  func deactivateAssistant() async {
    pendingGlassesActivation = false
    pendingActivationMode = .standard
    isProfileOnboardingReady = false
    await controller.deactivate()
    await wearablesRuntimeManager.stopGlassesSession()
    publishMergedStatus()
  }

  func startGuidedConversation(
    backendValidationState: AppSettingsStore.BackendValidationState,
    backendReadinessDetail: String
  ) async -> GuidedConversationStartResult {
    debugLog("Guided onboarding start requested")
    guard controllerStatus.assistantRuntimeState == .inactive else {
      let blocker = GuidedConversationStartBlocker.runtimeUnavailable("The onboarding interview is already running.")
      debugLog("Guided onboarding blocked before backend: \(blocker.message)")
      return .blocked(blocker)
    }

    guard backendValidationState == .valid else {
      let blocker = GuidedConversationStartBlocker.backendNotReady(backendReadinessDetail)
      debugLog("Guided onboarding blocked before backend: \(blocker.message)")
      return .blocked(blocker)
    }

    if let activationBlocker = wearablesRuntimeManager.activationBlocker {
      let blocker = GuidedConversationStartBlocker.glassesNotReady(activationBlocker.message)
      debugLog("Guided onboarding blocked before backend: \(blocker.message)")
      publishMergedStatus()
      return .blocked(blocker)
    }

    isProfileOnboardingReady = false
    pendingActivationMode = .guidedOnboarding
    pendingGlassesActivation = true
    publishMergedStatus()
    await wearablesRuntimeManager.startGlassesSession()
    await synchronizeGlassesRouteIfNeeded()
    publishMergedStatus()

    switch controllerStatus.assistantRuntimeState {
    case .connectingConversation, .activeConversation:
      return .started
    case .inactive, .armedListening, .pausedByHardware, .deactivating:
      let message =
        controllerStatus.errorText.isEmpty == false
        ? controllerStatus.errorText
        : (wearablesRuntimeManager.glassesSessionErrorMessage ?? "Interview unavailable.")
      let blocker = GuidedConversationStartBlocker.runtimeUnavailable(message)
      debugLog("Guided onboarding blocked before backend: \(blocker.message)")
      return .blocked(blocker)
    }
  }

  func stopGuidedConversation() async {
    pendingGlassesActivation = false
    pendingActivationMode = .standard
    isProfileOnboardingReady = false
    await controller.deactivate()
    await wearablesRuntimeManager.stopGlassesSession()
    publishMergedStatus()
  }

  func handleScenePhaseChange(_ phase: ScenePhase) {
    controller.handleScenePhaseChange(phase)
  }

  private var canActivateGlassesRoute: Bool {
    guard controllerStatus.assistantRuntimeState == .inactive else { return false }
    guard pendingGlassesActivation == false else { return false }
    guard wearablesRuntimeManager.isGlassesSessionRequested == false else { return false }
    return wearablesRuntimeManager.isGlassesActivationReady
  }

  private func bindController() {
    controller.onStatusUpdated = { [weak self] status in
      Task { @MainActor [weak self] in
        guard let self else { return }
        self.controllerStatus = status
        await self.synchronizeVisionCaptureIfNeeded()
        self.publishMergedStatus()
      }
    }

    controller.onGlassesAudioModeUpdated = { [weak self] mode, _ in
      Task { @MainActor [weak self] in
        self?.wearablesRuntimeManager.setGlassesAudioMode(mode)
        self?.publishMergedStatus()
      }
    }

    controller.onProfileOnboardingReady = { [weak self] in
      Task { @MainActor [weak self] in
        self?.isProfileOnboardingReady = true
      }
    }
  }

  private func bindWearablesRuntimeManager() {
    wearablesRuntimeManager.$configurationState
      .sink { [weak self] _ in self?.handleWearablesRuntimeManagerChange() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$configurationErrorMessage
      .sink { [weak self] _ in self?.handleWearablesRuntimeManagerChange() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$registrationState
      .sink { [weak self] _ in self?.handleWearablesRuntimeManagerChange() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$discoveryPermissionState
      .sink { [weak self] _ in self?.handleWearablesRuntimeManagerChange() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$devices
      .sink { [weak self] _ in self?.handleWearablesRuntimeManagerChange() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$activeCompatibilityMessage
      .sink { [weak self] _ in self?.handleWearablesRuntimeManagerChange() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$glassesSessionPhase
      .sink { [weak self] _ in self?.handleWearablesRuntimeManagerChange() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$glassesSessionState
      .sink { [weak self] _ in self?.handleWearablesRuntimeManagerChange() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$isGlassesSessionRequested
      .sink { [weak self] _ in self?.handleWearablesRuntimeManagerChange() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$glassesSessionErrorMessage
      .sink { [weak self] _ in self?.handleWearablesRuntimeManagerChange() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$hfpRouteAvailability
      .sink { [weak self] _ in self?.handleWearablesRuntimeManagerChange() }
      .store(in: &cancellables)
  }

  private func handleWearablesRuntimeManagerChange() {
    Task { @MainActor [weak self] in
      await self?.synchronizeGlassesRouteIfNeeded()
      await self?.synchronizeVisionCaptureIfNeeded()
      self?.publishMergedStatus()
    }
  }

  private func synchronizeVisionCaptureIfNeeded() async {
    let shouldCaptureVision =
      controllerStatus.assistantRuntimeState == .activeConversation &&
      controllerStatus.sessionID != "-"

    await wearablesRuntimeManager.setVisionCaptureActive(
      shouldCaptureVision,
      sessionID: shouldCaptureVision ? controllerStatus.sessionID : nil,
      endpointURL: controller.config.visionFrameURL,
      requestHeaders: controller.config.requestHeaders,
      photoFps: controller.config.photoFps
    )
  }

  private func synchronizeGlassesRouteIfNeeded() async {
    guard pendingGlassesActivation ||
      wearablesRuntimeManager.isGlassesSessionRequested ||
      controllerStatus.assistantRuntimeState == .pausedByHardware ||
      controllerStatus.assistantRuntimeState != .inactive else {
      return
    }

    let glassesSessionPhase = wearablesRuntimeManager.glassesSessionPhase
    let glassesSessionState = wearablesRuntimeManager.glassesSessionState

    if glassesRoutePrerequisitesInvalidated {
      await stopGlassesRouteIfNeeded()
      return
    }

    if glassesSessionPhase == .running {
      if pendingGlassesActivation &&
        controllerStatus.assistantRuntimeState == .inactive &&
        isStartingGlassesRuntime == false {
        isStartingGlassesRuntime = true
        pendingGlassesActivation = false
        switch pendingActivationMode {
        case .standard:
          await controller.activate()
        case .guidedOnboarding:
          await controller.startGuidedConversation()
        }
        pendingActivationMode = .standard
        isStartingGlassesRuntime = false
        if controller.status.assistantRuntimeState == .inactive {
          await stopGlassesRouteIfNeeded()
        }
        return
      }

      if controllerStatus.assistantRuntimeState == .pausedByHardware {
        await controller.resumeFromExternalRoutePause()
        return
      }
    }

    if glassesSessionPhase == .paused {
      switch controllerStatus.assistantRuntimeState {
      case .armedListening, .connectingConversation, .activeConversation:
        await controller.suspendForExternalRoutePause()
        return
      case .inactive, .pausedByHardware, .deactivating:
        break
      }
    }

    if glassesSessionPhase == .waitingForDevice && shouldTearDownForWaitingDeviceLoss {
      await stopGlassesRouteIfNeeded()
      return
    }

    if glassesSessionPhase == .failed || glassesSessionState == .stopped {
      await stopGlassesRouteIfNeeded()
    }
  }

  private var glassesRoutePrerequisitesInvalidated: Bool {
    guard pendingGlassesActivation ||
      wearablesRuntimeManager.isGlassesSessionRequested ||
      controllerStatus.assistantRuntimeState != .inactive else {
      return false
    }

    return wearablesRuntimeManager.isGlassesActivationReady == false
  }

  private var shouldTearDownForWaitingDeviceLoss: Bool {
    guard pendingGlassesActivation == false else { return false }
    return wearablesRuntimeManager.isGlassesSessionRequested ||
      controllerStatus.assistantRuntimeState == .pausedByHardware ||
      controllerStatus.assistantRuntimeState != .inactive
  }

  private func stopGlassesRouteIfNeeded() async {
    guard isStoppingGlassesRoute == false else { return }
    guard pendingGlassesActivation ||
      wearablesRuntimeManager.isGlassesSessionRequested ||
      controllerStatus.assistantRuntimeState != .inactive else {
      return
    }

    isStoppingGlassesRoute = true
    pendingGlassesActivation = false
    pendingActivationMode = .standard
    isStartingGlassesRuntime = false
    if controllerStatus.assistantRuntimeState != .inactive {
      await controller.deactivate()
    }
    await wearablesRuntimeManager.stopGlassesSession()
    isStoppingGlassesRoute = false
  }

  private func publishMergedStatus() {
    var mergedStatus = controllerStatus
    mergedStatus.activationButtonTitle = activationButtonTitle()

    status = mergedStatus
  }

  private func activationButtonTitle() -> String {
    if pendingGlassesActivation || wearablesRuntimeManager.glassesSessionPhase == .starting {
      return "Starting Glasses Session..."
    }
    return "Activate Assistant"
  }

  private func debugLog(_ message: String) {
    #if DEBUG
      print("[AssistantRuntimeViewModel] \(message)")
    #endif
  }
}

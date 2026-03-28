// View model that bridges assistant runtime actions and published UI status.
import Combine
import MWDATCore
import SwiftUI

@MainActor
final class AssistantRuntimeViewModel: ObservableObject {
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

  func startGuidedConversation() async {
    guard controllerStatus.assistantRuntimeState == .inactive else { return }
    guard canActivateGlassesRoute else {
      publishMergedStatus()
      return
    }

    isProfileOnboardingReady = false
    pendingActivationMode = .guidedOnboarding
    pendingGlassesActivation = true
    publishMergedStatus()
    await wearablesRuntimeManager.startGlassesSession()
    await synchronizeGlassesRouteIfNeeded()
    publishMergedStatus()
  }

  func stopGuidedConversation() async {
    pendingGlassesActivation = false
    pendingActivationMode = .standard
    isProfileOnboardingReady = false
    await controller.deactivate()
    await wearablesRuntimeManager.stopGlassesSession()
    publishMergedStatus()
  }

  private var canActivateGlassesRoute: Bool {
    guard controllerStatus.assistantRuntimeState == .inactive else { return false }
    guard pendingGlassesActivation == false else { return false }
    guard wearablesRuntimeManager.isGlassesSessionRequested == false else { return false }
    return areGlassesReadyForActivation
  }

  private var areGlassesReadyForActivation: Bool {
    guard wearablesRuntimeManager.configurationState == .ready else { return false }
    guard wearablesRuntimeManager.registrationState == .registered else { return false }
    guard wearablesRuntimeManager.devices.isEmpty == false else { return false }
    guard wearablesRuntimeManager.activeCompatibilityMessage == nil else { return false }
    guard wearablesRuntimeManager.glassesSessionPhase != .failed else { return false }
    guard wearablesRuntimeManager.isHFPRouteAvailable else { return false }
    return true
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

    wearablesRuntimeManager.$activeGlassesDeviceName
      .sink { [weak self] _ in self?.publishMergedStatus() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$isGlassesSessionRequested
      .sink { [weak self] _ in self?.handleWearablesRuntimeManagerChange() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$glassesSessionErrorMessage
      .sink { [weak self] _ in self?.handleWearablesRuntimeManagerChange() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$isHFPRouteAvailable
      .sink { [weak self] _ in self?.publishMergedStatus() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$glassesAudioMode
      .sink { [weak self] _ in self?.publishMergedStatus() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$glassesAudioDetailText
      .sink { [weak self] _ in self?.publishMergedStatus() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$glassesDevelopmentReadinessDetail
      .sink { [weak self] _ in self?.publishMergedStatus() }
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
      wearablesRuntimeManager.glassesSessionPhase == .running &&
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
          await controller.activate(using: .glasses)
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

    if wearablesRuntimeManager.configurationState != .ready {
      return true
    }

    if wearablesRuntimeManager.registrationState != .registered {
      return true
    }

    if wearablesRuntimeManager.devices.isEmpty {
      return true
    }

    if wearablesRuntimeManager.activeCompatibilityMessage != nil {
      return true
    }

    return wearablesRuntimeManager.isHFPRouteAvailable == false
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
    let readiness = makeGlassesReadiness()

    mergedStatus.selectedRoute = .glasses
    mergedStatus.activeRouteText = activeRouteText()
    mergedStatus.glassesReadinessTitle = readiness.title
    mergedStatus.glassesReadinessDetail = readiness.detail
    mergedStatus.glassesReadinessKind = readiness.kind
    mergedStatus.glassesSessionText = glassesSessionText()
    mergedStatus.activeGlassesDeviceText = wearablesRuntimeManager.activeGlassesDeviceName
    mergedStatus.glassesAudioModeText = glassesAudioModeText()
    mergedStatus.hfpRouteText = wearablesRuntimeManager.isHFPRouteAvailable ? "bidirectional_ready" : "not_ready"
    mergedStatus.glassesAudioDetailText = wearablesRuntimeManager.glassesAudioDetailText
    mergedStatus.visionCaptureStateText = wearablesRuntimeManager.visionCaptureStateText
    mergedStatus.visionUploadCount = wearablesRuntimeManager.visionUploadCount
    mergedStatus.visionUploadFailureCount = wearablesRuntimeManager.visionUploadFailureCount
    mergedStatus.visionLastErrorText = wearablesRuntimeManager.visionLastErrorText
    mergedStatus.glassesDevelopmentDetailText = glassesRouteDetailText()
    mergedStatus.canChangeRoute = false
    mergedStatus.canActivateSelectedRoute = canActivateGlassesRoute
    mergedStatus.activationButtonTitle = activationButtonTitle()

    status = mergedStatus
  }

  private func makeGlassesReadiness() -> (title: String, detail: String, kind: GlassesReadinessKind) {
    if let compatibilityMessage = wearablesRuntimeManager.activeCompatibilityMessage {
      return ("Glasses need attention", compatibilityMessage, .warning)
    }

    switch wearablesRuntimeManager.configurationState {
    case .idle, .configuring:
      return (
        "Initializing glasses support",
        "The app is preparing shared DAT support in the background.",
        .neutral
      )

    case .failed:
      let detail = wearablesRuntimeManager.configurationErrorMessage
        ?? "Wearables SDK initialization failed. Open Glasses Setup to retry."
      return ("Glasses unavailable", detail, .error)

    case .ready:
      break
    }

    guard wearablesRuntimeManager.registrationState == .registered else {
      return ("Glasses setup required", wearablesRuntimeManager.glassesDevelopmentReadinessDetail, .neutral)
    }

    guard wearablesRuntimeManager.devices.isEmpty == false else {
      return ("Waiting for glasses", wearablesRuntimeManager.glassesDevelopmentReadinessDetail, .neutral)
    }

    guard wearablesRuntimeManager.isHFPRouteAvailable else {
      return (
        "Glasses audio unavailable",
        "Connect the glasses audio route before activating the assistant.",
        .warning
      )
    }

    switch wearablesRuntimeManager.glassesSessionPhase {
    case .starting:
      return (
        "Starting glasses session",
        "Requesting a device-owned DAT session before the assistant arms and confirms live glasses audio.",
        .neutral
      )

    case .waitingForDevice:
      if wearablesRuntimeManager.isGlassesSessionRequested {
        return (
          "Waiting for glasses session",
          "The assistant is waiting for your glasses to become available nearby.",
          .warning
        )
      }
      fallthrough

    case .inactive:
      return (
        "Glasses detected",
        "Glasses lifecycle and Bluetooth HFP audio are ready to activate.",
        .success
      )

    case .running:
      return (
        "Glasses audio live",
        wearablesRuntimeManager.glassesAudioDetailText,
        .success
      )

    case .paused:
      return (
        "Glasses paused",
        "The glasses session is paused by hardware state. The assistant will resume when the session returns.",
        .warning
      )

    case .stopping:
      return (
        "Stopping glasses session",
        "Releasing the current DAT session and returning the app to idle.",
        .neutral
      )

    case .failed:
      let detail = wearablesRuntimeManager.glassesSessionErrorMessage
        ?? "The DAT device session failed to start or continue."
      return ("Glasses session failed", detail, .error)
    }
  }

  private func activationButtonTitle() -> String {
    if pendingGlassesActivation || wearablesRuntimeManager.glassesSessionPhase == .starting {
      return "Starting Glasses Session..."
    }
    return "Activate Assistant"
  }

  private func activeRouteText() -> String {
    if pendingGlassesActivation ||
      wearablesRuntimeManager.isGlassesSessionRequested ||
      controllerStatus.assistantRuntimeState == .pausedByHardware ||
      controllerStatus.assistantRuntimeState != .inactive {
      return AssistantRoute.glasses.rawValue
    }

    return "none"
  }

  private func glassesSessionText() -> String {
    if let sessionState = wearablesRuntimeManager.glassesSessionState {
      return sessionState.description
    }
    return wearablesRuntimeManager.glassesSessionPhase.rawValue
  }

  private func glassesAudioModeText() -> String {
    switch wearablesRuntimeManager.glassesAudioMode {
    case .inactive:
      return "inactive"
    case .glassesHFP:
      return "hfp_live"
    }
  }

  private func glassesRouteDetailText() -> String {
    if controllerStatus.assistantRuntimeState != .inactive &&
      wearablesRuntimeManager.glassesAudioMode != .inactive {
      return wearablesRuntimeManager.glassesAudioDetailText
    }

    return wearablesRuntimeManager.glassesDevelopmentReadinessDetail
  }
}

// View model that bridges assistant runtime actions and published UI status.
import Combine
import MWDATCore
import SwiftUI

@MainActor
final class AssistantRuntimeViewModel: ObservableObject {
  @Published private(set) var status: AssistantRuntimeStatus

  private let controller: AssistantRuntimeController
  private let wearablesRuntimeManager: WearablesRuntimeManager
  private var controllerStatus: AssistantRuntimeStatus
  private var selectedRoute: AssistantRoute = .phone
  private var pendingGlassesActivation = false
  private var isStartingPhoneRuntimeForGlassesRoute = false
  private var isStoppingGlassesRoute = false
  private var cancellables = Set<AnyCancellable>()

  init(wearablesRuntimeManager: WearablesRuntimeManager) {
    self.wearablesRuntimeManager = wearablesRuntimeManager
    let config = AssistantRuntimeConfig.load()
    self.controller = AssistantRuntimeController(config: config)
    self.controllerStatus = controller.status
    self.status = controller.status
    bindController()
    bindWearablesRuntimeManager()
    publishMergedStatus()
  }

  func activateAssistant() async {
    guard controllerStatus.assistantRuntimeState == .inactive else { return }

    switch selectedRoute {
    case .phone:
      pendingGlassesActivation = false
      wearablesRuntimeManager.setGlassesAudioMode(.inactive)
      await controller.activate(using: .phone)

    case .glasses:
      guard canActivateGlassesRoute else {
        publishMergedStatus()
        return
      }

      pendingGlassesActivation = true
      publishMergedStatus()
      await wearablesRuntimeManager.startGlassesSession()
      await synchronizeGlassesRouteIfNeeded()
    }

    publishMergedStatus()
  }

  func deactivateAssistant() async {
    if isGlassesRouteOwned {
      pendingGlassesActivation = false
      await controller.deactivate()
      await wearablesRuntimeManager.stopGlassesSession()
    } else {
      await controller.deactivate()
    }

    publishMergedStatus()
  }

  func endConversation() async {
    await controller.endConversation()
  }

  func selectRoute(_ route: AssistantRoute) {
    guard controllerStatus.assistantRuntimeState == .inactive else { return }
    guard pendingGlassesActivation == false else { return }
    guard wearablesRuntimeManager.isGlassesSessionRequested == false else { return }
    guard selectedRoute != route else { return }
    selectedRoute = route
    if route == .phone {
      wearablesRuntimeManager.setGlassesAudioMode(.inactive)
    }
    publishMergedStatus()
  }

  func handleScenePhaseChange(_ phase: ScenePhase) {
    controller.handleScenePhaseChange(phase)
  }

  private var isGlassesRouteOwned: Bool {
    selectedRoute == .glasses &&
      (
        pendingGlassesActivation ||
          wearablesRuntimeManager.isGlassesSessionRequested ||
          controllerStatus.assistantRuntimeState != .inactive
      )
  }

  private var canActivateGlassesRoute: Bool {
    guard controllerStatus.assistantRuntimeState == .inactive else { return false }
    guard pendingGlassesActivation == false else { return false }
    guard wearablesRuntimeManager.isGlassesSessionRequested == false else { return false }
    guard wearablesRuntimeManager.configurationState == .ready else { return false }
    guard wearablesRuntimeManager.registrationState == .registered else { return false }
    guard wearablesRuntimeManager.devices.isEmpty == false else { return false }
    guard wearablesRuntimeManager.activeCompatibilityMessage == nil else { return false }
    guard wearablesRuntimeManager.glassesSessionPhase != .failed else { return false }
    return true
  }

  private func bindController() {
    controller.onStatusUpdated = { [weak self] status in
      guard let self else { return }
      self.controllerStatus = status
      self.publishMergedStatus()
    }
    controller.onGlassesAudioModeUpdated = { [weak self] mode, _ in
      Task { @MainActor [weak self] in
        self?.wearablesRuntimeManager.setGlassesAudioMode(mode)
        self?.publishMergedStatus()
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

    wearablesRuntimeManager.$mockWorkflowState
      .sink { [weak self] _ in self?.publishMergedStatus() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$mockWorkflowDetail
      .sink { [weak self] _ in self?.publishMergedStatus() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$glassesDevelopmentReadinessDetail
      .sink { [weak self] _ in self?.publishMergedStatus() }
      .store(in: &cancellables)
  }

  private func handleWearablesRuntimeManagerChange() {
    Task { @MainActor [weak self] in
      await self?.synchronizeGlassesRouteIfNeeded()
      self?.publishMergedStatus()
    }
  }

  private func synchronizeGlassesRouteIfNeeded() async {
    guard selectedRoute == .glasses || wearablesRuntimeManager.isGlassesSessionRequested || pendingGlassesActivation ||
      controllerStatus.assistantRuntimeState == .pausedByHardware else {
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
        isStartingPhoneRuntimeForGlassesRoute == false {
        isStartingPhoneRuntimeForGlassesRoute = true
        pendingGlassesActivation = false
        await controller.activate(using: .glasses)
        isStartingPhoneRuntimeForGlassesRoute = false
        if controllerStatus.assistantRuntimeState == .inactive {
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

    return wearablesRuntimeManager.activeCompatibilityMessage != nil
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
    isStartingPhoneRuntimeForGlassesRoute = false
    if controllerStatus.assistantRuntimeState != .inactive {
      await controller.deactivate()
    }
    await wearablesRuntimeManager.stopGlassesSession()
    isStoppingGlassesRoute = false
  }

  private func publishMergedStatus() {
    var mergedStatus = controllerStatus
    let readiness = makeGlassesReadiness()

    mergedStatus.selectedRoute = selectedRoute
    mergedStatus.activeRouteText = activeRouteText()
    mergedStatus.glassesReadinessTitle = readiness.title
    mergedStatus.glassesReadinessDetail = readiness.detail
    mergedStatus.glassesReadinessKind = readiness.kind
    mergedStatus.glassesSessionText = glassesSessionText()
    mergedStatus.activeGlassesDeviceText = wearablesRuntimeManager.activeGlassesDeviceName
    mergedStatus.glassesAudioModeText = glassesAudioModeText()
    mergedStatus.hfpRouteText = wearablesRuntimeManager.isHFPRouteAvailable ? "ready" : "not_ready"
    mergedStatus.glassesAudioDetailText = wearablesRuntimeManager.glassesAudioDetailText
    mergedStatus.mockWorkflowText = mockWorkflowText()
    mergedStatus.glassesDevelopmentDetailText = glassesRouteDetailText()
    mergedStatus.canChangeRoute =
      controllerStatus.assistantRuntimeState == .inactive &&
      pendingGlassesActivation == false &&
      wearablesRuntimeManager.isGlassesSessionRequested == false
    mergedStatus.canActivateSelectedRoute = selectedRoute == .phone
      ? controllerStatus.assistantRuntimeState == .inactive
      : canActivateGlassesRoute
    mergedStatus.activationButtonTitle = activationButtonTitle()

    status = mergedStatus
  }

  private func makeGlassesReadiness() -> (title: String, detail: String, kind: GlassesReadinessKind) {
    if let compatibilityMessage = wearablesRuntimeManager.activeCompatibilityMessage {
      return (
        "Glasses need attention",
        compatibilityMessage,
        .warning
      )
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
      return (
        "Glasses unavailable",
        detail,
        .error
      )

    case .ready:
      break
    }

    guard wearablesRuntimeManager.registrationState == .registered else {
      return (
        "Glasses setup required",
        wearablesRuntimeManager.glassesDevelopmentReadinessDetail,
        .neutral
      )
    }

    guard wearablesRuntimeManager.devices.isEmpty == false else {
      return (
        "Waiting for glasses",
        wearablesRuntimeManager.glassesDevelopmentReadinessDetail,
        .neutral
      )
    }

    switch wearablesRuntimeManager.glassesSessionPhase {
    case .starting:
      return (
        "Starting glasses session",
        "Requesting a device-owned DAT session before the assistant arms and selects the best available glasses audio path.",
        .neutral
      )

    case .waitingForDevice:
      if wearablesRuntimeManager.isGlassesSessionRequested {
        return (
          "Waiting for glasses session",
          "The glasses route is selected, but DAT is still waiting for a device to become available.",
          .warning
        )
      }
      fallthrough

    case .inactive:
      if wearablesRuntimeManager.isHFPRouteAvailable {
        return (
          "Glasses detected",
          "Glasses lifecycle is ready and Bluetooth HFP is available for live glasses audio.",
          .success
        )
      }
      return (
        "Glasses detected",
        "Glasses lifecycle can now activate through DAT. Without physical HFP hardware, audio will use the labeled phone fallback for development.",
        .success
      )

    case .running:
      if wearablesRuntimeManager.glassesAudioMode == .glassesHFP {
        return (
          "Glasses audio live",
          "DAT lifecycle and Bluetooth HFP audio are both active for the glasses route.",
          .success
        )
      }
      return (
        "Glasses session live",
        glassesRouteDetailText(),
        .success
      )

    case .paused:
      return (
        "Glasses paused",
        "The glasses session is paused by hardware state. The assistant will resume when DAT returns to running, keeping the same audio mode.",
        .warning
      )

    case .stopping:
      return (
        "Stopping glasses session",
        "Releasing the current DAT session and returning control to the main runtime.",
        .neutral
      )

    case .failed:
      let detail = wearablesRuntimeManager.glassesSessionErrorMessage
        ?? "The DAT device session failed to start or continue."
      return (
        "Glasses session failed",
        detail,
        .error
      )
    }
  }

  private func activationButtonTitle() -> String {
    switch selectedRoute {
    case .phone:
      return "Activate Assistant"
    case .glasses:
      if pendingGlassesActivation || wearablesRuntimeManager.glassesSessionPhase == .starting {
        return "Starting Glasses Session..."
      }
      return "Activate Glasses Runtime"
    }
  }

  private func activeRouteText() -> String {
    if selectedRoute == .glasses &&
      (
        pendingGlassesActivation ||
          wearablesRuntimeManager.isGlassesSessionRequested ||
          controllerStatus.assistantRuntimeState == .pausedByHardware ||
          controllerStatus.assistantRuntimeState != .inactive
      ) {
      return AssistantRoute.glasses.rawValue
    }

    if controllerStatus.assistantRuntimeState != .inactive {
      return AssistantRoute.phone.rawValue
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
    case .phone:
      return "phone"
    case .glassesHFP:
      return "hfp_live"
    case .glassesMockFallback:
      return "mock_fallback_phone_audio"
    }
  }

  private func glassesRouteDetailText() -> String {
    if selectedRoute == .glasses &&
      controllerStatus.assistantRuntimeState != .inactive &&
      wearablesRuntimeManager.glassesAudioMode != .inactive {
      return wearablesRuntimeManager.glassesAudioDetailText
    }

    return wearablesRuntimeManager.glassesDevelopmentReadinessDetail
  }

  private func mockWorkflowText() -> String {
    switch wearablesRuntimeManager.mockWorkflowState {
    case .disabled:
      return "disabled"
    case .preparing:
      return "preparing"
    case .ready:
      return "ready"
    case .failed:
      return "failed"
    }
  }
}

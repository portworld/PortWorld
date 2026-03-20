// View model that bridges assistant runtime actions and published UI status.
import Combine
import MWDATCore
import SwiftUI

@MainActor
final class AssistantRuntimeViewModel: ObservableObject {
  @Published private(set) var status: AssistantRuntimeStatus
  @Published private(set) var isProfileOnboardingReady = false

  private let appSettingsStore: AppSettingsStore
  private let controller: AssistantRuntimeController
  private let wearablesRuntimeManager: WearablesRuntimeManager
  private let phoneVisionStatusClient = SessionMemoryStatusClient()
  private var controllerStatus: AssistantRuntimeStatus
  private var selectedRoute: AssistantRoute = .phone
  private var hasResolvedInitialRouteSelection = false
  private var pendingGlassesActivation = false
  private var isStartingPhoneRuntimeForGlassesRoute = false
  private var isStoppingGlassesRoute = false
  private var isPhoneVisionEnabled = false
  private var phonePhotoCaptureController: PhonePhotoCaptureController?
  private var phoneVisionFrameUploader: VisionFrameUploaderProtocol?
  private var phoneVisionStatusPollTask: Task<Void, Never>?
  private var phoneVisionStatusPollSessionID: String?
  private var phoneVisionCaptureStateText = "inactive"
  private var phoneVisionUploadCount = 0
  private var phoneVisionUploadFailureCount = 0
  private var phoneVisionCaptureErrorText = ""
  private var phoneVisionUploadErrorText = ""
  private var phoneVisionAnalysisWarningText = ""
  private var cancellables = Set<AnyCancellable>()

  init(
    appSettingsStore: AppSettingsStore,
    wearablesRuntimeManager: WearablesRuntimeManager,
    config: AssistantRuntimeConfig = AssistantRuntimeConfig.load()
  ) {
    self.appSettingsStore = appSettingsStore
    self.wearablesRuntimeManager = wearablesRuntimeManager
    self.controller = AssistantRuntimeController(config: config)
    self.controllerStatus = controller.status
    self.status = controller.status
    self.isPhoneVisionEnabled = appSettingsStore.settings.phoneVisionEnabled
    bindController()
    bindWearablesRuntimeManager()
    resolveRouteSelectionIfNeeded()
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

  func startGuidedConversation() async {
    pendingGlassesActivation = false
    selectedRoute = .phone
    isProfileOnboardingReady = false
    wearablesRuntimeManager.setGlassesAudioMode(.inactive)
    await controller.startGuidedConversation()
    publishMergedStatus()
  }

  func stopGuidedConversation() async {
    await controller.deactivate()
    isProfileOnboardingReady = false
    publishMergedStatus()
  }

  func selectRoute(_ route: AssistantRoute) {
    guard controllerStatus.assistantRuntimeState == .inactive else { return }
    guard pendingGlassesActivation == false else { return }
    guard wearablesRuntimeManager.isGlassesSessionRequested == false else { return }
    guard selectedRoute != route else { return }
    hasResolvedInitialRouteSelection = true
    selectedRoute = route
    if route == .phone {
      wearablesRuntimeManager.setGlassesAudioMode(.inactive)
    }
    publishMergedStatus()
  }

  func handleScenePhaseChange(_ phase: ScenePhase) {
    controller.handleScenePhaseChange(phase)
  }

  func setPhoneVisionEnabled(_ enabled: Bool) {
    guard isPhoneVisionEnabled != enabled else { return }
    isPhoneVisionEnabled = enabled
    appSettingsStore.setPhoneVisionEnabled(enabled)
    Task { @MainActor [weak self] in
      await self?.synchronizeVisionCaptureIfNeeded()
      self?.publishMergedStatus()
    }
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
    return canKeepGlassesRouteSelected
  }

  private var canKeepGlassesRouteSelected: Bool {
    guard wearablesRuntimeManager.configurationState == .ready else { return false }
    guard wearablesRuntimeManager.registrationState == .registered ||
      wearablesRuntimeManager.canActivateGlassesRouteForDebugMock else { return false }
    guard wearablesRuntimeManager.devices.isEmpty == false else { return false }
    guard wearablesRuntimeManager.activeCompatibilityMessage == nil else { return false }
    guard wearablesRuntimeManager.glassesSessionPhase != .failed else { return false }
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

    wearablesRuntimeManager.$mockWorkflowState
      .sink { [weak self] _ in self?.publishMergedStatus() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$mockWorkflowDetail
      .sink { [weak self] _ in self?.publishMergedStatus() }
      .store(in: &cancellables)

    wearablesRuntimeManager.$glassesDevelopmentReadinessDetail
      .sink { [weak self] _ in self?.publishMergedStatus() }
      .store(in: &cancellables)

    appSettingsStore.$settings
      .sink { [weak self] settings in
        guard let self else { return }
        guard self.isPhoneVisionEnabled != settings.phoneVisionEnabled else { return }
        self.isPhoneVisionEnabled = settings.phoneVisionEnabled
        Task { @MainActor [weak self] in
          await self?.synchronizeVisionCaptureIfNeeded()
          self?.publishMergedStatus()
        }
      }
      .store(in: &cancellables)
  }

  private func handleWearablesRuntimeManagerChange() {
    Task { @MainActor [weak self] in
      self?.resolveRouteSelectionIfNeeded()
      await self?.synchronizeGlassesRouteIfNeeded()
      await self?.synchronizeVisionCaptureIfNeeded()
      self?.publishMergedStatus()
    }
  }

  private func resolveRouteSelectionIfNeeded() {
    guard controllerStatus.assistantRuntimeState == .inactive else { return }
    guard pendingGlassesActivation == false else { return }
    guard wearablesRuntimeManager.isGlassesSessionRequested == false else { return }

    if hasResolvedInitialRouteSelection == false {
      selectedRoute = canKeepGlassesRouteSelected ? .glasses : .phone
      hasResolvedInitialRouteSelection = true
      if selectedRoute == .phone {
        wearablesRuntimeManager.setGlassesAudioMode(.inactive)
      }
      return
    }

    guard selectedRoute == .glasses else { return }
    guard canKeepGlassesRouteSelected == false else { return }

    selectedRoute = .phone
    wearablesRuntimeManager.setGlassesAudioMode(.inactive)
  }

  private func synchronizeVisionCaptureIfNeeded() async {
    let shouldCapturePhoneVision =
      selectedRoute == .phone &&
      isPhoneVisionEnabled &&
      controllerStatus.assistantRuntimeState == .activeConversation &&
      controllerStatus.sessionID != "-"

    let shouldCaptureVision =
      selectedRoute == .glasses &&
      controllerStatus.assistantRuntimeState == .activeConversation &&
      (wearablesRuntimeManager.glassesSessionPhase == .running ||
        wearablesRuntimeManager.canActivateGlassesRouteForDebugMock) &&
      controllerStatus.sessionID != "-"

    if shouldCapturePhoneVision {
      await startPhoneVisionCaptureIfNeeded()
      startPhoneVisionStatusPollingIfNeeded(sessionID: controllerStatus.sessionID)
    } else {
      stopPhoneVisionStatusPolling(resetState: selectedRoute != .phone || !isPhoneVisionEnabled)
      await stopPhoneVisionCapture(resetState: selectedRoute != .phone || !isPhoneVisionEnabled)
    }

    await wearablesRuntimeManager.setVisionCaptureActive(
      shouldCaptureVision,
      sessionID: shouldCaptureVision ? controllerStatus.sessionID : nil,
      endpointURL: controller.config.visionFrameURL,
      requestHeaders: controller.config.requestHeaders,
      photoFps: controller.config.photoFps
    )
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

    if glassesSessionPhase == .running || wearablesRuntimeManager.canActivateGlassesRouteForDebugMock {
      if pendingGlassesActivation &&
        controllerStatus.assistantRuntimeState == .inactive &&
        isStartingPhoneRuntimeForGlassesRoute == false {
        isStartingPhoneRuntimeForGlassesRoute = true
        pendingGlassesActivation = false
        await controller.activate(using: .glasses)
        isStartingPhoneRuntimeForGlassesRoute = false
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

    if glassesSessionPhase == .waitingForDevice &&
      shouldTearDownForWaitingDeviceLoss &&
      wearablesRuntimeManager.canActivateGlassesRouteForDebugMock == false {
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
    let visionStatus = resolvedVisionStatus()

    mergedStatus.selectedRoute = selectedRoute
    mergedStatus.activeRouteText = activeRouteText()
    mergedStatus.glassesReadinessTitle = readiness.title
    mergedStatus.glassesReadinessDetail = readiness.detail
    mergedStatus.glassesReadinessKind = readiness.kind
    mergedStatus.glassesSessionText = glassesSessionText()
    mergedStatus.activeGlassesDeviceText = wearablesRuntimeManager.activeGlassesDeviceName
    mergedStatus.glassesAudioModeText = glassesAudioModeText()
    mergedStatus.hfpRouteText = wearablesRuntimeManager.isHFPRouteAvailable ? "bidirectional_ready" : "not_ready"
    mergedStatus.glassesAudioDetailText = wearablesRuntimeManager.glassesAudioDetailText
    mergedStatus.visionCaptureStateText = visionStatus.captureStateText
    mergedStatus.visionUploadCount = visionStatus.uploadCount
    mergedStatus.visionUploadFailureCount = visionStatus.uploadFailureCount
    mergedStatus.visionLastErrorText = visionStatus.lastErrorText
    mergedStatus.phoneVisionCaptureStateText = phoneVisionCaptureStateText
    mergedStatus.phoneVisionUploadCount = phoneVisionUploadCount
    mergedStatus.phoneVisionUploadFailureCount = phoneVisionUploadFailureCount
    mergedStatus.phoneVisionLastErrorText = resolvedPhoneVisionIssueText()
    mergedStatus.phoneVisionHasAnalysisWarning = phoneVisionHasAnalysisWarning
    mergedStatus.phoneVisionModeText = phoneVisionModeText()
    mergedStatus.phoneVisionDetailText = phoneVisionDetailText()
    mergedStatus.phoneVisionToggleTitle = isPhoneVisionEnabled
      ? "Disable Phone Vision"
      : "Enable Phone Vision"
    mergedStatus.canTogglePhoneVision = canTogglePhoneVision()
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

    guard wearablesRuntimeManager.registrationState == .registered ||
      wearablesRuntimeManager.canActivateGlassesRouteForDebugMock else {
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
          "Glasses lifecycle is ready and bidirectional Bluetooth HFP is currently available on this phone.",
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

  private func resolvedVisionStatus() -> (
    captureStateText: String,
    uploadCount: Int,
    uploadFailureCount: Int,
    lastErrorText: String
  ) {
    if selectedRoute == .phone &&
      (isPhoneVisionEnabled ||
        phoneVisionCaptureStateText != "inactive" ||
        phoneVisionUploadCount > 0 ||
        phoneVisionUploadFailureCount > 0 ||
        !resolvedPhoneVisionIssueText().isEmpty) {
      return (
        phoneVisionCaptureStateText,
        phoneVisionUploadCount,
        phoneVisionUploadFailureCount,
        resolvedPhoneVisionIssueText()
      )
    }

    return (
      wearablesRuntimeManager.visionCaptureStateText,
      wearablesRuntimeManager.visionUploadCount,
      wearablesRuntimeManager.visionUploadFailureCount,
      wearablesRuntimeManager.visionLastErrorText
    )
  }

  private func phoneVisionModeText() -> String {
    isPhoneVisionEnabled ? "enabled" : "disabled"
  }

  private func phoneVisionDetailText() -> String {
    if isPhoneVisionEnabled {
      return "During active phone conversations, PortWorld captures phone-camera JPEG frames, uploads them to the backend vision endpoint, and monitors async analysis health."
    }
    return "Opt in to share phone-camera frames with the backend during active phone conversations."
  }

  private var phoneVisionHasAnalysisWarning: Bool {
    !phoneVisionAnalysisWarningText.isEmpty &&
      phoneVisionCaptureErrorText.isEmpty &&
      phoneVisionUploadErrorText.isEmpty
  }

  private func canTogglePhoneVision() -> Bool {
    pendingGlassesActivation == false && wearablesRuntimeManager.isGlassesSessionRequested == false
  }

  private func startPhoneVisionCaptureIfNeeded() async {
    if phonePhotoCaptureController == nil {
      let captureController = PhonePhotoCaptureController()
      captureController.onSnapshotUpdated = { [weak self] snapshot in
        guard let self else { return }
        self.applyPhoneVisionSnapshot(snapshot)
      }
      captureController.onPhotoCaptured = { [weak self] image, timestampMs in
        guard let self else { return }
        Task {
          await self.phoneVisionFrameUploader?.submitLatestFrame(image, captureTimestampMs: timestampMs)
        }
      }
      phonePhotoCaptureController = captureController
    }

    let sessionID = controllerStatus.sessionID
    guard !sessionID.isEmpty, sessionID != "-" else { return }

    if phoneVisionFrameUploader == nil {
      let uploader = VisionFrameUploader(
        endpointURL: controller.config.visionFrameURL,
        defaultHeaders: controller.config.requestHeaders,
        sessionID: sessionID,
        uploadIntervalMs: Int64((1000.0 / max(0.1, controller.config.photoFps)).rounded())
      )
      await uploader.bindUploadResultHandler { [weak self] result in
        self?.handlePhoneVisionUploadResult(result)
      }
      phoneVisionFrameUploader = uploader
    } else {
      await phoneVisionFrameUploader?.updateSessionID(sessionID)
      await phoneVisionFrameUploader?.bindUploadResultHandler { [weak self] result in
        self?.handlePhoneVisionUploadResult(result)
      }
    }

    await phoneVisionFrameUploader?.start()
    await phonePhotoCaptureController?.start(photoFps: controller.config.photoFps)
  }

  private func stopPhoneVisionCapture(resetState: Bool) async {
    await phonePhotoCaptureController?.stop()
    await phoneVisionFrameUploader?.stop()
    phoneVisionFrameUploader = nil

    if resetState {
      phoneVisionCaptureStateText = "inactive"
      phoneVisionUploadCount = 0
      phoneVisionUploadFailureCount = 0
      phoneVisionCaptureErrorText = ""
      phoneVisionUploadErrorText = ""
      phoneVisionAnalysisWarningText = ""
    }
  }

  private func applyPhoneVisionSnapshot(_ snapshot: PhonePhotoCaptureController.Snapshot) {
    phoneVisionCaptureStateText = snapshot.phase.rawValue
    if let errorMessage = snapshot.errorMessage, !errorMessage.isEmpty {
      phoneVisionCaptureErrorText = errorMessage
    } else if snapshot.phase != .failed {
      phoneVisionCaptureErrorText = ""
    }
    publishMergedStatus()
  }

  private func handlePhoneVisionUploadResult(_ result: VisionFrameUploadResult) {
    if result.success {
      phoneVisionUploadCount += 1
      phoneVisionUploadErrorText = ""
      if phoneVisionCaptureStateText == PhonePhotoCaptureController.Phase.failed.rawValue {
        phoneVisionCaptureStateText = PhonePhotoCaptureController.Phase.capturing.rawValue
      }
    } else {
      phoneVisionUploadFailureCount += 1
      phoneVisionUploadErrorText = result.errorDescription ?? "Phone vision upload failed."
    }

    publishMergedStatus()
  }

  private func startPhoneVisionStatusPollingIfNeeded(sessionID: String) {
    guard !sessionID.isEmpty, sessionID != "-" else { return }
    guard phoneVisionStatusPollSessionID != sessionID || phoneVisionStatusPollTask == nil else { return }

    stopPhoneVisionStatusPolling(resetState: false)
    phoneVisionStatusPollSessionID = sessionID
    phoneVisionStatusPollTask = Task { [weak self] in
      await self?.runPhoneVisionStatusPolling(sessionID: sessionID)
    }
  }

  private func stopPhoneVisionStatusPolling(resetState: Bool) {
    phoneVisionStatusPollTask?.cancel()
    phoneVisionStatusPollTask = nil
    phoneVisionStatusPollSessionID = nil

    if resetState {
      phoneVisionAnalysisWarningText = ""
    }
  }

  private func runPhoneVisionStatusPolling(sessionID: String) async {
    while !Task.isCancelled {
      do {
        let status = try await phoneVisionStatusClient.fetchStatus(
          sessionID: sessionID,
          endpointURL: controller.config.visionFrameURL,
          headers: controller.config.requestHeaders
        )
        guard phoneVisionStatusPollSessionID == sessionID else { return }
        applyPhoneVisionSessionMemoryStatus(status)
      } catch {
        guard Task.isCancelled == false else { return }
        phoneVisionAnalysisWarningText = "Async phone vision status is unavailable."
        publishMergedStatus()
      }

      do {
        try await Task.sleep(nanoseconds: 3_000_000_000)
      } catch {
        return
      }
    }
  }

  private func applyPhoneVisionSessionMemoryStatus(_ status: SessionMemoryStatusClient.SessionMemoryStatus) {
    phoneVisionAnalysisWarningText = phoneVisionAnalysisWarning(from: status)
    publishMergedStatus()
  }

  private func phoneVisionAnalysisWarning(
    from status: SessionMemoryStatusClient.SessionMemoryStatus
  ) -> String {
    if let failedFrame = status.recentFrames.first(where: {
      let processingStatus = $0.processingStatus.lowercased()
      return processingStatus == "analysis_failed" ||
        processingStatus == "bootstrap_degraded" ||
        processingStatus == "analysis_rate_limited" ||
        processingStatus == "retry_pending"
    }) {
      if let providerMessage = failedFrame.errorDetails?.providerMessage,
         providerMessage.isEmpty == false
      {
        return "Phone vision analysis degraded: \(providerMessage)"
      }
      if let providerErrorCode = failedFrame.errorDetails?.providerErrorCode,
         providerErrorCode.isEmpty == false
      {
        return "Phone vision analysis degraded: \(providerErrorCode)."
      }
      if let errorCode = failedFrame.errorCode, errorCode.isEmpty == false {
        return "Phone vision analysis degraded: \(errorCode)."
      }
      return "Phone vision analysis is degraded (\(failedFrame.processingStatus))."
    }

    if status.status == "bootstrap_degraded" {
      return "Phone vision analysis is degraded while session memory is bootstrapping."
    }

    return ""
  }

  private func resolvedPhoneVisionIssueText() -> String {
    if !phoneVisionCaptureErrorText.isEmpty {
      return phoneVisionCaptureErrorText
    }
    if !phoneVisionUploadErrorText.isEmpty {
      return phoneVisionUploadErrorText
    }
    return phoneVisionAnalysisWarningText
  }
}

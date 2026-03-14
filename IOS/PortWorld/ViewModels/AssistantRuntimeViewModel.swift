// View model that bridges assistant runtime actions and published UI status.
import Combine
import MWDATCore
import SwiftUI

@MainActor
final class AssistantRuntimeViewModel: ObservableObject {
  @Published private(set) var status: AssistantRuntimeStatus
  @Published private(set) var isProfileOnboardingReady = false

  #if DEBUG
    private static let debugPhoneVisionPreferenceKey = "portworld.debug.phoneVisionEnabled"
  #endif

  private let controller: AssistantRuntimeController
  private let wearablesRuntimeManager: WearablesRuntimeManager
  private var controllerStatus: AssistantRuntimeStatus
  private var selectedRoute: AssistantRoute = .phone
  private var pendingGlassesActivation = false
  private var isStartingPhoneRuntimeForGlassesRoute = false
  private var isStoppingGlassesRoute = false
  private var isDebugPhoneVisionEnabled = false
  private var phonePhotoCaptureController: PhonePhotoCaptureController?
  private var phoneVisionFrameUploader: VisionFrameUploaderProtocol?
  private var phoneVisionCaptureStateText = "inactive"
  private var phoneVisionUploadCount = 0
  private var phoneVisionUploadFailureCount = 0
  private var phoneVisionLastErrorText = ""
  private var cancellables = Set<AnyCancellable>()

  init(
    wearablesRuntimeManager: WearablesRuntimeManager,
    config: AssistantRuntimeConfig = AssistantRuntimeConfig.load()
  ) {
    self.wearablesRuntimeManager = wearablesRuntimeManager
    self.controller = AssistantRuntimeController(config: config)
    self.controllerStatus = controller.status
    self.status = controller.status
    #if DEBUG
      self.isDebugPhoneVisionEnabled = UserDefaults.standard.bool(
        forKey: Self.debugPhoneVisionPreferenceKey
      )
    #endif
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

  func startGuidedConversation(instructions: String) async {
    pendingGlassesActivation = false
    selectedRoute = .phone
    isProfileOnboardingReady = false
    wearablesRuntimeManager.setGlassesAudioMode(.inactive)
    await controller.startGuidedConversation(instructions: instructions)
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
    selectedRoute = route
    if route == .phone {
      wearablesRuntimeManager.setGlassesAudioMode(.inactive)
    }
    publishMergedStatus()
  }

  func handleScenePhaseChange(_ phase: ScenePhase) {
    controller.handleScenePhaseChange(phase)
  }

  func toggleDebugPhoneVisionMode() {
    #if DEBUG
      isDebugPhoneVisionEnabled.toggle()
      UserDefaults.standard.set(isDebugPhoneVisionEnabled, forKey: Self.debugPhoneVisionPreferenceKey)
      Task { @MainActor [weak self] in
        await self?.synchronizeVisionCaptureIfNeeded()
        self?.publishMergedStatus()
      }
    #endif
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
  }

  private func handleWearablesRuntimeManagerChange() {
    Task { @MainActor [weak self] in
      await self?.synchronizeGlassesRouteIfNeeded()
      await self?.synchronizeVisionCaptureIfNeeded()
      self?.publishMergedStatus()
    }
  }

  private func synchronizeVisionCaptureIfNeeded() async {
    let shouldCapturePhoneVision =
      selectedRoute == .phone &&
      isDebugPhoneVisionEnabled &&
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
    } else {
      await stopPhoneVisionCapture(resetState: selectedRoute != .phone || !isDebugPhoneVisionEnabled)
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
    mergedStatus.debugPhoneVisionModeText = debugPhoneVisionModeText()
    mergedStatus.debugPhoneVisionDetailText = debugPhoneVisionDetailText()
    mergedStatus.debugPhoneVisionToggleTitle = isDebugPhoneVisionEnabled
      ? "Disable Phone Camera Vision Test"
      : "Enable Phone Camera Vision Test"
    mergedStatus.canToggleDebugPhoneVision = canToggleDebugPhoneVision()
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
      (isDebugPhoneVisionEnabled ||
        phoneVisionCaptureStateText != "inactive" ||
        phoneVisionUploadCount > 0 ||
        phoneVisionUploadFailureCount > 0 ||
        !phoneVisionLastErrorText.isEmpty) {
      return (
        phoneVisionCaptureStateText,
        phoneVisionUploadCount,
        phoneVisionUploadFailureCount,
        phoneVisionLastErrorText
      )
    }

    return (
      wearablesRuntimeManager.visionCaptureStateText,
      wearablesRuntimeManager.visionUploadCount,
      wearablesRuntimeManager.visionUploadFailureCount,
      wearablesRuntimeManager.visionLastErrorText
    )
  }

  private func debugPhoneVisionModeText() -> String {
    #if DEBUG
      return isDebugPhoneVisionEnabled ? "enabled" : "disabled"
    #else
      return "unavailable"
    #endif
  }

  private func debugPhoneVisionDetailText() -> String {
    #if DEBUG
      if isDebugPhoneVisionEnabled {
        return "When the phone route reaches an active conversation, the app will capture one phone-camera JPEG per second and upload it to /vision/frame."
      }
      return "Debug-only test path. Enable this to exercise the backend vision endpoint from the iPhone camera without glasses."
    #else
      return ""
    #endif
  }

  private func canToggleDebugPhoneVision() -> Bool {
    #if DEBUG
      return pendingGlassesActivation == false && wearablesRuntimeManager.isGlassesSessionRequested == false
    #else
      return false
    #endif
  }

  private func startPhoneVisionCaptureIfNeeded() async {
    #if DEBUG
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
    #endif
  }

  private func stopPhoneVisionCapture(resetState: Bool) async {
    #if DEBUG
      await phonePhotoCaptureController?.stop()
      await phoneVisionFrameUploader?.stop()
      phoneVisionFrameUploader = nil

      if resetState {
        phoneVisionCaptureStateText = "inactive"
        phoneVisionUploadCount = 0
        phoneVisionUploadFailureCount = 0
        phoneVisionLastErrorText = ""
      }
    #endif
  }

  private func applyPhoneVisionSnapshot(_ snapshot: PhonePhotoCaptureController.Snapshot) {
    phoneVisionCaptureStateText = snapshot.phase.rawValue
    if let errorMessage = snapshot.errorMessage, !errorMessage.isEmpty {
      phoneVisionLastErrorText = errorMessage
    } else if snapshot.phase != .failed {
      phoneVisionLastErrorText = ""
    }
    publishMergedStatus()
  }

  private func handlePhoneVisionUploadResult(_ result: VisionFrameUploadResult) {
    if result.success {
      phoneVisionUploadCount += 1
      phoneVisionLastErrorText = ""
      if phoneVisionCaptureStateText == PhonePhotoCaptureController.Phase.failed.rawValue {
        phoneVisionCaptureStateText = PhonePhotoCaptureController.Phase.capturing.rawValue
      }
    } else {
      phoneVisionUploadFailureCount += 1
      phoneVisionLastErrorText = result.errorDescription ?? "Phone vision upload failed."
    }

    publishMergedStatus()
  }
}

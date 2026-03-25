// Shared app-scoped owner for DAT configuration, registration, discovery, and glasses session state.
import AVFAudio
import Combine
import Foundation
import MWDATCore
import UIKit

@MainActor
final class WearablesRuntimeManager: ObservableObject {
  enum ConfigurationState: Equatable {
    case idle
    case configuring
    case ready
    case failed
  }

  private enum DATConfigurationMode: String {
    case developerMode
    case registeredProject
    case invalidMixedConfig
  }

  private struct DATConfiguration {
    let rawAppLinkURLScheme: String?
    let normalizedAppLinkURLScheme: String?
    let metaAppID: String?
    let clientToken: String?
    let teamID: String?
    let bundleURLSchemes: [String]
    let hasMetaAppIDKey: Bool
    let hasClientTokenKey: Bool
    let hasTeamIDKey: Bool

    init(bundle: Bundle = .main) {
      let mwdat = bundle.object(forInfoDictionaryKey: "MWDAT") as? [String: Any] ?? [:]
      let urlTypes = bundle.object(forInfoDictionaryKey: "CFBundleURLTypes") as? [[String: Any]] ?? []

      rawAppLinkURLScheme = Self.normalizedValue(from: mwdat["AppLinkURLScheme"])
      normalizedAppLinkURLScheme = Self.normalizeScheme(rawAppLinkURLScheme)
      metaAppID = Self.normalizedValue(from: mwdat["MetaAppID"])
      clientToken = Self.normalizedValue(from: mwdat["ClientToken"])
      teamID = Self.normalizedValue(from: mwdat["TeamID"])
      hasMetaAppIDKey = mwdat.keys.contains("MetaAppID")
      hasClientTokenKey = mwdat.keys.contains("ClientToken")
      hasTeamIDKey = mwdat.keys.contains("TeamID")
      bundleURLSchemes = urlTypes
        .flatMap { $0["CFBundleURLSchemes"] as? [String] ?? [] }
        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
        .filter { $0.isEmpty == false }
    }

    var mode: DATConfigurationMode {
      validationFailure == nil ? inferredMode : .invalidMixedConfig
    }

    var validationFailure: (message: String, diagnostics: [String])? {
      guard let normalizedAppLinkURLScheme else {
        return invalid(
          message: "MWDAT.AppLinkURLScheme is missing or empty.",
          detail: "Define `MWDAT.AppLinkURLScheme` in `Info.plist` so Meta AI can callback into the app."
        )
      }

      guard bundleURLSchemes.contains(normalizedAppLinkURLScheme) else {
        return invalid(
          message: "MWDAT.AppLinkURLScheme does not match a registered app URL scheme.",
          detail: "Keep `MWDAT.AppLinkURLScheme` aligned with `CFBundleURLSchemes`."
        )
      }

      if hasClientTokenKey && clientToken == nil {
        return invalid(
          message: "MWDAT.ClientToken is present but empty.",
          detail: "Omit `ClientToken` for developer mode, or provide a real token for registered-project mode."
        )
      }

      if let clientToken, clientToken.isEmpty == false, (metaAppID == nil || metaAppID == "0") {
        return invalid(
          message: "MWDAT.ClientToken is configured without a registered Meta application ID.",
          detail: "Use developer mode without `ClientToken`, or provide both `MetaAppID` and `ClientToken` for registered-project mode."
        )
      }

      if let metaAppID, metaAppID.isEmpty == false, metaAppID != "0" {
        guard let teamID, teamID.isEmpty == false else {
          return invalid(
            message: "MWDAT.TeamID is required when using a registered Meta application ID.",
            detail: "Registered-project mode requires `TeamID` to match the Xcode signing team."
          )
        }

        guard let clientToken, clientToken.isEmpty == false else {
          return invalid(
            message: "MWDAT.ClientToken is required when using a registered Meta application ID.",
            detail: "Add the Meta Developer Center client token when enabling registered-project mode."
          )
        }
      }

      return nil
    }

    private var inferredMode: DATConfigurationMode {
      if let metaAppID, metaAppID.isEmpty == false, metaAppID != "0" {
        return .registeredProject
      }

      return .developerMode
    }

    func diagnostics() -> [String] {
      [
        "Detected DAT configuration mode: \(mode.rawValue).",
        normalizedAppLinkURLScheme.map { "AppLinkURLScheme: \($0)://." } ?? "AppLinkURLScheme: missing.",
        "Registered app URL schemes: \(bundleURLSchemes.joined(separator: ", ")).",
        "MetaAppID configured: \(metaAppID ?? "<empty>").",
        "ClientToken configured: \(clientToken == nil ? "no" : "yes").",
        "TeamID configured: \(teamID == nil ? "no" : "yes")."
      ]
    }

    private func invalid(message: String, detail: String) -> (message: String, diagnostics: [String]) {
      var diagnostics = diagnostics()
      diagnostics.append(detail)
      return (message, diagnostics)
    }

    private static func normalizedValue(from rawValue: Any?) -> String? {
      guard let rawValue else { return nil }

      let stringValue: String
      if let rawString = rawValue as? String {
        stringValue = rawString
      } else {
        stringValue = String(describing: rawValue)
      }

      let trimmed = stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
      return trimmed.isEmpty ? nil : trimmed
    }

    private static func normalizeScheme(_ rawScheme: String?) -> String? {
      guard let rawScheme else { return nil }

      let normalizedScheme = rawScheme
        .trimmingCharacters(in: .whitespacesAndNewlines)
        .replacingOccurrences(of: "://", with: "")
        .lowercased()

      return normalizedScheme.isEmpty ? nil : normalizedScheme
    }
  }

  @Published private(set) var configurationState: ConfigurationState = .idle
  @Published private(set) var configurationErrorMessage: String?
  @Published private(set) var configurationDiagnostics: [String] = []
  @Published private(set) var registrationState: RegistrationState?
  @Published private(set) var devices: [DeviceIdentifier] = []
  @Published private(set) var activeCompatibilityMessage: String?
  @Published private(set) var glassesSessionPhase: GlassesSessionPhase = .inactive
  @Published private(set) var glassesSessionState: SessionState?
  @Published private(set) var activeGlassesDeviceName: String = "-"
  @Published private(set) var isHFPRouteAvailable: Bool = false
  @Published private(set) var glassesAudioMode: AssistantAudioMode = .inactive
  @Published private(set) var glassesAudioDetailText: String = "No glasses audio path is active."
  @Published private(set) var isGlassesSessionRequested: Bool = false
  @Published private(set) var glassesSessionErrorMessage: String?
  @Published private(set) var glassesDevelopmentReadinessDetail: String =
    "Complete DAT setup before validating the glasses runtime."
  @Published private(set) var visionCaptureStateText: String = "inactive"
  @Published private(set) var visionUploadCount: Int = 0
  @Published private(set) var visionUploadFailureCount: Int = 0
  @Published private(set) var visionLastErrorText: String = ""
  @Published var showError: Bool = false
  @Published var errorMessage: String = ""

  private let configure: () throws -> Void
  private let wearablesProvider: () -> WearablesInterface
  private let audioSession: AVAudioSession
  private let datConfiguration: DATConfiguration
  private let appLinkURLScheme: String?
  private var wearables: WearablesInterface?
  private var glassesSessionCoordinator: GlassesSessionCoordinator?
  private var glassesPhotoCaptureController: GlassesPhotoCaptureController?
  private var visionFrameUploader: VisionFrameUploaderProtocol?
  private var registrationTask: Task<Void, Never>?
  private var deviceStreamTask: Task<Void, Never>?
  private var audioRouteObserver: NSObjectProtocol?
  private var compatibilityListenerTokens: [DeviceIdentifier: AnyListenerToken] = [:]
  private var compatibilityMessages: [DeviceIdentifier: String] = [:]
  private var activeGlassesDeviceID: DeviceIdentifier?
  private var isEnsuringDiscoveryPermission = false
  private var wantsVisionCapture = false
  private var visionSessionID: String?
  private var visionEndpointURL: URL?
  private var visionRequestHeaders: [String: String] = [:]
  private var visionPhotoFps: Double = 1.0

  init(
    configure: @escaping () throws -> Void = { try Wearables.configure() },
    wearablesProvider: @escaping () -> WearablesInterface = { Wearables.shared },
    audioSession: AVAudioSession = .sharedInstance(),
    appLinkURLScheme: String? = nil
  ) {
    let datConfiguration = WearablesRuntimeManager.loadDATConfiguration()
    self.configure = configure
    self.wearablesProvider = wearablesProvider
    self.audioSession = audioSession
    self.datConfiguration = datConfiguration
    self.appLinkURLScheme = (appLinkURLScheme ?? datConfiguration.normalizedAppLinkURLScheme)?.lowercased()
    refreshAudioRouteAvailability()
    registerAudioRouteObserverIfNeeded()
  }

  deinit {
    registrationTask?.cancel()
    deviceStreamTask?.cancel()
    if let audioRouteObserver {
      NotificationCenter.default.removeObserver(audioRouteObserver)
    }
  }

  func startIfNeeded() async {
    switch configurationState {
    case .configuring, .ready, .failed:
      return
    case .idle:
      await configureWearables(forceRetry: false)
    }
  }

  func retryConfiguration() async {
    await configureWearables(forceRetry: true)
  }

  func connectGlasses() {
    guard registrationState != .registering else { return }

    Task { @MainActor [weak self] in
      guard let self else { return }
      if self.configurationState != .ready {
        await self.startIfNeeded()
      }
      guard let wearables = self.wearables else {
        self.presentError(self.configurationErrorMessage ?? "Wearables SDK is not configured.")
        return
      }

      do {
        try await wearables.startRegistration()
      } catch let error as RegistrationError {
        if error == .alreadyRegistered {
          self.registrationState = .registered
          await self.ensureDiscoveryPermissionIfNeeded(using: wearables)
        } else {
          self.presentError(error.description)
        }
      } catch {
        self.presentError(error.localizedDescription)
      }
    }
  }

  func disconnectGlasses() {
    Task { @MainActor [weak self] in
      guard let self else { return }
      await self.stopGlassesSession()
      guard let wearables = self.wearables else {
        self.presentError(self.configurationErrorMessage ?? "Wearables SDK is not configured.")
        return
      }

      do {
        try await wearables.startUnregistration()
      } catch let error as UnregistrationError {
        self.presentError(error.description)
      } catch {
        self.presentError(error.localizedDescription)
      }
    }
  }

  func handleIncomingURL(_ url: URL) async {
    guard Self.matchesAppLinkURLScheme(url, expectedScheme: appLinkURLScheme) else { return }

    if configurationState != .ready {
      await configureWearables(forceRetry: configurationState == .failed)
    }

    guard let wearables else {
      presentError(configurationErrorMessage ?? "Wearables SDK is not configured.")
      return
    }

    do {
      let handled = try await wearables.handleUrl(url)
      if handled == false {
        debugLog("Ignoring DAT callback URL because the SDK did not claim it: \(url.absoluteString)")
      }
    } catch {
      presentError(error.localizedDescription)
    }
  }

  func dismissError() {
    errorMessage = ""
    showError = false
  }

  func setGlassesAudioMode(_ mode: AssistantAudioMode) {
    glassesAudioMode = mode
    refreshDevelopmentReadiness()
  }

  func startGlassesSession() async {
    if configurationState != .ready {
      await startIfNeeded()
    }

    guard configurationState == .ready else {
      glassesSessionErrorMessage = configurationErrorMessage ?? "Wearables SDK is not configured."
      return
    }

    guard registrationState == .registered else {
      glassesSessionErrorMessage = "Meta registration is not complete yet."
      return
    }

    guard devices.isEmpty == false else {
      glassesSessionErrorMessage = "No compatible glasses are currently available."
      return
    }

    guard activeCompatibilityMessage == nil else {
      glassesSessionErrorMessage = activeCompatibilityMessage
      return
    }

    guard let glassesSessionCoordinator else {
      glassesSessionErrorMessage = "Glasses session support is not ready yet."
      return
    }

    isGlassesSessionRequested = true
    glassesSessionErrorMessage = nil
    refreshAudioRouteAvailability()

    do {
      try await glassesSessionCoordinator.start()
    } catch {
      isGlassesSessionRequested = false
      glassesSessionErrorMessage = error.localizedDescription
    }
  }

  func stopGlassesSession() async {
    isGlassesSessionRequested = false
    await stopVisionCapture(resetState: true)
    guard let glassesSessionCoordinator else {
      resetGlassesSessionSnapshot()
      return
    }

    await glassesSessionCoordinator.stop()
    glassesSessionErrorMessage = nil
  }

  func setVisionCaptureActive(
    _ isActive: Bool,
    sessionID: String?,
    endpointURL: URL,
    requestHeaders: [String: String],
    photoFps: Double
  ) async {
    wantsVisionCapture = isActive
    visionSessionID = sessionID
    visionEndpointURL = endpointURL
    visionRequestHeaders = requestHeaders
    visionPhotoFps = photoFps

    if !isActive {
      await stopVisionCapture(resetState: true)
      return
    }

    await synchronizeVisionCapture()
  }

  private func configureWearables(forceRetry: Bool) async {
    switch configurationState {
    case .configuring:
      return
    case .ready where forceRetry == false:
      return
    case .failed where forceRetry == false:
      return
    case .idle, .ready, .failed:
      break
    }

    configurationState = .configuring
    configurationErrorMessage = nil
    configurationDiagnostics = []
    activeCompatibilityMessage = nil
    compatibilityMessages.removeAll(keepingCapacity: false)

    if forceRetry {
      registrationTask?.cancel()
      registrationTask = nil
      deviceStreamTask?.cancel()
      deviceStreamTask = nil
      compatibilityListenerTokens.removeAll(keepingCapacity: false)
      glassesSessionCoordinator = nil
      glassesPhotoCaptureController = nil
      visionFrameUploader = nil
      wearables = nil
      registrationState = nil
      devices = []
      resetGlassesSessionSnapshot()
    }

    if let validationFailure = datConfiguration.validationFailure {
      failConfiguration(message: validationFailure.message, diagnostics: validationFailure.diagnostics)
      return
    }

    do {
      try configure()
      let wearables = wearablesProvider()
      self.wearables = wearables
      registrationState = wearables.registrationState
      devices = wearables.devices
      configurationState = .ready
      observeWearablesStreams(using: wearables)
      monitorDeviceCompatibility(devices: wearables.devices)
      ensureGlassesSessionCoordinator(using: wearables)
      ensureGlassesPhotoCaptureController(using: wearables)
      await ensureDiscoveryPermissionIfNeeded(using: wearables)
      refreshDevelopmentReadiness()
    } catch {
      failConfiguration(
        message: error.localizedDescription,
        diagnostics: Self.buildInitializationDiagnostics(from: error, datConfiguration: datConfiguration)
      )
    }
  }

  private func observeWearablesStreams(using wearables: WearablesInterface) {
    registrationTask?.cancel()
    registrationTask = Task { [weak self] in
      guard let self else { return }
      for await state in wearables.registrationStateStream() {
        self.registrationState = state
        if state == .registered {
          await self.ensureDiscoveryPermissionIfNeeded(using: wearables)
        }
        self.refreshDevelopmentReadiness()
        await self.reconcileActiveGlassesSession()
      }
    }

    deviceStreamTask?.cancel()
    deviceStreamTask = Task { [weak self] in
      guard let self else { return }
      for await devices in wearables.devicesStream() {
        self.devices = devices
        self.monitorDeviceCompatibility(devices: devices)
        self.refreshDevelopmentReadiness()
        await self.reconcileActiveGlassesSession()
      }
    }
  }

  private func monitorDeviceCompatibility(devices: [DeviceIdentifier]) {
    guard let wearables else { return }

    let deviceSet = Set(devices)
    compatibilityListenerTokens = compatibilityListenerTokens.filter { entry in
      if deviceSet.contains(entry.key) {
        return true
      }
      compatibilityMessages[entry.key] = nil
      return false
    }
    updateActiveCompatibilityMessage()

    for deviceID in devices {
      guard compatibilityListenerTokens[deviceID] == nil else { continue }
      guard let device = wearables.deviceForIdentifier(deviceID) else { continue }

      let deviceName = device.nameOrId()
      let token = device.addCompatibilityListener { [weak self] compatibility in
        Task { @MainActor [weak self] in
          guard let self else { return }
          if compatibility == .deviceUpdateRequired {
            let message = "Device '\(deviceName)' requires an update to work with this app"
            self.compatibilityMessages[deviceID] = message
            self.updateActiveCompatibilityMessage()
            self.presentError(message)
          } else {
            self.compatibilityMessages[deviceID] = nil
            self.updateActiveCompatibilityMessage()
          }
          await self.reconcileActiveGlassesSession()
        }
      }
      compatibilityListenerTokens[deviceID] = token
    }
  }

  private func ensureDiscoveryPermissionIfNeeded(using wearables: WearablesInterface) async {
    guard registrationState == .registered else { return }
    guard devices.isEmpty else { return }
    guard isEnsuringDiscoveryPermission == false else { return }

    isEnsuringDiscoveryPermission = true
    defer { isEnsuringDiscoveryPermission = false }

    do {
      let status = try await wearables.checkPermissionStatus(.camera)
      guard status != .granted else { return }

      let requestStatus = try await wearables.requestPermission(.camera)
      if requestStatus != .granted {
        presentError("Grant camera access in the Meta AI app so your glasses can appear in PortWorld.")
      }
    } catch {
      presentError("Unable to request Meta camera access. Open the Meta AI app and grant camera permission for PortWorld.")
    }
  }

  private func updateActiveCompatibilityMessage() {
    if let activeGlassesDeviceID, let activeMessage = compatibilityMessages[activeGlassesDeviceID] {
      activeCompatibilityMessage = activeMessage
      refreshDevelopmentReadiness()
      return
    }

    if hasCompatibleDiscoveredDevice {
      activeCompatibilityMessage = nil
    } else {
      activeCompatibilityMessage = devices.compactMap { compatibilityMessages[$0] }.first
    }
    refreshDevelopmentReadiness()
  }

  private func ensureGlassesSessionCoordinator(using wearables: WearablesInterface) {
    guard glassesSessionCoordinator == nil else { return }

    let coordinator = GlassesSessionCoordinator(wearables: wearables)
    coordinator.onSnapshotUpdated = { [weak self] snapshot in
      guard let self else { return }
      self.applyGlassesSessionSnapshot(snapshot)
    }
    glassesSessionCoordinator = coordinator
  }

  private func ensureGlassesPhotoCaptureController(using wearables: WearablesInterface) {
    guard glassesPhotoCaptureController == nil else { return }

    let controller = GlassesPhotoCaptureController(wearables: wearables)
    controller.onSnapshotUpdated = { [weak self] snapshot in
      guard let self else { return }
      self.applyGlassesPhotoCaptureSnapshot(snapshot)
    }
    controller.onPhotoCaptured = { [weak self] image, timestampMs in
      guard let self else { return }
      Task {
        await self.visionFrameUploader?.submitLatestFrame(image, captureTimestampMs: timestampMs)
      }
    }
    glassesPhotoCaptureController = controller
  }

  private func applyGlassesSessionSnapshot(_ snapshot: GlassesSessionCoordinator.Snapshot) {
    let previousPhase = glassesSessionPhase
    glassesSessionPhase = snapshot.phase
    glassesSessionState = snapshot.sessionState
    activeGlassesDeviceID = snapshot.activeDeviceID
    activeGlassesDeviceName = snapshot.activeDeviceName
    if let errorMessage = snapshot.errorMessage {
      glassesSessionErrorMessage = errorMessage
    } else if snapshot.phase != .failed {
      glassesSessionErrorMessage = nil
    }
    refreshDevelopmentReadiness()

    if isGlassesSessionRequested &&
      snapshot.phase == .waitingForDevice &&
      (previousPhase == .running || previousPhase == .paused) {
      Task { @MainActor [weak self] in
        await self?.stopGlassesSession()
      }
    }
  }

  private func resetGlassesSessionSnapshot() {
    glassesSessionPhase = .inactive
    glassesSessionState = nil
    activeGlassesDeviceID = nil
    activeGlassesDeviceName = "-"
    glassesAudioMode = .inactive
    isGlassesSessionRequested = false
    glassesSessionErrorMessage = nil
    visionCaptureStateText = "inactive"
    visionUploadCount = 0
    visionUploadFailureCount = 0
    visionLastErrorText = ""
    refreshDevelopmentReadiness()
  }

  private func applyGlassesPhotoCaptureSnapshot(_ snapshot: GlassesPhotoCaptureController.Snapshot) {
    visionCaptureStateText = snapshot.phase.rawValue
    if let errorMessage = snapshot.errorMessage, !errorMessage.isEmpty {
      visionLastErrorText = errorMessage
    } else if snapshot.phase != .failed {
      visionLastErrorText = ""
    }
  }

  private func presentError(_ message: String) {
    errorMessage = message
    showError = true
  }

  private func failConfiguration(message: String, diagnostics: [String]) {
    wearables = nil
    registrationState = nil
    devices = []
    configurationState = .failed
    configurationErrorMessage = message
    configurationDiagnostics = diagnostics
    glassesSessionCoordinator = nil
    glassesPhotoCaptureController = nil
    visionFrameUploader = nil
    resetGlassesSessionSnapshot()
    refreshDevelopmentReadiness()
  }

  private func registerAudioRouteObserverIfNeeded() {
    guard audioRouteObserver == nil else { return }
    audioRouteObserver = NotificationCenter.default.addObserver(
      forName: AVAudioSession.routeChangeNotification,
      object: audioSession,
      queue: .main
    ) { [weak self] _ in
      MainActor.assumeIsolated {
        self?.refreshAudioRouteAvailability()
      }
    }
  }

  private func refreshAudioRouteAvailability() {
    let currentRoute = audioSession.currentRoute
    let inputReady = currentRoute.inputs.contains { $0.portType == .bluetoothHFP }
    let outputReady = currentRoute.outputs.contains { $0.portType == .bluetoothHFP }
    isHFPRouteAvailable = inputReady && outputReady
    refreshDevelopmentReadiness()
  }

  private func refreshDevelopmentReadiness() {
    switch glassesAudioMode {
    case .inactive:
      if isHFPRouteAvailable {
        glassesAudioDetailText = "Bidirectional Bluetooth HFP is available on this phone for the next glasses activation."
      } else {
        glassesAudioDetailText = "No live Bluetooth HFP route is detected. The glasses route will fall back to phone audio while developing without hardware."
      }
    case .phone:
      glassesAudioDetailText = "Phone audio is active."
    case .glassesHFP:
      glassesAudioDetailText = "Glasses lifecycle and Bluetooth HFP audio are both active."
    case .glassesMockFallback:
      glassesAudioDetailText = "Glasses lifecycle is active, but audio is using the phone fallback because no live HFP route is available."
    }

    switch configurationState {
    case .idle, .configuring:
      glassesDevelopmentReadinessDetail = "Shared DAT support is still initializing."

    case .failed:
      glassesDevelopmentReadinessDetail = "Wearables SDK initialization failed. The glasses runtime cannot activate until DAT is configured."

    case .ready:
      if let activeCompatibilityMessage {
        glassesDevelopmentReadinessDetail = activeCompatibilityMessage
        return
      }

      if registrationState != .registered {
        glassesDevelopmentReadinessDetail =
          "Complete Meta registration before the glasses runtime can activate."
        return
      }

      if devices.isEmpty {
        glassesDevelopmentReadinessDetail =
          "Registration is complete, but no compatible glasses are currently discovered."
        return
      }

      if glassesSessionPhase == .running {
        glassesDevelopmentReadinessDetail = glassesAudioDetailText
        return
      }

      if isHFPRouteAvailable {
        glassesDevelopmentReadinessDetail =
          "Glasses runtime can activate now. Bidirectional Bluetooth HFP is currently available on this phone."
        return
      }

      glassesDevelopmentReadinessDetail =
        "Glasses runtime can activate, but live audio still depends on a real Bluetooth HFP route."
    }
  }

  private func reconcileActiveGlassesSession() async {
    guard isGlassesSessionRequested else { return }

    guard configurationState == .ready else {
      await stopGlassesSession()
      return
    }

    guard registrationState == .registered else {
      await stopGlassesSession()
      return
    }

    guard hasCompatibleDiscoveredDevice else {
      await stopGlassesSession()
      return
    }

    guard activeCompatibilityMessage == nil else {
      await stopGlassesSession()
      return
    }

    await synchronizeVisionCapture()
  }

  private func synchronizeVisionCapture() async {
    guard wantsVisionCapture else { return }
    guard configurationState == .ready else { return }
    guard registrationState == .registered else { return }
    guard isGlassesSessionRequested else { return }
    guard glassesSessionPhase == .running else {
      if visionCaptureStateText != GlassesPhotoCaptureController.Phase.failed.rawValue {
        visionCaptureStateText = "waiting_for_glasses"
      }
      return
    }
    guard let wearables, let visionEndpointURL else { return }
    guard let sessionID = visionSessionID, !sessionID.isEmpty, sessionID != "-" else { return }

    ensureGlassesPhotoCaptureController(using: wearables)

    if visionFrameUploader == nil {
      let uploader = VisionFrameUploader(
        endpointURL: visionEndpointURL,
        defaultHeaders: visionRequestHeaders,
        sessionID: sessionID,
        uploadIntervalMs: Int64((1000.0 / max(0.1, visionPhotoFps)).rounded())
      )
      await uploader.bindUploadResultHandler { [weak self] result in
        self?.handleVisionUploadResult(result)
      }
      visionFrameUploader = uploader
    } else {
      await visionFrameUploader?.updateSessionID(sessionID)
      await visionFrameUploader?.bindUploadResultHandler { [weak self] result in
        self?.handleVisionUploadResult(result)
      }
    }

    await visionFrameUploader?.start()
    await glassesPhotoCaptureController?.start(photoFps: visionPhotoFps)
  }

  private func stopVisionCapture(resetState: Bool) async {
    wantsVisionCapture = false
    visionSessionID = nil
    await glassesPhotoCaptureController?.stop()
    await visionFrameUploader?.stop()
    visionFrameUploader = nil

    if resetState {
      visionCaptureStateText = "inactive"
      visionUploadCount = 0
      visionUploadFailureCount = 0
      visionLastErrorText = ""
    }
  }

  private func handleVisionUploadResult(_ result: VisionFrameUploadResult) {
    if result.success {
      visionUploadCount += 1
      visionLastErrorText = ""
      if visionCaptureStateText == GlassesPhotoCaptureController.Phase.failed.rawValue {
        visionCaptureStateText = GlassesPhotoCaptureController.Phase.capturing.rawValue
      }
      return
    }

    visionUploadFailureCount += 1
    visionLastErrorText = result.errorDescription ?? "Vision upload failed."
  }

  private var hasCompatibleDiscoveredDevice: Bool {
    devices.contains { compatibilityMessages[$0] == nil }
  }

  private func debugLog(_ message: String) {
    #if DEBUG
      print("[WearablesRuntimeManager] \(message)")
    #endif
  }

  private nonisolated static func matchesAppLinkURLScheme(_ url: URL, expectedScheme: String?) -> Bool {
    guard let expectedScheme else { return false }
    guard let actualScheme = url.scheme?.lowercased() else {
      return false
    }
    return actualScheme == expectedScheme
  }

  private static func loadDATConfiguration() -> DATConfiguration {
    DATConfiguration()
  }

  private static func buildInitializationDiagnostics(from error: Error, datConfiguration: DATConfiguration) -> [String] {
    let nsError = error as NSError
    var diagnostics = datConfiguration.diagnostics() + [
      "Confirm the Meta AI app is installed and developer mode is enabled for this build.",
      "Verify the DAT mode is consistent: developer mode should not define `ClientToken`; registered-project mode requires `MetaAppID`, `ClientToken`, and `TeamID`.",
      "Check that Bluetooth is enabled and your glasses can be discovered by the phone.",
      "Retry initialization after correcting the issue."
    ]

    #if DEBUG
      diagnostics.append("Debug details: domain=\(nsError.domain), code=\(nsError.code)")
    #endif

    return diagnostics
  }
}

// Shared app-scoped owner for DAT configuration, registration, discovery, and glasses session state.
import AVFAudio
import Combine
import Foundation
import MWDATCore
import UIKit

@MainActor
final class WearablesRuntimeManager: ObservableObject {
  private struct VisionUploaderConfiguration: Equatable {
    let endpointURL: URL
    let requestHeaders: [String: String]
  }

  enum ConfigurationState: Equatable {
    case idle
    case configuring
    case ready
    case failed
  }

  enum DiscoveryPermissionState: Equatable {
    case unknown
    case requesting
    case granted
    case needsApproval
    case failed(String)
  }

  enum HFPRouteAvailability: Equatable {
    case unknown
    case unavailable
    case selectable
    case active
  }

  enum ActivationBlocker: Equatable {
    case initializing
    case configurationFailed(String)
    case registrationRequired
    case cameraPermissionRequired
    case cameraPermissionFailed(String)
    case glassesNotDiscovered
    case compatibilityIssue(String)
    case hfpAudioUnavailable
    case sessionFailed(String)

    var message: String {
      switch self {
      case .initializing:
        return "Preparing Meta wearables support for the app."
      case .configurationFailed(let message):
        return message
      case .registrationRequired:
        return "Authorize PortWorld in the Meta AI app before starting the assistant."
      case .cameraPermissionRequired:
        return "Grant Meta camera access in the Meta AI app so your glasses can appear in PortWorld."
      case .cameraPermissionFailed(let message):
        return message
      case .glassesNotDiscovered:
        return "Bring your paired glasses nearby and reconnect."
      case .compatibilityIssue(let message):
        return message
      case .hfpAudioUnavailable:
        return "Connect the glasses audio route before activating the assistant."
      case .sessionFailed(let message):
        return message
      }
    }
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
  @Published private(set) var discoveryPermissionState: DiscoveryPermissionState = .unknown
  @Published private(set) var devices: [DeviceIdentifier] = []
  @Published private(set) var activeCompatibilityMessage: String?
  @Published private(set) var glassesSessionPhase: GlassesSessionPhase = .inactive
  @Published private(set) var glassesSessionState: SessionState?
  @Published private(set) var activeGlassesDeviceName: String = "-"
  @Published private(set) var hfpRouteAvailability: HFPRouteAvailability = .unknown
  @Published private(set) var glassesAudioMode: AssistantAudioMode = .inactive
  @Published private(set) var glassesAudioDetailText: String = "No glasses audio path is active."
  @Published private(set) var isGlassesSessionRequested: Bool = false
  @Published private(set) var glassesSessionErrorMessage: String?
  @Published private(set) var glassesDevelopmentReadinessDetail: String =
    "Complete DAT setup before validating the glasses runtime."
  @Published private(set) var visionStreamPhase: GlassesPhotoCaptureController.Phase = .inactive
  @Published private(set) var visionCaptureStateText: String = "inactive"
  @Published private(set) var visionUploadCount: Int = 0
  @Published private(set) var visionUploadFailureCount: Int = 0
  @Published private(set) var visionLastErrorText: String = ""
  @Published var showError: Bool = false
  @Published var errorMessage: String = ""

  var activationBlocker: ActivationBlocker? {
    switch configurationState {
    case .idle, .configuring:
      return .initializing
    case .failed:
      return .configurationFailed(configurationErrorMessage ?? "Meta wearables support failed to initialize.")
    case .ready:
      break
    }

    if let activeCompatibilityMessage {
      return .compatibilityIssue(activeCompatibilityMessage)
    }

    guard registrationState == .registered else {
      return .registrationRequired
    }

    if hasSatisfiedDiscoveryPermission == false {
      switch discoveryPermissionState {
      case .unknown, .requesting, .needsApproval:
        return .cameraPermissionRequired
      case .failed(let message):
        return .cameraPermissionFailed(message)
      case .granted:
        break
      }
    }

    if glassesSessionPhase == .failed {
      return .sessionFailed(glassesSessionErrorMessage ?? "The glasses session could not start.")
    }

    guard devices.isEmpty == false else {
      return .glassesNotDiscovered
    }

    return nil
  }

  var isGlassesActivationReady: Bool {
    activationBlocker == nil
  }

  var isVisionCaptureRequested: Bool {
    wantsVisionCapture
  }

  var isVisionStreamReady: Bool {
    visionStreamPhase == .capturing
  }

  var hasSatisfiedDiscoveryPermission: Bool {
    discoveryPermissionState == .granted || devices.isEmpty == false
  }

  var isHFPRouteSelectable: Bool {
    switch hfpRouteAvailability {
    case .selectable, .active:
      return true
    case .unknown, .unavailable:
      return false
    }
  }

  var isHFPRouteActive: Bool {
    hfpRouteAvailability == .active
  }

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
  private var visionUploaderConfiguration: VisionUploaderConfiguration?

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
          await self.refreshDiscoveryPermissionStatus(using: wearables)
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
      } else {
        await refreshDiscoveryPermissionStatus(using: wearables)
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

  func requestDiscoveryPermissionFromMetaOnboarding() async {
    if configurationState != .ready {
      await startIfNeeded()
    }

    guard configurationState == .ready, let wearables else {
      discoveryPermissionState = .failed(configurationErrorMessage ?? "Wearables SDK is not configured.")
      refreshDevelopmentReadiness()
      return
    }

    guard registrationState == .registered else {
      discoveryPermissionState = .unknown
      refreshDevelopmentReadiness()
      return
    }

    discoveryPermissionState = .requesting
    refreshDevelopmentReadiness()

    do {
      let requestStatus = try await wearables.requestPermission(.camera)
      discoveryPermissionState = requestStatus == .granted ? .granted : .needsApproval
    } catch {
      let message = "Unable to request Meta camera access. Open the Meta AI app and grant camera permission for PortWorld."
      discoveryPermissionState = .failed(message)
      presentError(message)
    }

    refreshDevelopmentReadiness()
    await reconcileActiveGlassesSession()
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

    if hasSatisfiedDiscoveryPermission == false, let wearables {
      await refreshDiscoveryPermissionStatus(using: wearables)
    }

    if hasSatisfiedDiscoveryPermission == false {
      switch discoveryPermissionState {
      case .granted:
        break
      case .failed(let message):
        glassesSessionErrorMessage = message
        return
      case .unknown, .requesting, .needsApproval:
        glassesSessionErrorMessage = ActivationBlocker.cameraPermissionRequired.message
        return
      }
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
      visionUploaderConfiguration = nil
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
      discoveryPermissionState = .unknown
      devices = wearables.devices
      configurationState = .ready
      observeWearablesStreams(using: wearables)
      monitorDeviceCompatibility(devices: wearables.devices)
      ensureGlassesSessionCoordinator(using: wearables)
      ensureGlassesPhotoCaptureController(using: wearables)
      await refreshDiscoveryPermissionStatus(using: wearables)
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
          await self.refreshDiscoveryPermissionStatus(using: wearables)
        } else {
          self.discoveryPermissionState = .unknown
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

  private func refreshDiscoveryPermissionStatus(using wearables: WearablesInterface) async {
    guard registrationState == .registered else {
      discoveryPermissionState = .unknown
      refreshDevelopmentReadiness()
      return
    }
    guard isEnsuringDiscoveryPermission == false else { return }

    isEnsuringDiscoveryPermission = true
    defer { isEnsuringDiscoveryPermission = false }

    do {
      let status = try await wearables.checkPermissionStatus(.camera)
      if status == .granted || devices.isEmpty == false {
        discoveryPermissionState = .granted
      } else {
        discoveryPermissionState = .needsApproval
      }
    } catch {
      if hasSatisfiedDiscoveryPermission {
        discoveryPermissionState = .granted
      } else {
        discoveryPermissionState = .failed("Unable to check Meta camera access right now.")
      }
    }

    refreshDevelopmentReadiness()
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
    visionStreamPhase = .inactive
    visionCaptureStateText = "inactive"
    visionUploadCount = 0
    visionUploadFailureCount = 0
    visionLastErrorText = ""
    refreshDevelopmentReadiness()
  }

  private func applyGlassesPhotoCaptureSnapshot(_ snapshot: GlassesPhotoCaptureController.Snapshot) {
    visionStreamPhase = snapshot.phase
    visionCaptureStateText = snapshot.phase.rawValue
    if let errorMessage = snapshot.errorMessage, !errorMessage.isEmpty {
      visionLastErrorText = errorMessage
    } else if snapshot.phase != .failed {
      visionLastErrorText = ""
    }

    Task { @MainActor [weak self] in
      await self?.synchronizeVisionCapture()
    }
  }

  private func presentError(_ message: String) {
    errorMessage = message
    showError = true
  }

  private func failConfiguration(message: String, diagnostics: [String]) {
    wearables = nil
    registrationState = nil
    discoveryPermissionState = .unknown
    devices = []
    configurationState = .failed
    configurationErrorMessage = message
    configurationDiagnostics = diagnostics
    glassesSessionCoordinator = nil
    glassesPhotoCaptureController = nil
    visionFrameUploader = nil
    visionUploaderConfiguration = nil
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
    if inputReady && outputReady {
      hfpRouteAvailability = .active
    } else if let availableInputs = audioSession.availableInputs {
      hfpRouteAvailability = availableInputs.contains(where: { $0.portType == .bluetoothHFP })
        ? .selectable
        : .unavailable
    } else {
      hfpRouteAvailability = .unknown
    }
    refreshDevelopmentReadiness()
  }

  private func refreshDevelopmentReadiness() {
    switch glassesAudioMode {
    case .inactive:
      switch hfpRouteAvailability {
      case .active:
        glassesAudioDetailText = "Bidirectional Bluetooth HFP is active on this phone now."
      case .selectable:
        glassesAudioDetailText = "Glasses audio is available and PortWorld can request it during the next activation."
      case .unknown:
        glassesAudioDetailText = "PortWorld will request the glasses audio route when activation starts."
      case .unavailable:
        glassesAudioDetailText = "No Bluetooth HFP glasses audio route is available right now. Connect the glasses audio route before activating the assistant."
      }
    case .glassesHFP:
      glassesAudioDetailText = "Glasses lifecycle and Bluetooth HFP audio are both active."
    }

    switch configurationState {
    case .idle, .configuring:
      glassesDevelopmentReadinessDetail = "Shared DAT support is still initializing."

    case .failed:
      glassesDevelopmentReadinessDetail = "Wearables SDK initialization failed. The glasses runtime cannot activate until DAT is configured."

    case .ready:
      if glassesSessionPhase == .running {
        glassesDevelopmentReadinessDetail = glassesAudioDetailText
        return
      }

      if let activationBlocker {
        glassesDevelopmentReadinessDetail = activationBlocker.message
        return
      }

      glassesDevelopmentReadinessDetail = glassesAudioDetailText
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

    guard hasSatisfiedDiscoveryPermission else {
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
    guard let wearables, let visionEndpointURL else { return }
    guard let sessionID = visionSessionID, !sessionID.isEmpty, sessionID != "-" else { return }

    ensureGlassesPhotoCaptureController(using: wearables)

    guard glassesSessionPhase == .running else {
      await stopVisionUploader(discardConfiguration: false)
      return
    }

    await glassesPhotoCaptureController?.start(photoFps: visionPhotoFps)

    guard isVisionStreamReady else {
      await stopVisionUploader(discardConfiguration: false)
      return
    }

    debugLog(
      "Synchronizing vision capture session=\(sessionID) endpoint=\(visionEndpointURL.absoluteString) fps=\(visionPhotoFps)"
    )

    await ensureVisionFrameUploader(
      sessionID: sessionID,
      endpointURL: visionEndpointURL,
      requestHeaders: visionRequestHeaders
    )
    await visionFrameUploader?.start()
    debugLog("Started vision uploader for session=\(sessionID)")
  }

  private func stopVisionCapture(resetState: Bool) async {
    wantsVisionCapture = false
    visionSessionID = nil
    await glassesPhotoCaptureController?.stop()
    await stopVisionUploader(discardConfiguration: true)

    if resetState {
      visionStreamPhase = .inactive
      visionCaptureStateText = "inactive"
      visionUploadCount = 0
      visionUploadFailureCount = 0
      visionLastErrorText = ""
    }
  }

  private func ensureVisionFrameUploader(
    sessionID: String,
    endpointURL: URL,
    requestHeaders: [String: String]
  ) async {
    let desiredConfiguration = VisionUploaderConfiguration(
      endpointURL: endpointURL,
      requestHeaders: requestHeaders
    )

    if visionUploaderConfiguration != desiredConfiguration {
      await stopVisionUploader(discardConfiguration: true)
    }

    if visionFrameUploader == nil {
      let uploader = VisionFrameUploader(
        endpointURL: endpointURL,
        defaultHeaders: requestHeaders,
        sessionID: sessionID,
        uploadIntervalMs: Int64((1000.0 / max(0.1, visionPhotoFps)).rounded())
      )
      await uploader.bindUploadResultHandler { [weak self] result in
        self?.handleVisionUploadResult(result)
      }
      visionFrameUploader = uploader
      visionUploaderConfiguration = desiredConfiguration
      debugLog("Created vision uploader for session=\(sessionID)")
      return
    }

    await visionFrameUploader?.updateSessionID(sessionID)
    await visionFrameUploader?.bindUploadResultHandler { [weak self] result in
      self?.handleVisionUploadResult(result)
    }
    debugLog("Updated vision uploader session=\(sessionID)")
  }

  private func stopVisionUploader(discardConfiguration: Bool) async {
    await visionFrameUploader?.stop()
    if discardConfiguration {
      visionFrameUploader = nil
      visionUploaderConfiguration = nil
    }
  }

  private func handleVisionUploadResult(_ result: VisionFrameUploadResult) {
    if result.success {
      visionUploadCount += 1
      visionLastErrorText = ""
      debugLog(
        "Vision upload succeeded frame=\(result.frameID) status=\(result.httpStatusCode ?? -1) attempts=\(result.attemptCount) latencyMs=\(result.latencyMs) payloadBytes=\(result.payloadBytes)"
      )
      return
    }

    visionUploadFailureCount += 1
    visionLastErrorText = result.errorDescription ?? "Vision upload failed."
    debugLog(
      "Vision upload failed frame=\(result.frameID) status=\(result.httpStatusCode ?? -1) attempts=\(result.attemptCount) errorCode=\(result.errorCode ?? "-") error=\(result.errorDescription ?? "unknown")"
    )
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

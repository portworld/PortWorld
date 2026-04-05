import Combine
import SwiftUI

@MainActor
final class WakePracticeSessionViewModel: ObservableObject {
  struct Feedback {
    let title: String
    let detail: String
    let tone: FeedbackTone
  }

  enum Stage {
    case wake
    case sleep
    case completed
  }

  enum FeedbackTone {
    case neutral
    case success
    case error
  }

  @Published private(set) var stage: Stage = .wake
  @Published private(set) var wakeCount = 0
  @Published private(set) var sleepCount = 0
  @Published private(set) var isListening = false
  @Published private(set) var isStarting = false
  @Published private(set) var didAttemptStart = false
  @Published private(set) var feedback = Feedback(
    title: "Ready?",
    detail: "We’ll verify your glasses voice commands here.",
    tone: .neutral
  )
  @Published private(set) var startupBlockerMessage: String?
  @Published private(set) var audioRouteDetail = "PortWorld will request the glasses audio route when practice starts."
  @Published private(set) var sessionPhase: GlassesSessionPhase = .inactive
  @Published private(set) var sessionErrorMessage: String?

  let wakePhrase: String
  let sleepPhrase: String

  private let wearablesRuntimeManager: WearablesRuntimeManager
  private let sessionObserver: OnboardingGlassesSessionObserver
  private let glassesAudioIO: GlassesAudioIO
  private let wakePhraseDetector: WakePhraseDetector
  private var cancellables = Set<AnyCancellable>()
  private var feedbackResetTask: Task<Void, Never>?
  private var isStopping = false

  init(
    wearablesRuntimeManager: WearablesRuntimeManager,
    settings: AppSettingsStore.Settings
  ) {
    let config = OnboardingSessionSupport.makeConfig(from: settings)
    self.wearablesRuntimeManager = wearablesRuntimeManager
    self.sessionObserver = OnboardingGlassesSessionObserver(wearablesRuntimeManager: wearablesRuntimeManager)
    self.glassesAudioIO = GlassesAudioIO()
    self.wakePhraseDetector = WakePhraseDetector(config: config)
    self.wakePhrase = config.wakePhrase
    self.sleepPhrase = config.sleepPhrase

    glassesAudioIO.onWakePCMFrame = { [weak self] frame in
      self?.wakePhraseDetector.processPCMFrame(frame)
    }

    wakePhraseDetector.onWakeDetected = { [weak self] _ in
      Task { @MainActor [weak self] in
        self?.handleWakeDetected()
      }
    }

    wakePhraseDetector.onSleepDetected = { [weak self] _ in
      Task { @MainActor [weak self] in
        self?.handleSleepDetected()
      }
    }

    wakePhraseDetector.onError = { [weak self] message in
      Task { @MainActor [weak self] in
        self?.handleDetectorError(message)
      }
    }

    wakePhraseDetector.onStatusChanged = { [weak self] status in
      Task { @MainActor [weak self] in
        self?.handleDetectorStatusChanged(status)
      }
    }

    sessionObserver.$audioRouteDetail
      .receive(on: RunLoop.main)
      .sink { [weak self] in
        self?.audioRouteDetail = $0
      }
      .store(in: &cancellables)

    sessionObserver.$sessionPhase
      .receive(on: RunLoop.main)
      .sink { [weak self] in
        self?.sessionPhase = $0
      }
      .store(in: &cancellables)

    sessionObserver.$sessionErrorMessage
      .receive(on: RunLoop.main)
      .sink { [weak self] in
        self?.sessionErrorMessage = $0
      }
      .store(in: &cancellables)

    refreshNeutralFeedback()
  }

  deinit {
    feedbackResetTask?.cancel()
  }

  var currentCompletedCount: Int {
    switch stage {
    case .wake:
      return wakeCount
    case .sleep:
      return sleepCount
    case .completed:
      return 3
    }
  }

  var canContinue: Bool {
    stage == .completed
  }

  func startListening() async {
    guard isListening == false else { return }
    guard isStarting == false else { return }

    isStarting = true
    didAttemptStart = true
    startupBlockerMessage = nil
    sessionErrorMessage = nil

    if let blocker = wearablesRuntimeManager.activationBlocker {
      startupBlockerMessage = blocker.message
      isStarting = false
      refreshNeutralFeedback()
      return
    }

    await wearablesRuntimeManager.startGlassesSession()

    if let blocker = wearablesRuntimeManager.activationBlocker {
      startupBlockerMessage = blocker.message
      isStarting = false
      refreshNeutralFeedback()
      return
    }

    if let sessionErrorMessage = wearablesRuntimeManager.glassesSessionErrorMessage,
       sessionErrorMessage.isEmpty == false
    {
      self.sessionErrorMessage = sessionErrorMessage
      isStarting = false
      refreshNeutralFeedback()
      return
    }

    let authorization = await wakePhraseDetector.requestAuthorizationIfNeeded()
    guard authorization == .authorized || authorization == .notRequired else {
      let permissionMessage = "Speech recognition permission is required to verify voice commands through your glasses."
      startupBlockerMessage = permissionMessage
      setFeedback(title: "Permission needed", detail: permissionMessage, tone: .error)
      await cleanupAfterFailedStart()
      isStarting = false
      return
    }

    do {
      try await glassesAudioIO.prepareForArmedListening()
      wakePhraseDetector.startArmedListening()
      isListening = true
      refreshNeutralFeedback()
    } catch {
      sessionErrorMessage = error.localizedDescription
      setFeedback(title: "Glasses audio unavailable", detail: error.localizedDescription, tone: .error)
      await cleanupAfterFailedStart()
    }

    isStarting = false
  }

  func stopListening() async {
    guard isStopping == false else { return }

    isStopping = true
    feedbackResetTask?.cancel()
    wakePhraseDetector.stop()
    await glassesAudioIO.stop()
    await wearablesRuntimeManager.stopGlassesSession()
    isListening = false
    isStarting = false
    isStopping = false

    if stage != .completed {
      refreshNeutralFeedback()
    }
  }

  func handleScenePhaseChange(_ phase: ScenePhase) {
    guard phase != .active else { return }
    guard isListening else { return }
    Task {
      await stopListening()
    }
  }

  private func handleWakeDetected() {
    guard isListening else { return }
    guard stage == .wake else { return }
    guard wakeCount < 3 else { return }

    wakeCount += 1
    showSuccessFeedback(detail: "\(wakeCount) of 3 complete")

    if wakeCount == 3 {
      transitionToSleepStage()
    } else {
      scheduleFeedbackReset()
    }
  }

  private func handleSleepDetected() {
    guard isListening else { return }
    guard stage == .sleep else { return }
    guard sleepCount < 3 else { return }

    sleepCount += 1
    showSuccessFeedback(detail: "\(sleepCount) of 3 complete")

    if sleepCount == 3 {
      feedbackResetTask?.cancel()
      stage = .completed
      setFeedback(
        title: "Voice commands are ready",
        detail: "Your glasses detected both phrases three times.",
        tone: .success
      )
      Task {
        await stopListening()
      }
    } else {
      scheduleFeedbackReset()
    }
  }

  private func transitionToSleepStage() {
    feedbackResetTask?.cancel()
    setFeedback(
      title: "Great!",
      detail: "Now say your sleep phrase through the glasses.",
      tone: .success
    )

    Task { @MainActor [weak self] in
      try? await Task.sleep(nanoseconds: 900_000_000)
      guard let self else { return }
      guard self.stage == .wake else { return }
      self.stage = .sleep
      self.refreshNeutralFeedback()
    }
  }

  private func handleDetectorError(_ message: String) {
    guard stage != .completed else { return }
    guard isStopping == false else { return }
    sessionErrorMessage = message
    setFeedback(title: "Try again", detail: message, tone: .error)
  }

  private func handleDetectorStatusChanged(_ status: WakePhraseDetector.StatusSnapshot) {
    if status.authorization == WakeWordAuthorizationState.denied.rawValue ||
      status.authorization == WakeWordAuthorizationState.restricted.rawValue
    {
      let permissionMessage = "Speech recognition permission is required to verify voice commands through your glasses."
      startupBlockerMessage = permissionMessage
      setFeedback(title: "Permission needed", detail: permissionMessage, tone: .error)
    }
  }

  private func showSuccessFeedback(detail: String) {
    feedbackResetTask?.cancel()
    setFeedback(title: "Great!", detail: detail, tone: .success)
  }

  private func scheduleFeedbackReset() {
    feedbackResetTask?.cancel()
    feedbackResetTask = Task { @MainActor [weak self] in
      try? await Task.sleep(nanoseconds: 900_000_000)
      guard let self else { return }
      guard self.stage != .completed else { return }
      guard self.isStopping == false else { return }
      self.refreshNeutralFeedback()
    }
  }

  private func refreshNeutralFeedback() {
    if let startupBlockerMessage, isListening == false {
      setFeedback(title: "Glasses not ready", detail: startupBlockerMessage, tone: .error)
      return
    }

    if let sessionErrorMessage, isListening == false, stage != .completed {
      setFeedback(title: "Glasses audio unavailable", detail: sessionErrorMessage, tone: .error)
      return
    }

    switch stage {
    case .wake:
      setFeedback(
        title: isListening ? "Listening..." : "Ready?",
        detail: "Say \"\(displayWakePhrase)\" clearly through your glasses.",
        tone: .neutral
      )
    case .sleep:
      setFeedback(
        title: isListening ? "Listening..." : "Ready?",
        detail: "Say \"\(displaySleepPhrase)\" clearly through your glasses.",
        tone: .neutral
      )
    case .completed:
      setFeedback(
        title: "Voice commands are ready",
        detail: "Your glasses detected both phrases three times.",
        tone: .success
      )
    }
  }

  private func setFeedback(title: String, detail: String, tone: FeedbackTone) {
    feedback = Feedback(title: title, detail: detail, tone: tone)
  }

  private func cleanupAfterFailedStart() async {
    wakePhraseDetector.stop()
    await glassesAudioIO.stop()
    await wearablesRuntimeManager.stopGlassesSession()
    isListening = false
  }

  private var displayWakePhrase: String {
    OnboardingSessionSupport.formattedPhrase(wakePhrase)
  }

  private var displaySleepPhrase: String {
    OnboardingSessionSupport.formattedPhrase(sleepPhrase)
  }
}

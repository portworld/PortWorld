import Combine
import Foundation

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
  @Published private(set) var feedback = Feedback(
    title: "Ready?",
    detail: "We’ll listen for your phrase three times.",
    tone: .neutral
  )
  @Published private(set) var errorText = ""

  let wakePhrase: String
  let sleepPhrase: String

  private let phoneAudioIO = PhoneAudioIO(preferSpeakerOutput: false)
  private let wakePhraseDetector: WakePhraseDetector
  private var feedbackResetTask: Task<Void, Never>?
  private var attemptTimeoutTask: Task<Void, Never>?
  private var isStopping = false

  init(config: AssistantRuntimeConfig) {
    self.wakePhrase = config.wakePhrase
    self.sleepPhrase = config.sleepPhrase
    self.wakePhraseDetector = WakePhraseDetector(config: config)

    phoneAudioIO.onWakePCMFrame = { [weak self] frame in
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
        guard let self else { return }
        guard self.shouldAcceptDetectorUpdates else { return }
        self.errorText = message
        self.setFeedback(title: "Try again", detail: message, tone: .error)
      }
    }

    wakePhraseDetector.onStatusChanged = { [weak self] status in
      Task { @MainActor [weak self] in
        guard let self else { return }
        guard self.shouldAcceptDetectorUpdates else { return }
        self.handleStatusChanged(status)
      }
    }
  }

  deinit {
    feedbackResetTask?.cancel()
    attemptTimeoutTask?.cancel()
  }

  func startListening() async {
    errorText = ""

    let authorization = await wakePhraseDetector.requestAuthorizationIfNeeded()
    guard authorization == .authorized || authorization == .notRequired else {
      errorText = "Speech recognition permission is required to test your voice commands."
      setFeedback(title: "Permission needed", detail: errorText, tone: .error)
      return
    }

    do {
      try await phoneAudioIO.prepareForArmedListening()
      wakePhraseDetector.startArmedListening()
      isListening = true
      refreshNeutralFeedback()
      scheduleAttemptTimeout()
    } catch {
      errorText = error.localizedDescription
      setFeedback(title: "Microphone unavailable", detail: error.localizedDescription, tone: .error)
    }
  }

  func stopListening() async {
    isStopping = true
    feedbackResetTask?.cancel()
    attemptTimeoutTask?.cancel()
    wakePhraseDetector.stop()
    await phoneAudioIO.stop()
    isListening = false
    isStopping = false
    if stage != .completed {
      refreshNeutralFeedback()
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
      attemptTimeoutTask?.cancel()
      stage = .completed
      setFeedback(title: "All set", detail: "Both phrases were detected three times.", tone: .success)
      Task { await stopListening() }
    } else {
      scheduleFeedbackReset()
    }
  }

  private func transitionToSleepStage() {
    feedbackResetTask?.cancel()
    attemptTimeoutTask?.cancel()
    setFeedback(title: "Great!", detail: "Now let’s practice your sleep phrase.", tone: .success)

    Task { @MainActor [weak self] in
      try? await Task.sleep(nanoseconds: 900_000_000)
      guard let self else { return }
      self.stage = .sleep
      self.refreshNeutralFeedback()
      self.scheduleAttemptTimeout()
    }
  }

  private func showSuccessFeedback(detail: String) {
    feedbackResetTask?.cancel()
    attemptTimeoutTask?.cancel()
    setFeedback(title: "Great!", detail: detail, tone: .success)
  }

  private func scheduleFeedbackReset() {
    feedbackResetTask?.cancel()
    feedbackResetTask = Task { @MainActor [weak self] in
      try? await Task.sleep(nanoseconds: 900_000_000)
      guard let self else { return }
      guard self.shouldAcceptDetectorUpdates else { return }
      self.refreshNeutralFeedback()
      self.scheduleAttemptTimeout()
    }
  }

  private func scheduleAttemptTimeout() {
    attemptTimeoutTask?.cancel()
    attemptTimeoutTask = Task { @MainActor [weak self] in
      try? await Task.sleep(nanoseconds: 6_000_000_000)
      guard let self else { return }
      guard self.isListening else { return }
      guard self.shouldAcceptDetectorUpdates else { return }
      self.refreshNeutralFeedback()
      self.scheduleAttemptTimeout()
    }
  }

  private func refreshNeutralFeedback() {
    switch stage {
    case .wake:
      setFeedback(
        title: isListening ? "Listening..." : "Ready?",
        detail: "Say \"\(displayWakePhrase)\" clearly.",
        tone: .neutral
      )
    case .sleep:
      setFeedback(
        title: isListening ? "Listening..." : "Ready?",
        detail: "Say \"\(displaySleepPhrase)\" clearly.",
        tone: .neutral
      )
    case .completed:
      setFeedback(
        title: "All set",
        detail: "Both phrases were detected three times.",
        tone: .success
      )
    }
  }

  private func handleStatusChanged(_ status: WakePhraseDetector.StatusSnapshot) {
    if status.authorization == "denied" || status.authorization == "restricted" {
      errorText = "Speech recognition permission is required to test your voice commands."
      setFeedback(title: "Permission needed", detail: errorText, tone: .error)
    }
  }

  private func setFeedback(title: String, detail: String, tone: FeedbackTone) {
    feedback = Feedback(title: title, detail: detail, tone: tone)
  }

  private var shouldAcceptDetectorUpdates: Bool {
    stage != .completed && isStopping == false
  }

  private var displayWakePhrase: String {
    formattedPhrase(wakePhrase)
  }

  private var displaySleepPhrase: String {
    formattedPhrase(sleepPhrase)
  }

  private func formattedPhrase(_ phrase: String) -> String {
    phrase
      .split(separator: " ")
      .map { $0.prefix(1).uppercased() + $0.dropFirst().lowercased() }
      .joined(separator: " ")
  }
}

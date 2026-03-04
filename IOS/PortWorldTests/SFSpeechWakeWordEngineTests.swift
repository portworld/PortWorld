import Speech
import XCTest
@testable import PortWorld

@MainActor
final class SFSpeechWakeWordEngineTests: XCTestCase {

  func testNormalizationDetectsWakeWithDiacriticsAndPunctuation() async throws {
    let rig = TestRig(nowMs: 10_000)
    let engine = rig.makeEngine(wakePhrase: "Héy, Mário!", sleepPhrase: nil, detectionCooldownMs: 250)

    var detections: [WakeWordDetectionEvent] = []
    engine.onWakeDetected = { detections.append($0) }

    engine.startListening()
    rig.emitTranscript("... hey mario ???", isFinal: false)

    try await AsyncTestWait.until {
      detections.count == 1
    }
    XCTAssertEqual(detections.count, 1)
    XCTAssertEqual(detections.first?.wakePhrase, "Héy, Mário!")
    XCTAssertEqual(detections.first?.timestampMs, 10_000)
  }

  func testCooldownSuppressesRepeatedWakeWithinWindow() async throws {
    let rig = TestRig(nowMs: 2_000)
    let engine = rig.makeEngine(wakePhrase: "hey mario", sleepPhrase: nil, detectionCooldownMs: 1_500)

    var detections: [WakeWordDetectionEvent] = []
    engine.onWakeDetected = { detections.append($0) }

    engine.startListening()

    rig.emitTranscript("hey mario", isFinal: false)
    try await AsyncTestWait.until {
      detections.count == 1
    }

    rig.nowMs = 3_000
    rig.emitTranscript("hey mario", isFinal: false)
    try await Task.sleep(nanoseconds: 60_000_000)
    XCTAssertEqual(detections.count, 1)

    rig.nowMs = 3_600
    rig.emitTranscript("hey mario", isFinal: false)

    try await AsyncTestWait.until {
      detections.count == 2
    }
    XCTAssertEqual(detections.count, 2)
    XCTAssertEqual(detections[0].timestampMs, 2_000)
    XCTAssertEqual(detections[1].timestampMs, 3_600)
  }

  func testSleepDetectionHasPriorityOverWakeWhenBothPresent() async throws {
    let rig = TestRig(nowMs: 7_500)
    let engine = rig.makeEngine(wakePhrase: "hey mario", sleepPhrase: "go to sleep", detectionCooldownMs: 250)

    var wakeDetections: [WakeWordDetectionEvent] = []
    var sleepDetections: [WakeWordDetectionEvent] = []
    engine.onWakeDetected = { wakeDetections.append($0) }
    engine.onSleepDetected = { sleepDetections.append($0) }

    engine.startListening()
    rig.emitTranscript("hey mario please go to sleep now", isFinal: false)

    try await AsyncTestWait.until {
      sleepDetections.count == 1
    }
    XCTAssertTrue(wakeDetections.isEmpty)
    XCTAssertEqual(sleepDetections.count, 1)
    XCTAssertEqual(sleepDetections.first?.wakePhrase, "go to sleep")
    XCTAssertEqual(sleepDetections.first?.timestampMs, 7_500)
  }

  func testCircuitBreakerStopsListeningAfterFiveConsecutiveErrors() async throws {
    let rig = TestRig(nowMs: 100)
    let engine = rig.makeEngine(wakePhrase: "hey mario", sleepPhrase: nil, detectionCooldownMs: 250)

    var statuses: [WakeWordStatusSnapshot] = []
    var errors: [Error] = []
    engine.onStatusChanged = { statuses.append($0) }
    engine.onError = { errors.append($0) }

    engine.startListening()

    for i in 1...5 {
      try await AsyncTestWait.until {
        rig.hasActiveRecognitionTask
      }
      rig.emitError(TestError(id: i))
    }

    try await AsyncTestWait.until {
      errors.count == 5 && engine.isListening == false && rig.hasActiveRecognitionTask == false
    }
    let emittedTestErrors = errors.compactMap { $0 as? TestError }
    XCTAssertEqual(emittedTestErrors.count, 5)
    XCTAssertFalse(engine.isListening)
    XCTAssertFalse(rig.hasActiveRecognitionTask)

    let failedStatuses = statuses.filter { $0.runtime == .failed }
    XCTAssertFalse(failedStatuses.isEmpty)
    XCTAssertTrue(failedStatuses.last?.detail?.contains("Recognition failed 5 times consecutively") == true)
  }

  func testFinalResultRestartsRecognitionTask() async throws {
    let rig = TestRig(nowMs: 1_000)
    let engine = rig.makeEngine(wakePhrase: "hey mario", sleepPhrase: nil, detectionCooldownMs: 250)

    engine.startListening()
    XCTAssertEqual(rig.startedTaskCount, 1)

    rig.emitTranscript("noise", isFinal: true)
    try await AsyncTestWait.until {
      rig.startedTaskCount == 2
    }
    XCTAssertEqual(rig.startedTaskCount, 2)
  }

  func testStartListeningStopsWhenRecognitionTaskCreationFails() async throws {
    let rig = TestRig(nowMs: 1_000)
    rig.failNextRecognitionTaskCreation()
    let engine = rig.makeEngine(wakePhrase: "hey mario", sleepPhrase: nil, detectionCooldownMs: 250)

    var statuses: [WakeWordStatusSnapshot] = []
    var errors: [Error] = []
    engine.onStatusChanged = { statuses.append($0) }
    engine.onError = { errors.append($0) }

    engine.startListening()

    try await AsyncTestWait.until {
      engine.isListening == false && rig.hasActiveRecognitionTask == false && errors.isEmpty == false
    }

    XCTAssertFalse(engine.isListening)
    XCTAssertFalse(rig.hasActiveRecognitionTask)
    XCTAssertEqual(rig.startedTaskCount, 1)
    XCTAssertTrue(
      errors.contains { error in
        guard let wakeWordError = error as? WakeWordEngineError else { return false }
        if case .recognitionTaskCreationFailed = wakeWordError {
          return true
        }
        return false
      }
    )

    let failedStatuses = statuses.filter { $0.runtime == .failed }
    XCTAssertFalse(failedStatuses.isEmpty)
    XCTAssertEqual(failedStatuses.last?.detail, "Failed to start speech recognition task")
  }

  func testWakeDetectionFromOffMainRecognizerCallbackIsProcessed() async throws {
    let rig = TestRig(nowMs: 10_000)
    let engine = rig.makeEngine(wakePhrase: "hey mario", sleepPhrase: nil, detectionCooldownMs: 250)

    var detections: [WakeWordDetectionEvent] = []
    engine.onWakeDetected = { detections.append($0) }

    engine.startListening()
    rig.emitTranscriptOffMain("hey mario", isFinal: false)

    try await AsyncTestWait.until {
      detections.count == 1
    }

    XCTAssertEqual(detections.count, 1)
    XCTAssertEqual(detections.first?.wakePhrase, "hey mario")
    XCTAssertEqual(detections.first?.timestampMs, 10_000)
  }
}

private struct TestError: LocalizedError {
  let id: Int

  var errorDescription: String? {
    "test-error-\(id)"
  }
}

private final class TestRig {
  var nowMs: Int64
  private let recognizer = FakeSpeechRecognizer()
  private var requestSequence: Int = 0
  private var taskCreationFailuresRemaining: Int = 0
  private(set) var startedTaskCount: Int = 0
  var hasActiveRecognitionTask: Bool { recognizer.hasActiveRecognitionTask }

  init(nowMs: Int64) {
    self.nowMs = nowMs
  }

  func makeEngine(
    wakePhrase: String,
    sleepPhrase: String?,
    detectionCooldownMs: Int64
  ) -> SFSpeechWakeWordEngine {
    SFSpeechWakeWordEngine(
      wakePhrase: wakePhrase,
      sleepPhrase: sleepPhrase,
      localeIdentifier: "en-US",
      requiresOnDeviceRecognition: false,
      detectionCooldownMs: detectionCooldownMs,
      nowMsProvider: { [weak self] in
        self?.nowMs ?? 0
      },
      authorizationStatusProvider: { .authorized },
      recognizerFactory: { [recognizer] _ in recognizer },
      recognitionRequestFactory: { [weak self] in
        guard let self else { return FakeRecognitionRequest(id: -1) }
        self.requestSequence += 1
        return FakeRecognitionRequest(id: self.requestSequence)
      },
      recognitionTaskFactory: { [weak self] recognizer, request, handler in
        guard let self else { return nil }
        self.startedTaskCount += 1
        if self.taskCreationFailuresRemaining > 0 {
          self.taskCreationFailuresRemaining -= 1
          return nil
        }
        return recognizer.recognitionTask(with: request, resultHandler: handler)
      }
    )
  }

  func failNextRecognitionTaskCreation(count: Int = 1) {
    taskCreationFailuresRemaining = count
  }

  func emitTranscript(_ transcript: String, isFinal: Bool) {
    recognizer.emit(update: WakeWordRecognitionUpdate(transcript: transcript, isFinal: isFinal), error: nil)
  }

  func emitError(_ error: Error) {
    recognizer.emit(update: nil, error: error)
  }

  func emitTranscriptOffMain(_ transcript: String, isFinal: Bool) {
    DispatchQueue.global(qos: .userInitiated).async { [recognizer] in
      recognizer.emit(update: WakeWordRecognitionUpdate(transcript: transcript, isFinal: isFinal), error: nil)
    }
  }
}

private final class FakeSpeechRecognizer: WakeWordSpeechRecognizer {
  var isAvailable: Bool = true
  var supportsOnDeviceRecognition: Bool = true
  weak var delegate: SFSpeechRecognizerDelegate?

  private struct ActiveRecognition {
    let token: Int
    let handler: (WakeWordRecognitionUpdate?, Error?) -> Void
  }

  private var nextToken: Int = 0
  private var activeRecognition: ActiveRecognition?
  var hasActiveRecognitionTask: Bool { activeRecognition != nil }

  func recognitionTask(
    with request: any WakeWordSpeechRecognitionRequest,
    resultHandler: @escaping (WakeWordRecognitionUpdate?, Error?) -> Void
  ) -> (any WakeWordSpeechRecognitionTask)? {
    _ = request
    nextToken += 1
    let token = nextToken
    activeRecognition = ActiveRecognition(token: token, handler: resultHandler)
    return FakeSpeechRecognitionTask(token: token) { [weak self] token in
      self?.cancelTask(token: token)
    }
  }

  func emit(update: WakeWordRecognitionUpdate?, error: Error?) {
    guard let activeRecognition else {
      XCTFail("No active recognition handler available")
      return
    }
    activeRecognition.handler(update, error)
  }

  private func cancelTask(token: Int) {
    guard activeRecognition?.token == token else { return }
    activeRecognition = nil
  }
}

private final class FakeRecognitionRequest: WakeWordSpeechRecognitionRequest {
  let id: Int
  private(set) var shouldReportPartialResults: Bool = false
  private(set) var requiresOnDeviceRecognition: Bool = false

  init(id: Int) {
    self.id = id
  }

  func append(_ audioPCMBuffer: AVAudioPCMBuffer) {
    _ = audioPCMBuffer
  }

  func endAudio() {}

  func configure(shouldReportPartialResults: Bool, requiresOnDeviceRecognition: Bool) {
    self.shouldReportPartialResults = shouldReportPartialResults
    self.requiresOnDeviceRecognition = requiresOnDeviceRecognition
  }
}

private final class FakeSpeechRecognitionTask: WakeWordSpeechRecognitionTask {
  private let token: Int
  private let onCancel: (Int) -> Void

  init(token: Int, onCancel: @escaping (Int) -> Void) {
    self.token = token
    self.onCancel = onCancel
  }

  func cancel() {
    onCancel(token)
  }
}

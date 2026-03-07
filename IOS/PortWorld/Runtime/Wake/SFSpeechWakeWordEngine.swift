// Speech-recognition-backed wake-word engine for the assistant runtime.
import AVFAudio
import Foundation
import OSLog
import Speech

struct WakeWordRecognitionUpdate {
  let transcript: String
  let isFinal: Bool
}

protocol WakeWordSpeechRecognitionRequest: AnyObject {
  func append(_ audioPCMBuffer: AVAudioPCMBuffer)
  func endAudio()
  func configure(
    shouldReportPartialResults: Bool,
    requiresOnDeviceRecognition: Bool
  )
}

private final class SFSpeechAudioBufferRecognitionRequestAdapter: WakeWordSpeechRecognitionRequest {
  private let base: SFSpeechAudioBufferRecognitionRequest

  init(base: SFSpeechAudioBufferRecognitionRequest = SFSpeechAudioBufferRecognitionRequest()) {
    self.base = base
  }

  func append(_ audioPCMBuffer: AVAudioPCMBuffer) {
    base.append(audioPCMBuffer)
  }

  func endAudio() {
    base.endAudio()
  }

  func configure(
    shouldReportPartialResults: Bool,
    requiresOnDeviceRecognition: Bool
  ) {
    base.shouldReportPartialResults = shouldReportPartialResults
    if #available(iOS 13.0, *) {
      base.requiresOnDeviceRecognition = requiresOnDeviceRecognition
    }
  }

  var baseRequest: SFSpeechAudioBufferRecognitionRequest {
    base
  }
}

protocol WakeWordSpeechRecognitionTask: AnyObject {
  func cancel()
}

extension SFSpeechRecognitionTask: WakeWordSpeechRecognitionTask {}

protocol WakeWordSpeechRecognizer: AnyObject {
  var isAvailable: Bool { get }
  var supportsOnDeviceRecognition: Bool { get }
  var delegate: SFSpeechRecognizerDelegate? { get set }
  func recognitionTask(
    with request: any WakeWordSpeechRecognitionRequest,
    resultHandler: @escaping (WakeWordRecognitionUpdate?, Error?) -> Void
  ) -> (any WakeWordSpeechRecognitionTask)?
}

private final class SFSpeechRecognizerAdapter: WakeWordSpeechRecognizer {
  private enum AdapterError: LocalizedError {
    case unsupportedRecognitionRequest(String)

    var errorDescription: String? {
      switch self {
      case .unsupportedRecognitionRequest(let requestType):
        return "Unsupported recognition request type: \(requestType)"
      }
    }
  }

  private let base: SFSpeechRecognizer

  init(base: SFSpeechRecognizer) {
    self.base = base
  }

  var isAvailable: Bool { base.isAvailable }
  var supportsOnDeviceRecognition: Bool { base.supportsOnDeviceRecognition }

  var delegate: SFSpeechRecognizerDelegate? {
    get { base.delegate }
    set { base.delegate = newValue }
  }

  func recognitionTask(
    with request: any WakeWordSpeechRecognitionRequest,
    resultHandler: @escaping (WakeWordRecognitionUpdate?, Error?) -> Void
  ) -> (any WakeWordSpeechRecognitionTask)? {
    guard let requestAdapter = request as? SFSpeechAudioBufferRecognitionRequestAdapter else {
      resultHandler(
        nil,
        AdapterError.unsupportedRecognitionRequest(String(describing: type(of: request)))
      )
      return nil
    }

    return base.recognitionTask(with: requestAdapter.baseRequest) { result, error in
      let update = result.map {
        WakeWordRecognitionUpdate(
          transcript: $0.bestTranscription.formattedString,
          isFinal: $0.isFinal
        )
      }
      resultHandler(update, error)
    }
  }
}

@MainActor
final class SFSpeechWakeWordEngine: NSObject, WakeWordEngine {
  typealias RecognizerFactory = @MainActor (Locale) -> (any WakeWordSpeechRecognizer)?
  typealias RecognitionTaskFactory = @MainActor (
    any WakeWordSpeechRecognizer,
    any WakeWordSpeechRecognitionRequest,
    @escaping (WakeWordRecognitionUpdate?, Error?) -> Void
  ) -> (any WakeWordSpeechRecognitionTask)?
  typealias RecognitionRequestFactory = @MainActor () -> any WakeWordSpeechRecognitionRequest

  var onWakeDetected: ((WakeWordDetectionEvent) -> Void)?
  var onSleepDetected: ((WakeWordDetectionEvent) -> Void)?
  var onError: ((Error) -> Void)?
  var onStatusChanged: ((WakeWordStatusSnapshot) -> Void)?

  private(set) var isListening: Bool = false
  let engineKind: WakeWordEngineKind = .sfspeechKeyword

  private let wakePhrase: String
  private let sleepPhrase: String?
  private let normalizedWakePhrase: String
  private let normalizedSleepPhrase: String?
  private let localeIdentifier: String
  private let requiresOnDeviceRecognition: Bool
  private let detectionCooldownMs: Int64
  private let nowMsProvider: () -> Int64
  private let authorizationStatusProvider: () -> SFSpeechRecognizerAuthorizationStatus
  private let recognizerFactory: RecognizerFactory
  private let recognitionRequestFactory: RecognitionRequestFactory
  private let recognitionTaskFactory: RecognitionTaskFactory
  private let maxConsecutiveRecognitionErrors: Int = 5
  private static let logger = Logger(subsystem: "PortWorld", category: "WakeWordEngine")
  private static let noSpeechDetectedErrorDomain = "kAFAssistantErrorDomain"
  private static let noSpeechDetectedErrorCode = 1_110
  private static let speechErrorDomain = "SFSpeechErrorDomain"
  private static let noSpeechDetectedMessageFragment = "no speech detected"
  private static let transientLogThrottleMs: Int64 = 5_000
  private static let noRecognitionUpdateRecoveryDelayMs: Int64 = 1_500
  private var recognizer: (any WakeWordSpeechRecognizer)?
  private var recognitionRequest: (any WakeWordSpeechRecognitionRequest)?
  private var recognitionTask: (any WakeWordSpeechRecognitionTask)?
  private var authorization: WakeWordAuthorizationState = .notDetermined
  private var runtimeStatus: WakeWordRuntimeStatus = .idle
  private var lastDetectionTimestampMs: Int64 = 0
  private var consecutiveRecognitionErrorCount: Int = 0
  private var lastTransientRecognitionErrorLogMs: Int64 = 0
  private var listeningStartedAtMs: Int64 = 0
  private var attemptedColdStartRecognizerRecovery = false
  private var attemptedNoRecognitionUpdateRecovery = false
  private var receivedRecognitionUpdateSinceStart = false
  private var firstAudioAppendTimestampMs: Int64 = 0
  private var listeningSessionGeneration: Int = 0
  private var recognitionTaskGeneration: Int = 0
  private var noRecognitionUpdateRecoveryTask: Task<Void, Never>?

  init(
    wakePhrase: String,
    sleepPhrase: String? = nil,
    localeIdentifier: String,
    requiresOnDeviceRecognition: Bool,
    detectionCooldownMs: Int64 = 1_500,
    nowMsProvider: @escaping () -> Int64 = { Clocks.nowMs() },
    authorizationStatusProvider: @escaping () -> SFSpeechRecognizerAuthorizationStatus = {
      SFSpeechRecognizer.authorizationStatus()
    },
    recognizerFactory: @escaping RecognizerFactory = { locale in
      guard let recognizer = SFSpeechRecognizer(locale: locale) else {
        return nil
      }
      return SFSpeechRecognizerAdapter(base: recognizer)
    },
    recognitionRequestFactory: @escaping RecognitionRequestFactory = {
      SFSpeechAudioBufferRecognitionRequestAdapter()
    },
    recognitionTaskFactory: @escaping RecognitionTaskFactory = { recognizer, request, handler in
      recognizer.recognitionTask(with: request, resultHandler: handler)
    }
  ) {
    self.wakePhrase = wakePhrase
    self.sleepPhrase = sleepPhrase
    self.normalizedWakePhrase = Self.normalizePhrase(wakePhrase)
    self.normalizedSleepPhrase = sleepPhrase
      .map(Self.normalizePhrase)
      .flatMap { $0.isEmpty ? nil : $0 }
    self.localeIdentifier = localeIdentifier
    self.requiresOnDeviceRecognition = requiresOnDeviceRecognition
    self.detectionCooldownMs = max(250, detectionCooldownMs)
    self.nowMsProvider = nowMsProvider
    self.authorizationStatusProvider = authorizationStatusProvider
    self.recognizerFactory = recognizerFactory
    self.recognitionRequestFactory = recognitionRequestFactory
    self.recognitionTaskFactory = recognitionTaskFactory
    super.init()
    configureRecognizerIfNeeded()
  }

  func currentAuthorizationStatus() -> WakeWordAuthorizationState {
    let status = Self.mapAuthorization(authorizationStatusProvider())
    authorization = status
    return status
  }

  func requestAuthorizationIfNeeded() async -> WakeWordAuthorizationState {
    let current = currentAuthorizationStatus()
    if current != .notDetermined {
      let runtime: WakeWordRuntimeStatus = current == .authorized ? .idle : .fallbackManual
      authorization = current
      runtimeStatus = runtime
      publishStatus(authorization: current, runtime: runtime)
      return current
    }

    runtimeStatus = .requestingAuthorization
    publishStatus(authorization: .notDetermined, runtime: .requestingAuthorization)

    let status = await withCheckedContinuation { continuation in
      SFSpeechRecognizer.requestAuthorization { auth in
        continuation.resume(returning: Self.mapAuthorization(auth))
      }
    }

    authorization = status
    runtimeStatus = status == .authorized ? .idle : .fallbackManual
    publishStatus(authorization: status, runtime: status == .authorized ? .idle : .fallbackManual)
    return status
  }

  func startListening() {
    authorization = Self.mapAuthorization(authorizationStatusProvider())
    guard authorization == .authorized else {
      runtimeStatus = .fallbackManual
      publishStatus(authorization: authorization, runtime: .fallbackManual)
      return
    }

    rebuildRecognizer()
    guard let recognizer, recognizer.isAvailable else {
      runtimeStatus = .fallbackManual
      publishStatus(authorization: .unavailable, runtime: .fallbackManual, detail: "Recognizer unavailable")
      return
    }

    if requiresOnDeviceRecognition, recognizer.supportsOnDeviceRecognition == false {
      runtimeStatus = .fallbackManual
      publishStatus(authorization: .unavailable, runtime: .fallbackManual, detail: "On-device recognition unsupported")
      onError?(WakeWordEngineError.onDeviceRecognitionUnavailable)
      return
    }

    isListening = true
    listeningSessionGeneration += 1
    listeningStartedAtMs = nowMsProvider()
    attemptedColdStartRecognizerRecovery = false
    attemptedNoRecognitionUpdateRecovery = false
    receivedRecognitionUpdateSinceStart = false
    firstAudioAppendTimestampMs = 0
    lastDetectionTimestampMs = 0
    consecutiveRecognitionErrorCount = 0
    cancelNoRecognitionUpdateRecoveryTask()
    guard startRecognitionTaskLocked() else {
      failRecognitionStartLocked(detail: "Failed to start speech recognition task")
      onError?(WakeWordEngineError.recognitionTaskCreationFailed)
      return
    }
    runtimeStatus = .listening
    publishStatus(authorization: authorization, runtime: .listening)
  }

  func stopListening() {
    isListening = false
    listeningStartedAtMs = 0
    attemptedColdStartRecognizerRecovery = false
    attemptedNoRecognitionUpdateRecovery = false
    receivedRecognitionUpdateSinceStart = false
    firstAudioAppendTimestampMs = 0
    consecutiveRecognitionErrorCount = 0
    cancelNoRecognitionUpdateRecoveryTask()
    stopRecognitionTaskLocked()
    runtimeStatus = .idle
    publishStatus(authorization: authorization, runtime: .idle)
  }

  func processPCMFrame(_ frame: WakeWordPCMFrame) {
    guard isListening else { return }
    guard let request = recognitionRequest else { return }
    guard let buffer = Self.makeRecognitionBuffer(from: frame) else { return }
    if firstAudioAppendTimestampMs == 0 {
      firstAudioAppendTimestampMs = nowMsProvider()
      scheduleNoRecognitionUpdateRecoveryIfNeeded(for: listeningSessionGeneration)
    }
    request.append(buffer)
  }

  private func configureRecognizerIfNeeded() {
    if recognizer != nil { return }
    recognizer = recognizerFactory(Locale(identifier: localeIdentifier))
    recognizer?.delegate = self
  }

  private func rebuildRecognizer() {
    stopRecognitionTaskLocked()
    recognizer?.delegate = nil
    recognizer = recognizerFactory(Locale(identifier: localeIdentifier))
    recognizer?.delegate = self
  }

  @discardableResult
  private func startRecognitionTaskLocked() -> Bool {
    stopRecognitionTaskLocked()

    let request = recognitionRequestFactory()
    request.configure(
      shouldReportPartialResults: true,
      requiresOnDeviceRecognition: requiresOnDeviceRecognition
    )
    recognitionRequest = request

    recognitionTask = recognizer.flatMap { recognizer in
      let taskGeneration = recognitionTaskGeneration + 1
      recognitionTaskGeneration = taskGeneration
      return recognitionTaskFactory(recognizer, request) { [weak self] update, error in
        Task { @MainActor [weak self] in
          guard let self else { return }
          self.handleRecognitionUpdateLocked(
            update: update,
            error: error,
            taskGeneration: taskGeneration
          )
        }
      }
    }

    guard recognitionTask != nil else {
      recognitionRequest?.endAudio()
      recognitionRequest = nil
      return false
    }

    scheduleNoRecognitionUpdateRecoveryIfNeeded(for: listeningSessionGeneration)
    return true
  }

  private func stopRecognitionTaskLocked() {
    recognitionRequest?.endAudio()
    recognitionTask?.cancel()
    recognitionTask = nil
    recognitionRequest = nil
  }

  private func handleRecognitionUpdateLocked(
    update: WakeWordRecognitionUpdate?,
    error: Error?,
    taskGeneration: Int
  ) {
    guard taskGeneration == recognitionTaskGeneration else {
      return
    }

    if let update {
      consecutiveRecognitionErrorCount = 0
      if receivedRecognitionUpdateSinceStart == false {
        receivedRecognitionUpdateSinceStart = true
        cancelNoRecognitionUpdateRecoveryTask()
      }
      let transcript = Self.normalizePhrase(update.transcript)
      if !transcript.isEmpty {
        let now = nowMsProvider()
        if now - lastDetectionTimestampMs >= detectionCooldownMs {
          let detectedSleep = normalizedSleepPhrase.map { transcript.contains($0) } ?? false
          let detectedWake = transcript.contains(normalizedWakePhrase)

          if detectedSleep, let sleepPhrase {
            lastDetectionTimestampMs = now
            attemptedColdStartRecognizerRecovery = true
            onSleepDetected?(
              WakeWordDetectionEvent(
                wakePhrase: sleepPhrase,
                timestampMs: now,
                engine: engineKind.rawValue,
                confidence: nil
              )
            )
          } else if detectedWake {
            lastDetectionTimestampMs = now
            attemptedColdStartRecognizerRecovery = true
            onWakeDetected?(
              WakeWordDetectionEvent(
                wakePhrase: wakePhrase,
                timestampMs: now,
                engine: engineKind.rawValue,
                confidence: nil
              )
            )
          }
        }
      }

      if update.isFinal, isListening, startRecognitionTaskLocked() == false {
        failRecognitionStartLocked(detail: "Failed to restart speech recognition task after final result")
        onError?(WakeWordEngineError.recognitionTaskCreationFailed)
        return
      }
    }

    if let error {
      if let transientError = Self.transientRecognitionError(from: error) {
        debugLogTransientRecognitionErrorIfNeeded(transientError)
        if isListening {
          let recovered: Bool
          if shouldAttemptColdStartRecognizerRecovery() {
            recovered = performColdStartRecognizerRecoveryLocked()
          } else {
            recovered = startRecognitionTaskLocked()
          }

          guard recovered else {
            failRecognitionStartLocked(detail: "Failed to restart speech recognition task after transient recognition error")
            onError?(WakeWordEngineError.recognitionTaskCreationFailed)
            return
          }
          runtimeStatus = .listening
          publishStatus(authorization: authorization, runtime: .listening)
        }
        return
      }

      onError?(error)
      if isListening {
        consecutiveRecognitionErrorCount += 1
        runtimeStatus = .failed
        if consecutiveRecognitionErrorCount >= maxConsecutiveRecognitionErrors {
          isListening = false
          stopRecognitionTaskLocked()
          publishStatus(
            authorization: authorization,
            runtime: .failed,
            detail: "Recognition failed \(consecutiveRecognitionErrorCount) times consecutively: \(error.localizedDescription)"
          )
          return
        }

        publishStatus(authorization: authorization, runtime: .failed, detail: error.localizedDescription)
        guard startRecognitionTaskLocked() else {
          failRecognitionStartLocked(detail: "Failed to restart speech recognition task after recognition error")
          onError?(WakeWordEngineError.recognitionTaskCreationFailed)
          return
        }
        runtimeStatus = .listening
        publishStatus(authorization: authorization, runtime: .listening)
      }
    }
  }

  private static func transientRecognitionError(from error: Error) -> NSError? {
    let nsError = error as NSError
    if isTransientRecognitionNSError(nsError) {
      return nsError
    }
    if let underlying = nsError.userInfo[NSUnderlyingErrorKey] as? NSError,
       isTransientRecognitionNSError(underlying)
    {
      return underlying
    }
    return nil
  }

  private static func isTransientRecognitionNSError(_ error: NSError) -> Bool {
    if error.domain == noSpeechDetectedErrorDomain, error.code == noSpeechDetectedErrorCode {
      return true
    }

    guard error.domain == noSpeechDetectedErrorDomain || error.domain == speechErrorDomain else {
      return false
    }
    let description = error.localizedDescription.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    return description.contains(noSpeechDetectedMessageFragment)
  }

  private func shouldAttemptColdStartRecognizerRecovery() -> Bool {
    guard attemptedColdStartRecognizerRecovery == false else { return false }
    guard lastDetectionTimestampMs == 0 else { return false }
    guard listeningStartedAtMs > 0 else { return false }
    return nowMsProvider() - listeningStartedAtMs <= 5_000
  }

  private func performColdStartRecognizerRecoveryLocked() -> Bool {
    attemptedColdStartRecognizerRecovery = true
    rebuildRecognizer()
    return startRecognitionTaskLocked()
  }

  private func scheduleNoRecognitionUpdateRecoveryIfNeeded(for generation: Int) {
    guard isListening else { return }
    guard receivedRecognitionUpdateSinceStart == false else { return }
    guard firstAudioAppendTimestampMs > 0 else { return }

    noRecognitionUpdateRecoveryTask?.cancel()
    noRecognitionUpdateRecoveryTask = Task { @MainActor [weak self] in
      do {
        try await Task.sleep(for: .milliseconds(Self.noRecognitionUpdateRecoveryDelayMs))
      } catch {
        return
      }

      guard let self else { return }
      guard self.isListening else { return }
      guard self.listeningSessionGeneration == generation else { return }
      guard self.receivedRecognitionUpdateSinceStart == false else { return }
      guard self.firstAudioAppendTimestampMs > 0 else { return }
      guard self.attemptedNoRecognitionUpdateRecovery == false else { return }

      self.attemptedNoRecognitionUpdateRecovery = true
      self.firstAudioAppendTimestampMs = 0
      self.debugLog(
        "No recognition update received after first audio; rebuilding recognizer generation=\(generation)"
      )
      self.rebuildRecognizer()
      guard self.startRecognitionTaskLocked() else {
        self.failRecognitionStartLocked(detail: "Failed to recover speech recognition task after missing initial updates")
        self.onError?(WakeWordEngineError.recognitionTaskCreationFailed)
        return
      }
      self.runtimeStatus = .listening
      self.publishStatus(authorization: self.authorization, runtime: .listening)
    }
  }

  private func cancelNoRecognitionUpdateRecoveryTask() {
    noRecognitionUpdateRecoveryTask?.cancel()
    noRecognitionUpdateRecoveryTask = nil
  }

  private func debugLogTransientRecognitionErrorIfNeeded(_ error: NSError) {
#if DEBUG
    let now = nowMsProvider()
    guard now - lastTransientRecognitionErrorLogMs >= Self.transientLogThrottleMs else { return }
    lastTransientRecognitionErrorLogMs = now
    Self.logger.debug(
      "[SFSpeechWakeWordEngine] Suppressed transient recognition error domain=\(error.domain, privacy: .public) code=\(error.code, privacy: .public) description=\(error.localizedDescription, privacy: .public)"
    )
#endif
  }

  private func failRecognitionStartLocked(detail: String) {
    isListening = false
    runtimeStatus = .failed
    cancelNoRecognitionUpdateRecoveryTask()
    stopRecognitionTaskLocked()
    publishStatus(authorization: authorization, runtime: .failed, detail: detail)
  }

  private func debugLog(_ message: String) {
#if DEBUG
    Self.logger.debug("[SFSpeechWakeWordEngine] \(message, privacy: .public)")
#endif
  }

  private func publishStatus(
    authorization: WakeWordAuthorizationState,
    runtime: WakeWordRuntimeStatus,
    detail: String? = nil
  ) {
    onStatusChanged?(
      WakeWordStatusSnapshot(
        engine: engineKind,
        authorization: authorization,
        runtime: runtime,
        detail: detail
      )
    )
  }

  private static func mapAuthorization(_ status: SFSpeechRecognizerAuthorizationStatus) -> WakeWordAuthorizationState {
    switch status {
    case .authorized:
      return .authorized
    case .denied:
      return .denied
    case .restricted:
      return .restricted
    case .notDetermined:
      return .notDetermined
    @unknown default:
      return .unavailable
    }
  }

  private static func normalizePhrase(_ text: String) -> String {
    let normalized = text
      .folding(options: .diacriticInsensitive, locale: .current)
      .lowercased()
      .components(separatedBy: CharacterSet.punctuationCharacters.union(.symbols))
      .joined(separator: " ")

    return normalized
      .components(separatedBy: .whitespacesAndNewlines)
      .filter { !$0.isEmpty }
      .joined(separator: " ")
  }

  private static func makeRecognitionBuffer(from frame: WakeWordPCMFrame) -> AVAudioPCMBuffer? {
    let frameCount = frame.samples.count
    guard frameCount > 0 else { return nil }

    guard let format = AVAudioFormat(
      commonFormat: .pcmFormatInt16,
      sampleRate: frame.sampleRateHz,
      channels: 1,
      interleaved: false
    ) else {
      return nil
    }

    guard let buffer = AVAudioPCMBuffer(
      pcmFormat: format,
      frameCapacity: AVAudioFrameCount(frameCount)
    ) else {
      return nil
    }

    buffer.frameLength = AVAudioFrameCount(frameCount)
    guard let channel = buffer.int16ChannelData?.pointee else {
      return nil
    }
    frame.samples.withUnsafeBytes { rawBytes in
      guard let source = rawBytes.baseAddress else { return }
      memcpy(channel, source, frameCount * MemoryLayout<Int16>.size)
    }
    return buffer
  }
}

extension SFSpeechWakeWordEngine: SFSpeechRecognizerDelegate {
  func speechRecognizer(_ speechRecognizer: SFSpeechRecognizer, availabilityDidChange available: Bool) {
    Task { @MainActor in
      if !available {
        self.runtimeStatus = .fallbackManual
        self.publishStatus(authorization: .unavailable, runtime: .fallbackManual, detail: "Recognizer unavailable")
      } else if self.isListening {
        self.runtimeStatus = .listening
        self.publishStatus(authorization: self.authorization, runtime: .listening)
      }
    }
  }
}

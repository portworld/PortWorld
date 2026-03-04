import AVFAudio
import Foundation
import Speech

struct WakeWordDetectionEvent {
  let wakePhrase: String
  let timestampMs: Int64
  let engine: String
  let confidence: Float?
}

enum WakeWordEngineKind: String, Codable {
  case manual
  case sfspeechKeyword = "sfspeech_keyword"
}

enum WakeWordAuthorizationState: String, Codable {
  case notRequired = "not_required"
  case notDetermined = "not_determined"
  case authorized
  case denied
  case restricted
  case unavailable
}

enum WakeWordRuntimeStatus: String, Codable {
  case idle
  case requestingAuthorization = "requesting_authorization"
  case listening
  case fallbackManual = "fallback_manual"
  case failed
}

struct WakeWordStatusSnapshot: Codable {
  let engine: WakeWordEngineKind
  let authorization: WakeWordAuthorizationState
  let runtime: WakeWordRuntimeStatus
  let detail: String?
}

struct WakeWordPCMFrame {
  let samples: [Int16]
  let sampleRateHz: Double
  let channelCount: Int
  let timestampMs: Int64
}

@MainActor
protocol WakeWordEngine: AnyObject {
  var onWakeDetected: ((WakeWordDetectionEvent) -> Void)? { get set }
  var onSleepDetected: ((WakeWordDetectionEvent) -> Void)? { get set }
  var onError: ((Error) -> Void)? { get set }
  var onStatusChanged: ((WakeWordStatusSnapshot) -> Void)? { get set }
  var isListening: Bool { get }
  var engineKind: WakeWordEngineKind { get }

  func currentAuthorizationStatus() -> WakeWordAuthorizationState
  func requestAuthorizationIfNeeded() async -> WakeWordAuthorizationState
  func startListening()
  func stopListening()
  func processPCMFrame(_ frame: WakeWordPCMFrame)
}

extension WakeWordEngine {
  func processPCMFrame(
    _ samples: [Int16],
    timestampMs: Int64,
    sampleRateHz: Double = 8_000,
    channelCount: Int = 1
  ) {
    processPCMFrame(
      WakeWordPCMFrame(
        samples: samples,
        sampleRateHz: sampleRateHz,
        channelCount: channelCount,
        timestampMs: timestampMs
      )
    )
  }
}

enum WakeWordEngineError: LocalizedError {
  case notListening
  case recognizerUnavailable
  case onDeviceRecognitionUnavailable
  case recognitionTaskCreationFailed

  var errorDescription: String? {
    switch self {
    case .notListening:
      return "Wake engine is not listening"
    case .recognizerUnavailable:
      return "Speech recognizer is unavailable"
    case .onDeviceRecognitionUnavailable:
      return "On-device speech recognition is unavailable for this locale/device"
    case .recognitionTaskCreationFailed:
      return "Unable to start speech recognition task"
    }
  }
}

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
    guard
      let requestAdapter = request as? SFSpeechAudioBufferRecognitionRequestAdapter
    else {
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
final class ManualWakeWordEngine: WakeWordEngine {
  var onWakeDetected: ((WakeWordDetectionEvent) -> Void)?
  var onSleepDetected: ((WakeWordDetectionEvent) -> Void)?
  var onError: ((Error) -> Void)?
  var onStatusChanged: ((WakeWordStatusSnapshot) -> Void)?

  private(set) var isListening: Bool = false
  let engineKind: WakeWordEngineKind = .manual
  private let defaultPhrase: String

  init(defaultPhrase: String = "hey mario") {
    self.defaultPhrase = defaultPhrase
  }

  func currentAuthorizationStatus() -> WakeWordAuthorizationState {
    .notRequired
  }

  func requestAuthorizationIfNeeded() async -> WakeWordAuthorizationState {
    .notRequired
  }

  func startListening() {
    isListening = true
    publishStatus(runtime: .listening)
  }

  func stopListening() {
    isListening = false
    publishStatus(runtime: .idle)
  }

  func processPCMFrame(_ frame: WakeWordPCMFrame) {
    _ = frame
  }

  func triggerManualWake(
    wakePhrase: String? = nil,
    timestampMs: Int64,
    confidence: Float = 1.0
  ) {
    guard isListening else {
      onError?(WakeWordEngineError.notListening)
      return
    }

    onWakeDetected?(
      WakeWordDetectionEvent(
        wakePhrase: wakePhrase ?? defaultPhrase,
        timestampMs: timestampMs,
        engine: engineKind.rawValue,
        confidence: confidence
      )
    )
  }

  private func publishStatus(runtime: WakeWordRuntimeStatus, detail: String? = nil) {
    onStatusChanged?(
      WakeWordStatusSnapshot(
        engine: engineKind,
        authorization: .notRequired,
        runtime: runtime,
        detail: detail
      )
    )
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
  private var recognizer: (any WakeWordSpeechRecognizer)?
  private var recognitionRequest: (any WakeWordSpeechRecognitionRequest)?
  private var recognitionTask: (any WakeWordSpeechRecognitionTask)?
  private var authorization: WakeWordAuthorizationState = .notDetermined
  private var runtimeStatus: WakeWordRuntimeStatus = .idle
  private var lastDetectionTimestampMs: Int64 = 0
  private var consecutiveRecognitionErrorCount: Int = 0

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

    configureRecognizerIfNeeded()
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
    lastDetectionTimestampMs = 0
    consecutiveRecognitionErrorCount = 0
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
    consecutiveRecognitionErrorCount = 0
    stopRecognitionTaskLocked()
    runtimeStatus = .idle
    publishStatus(authorization: authorization, runtime: .idle)
  }

  func processPCMFrame(_ frame: WakeWordPCMFrame) {
    guard isListening else { return }
    guard let request = recognitionRequest else { return }
    guard let buffer = Self.makeRecognitionBuffer(from: frame) else { return }
    request.append(buffer)
  }

  private func configureRecognizerIfNeeded() {
    if recognizer != nil { return }
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
      recognitionTaskFactory(recognizer, request) { [weak self] update, error in
        Task { @MainActor [weak self] in
          guard let self else { return }
          self.handleRecognitionUpdateLocked(update: update, error: error)
        }
      }
    }

    guard recognitionTask != nil else {
      recognitionRequest?.endAudio()
      recognitionRequest = nil
      return false
    }

    return true
  }

  private func stopRecognitionTaskLocked() {
    recognitionRequest?.endAudio()
    recognitionTask?.cancel()
    recognitionTask = nil
    recognitionRequest = nil
  }

  private func handleRecognitionUpdateLocked(update: WakeWordRecognitionUpdate?, error: Error?) {
    if let update {
      consecutiveRecognitionErrorCount = 0
      let transcript = Self.normalizePhrase(update.transcript)
      if !transcript.isEmpty {
        let now = nowMsProvider()
        if now - lastDetectionTimestampMs >= detectionCooldownMs {
          let detectedSleep = normalizedSleepPhrase.map { transcript.contains($0) } ?? false
          let detectedWake = transcript.contains(normalizedWakePhrase)

          if detectedSleep, let sleepPhrase {
            lastDetectionTimestampMs = now
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

  private func failRecognitionStartLocked(detail: String) {
    isListening = false
    runtimeStatus = .failed
    stopRecognitionTaskLocked()
    publishStatus(authorization: authorization, runtime: .failed, detail: detail)
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

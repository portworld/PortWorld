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

  var errorDescription: String? {
    switch self {
    case .notListening:
      return "Wake engine is not listening"
    case .recognizerUnavailable:
      return "Speech recognizer is unavailable"
    case .onDeviceRecognitionUnavailable:
      return "On-device speech recognition is unavailable for this locale/device"
    }
  }
}

@MainActor
final class ManualWakeWordEngine: WakeWordEngine {
  var onWakeDetected: ((WakeWordDetectionEvent) -> Void)?
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
  var onWakeDetected: ((WakeWordDetectionEvent) -> Void)?
  var onError: ((Error) -> Void)?
  var onStatusChanged: ((WakeWordStatusSnapshot) -> Void)?

  private(set) var isListening: Bool = false
  let engineKind: WakeWordEngineKind = .sfspeechKeyword

  private let wakePhrase: String
  private let normalizedWakePhrase: String
  private let localeIdentifier: String
  private let requiresOnDeviceRecognition: Bool
  private let detectionCooldownMs: Int64
  private let maxConsecutiveRecognitionErrors: Int = 5
  private var recognizer: SFSpeechRecognizer?
  private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
  private var recognitionTask: SFSpeechRecognitionTask?
  private var authorization: WakeWordAuthorizationState = .notDetermined
  private var runtimeStatus: WakeWordRuntimeStatus = .idle
  private var lastDetectionTimestampMs: Int64 = 0
  private var consecutiveRecognitionErrorCount: Int = 0

  init(
    wakePhrase: String,
    localeIdentifier: String,
    requiresOnDeviceRecognition: Bool,
    detectionCooldownMs: Int64 = 1_500
  ) {
    self.wakePhrase = wakePhrase
    self.normalizedWakePhrase = Self.normalizePhrase(wakePhrase)
    self.localeIdentifier = localeIdentifier
    self.requiresOnDeviceRecognition = requiresOnDeviceRecognition
    self.detectionCooldownMs = max(250, detectionCooldownMs)
    super.init()
    configureRecognizerIfNeeded()
  }

  func currentAuthorizationStatus() -> WakeWordAuthorizationState {
    let status = Self.mapAuthorization(SFSpeechRecognizer.authorizationStatus())
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
    authorization = Self.mapAuthorization(SFSpeechRecognizer.authorizationStatus())
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
    startRecognitionTaskLocked()
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
    recognizer = SFSpeechRecognizer(locale: Locale(identifier: localeIdentifier))
    recognizer?.delegate = self
  }

  private func startRecognitionTaskLocked() {
    stopRecognitionTaskLocked()

    let request = SFSpeechAudioBufferRecognitionRequest()
    request.shouldReportPartialResults = true
    if #available(iOS 13.0, *) {
      request.requiresOnDeviceRecognition = requiresOnDeviceRecognition
    }
    recognitionRequest = request

    recognitionTask = recognizer?.recognitionTask(with: request) { [weak self] result, error in
      guard let self else { return }
      Task { @MainActor in
        self.handleRecognitionUpdateLocked(result: result, error: error)
      }
    }
  }

  private func stopRecognitionTaskLocked() {
    recognitionRequest?.endAudio()
    recognitionTask?.cancel()
    recognitionTask = nil
    recognitionRequest = nil
  }

  private func handleRecognitionUpdateLocked(result: SFSpeechRecognitionResult?, error: Error?) {
    if let result {
      consecutiveRecognitionErrorCount = 0
      let transcript = Self.normalizePhrase(result.bestTranscription.formattedString)
      if !transcript.isEmpty, transcript.contains(normalizedWakePhrase) {
        let now = Clocks.nowMs()
        if now - lastDetectionTimestampMs >= detectionCooldownMs {
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

      if result.isFinal, isListening {
        startRecognitionTaskLocked()
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
        startRecognitionTaskLocked()
        runtimeStatus = .listening
        publishStatus(authorization: authorization, runtime: .listening)
      }
    }
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
    frame.samples.withUnsafeBufferPointer { source in
      channel.initialize(from: source.baseAddress!, count: frameCount)
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

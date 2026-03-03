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
    timestampMs: Int64 = Clocks.nowMs(),
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
  private let queue = DispatchQueue(label: "Runtime.SFSpeechWakeWordEngine")

  private var recognizer: SFSpeechRecognizer?
  private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
  private var recognitionTask: SFSpeechRecognitionTask?
  private var authorization: WakeWordAuthorizationState = .notDetermined
  private var runtimeStatus: WakeWordRuntimeStatus = .idle
  private var lastDetectionTimestampMs: Int64 = 0

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
    queue.sync {
      authorization = Self.mapAuthorization(SFSpeechRecognizer.authorizationStatus())
      return authorization
    }
  }

  func requestAuthorizationIfNeeded() async -> WakeWordAuthorizationState {
    let current = currentAuthorizationStatus()
    if current != .notDetermined {
      publishStatus(authorization: current, runtime: runtimeStatus)
      return current
    }

    queue.sync {
      runtimeStatus = .requestingAuthorization
    }
    publishStatus(authorization: .notDetermined, runtime: .requestingAuthorization)

    let status = await withCheckedContinuation { continuation in
      SFSpeechRecognizer.requestAuthorization { auth in
        continuation.resume(returning: Self.mapAuthorization(auth))
      }
    }

    queue.sync {
      authorization = status
      runtimeStatus = status == .authorized ? .idle : .fallbackManual
    }
    publishStatus(authorization: status, runtime: status == .authorized ? .idle : .fallbackManual)
    return status
  }

  func startListening() {
    queue.async {
      self.authorization = Self.mapAuthorization(SFSpeechRecognizer.authorizationStatus())
      guard self.authorization == .authorized else {
        self.runtimeStatus = .fallbackManual
        self.publishStatus(authorization: self.authorization, runtime: .fallbackManual)
        return
      }

      self.configureRecognizerIfNeeded()
      guard let recognizer = self.recognizer, recognizer.isAvailable else {
        self.runtimeStatus = .fallbackManual
        self.publishStatus(authorization: .unavailable, runtime: .fallbackManual, detail: "Recognizer unavailable")
        return
      }

      if self.requiresOnDeviceRecognition, recognizer.supportsOnDeviceRecognition == false {
        self.runtimeStatus = .fallbackManual
        self.publishStatus(authorization: .unavailable, runtime: .fallbackManual, detail: "On-device recognition unsupported")
        self.onError?(WakeWordEngineError.onDeviceRecognitionUnavailable)
        return
      }

      self.isListening = true
      self.lastDetectionTimestampMs = 0
      self.startRecognitionTaskLocked()
      self.runtimeStatus = .listening
      self.publishStatus(authorization: self.authorization, runtime: .listening)
    }
  }

  func stopListening() {
    queue.async {
      self.isListening = false
      self.stopRecognitionTaskLocked()
      self.runtimeStatus = .idle
      self.publishStatus(authorization: self.authorization, runtime: .idle)
    }
  }

  func processPCMFrame(_ frame: WakeWordPCMFrame) {
    queue.async {
      guard self.isListening else { return }
      guard let request = self.recognitionRequest else { return }
      guard let buffer = Self.makeRecognitionBuffer(from: frame) else { return }
      request.append(buffer)
    }
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
      self.queue.async {
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
        runtimeStatus = .failed
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
    text
      .folding(options: .diacriticInsensitive, locale: .current)
      .lowercased()
      .trimmingCharacters(in: .whitespacesAndNewlines)
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
    queue.async {
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

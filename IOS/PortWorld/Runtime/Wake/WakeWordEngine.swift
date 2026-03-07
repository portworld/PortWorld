// Shared wake-word types and engine protocol for the assistant runtime.
import Foundation

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

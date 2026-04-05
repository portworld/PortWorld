// Manual wake-word engine used when speech recognition is unavailable or disabled.
import Foundation

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

// Shared facade that configures and bridges the active wake-word engine.
import Foundation

@MainActor
final class WakePhraseDetector {
  struct StatusSnapshot {
    let engine: String
    let runtime: String
    let authorization: String
  }

  var onWakeDetected: ((WakeWordDetectionEvent) -> Void)?
  var onSleepDetected: ((WakeWordDetectionEvent) -> Void)?
  var onError: ((String) -> Void)?
  var onStatusChanged: ((StatusSnapshot) -> Void)?

  private let engine: WakeWordEngine
  private(set) var lastStatus = StatusSnapshot(
    engine: WakeWordEngineKind.manual.rawValue,
    runtime: WakeWordRuntimeStatus.idle.rawValue,
    authorization: WakeWordAuthorizationState.notRequired.rawValue
  )
  var isListening: Bool {
    engine.isListening
  }

  init(config: AssistantRuntimeConfig) {
    if config.wakeWordMode == .onDevicePreferred {
      self.engine = SFSpeechWakeWordEngine(
        wakePhrase: config.wakePhrase,
        sleepPhrase: config.sleepPhrase,
        localeIdentifier: config.wakeWordLocaleIdentifier,
        requiresOnDeviceRecognition: config.wakeWordRequiresOnDeviceRecognition,
        detectionCooldownMs: config.wakeWordDetectionCooldownMs
      )
    } else {
      self.engine = ManualWakeWordEngine(defaultPhrase: config.wakePhrase)
    }

    engine.onWakeDetected = { [weak self] event in
      self?.onWakeDetected?(event)
    }
    engine.onSleepDetected = { [weak self] event in
      self?.onSleepDetected?(event)
    }
    engine.onError = { [weak self] error in
      self?.onError?(error.localizedDescription)
    }
    engine.onStatusChanged = { [weak self] snapshot in
      let status = StatusSnapshot(
        engine: snapshot.engine.rawValue,
        runtime: snapshot.runtime.rawValue,
        authorization: snapshot.authorization.rawValue
      )
      self?.lastStatus = status
      self?.onStatusChanged?(status)
    }
  }

  func requestAuthorizationIfNeeded() async -> WakeWordAuthorizationState {
    await engine.requestAuthorizationIfNeeded()
  }

  func startArmedListening() {
    engine.startListening()
  }

  func stop() {
    engine.stopListening()
  }

  func processPCMFrame(_ frame: WakeWordPCMFrame) {
    engine.processPCMFrame(frame)
  }

  func statusSnapshot() -> StatusSnapshot {
    lastStatus
  }
}

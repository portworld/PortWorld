import Combine
import MWDATCamera
import SwiftUI

@MainActor
final class RuntimeCoordinator {
  private let store: SessionStateStore
  private let deviceSessionCoordinator: DeviceSessionCoordinator
  private let audioCollectionManager: AudioCollectionManager
  private let runtimeOrchestrator: SessionOrchestrator
  private let reachability = NWReachability()
  private var audioStateCancellables = Set<AnyCancellable>()
  private var firstFrameTimeoutTask: Task<Void, Never>?
  private let firstFrameTimeoutNanoseconds: UInt64 = 8_000_000_000

  init(
    store: SessionStateStore,
    deviceSessionCoordinator: DeviceSessionCoordinator,
    runtimeConfig: RuntimeConfig,
    preferSpeakerOutput: Bool = false
  ) {
    self.store = store
    self.deviceSessionCoordinator = deviceSessionCoordinator
    self.audioCollectionManager = AudioCollectionManager(
      speechRMSThreshold: runtimeConfig.speechRMSThreshold,
      speechActivityDebounceMs: runtimeConfig.speechActivityDebounceMs,
      preferSpeakerOutput: preferSpeakerOutput
    )
    let audioManager = self.audioCollectionManager
    let audioSessionLeaseManager = AudioSessionLeaseManager(arbiter: AudioSessionArbiter())

    let dependencies = SessionOrchestrator.Dependencies(
      startStream: {
        do {
          try await audioSessionLeaseManager.acquire(configuration: .playAndRecordHFP)
        } catch {
          let message = RuntimeCoordinator.audioLeaseErrorMessage(prefix: "Failed to acquire audio session lease", error: error)
          store.audioLastError = message
          store.runtimeErrorText = message
          if store.assistantRuntimeState != .inactive {
            store.assistantRuntimeState = .inactive
            store.runtimeSessionStateText = "idle"
          }
          return
        }

        await audioManager.prepareAudioSession()
        if audioManager.isAudioSessionReady {
          await audioManager.start()
        }
        await deviceSessionCoordinator.startSession()
      },
      stopStream: {
        await audioManager.stop()
        await deviceSessionCoordinator.stopSession()
        do {
          try await audioSessionLeaseManager.releaseIfNeeded()
        } catch {
          let message = RuntimeCoordinator.audioLeaseErrorMessage(prefix: "Failed to release audio session lease", error: error)
          store.audioLastError = message
          store.runtimeErrorText = message
        }
      },
      exportAudioClip: { window in
        try audioManager.exportWAVClip(window: window)
      },
      flushPendingAudioChunks: {
        audioManager.flushPendingAudioChunks()
      },
      audioBufferDurationProvider: {
        let bytes = audioManager.stats.bytesWritten
        return Int(bytes / 16)
      },
      sharedAudioEngine: audioManager.sharedAudioEngine,
      clock: { Clocks.nowMs() },
      makeVisionFrameUploader: SessionOrchestrator.Dependencies.live.makeVisionFrameUploader,
      makeRollingVideoBuffer: SessionOrchestrator.Dependencies.live.makeRollingVideoBuffer,
      makePlaybackEngine: SessionOrchestrator.Dependencies.live.makePlaybackEngine,
      eventLogger: SessionOrchestrator.Dependencies.live.eventLogger,
      suppressSpeakerRouteErrors: preferSpeakerOutput
    )

    self.runtimeOrchestrator = SessionOrchestrator(config: runtimeConfig, dependencies: dependencies)
    self.audioCollectionManager.isPlaybackPendingProvider = { [weak self] in
      self?.runtimeOrchestrator.hasPendingPlayback() ?? false
    }
    bindReachability()
    bindDeviceSession()
    bindRuntimeState()
    bindAudioState()
  }

  deinit {
    firstFrameTimeoutTask?.cancel()
  }

  func activate() async {
    store.markWaitingForFirstFrame()
    scheduleFirstFrameTimeoutIfNeeded()
    await runtimeOrchestrator.preflightWakeAuthorization()
    await runtimeOrchestrator.activate()
  }

  func deactivate() async {
    store.assistantRuntimeState = .deactivating
    store.resetFirstFrameState(status: "reset_manual_deactivate")
    cancelFirstFrameTimeout()
    await runtimeOrchestrator.deactivate()
  }

  func triggerWakeForTesting() {
    runtimeOrchestrator.triggerWakeForTesting()
  }

  func endConversation() async {
    await runtimeOrchestrator.endConversation()
  }

  func pushVideoFrame(_ image: UIImage, timestampMs: Int64) {
    runtimeOrchestrator.pushVideoFrame(image, timestampMs: timestampMs)
  }

  func submitCapturedPhoto(_ image: UIImage, timestampMs: Int64) {
    runtimeOrchestrator.submitCapturedPhoto(image, timestampMs: timestampMs)
  }

  func recordSpeechActivity(_ timestampMs: Int64) {
    runtimeOrchestrator.recordSpeechActivity(at: timestampMs)
  }

  func processWakePCMFrame(_ frame: WakeWordPCMFrame) {
    runtimeOrchestrator.processWakePCMFrame(frame)
  }

  func handleScenePhaseChange(_ phase: ScenePhase) {
    switch phase {
    case .active:
      runtimeOrchestrator.handleAppDidBecomeActive()
    case .inactive:
      runtimeOrchestrator.handleAppWillResignActive()
    case .background:
      runtimeOrchestrator.handleAppDidEnterBackground()
    @unknown default:
      break
    }
  }

  private func bindReachability() {
    reachability.onConnectivityStateChanged = { [weak self] connectivityState in
      guard let self else { return }
      self.store.internetReachabilityState = self.storeReachabilityState(from: connectivityState)

      switch connectivityState {
      case .unknown:
        break
      case .connected:
        self.runtimeOrchestrator.setNetworkAvailable(true)
      case .disconnected:
        self.runtimeOrchestrator.setNetworkAvailable(false)
      }
    }
    store.internetReachabilityState = .unknown
    reachability.startMonitoring()
  }

  var onWakeAuthorizationPreflight: (() async -> Void)?

  func preflightWakeAuthorization() async {
    await runtimeOrchestrator.preflightWakeAuthorization()
  }

  private func bindDeviceSession() {
    deviceSessionCoordinator.hooks.onActiveDeviceChanged = { [weak self] hasDevice in
      self?.store.hasActiveDevice = hasDevice
    }

    deviceSessionCoordinator.hooks.onStreamingStateChanged = { [weak self] state in
      self?.updateStatusFromStreamState(state)
    }

    deviceSessionCoordinator.hooks.onStreamError = { [weak self] error in
      self?.showError(DeviceSessionCoordinator.formatStreamingError(error))
    }

    deviceSessionCoordinator.hooks.onVideoFrame = { [weak self] image, timestampMs in
      guard let self else { return }
      store.currentVideoFrame = image
      if !store.hasReceivedFirstFrame {
        store.markFirstFrameReceived()
        cancelFirstFrameTimeout()
      }
      runtimeOrchestrator.pushVideoFrame(image, timestampMs: timestampMs)
    }

    deviceSessionCoordinator.hooks.onPhotoCaptured = { [weak self] image, timestampMs in
      guard let self else { return }
      store.capturedPhoto = image
      store.showPhotoPreview = true
      store.runtimePhotoStateText = "captured"
      runtimeOrchestrator.submitCapturedPhoto(image, timestampMs: timestampMs)
    }
  }

  private func bindRuntimeState() {
    runtimeOrchestrator.onStatusUpdated = { [weak self] snapshot in
      guard let self else { return }
      store.assistantRuntimeState = snapshot.assistantRuntimeState
      store.runtimeSessionStateText = snapshot.sessionState.rawValue
      store.runtimeWakeStateText = snapshot.wakeState.rawValue
      store.runtimeQueryStateText = snapshot.queryState.rawValue
      store.runtimePhotoStateText = snapshot.photoState.rawValue
      store.runtimePlaybackStateText = snapshot.playbackState
      store.runtimeWakeEngineText = snapshot.wakeEngine
      store.runtimeWakeRuntimeText = snapshot.wakeRuntimeStatus
      store.runtimeSpeechAuthorizationText = snapshot.speechAuthorization
      store.runtimeManualWakeFallbackText = snapshot.manualWakeFallbackEnabled ? "enabled" : "disabled"
      store.runtimeBackendText = snapshot.backendSummary
      store.runtimeSessionIdText = snapshot.sessionID
      store.runtimeQueryIdText = snapshot.queryID
      store.runtimeWakeCount = snapshot.wakeCount
      store.runtimeQueryCount = snapshot.queryCount
      store.runtimePhotoUploadCount = snapshot.photoUploadCount
      store.runtimePlaybackChunkCount = snapshot.playbackChunkCount
      store.runtimePendingPlaybackBufferCount = snapshot.pendingPlaybackBufferCount
      store.runtimeVideoFrameCount = snapshot.videoFrameCount
      store.runtimeErrorText = snapshot.lastError

      switch snapshot.sessionState {
      case .idle, .ended:
        store.resetFirstFrameState(status: "reset_runtime_inactive")
        cancelFirstFrameTimeout()
      case .connecting, .reconnecting, .active, .streaming:
        store.markWaitingForFirstFrame()
        scheduleFirstFrameTimeoutIfNeeded()
      case .disconnecting:
        store.resetFirstFrameState(status: "reset_runtime_deactivating")
        cancelFirstFrameTimeout()
      case .failed:
        store.resetFirstFrameState(status: "reset_runtime_failed")
        cancelFirstFrameTimeout()
      }

    }

    audioCollectionManager.onWakePCMFrame = { [weak self] frame in
      self?.runtimeOrchestrator.processWakePCMFrame(frame)
    }

    audioCollectionManager.onRealtimePCMFrame = { [weak self] payload, timestampMs in
      Task(priority: .userInitiated) { [weak self] in
        await self?.runtimeOrchestrator.processRealtimePCMFrame(payload, timestampMs: timestampMs)
      }
    }
  }

  private func bindAudioState() {
    audioCollectionManager.$state
      .sink { [weak self] state in
        guard let self else { return }
        switch state {
        case .idle:
          store.audioStateText = "idle"
        case .preparingAudioSession:
          store.audioStateText = "preparing"
        case .waitingForDevice:
          store.audioStateText = "waiting_for_device"
        case .recording:
          store.audioStateText = "recording"
        case .stopping:
          store.audioStateText = "stopping"
        case .failed(let message):
          store.audioStateText = "failed: \(message)"
          store.audioLastError = message
          store.runtimeErrorText = message
        }
        store.isAudioRecording = state == .recording
      }
      .store(in: &audioStateCancellables)

    audioCollectionManager.$stats
      .sink { [weak self] stats in
        guard let self else { return }
        store.audioChunkCount = stats.chunksWritten
        store.audioByteCount = stats.bytesWritten
        store.audioStatsText = "Chunks: \(stats.chunksWritten)  Bytes: \(stats.bytesWritten)"
        if let lastError = stats.lastError {
          store.audioLastError = lastError
          store.runtimeErrorText = lastError
        }
      }
      .store(in: &audioStateCancellables)

    audioCollectionManager.$lastSpeechActivityTimestampMs
      .sink { [weak self] timestampMs in
        guard let self, let timestampMs else { return }
        runtimeOrchestrator.recordSpeechActivity(at: timestampMs)
      }
      .store(in: &audioStateCancellables)

    audioCollectionManager.$isAudioSessionReady
      .sink { [weak self] ready in
        self?.store.isAudioReady = ready
      }
      .store(in: &audioStateCancellables)

    audioCollectionManager.$currentSessionDirectory
      .sink { [weak self] directory in
        self?.store.audioSessionPath = directory?.path ?? "No audio session directory"
      }
      .store(in: &audioStateCancellables)
  }

  private func updateStatusFromStreamState(_ state: StreamSessionState) {
    switch state {
    case .stopped:
      store.streamingStatus = .stopped
      store.resetFirstFrameState(status: "reset_stream_stopped")
      cancelFirstFrameTimeout()
      if store.assistantRuntimeState == .deactivating {
        store.runtimeSessionStateText = "idle"
      } else if store.assistantRuntimeState == .connectingConversation || store.assistantRuntimeState == .activeConversation {
        store.runtimeSessionStateText = "stopped"
      }
    case .waitingForDevice, .starting, .stopping, .paused:
      store.streamingStatus = .waiting
      if store.assistantRuntimeState != .inactive {
        store.runtimeSessionStateText = "waiting"
      }
    case .streaming:
      store.streamingStatus = .streaming
      store.markWaitingForFirstFrame()
      scheduleFirstFrameTimeoutIfNeeded()
      if store.assistantRuntimeState == .connectingConversation || store.assistantRuntimeState == .activeConversation {
        store.runtimeSessionStateText = "active"
      }
    }
  }

  private func showError(_ message: String) {
    store.errorMessage = message
    store.showError = true
    store.runtimeErrorText = message
    store.resetFirstFrameState(status: "reset_stream_error")
    cancelFirstFrameTimeout()

    if store.assistantRuntimeState != .inactive {
      store.assistantRuntimeState = .inactive
      store.runtimeSessionStateText = "idle"
    }
  }

  private static func audioLeaseErrorMessage(prefix: String, error: Error) -> String {
    "\(prefix): \(error.localizedDescription)"
  }

  private func scheduleFirstFrameTimeoutIfNeeded() {
    guard !store.hasReceivedFirstFrame else {
      cancelFirstFrameTimeout()
      return
    }
    firstFrameTimeoutTask?.cancel()
    firstFrameTimeoutTask = Task { @MainActor [weak self] in
      guard let self else { return }
      do {
        try await Task.sleep(nanoseconds: self.firstFrameTimeoutNanoseconds)
      } catch {
        return
      }
      guard !Task.isCancelled else { return }
      guard !self.store.hasReceivedFirstFrame else { return }
      guard self.store.assistantRuntimeState == .connectingConversation || self.store.assistantRuntimeState == .activeConversation else { return }
      self.store.resetFirstFrameState(status: "first_frame_timeout")
      if self.store.runtimeInfoText.isEmpty {
        self.store.runtimeInfoText = "No video frame received yet. Check mock device state (PowerOn/Unfold/Don) and retry activation."
      }
    }
  }

  private func cancelFirstFrameTimeout() {
    firstFrameTimeoutTask?.cancel()
    firstFrameTimeoutTask = nil
  }

  private func storeReachabilityState(
    from connectivityState: NWReachability.ConnectivityState
  ) -> InternetReachabilityState {
    switch connectivityState {
    case .unknown:
      return .unknown
    case .connected:
      return .connected
    case .disconnected:
      return .disconnected
    }
  }
}

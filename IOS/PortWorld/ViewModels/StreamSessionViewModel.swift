/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// StreamSessionViewModel.swift
//
// Core view model demonstrating video streaming from Meta wearable devices using the DAT SDK.
// This class showcases the key streaming patterns: device selection, session management,
// video frame handling, photo capture, and error handling.
//

import MWDATCamera
import MWDATCore
import Combine
import SwiftUI

enum StreamingStatus {
  case streaming
  case waiting
  case stopped
}

enum AssistantRuntimeState {
  case inactive
  case activating
  case active
  case deactivating
  case failed
}

struct AssistantRuntimeHooks {
  var pushVideoFrame: ((UIImage, Int64) -> Void)?
  var submitCapturedPhoto: ((UIImage, Int64) -> Void)?
  var recordSpeechActivity: ((Int64) -> Void)?
  var processWakePCMFrame: ((WakeWordPCMFrame) -> Void)?
}

@MainActor
class StreamSessionViewModel: ObservableObject {
  @Published var currentVideoFrame: UIImage?
  @Published var hasReceivedFirstFrame: Bool = false
  @Published var streamingStatus: StreamingStatus = .stopped
  @Published var showError: Bool = false
  @Published var errorMessage: String = ""
  @Published var hasActiveDevice: Bool = false

  @Published var assistantRuntimeState: AssistantRuntimeState = .inactive
  @Published var runtimeSessionStateText: String = "inactive"
  @Published var runtimeWakeStateText: String = "idle"
  @Published var runtimeQueryStateText: String = "idle"
  @Published var runtimePhotoStateText: String = "idle"
  @Published var runtimePlaybackStateText: String = "idle"
  @Published var runtimeWakeEngineText: String = "manual"
  @Published var runtimeWakeRuntimeText: String = "idle"
  @Published var runtimeSpeechAuthorizationText: String = "not_required"
  @Published var runtimeManualWakeFallbackText: String = "enabled"
  @Published var runtimeBackendText: String = "-"
  @Published var runtimeErrorText: String = ""
  @Published var runtimeSessionIdText: String = "-"
  @Published var runtimeQueryIdText: String = "-"
  @Published var runtimeWakeCount: Int = 0
  @Published var runtimeQueryCount: Int = 0
  @Published var runtimeVideoFrameCount: Int = 0
  @Published var runtimePhotoUploadCount: Int = 0
  @Published var runtimePlaybackChunkCount: Int = 0
  @Published var runtimePendingPlaybackBufferCount: Int = 0

  @Published var exampleTestStateText: String = "idle"
  @Published var exampleTestDetailText: String = "Ready"
  @Published var isRunningExampleTest: Bool = false

  @Published var audioStateText: String = "idle"
  @Published var audioStatsText: String = "Chunks: 0  Bytes: 0"
  @Published var isAudioReady: Bool = false
  @Published var isAudioRecording: Bool = false
  @Published var audioSessionPath: String = "No audio session directory"
  @Published var audioLastError: String = ""
  @Published var audioChunkCount: Int = 0
  @Published var audioByteCount: Int64 = 0

  @Published var capturedPhoto: UIImage?
  @Published var showPhotoPreview: Bool = false

  var isStreaming: Bool {
    switch assistantRuntimeState {
    case .activating, .active, .deactivating:
      return true
    case .inactive, .failed:
      return false
    }
  }

  var canActivateAssistantRuntime: Bool {
    hasActiveDevice && (assistantRuntimeState == .inactive || assistantRuntimeState == .failed)
  }

  var canDeactivateAssistantRuntime: Bool {
    switch assistantRuntimeState {
    case .activating, .active, .failed:
      return true
    case .inactive, .deactivating:
      return false
    }
  }

  private var streamSession: StreamSession

  private var stateListenerToken: AnyListenerToken?
  private var videoFrameListenerToken: AnyListenerToken?
  private var errorListenerToken: AnyListenerToken?
  private var photoDataListenerToken: AnyListenerToken?

  private let wearables: WearablesInterface
  private let deviceSelector: AutoDeviceSelector
  private var deviceMonitorTask: Task<Void, Never>?

  private let audioCollectionManager: AudioCollectionManager
  private var audioStateCancellables = Set<AnyCancellable>()

  private var runtimeHooks = AssistantRuntimeHooks()
  private let photoUploadCadenceMs: Int64 = 1000
  private var lastFramePhotoUploadTimestampMs: Int64 = 0
  private let runtimeConfig: RuntimeConfig
  private lazy var exampleMediaPipelineTester = ExampleMediaPipelineTester(runtimeConfig: runtimeConfig)

  private lazy var runtimeOrchestrator: SessionOrchestrator = makeRuntimeOrchestrator()

  init(wearables: WearablesInterface) {
    self.wearables = wearables
    let config = RuntimeConfig.load()
    self.runtimeConfig = config
    self.audioCollectionManager = AudioCollectionManager(
      speechRMSThreshold: config.speechRMSThreshold,
      speechActivityDebounceMs: config.speechActivityDebounceMs
    )

    self.deviceSelector = AutoDeviceSelector(wearables: wearables)
    let streamConfig = StreamSessionConfig(
      videoCodec: VideoCodec.raw,
      resolution: StreamingResolution.low,
      frameRate: 24)
    streamSession = StreamSession(streamSessionConfig: streamConfig, deviceSelector: deviceSelector)

    deviceMonitorTask = Task { @MainActor [weak self] in
      guard let self else { return }
      for await device in self.deviceSelector.activeDeviceStream() {
        self.hasActiveDevice = device != nil
      }
    }

    stateListenerToken = streamSession.statePublisher.listen { [weak self] state in
      Task { @MainActor [weak self] in
        self?.updateStatusFromState(state)
      }
    }

    videoFrameListenerToken = streamSession.videoFramePublisher.listen { [weak self] videoFrame in
      Task { @MainActor [weak self] in
        guard let self else { return }
        guard let image = videoFrame.makeUIImage() else { return }
        self.handleIncomingVideoFrame(image)
      }
    }

    errorListenerToken = streamSession.errorPublisher.listen { [weak self] error in
      Task { @MainActor [weak self] in
        guard let self else { return }
        let newErrorMessage = formatStreamingError(error)
        if newErrorMessage != self.errorMessage {
          showError(newErrorMessage)
        }
      }
    }

    updateStatusFromState(streamSession.state)
    runtimeBackendText = config.backendSummary

    photoDataListenerToken = streamSession.photoDataPublisher.listen { [weak self] photoData in
      Task { @MainActor [weak self] in
        guard let self else { return }
        guard let uiImage = UIImage(data: photoData.data) else { return }

        self.capturedPhoto = uiImage
        self.showPhotoPreview = true
        self.runtimePhotoStateText = "captured"
        let timestampMs = Self.nowMs()
        self.runtimeHooks.submitCapturedPhoto?(uiImage, timestampMs)
        self.runtimePhotoUploadCount += 1
      }
    }

    bindAudioCollectionState()
    configureDefaultRuntimeHooks()
  }

  deinit {
    deviceMonitorTask?.cancel()
  }

  func configureAssistantRuntimeHooks(_ hooks: AssistantRuntimeHooks) {
    runtimeHooks = hooks
    audioCollectionManager.onWakePCMFrame = { [weak self] frame in
      self?.runtimeHooks.processWakePCMFrame?(frame)
    }
  }

  private func configureDefaultRuntimeHooks() {
    configureAssistantRuntimeHooks(
      AssistantRuntimeHooks(
        pushVideoFrame: { [weak self] image, timestampMs in
          self?.runtimeOrchestrator.pushVideoFrame(image, timestampMs: timestampMs)
        },
        submitCapturedPhoto: { [weak self] image, timestampMs in
          self?.runtimeOrchestrator.submitCapturedPhoto(image, timestampMs: timestampMs)
        },
        recordSpeechActivity: { [weak self] timestampMs in
          self?.runtimeOrchestrator.recordSpeechActivity(at: timestampMs)
        },
        processWakePCMFrame: { [weak self] frame in
          self?.runtimeOrchestrator.processWakePCMFrame(frame)
        }
      )
    )
  }

  private func makeRuntimeOrchestrator() -> SessionOrchestrator {
    let config = runtimeConfig
    let dependencies = SessionOrchestrator.Dependencies(
      startStream: { [weak self] in
        guard let self else { return }
        await self.prepareAudioCollection()
        if self.isAudioReady {
          await self.startAudioCollection()
        }
        await self.startSession()
      },
      stopStream: { [weak self] in
        guard let self else { return }
        await self.stopAudioCollection()
        await self.stopSession()
      },
      exportAudioClip: { [weak self] window in
        guard let self else {
          throw AudioClipExportError.sessionDirectoryUnavailable
        }
        return try self.exportAudioClip(window: window)
      },
      flushPendingAudioChunks: { [weak self] in
        self?.flushPendingAudioChunks()
      },
      audioBufferDurationProvider: { [weak self] in
        guard let self else { return 0 }
        return self.estimatedAudioBufferDurationMs()
      },
      sharedAudioEngine: audioCollectionManager.sharedAudioEngine
    )

    let orchestrator = SessionOrchestrator(config: config, dependencies: dependencies)
    orchestrator.onStatusUpdated = { [weak self] snapshot in
      guard let self else { return }
      self.runtimeSessionStateText = snapshot.sessionState.rawValue
      self.runtimeWakeStateText = snapshot.wakeState.rawValue
      self.runtimeQueryStateText = snapshot.queryState.rawValue
      self.runtimePhotoStateText = snapshot.photoState.rawValue
      self.runtimePlaybackStateText = snapshot.playbackState
      self.runtimeWakeEngineText = snapshot.wakeEngine
      self.runtimeWakeRuntimeText = snapshot.wakeRuntimeStatus
      self.runtimeSpeechAuthorizationText = snapshot.speechAuthorization
      self.runtimeManualWakeFallbackText = snapshot.manualWakeFallbackEnabled ? "enabled" : "disabled"
      self.runtimeBackendText = snapshot.backendSummary
      self.runtimeSessionIdText = snapshot.sessionID
      self.runtimeQueryIdText = snapshot.queryID
      self.runtimeWakeCount = snapshot.wakeCount
      self.runtimeQueryCount = snapshot.queryCount
      self.runtimePhotoUploadCount = snapshot.photoUploadCount
      self.runtimePlaybackChunkCount = snapshot.playbackChunkCount
      self.runtimePendingPlaybackBufferCount = snapshot.pendingPlaybackBufferCount
      self.runtimeVideoFrameCount = snapshot.videoFrameCount
      self.runtimeErrorText = snapshot.lastError

      switch snapshot.sessionState {
      case .idle, .ended:
        self.assistantRuntimeState = .inactive
      case .connecting, .reconnecting:
        self.assistantRuntimeState = .activating
      case .active:
        self.assistantRuntimeState = .active
      case .failed:
        self.assistantRuntimeState = .failed
      }
    }
    return orchestrator
  }

  func updateAssistantRuntimeStatus(
    session: String? = nil,
    wake: String? = nil,
    query: String? = nil,
    photo: String? = nil,
    playback: String? = nil,
    sessionId: String? = nil,
    queryId: String? = nil,
    wakeCount: Int? = nil,
    queryCount: Int? = nil,
    playbackChunkCount: Int? = nil,
    error: String? = nil
  ) {
    if let session { runtimeSessionStateText = session }
    if let wake { runtimeWakeStateText = wake }
    if let query { runtimeQueryStateText = query }
    if let photo { runtimePhotoStateText = photo }
    if let playback { runtimePlaybackStateText = playback }
    if let sessionId { runtimeSessionIdText = sessionId }
    if let queryId { runtimeQueryIdText = queryId }
    if let wakeCount { runtimeWakeCount = wakeCount }
    if let queryCount { runtimeQueryCount = queryCount }
    if let playbackChunkCount { runtimePlaybackChunkCount = playbackChunkCount }
    if let error { runtimeErrorText = error }
  }

  func activateAssistantRuntime() async {
    guard canActivateAssistantRuntime else { return }

    assistantRuntimeState = .activating
    runtimeSessionStateText = "activating"
    runtimeErrorText = ""

    let permission = Permission.camera
    do {
      let status = try await wearables.checkPermissionStatus(permission)
      if status != .granted {
        let requestStatus = try await wearables.requestPermission(permission)
        guard requestStatus == .granted else {
          showError("Permission denied")
          return
        }
      }
      await runtimeOrchestrator.preflightWakeAuthorization()
      await runtimeOrchestrator.activate()
    } catch {
      showError("Permission error: \(error.description)")
    }
  }

  func preflightWakeAuthorization() async {
    await runtimeOrchestrator.preflightWakeAuthorization()
  }

  func deactivateAssistantRuntime() async {
    guard canDeactivateAssistantRuntime else { return }

    assistantRuntimeState = .deactivating
    runtimeSessionStateText = "deactivating"
    await runtimeOrchestrator.deactivate()
  }

  func handleStartStreaming() async {
    await activateAssistantRuntime()
  }

  func startSession() async {
    await streamSession.start()
  }

  private func showError(_ message: String) {
    errorMessage = message
    showError = true
    runtimeErrorText = message

    if assistantRuntimeState == .activating || assistantRuntimeState == .active {
      assistantRuntimeState = .failed
      runtimeSessionStateText = "failed"
    }
  }

  func stopSession() async {
    await streamSession.stop()
  }

  func dismissError() {
    showError = false
    errorMessage = ""
  }

  func capturePhoto() {
    runtimePhotoStateText = "capturing"
    streamSession.capturePhoto(format: .jpeg)
  }

  func triggerWakeForTesting() {
    runtimeOrchestrator.triggerWakeForTesting()
  }

  func runExampleMediaPipelineTest() async {
    guard !isRunningExampleTest else { return }

    isRunningExampleTest = true
    exampleTestStateText = "sending"
    exampleTestDetailText = "Uploading image, video and audio to backend..."

    do {
      let result = try await exampleMediaPipelineTester.runExamplePipeline()
      exampleTestStateText = "playing"
      exampleTestDetailText = "Backend HTTP \(result.statusCode), \(result.responseBytes) bytes"

      let playbackMs = max(0, result.playbackDurationMs)
      if playbackMs > 0 {
        try? await Task.sleep(nanoseconds: UInt64(playbackMs) * 1_000_000)
      }

      exampleTestStateText = "done"
      exampleTestDetailText = "Audio played on iPhone (\(playbackMs) ms)."
    } catch {
      let message = error.localizedDescription
      exampleTestStateText = "failed"
      exampleTestDetailText = message
      runtimeErrorText = message
    }

    isRunningExampleTest = false
  }

  func dismissPhotoPreview() {
    showPhotoPreview = false
    capturedPhoto = nil
  }

  func prepareAudioCollection() async {
    await audioCollectionManager.prepareAudioSession()
  }

  func startAudioCollection() async {
    await audioCollectionManager.start()
  }

  func stopAudioCollection() async {
    await audioCollectionManager.stop()
  }

  func exportAudioClip(window: AudioClipExportWindow) throws -> URL {
    try audioCollectionManager.exportWAVClip(window: window)
  }

  func flushPendingAudioChunks() {
    audioCollectionManager.flushPendingAudioChunks()
  }

  private func bindAudioCollectionState() {
    audioCollectionManager.$state
      .sink { [weak self] state in
        guard let self else { return }
        self.audioStateText = formatAudioState(state)
        self.isAudioRecording = state == .recording
        if case .failed(let message) = state {
          self.audioLastError = message
          self.runtimeErrorText = message
        }
      }
      .store(in: &audioStateCancellables)

    audioCollectionManager.$stats
      .sink { [weak self] stats in
        guard let self else { return }
        self.audioChunkCount = stats.chunksWritten
        self.audioByteCount = stats.bytesWritten
        self.audioStatsText = "Chunks: \(stats.chunksWritten)  Bytes: \(stats.bytesWritten)"
        if let lastError = stats.lastError {
          self.audioLastError = lastError
          self.runtimeErrorText = lastError
        }
      }
      .store(in: &audioStateCancellables)

    audioCollectionManager.$lastSpeechActivityTimestampMs
      .sink { [weak self] timestampMs in
        guard let self, let timestampMs else { return }
        self.runtimeHooks.recordSpeechActivity?(timestampMs)
      }
      .store(in: &audioStateCancellables)

    audioCollectionManager.$isAudioSessionReady
      .sink { [weak self] ready in
        self?.isAudioReady = ready
      }
      .store(in: &audioStateCancellables)

    audioCollectionManager.$currentSessionDirectory
      .sink { [weak self] directory in
        self?.audioSessionPath = directory?.path ?? "No audio session directory"
      }
      .store(in: &audioStateCancellables)
  }

  private func formatAudioState(_ state: AudioCollectionState) -> String {
    switch state {
    case .idle:
      return "idle"
    case .preparingAudioSession:
      return "preparing"
    case .waitingForDevice:
      return "waiting_for_device"
    case .recording:
      return "recording"
    case .stopping:
      return "stopping"
    case .failed(let message):
      return "failed: \(message)"
    }
  }

  private func updateStatusFromState(_ state: StreamSessionState) {
    switch state {
    case .stopped:
      currentVideoFrame = nil
      streamingStatus = .stopped
      if assistantRuntimeState == .deactivating {
        runtimeSessionStateText = "inactive"
      } else if assistantRuntimeState == .activating || assistantRuntimeState == .active {
        runtimeSessionStateText = "stopped"
      }
    case .waitingForDevice, .starting, .stopping, .paused:
      streamingStatus = .waiting
      if assistantRuntimeState != .inactive {
        runtimeSessionStateText = "waiting"
      }
    case .streaming:
      streamingStatus = .streaming
      if assistantRuntimeState == .activating || assistantRuntimeState == .active {
        runtimeSessionStateText = "active"
      }
    }
  }

  private func handleIncomingVideoFrame(_ image: UIImage) {
    currentVideoFrame = image
    if !hasReceivedFirstFrame {
      hasReceivedFirstFrame = true
    }

    runtimeVideoFrameCount += 1
    let timestampMs = Self.nowMs()
    runtimeHooks.pushVideoFrame?(image, timestampMs)

    if timestampMs - lastFramePhotoUploadTimestampMs >= photoUploadCadenceMs,
       image.jpegData(compressionQuality: 0.7) != nil
    {
      lastFramePhotoUploadTimestampMs = timestampMs
      enqueuePhotoForUpload(image, source: "frame")
    }
  }

  private func enqueuePhotoForUpload(_ image: UIImage, source: String) {
    let timestampMs = Self.nowMs()
    guard let submitCapturedPhoto = runtimeHooks.submitCapturedPhoto else {
      return
    }

    submitCapturedPhoto(image, timestampMs)
    runtimePhotoUploadCount += 1
    runtimePhotoStateText = "queued_\(source)"
  }

  private static func nowMs() -> Int64 {
    Int64((Date().timeIntervalSince1970 * 1000.0).rounded())
  }

  private func formatStreamingError(_ error: StreamSessionError) -> String {
    switch error {
    case .internalError:
      return "An internal error occurred. Please try again."
    case .deviceNotFound:
      return "Device not found. Please ensure your device is connected."
    case .deviceNotConnected:
      return "Device not connected. Please check your connection and try again."
    case .timeout:
      return "The operation timed out. Please try again."
    case .videoStreamingError:
      return "Video streaming failed. Please try again."
    case .audioStreamingError:
      return "Audio streaming failed. Please try again."
    case .permissionDenied:
      return "Camera permission denied. Please grant permission in Settings."
    case .hingesClosed:
      return "The hinges on the glasses were closed. Please open the hinges and try again."
    @unknown default:
      return "An unknown streaming error occurred."
    }
  }

  private func estimatedAudioBufferDurationMs() -> Int {
    // Use playback queue depth if available (pending buffers * ~100ms per buffer)
    // Otherwise fall back to capture byte count estimate
    if runtimePendingPlaybackBufferCount > 0 {
      return runtimePendingPlaybackBufferCount * 100  // ~100ms per buffer chunk
    }
    // Fallback: Audio chunks are recorded as PCM16 mono @ 8kHz: 16 bytes/ms.
    return Int(audioByteCount / 16)
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
}

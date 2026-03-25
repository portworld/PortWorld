import MWDATCamera
import MWDATCore
import Foundation
import SwiftUI

@MainActor
final class DeviceSessionCoordinator {
  struct Hooks {
    var onVideoFrame: ((UIImage, Int64) -> Void)?
    var onPhotoCaptured: ((UIImage, Int64) -> Void)?
    var onStreamError: ((StreamSessionError) -> Void)?
    var onStreamingStateChanged: ((StreamSessionState) -> Void)?
    var onActiveDeviceChanged: ((Bool) -> Void)?
  }

  private let wearables: WearablesInterface
  private let deviceSelector: AutoDeviceSelector
  private var streamSession: StreamSession
  private var deviceMonitorTask: Task<Void, Never>?

  private var stateListenerToken: AnyListenerToken?
  private var videoFrameListenerToken: AnyListenerToken?
  private var errorListenerToken: AnyListenerToken?
  private var photoDataListenerToken: AnyListenerToken?

  private let frameRate: UInt
  var hooks: Hooks

  init(wearables: WearablesInterface, frameRate: UInt = 24, hooks: Hooks? = nil) {
    self.wearables = wearables
    self.deviceSelector = AutoDeviceSelector(wearables: wearables)
    self.frameRate = frameRate
    self.hooks = hooks ?? Hooks()

    let streamConfig = StreamSessionConfig(
      videoCodec: VideoCodec.raw,
      resolution: StreamingResolution.low,
      frameRate: frameRate
    )
    self.streamSession = StreamSession(streamSessionConfig: streamConfig, deviceSelector: deviceSelector)

    bindListeners()
    startDeviceMonitor()
  }

  deinit {
    deviceMonitorTask?.cancel()
  }

  func ensureCameraPermissionIfNeeded() async throws {
    let permission = Permission.camera
    let status = try await wearables.checkPermissionStatus(permission)
    if status != .granted {
      let requestStatus = try await wearables.requestPermission(permission)
      if requestStatus != .granted {
        throw NSError(domain: "DeviceSessionCoordinator", code: 1, userInfo: [
          NSLocalizedDescriptionKey: "Permission denied"
        ])
      }
    }
  }

  func startSession() async {
    await streamSession.start()
  }

  func stopSession() async {
    await streamSession.stop()
  }

  func capturePhoto() {
    streamSession.capturePhoto(format: .jpeg)
  }

  private func startDeviceMonitor() {
    deviceMonitorTask = Task { @MainActor [weak self] in
      guard let self else { return }
      for await device in deviceSelector.activeDeviceStream() {
        hooks.onActiveDeviceChanged?(device != nil)
      }
    }
  }

  private func bindListeners() {
    stateListenerToken = streamSession.statePublisher.listen { [weak self] state in
      Task { @MainActor [weak self] in
        self?.hooks.onStreamingStateChanged?(state)
      }
    }

    videoFrameListenerToken = streamSession.videoFramePublisher.listen { [weak self] videoFrame in
      Task { @MainActor [weak self] in
        guard let self else { return }
        guard let image = videoFrame.makeUIImage() else { return }
        hooks.onVideoFrame?(image, Clocks.nowMs())
      }
    }

    errorListenerToken = streamSession.errorPublisher.listen { [weak self] error in
      Task { @MainActor [weak self] in
        self?.hooks.onStreamError?(error)
      }
    }

    photoDataListenerToken = streamSession.photoDataPublisher.listen { [weak self] photoData in
      Task { @MainActor [weak self] in
        guard let self else { return }
        guard let uiImage = UIImage(data: photoData.data) else { return }
        hooks.onPhotoCaptured?(uiImage, Clocks.nowMs())
      }
    }
  }

  static func formatStreamingError(_ error: StreamSessionError) -> String {
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
    case .permissionDenied:
      return "Camera permission denied. Please grant permission in Settings."
    case .hingesClosed:
      return "The hinges on the glasses were closed. Please open the hinges and try again."
    default:
      return "Streaming failed. Please try again."
    }
  }
}

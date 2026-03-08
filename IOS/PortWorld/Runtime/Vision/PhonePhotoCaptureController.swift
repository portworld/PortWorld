// Debug-only bounded still-photo capture controller for exercising vision upload from the iPhone camera.
import AVFoundation
import Foundation
import UIKit

@MainActor
final class PhonePhotoCaptureController: NSObject {
  enum Phase: String {
    case inactive
    case requestingPermission = "requesting_permission"
    case starting
    case capturing
    case stopping
    case failed
  }

  struct Snapshot {
    var phase: Phase = .inactive
    var errorMessage: String?
  }

  var onSnapshotUpdated: ((Snapshot) -> Void)?
  var onPhotoCaptured: ((UIImage, Int64) -> Void)?

  private let captureSession = AVCaptureSession()
  private let photoOutput = AVCapturePhotoOutput()
  private var snapshot = Snapshot()
  private var captureLoopTask: Task<Void, Never>?
  private var activePhotoProcessors: [Int64: PhotoCaptureProcessor] = [:]
  private var isSessionConfigured = false
  private var isActive = false
  private var inFlightCapture = false
  private var photoIntervalNs: UInt64 = 1_000_000_000

  deinit {
    captureLoopTask?.cancel()
    if captureSession.isRunning {
      captureSession.stopRunning()
    }
  }

  func start(photoFps: Double) async {
    photoIntervalNs = UInt64((1_000_000_000.0 / max(0.1, photoFps)).rounded())

    guard !isActive else {
      ensureCaptureLoop()
      return
    }

    isActive = true
    snapshot.phase = .requestingPermission
    snapshot.errorMessage = nil
    publishSnapshot()

    do {
      try await ensureCameraPermissionIfNeeded()
      try configureSessionIfNeeded()
      snapshot.phase = .starting
      publishSnapshot()
      if !captureSession.isRunning {
        captureSession.startRunning()
      }
      snapshot.phase = .capturing
      snapshot.errorMessage = nil
      publishSnapshot()
      ensureCaptureLoop()
    } catch {
      isActive = false
      snapshot.phase = .failed
      snapshot.errorMessage = error.localizedDescription
      publishSnapshot()
    }
  }

  func stop() async {
    guard isActive || captureLoopTask != nil else {
      snapshot.phase = .inactive
      snapshot.errorMessage = nil
      publishSnapshot()
      return
    }

    isActive = false
    snapshot.phase = .stopping
    publishSnapshot()
    captureLoopTask?.cancel()
    captureLoopTask = nil
    activePhotoProcessors.removeAll(keepingCapacity: false)
    inFlightCapture = false
    if captureSession.isRunning {
      captureSession.stopRunning()
    }
    snapshot.phase = .inactive
    snapshot.errorMessage = nil
    publishSnapshot()
  }

  private func ensureCameraPermissionIfNeeded() async throws {
    switch AVCaptureDevice.authorizationStatus(for: .video) {
    case .authorized:
      return
    case .notDetermined:
      let granted = await withCheckedContinuation { continuation in
        AVCaptureDevice.requestAccess(for: .video) { granted in
          continuation.resume(returning: granted)
        }
      }
      if !granted {
        throw PhonePhotoCaptureError.permissionDenied
      }
    case .denied, .restricted:
      throw PhonePhotoCaptureError.permissionDenied
    @unknown default:
      throw PhonePhotoCaptureError.permissionDenied
    }
  }

  private func configureSessionIfNeeded() throws {
    guard isSessionConfigured == false else { return }

    guard
      let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back)
        ?? AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .front)
    else {
      throw PhonePhotoCaptureError.cameraUnavailable
    }

    captureSession.beginConfiguration()
    captureSession.sessionPreset = .photo
    defer {
      captureSession.commitConfiguration()
    }

    captureSession.inputs.forEach { captureSession.removeInput($0) }
    captureSession.outputs.forEach { captureSession.removeOutput($0) }

    let input = try AVCaptureDeviceInput(device: device)
    guard captureSession.canAddInput(input) else {
      throw PhonePhotoCaptureError.configurationFailed("Unable to add phone camera input.")
    }
    captureSession.addInput(input)

    guard captureSession.canAddOutput(photoOutput) else {
      throw PhonePhotoCaptureError.configurationFailed("Unable to add phone camera photo output.")
    }
    captureSession.addOutput(photoOutput)
    photoOutput.isHighResolutionCaptureEnabled = false

    isSessionConfigured = true
  }

  private func ensureCaptureLoop() {
    guard captureLoopTask == nil else { return }

    captureLoopTask = Task { @MainActor [weak self] in
      guard let self else { return }

      while !Task.isCancelled {
        if isActive, snapshot.phase == .capturing {
          capturePhotoIfNeeded()
        }

        do {
          try await Task.sleep(nanoseconds: photoIntervalNs)
        } catch {
          break
        }
      }

      self.captureLoopTask = nil
    }
  }

  private func capturePhotoIfNeeded() {
    guard inFlightCapture == false else { return }
    guard captureSession.isRunning else { return }

    inFlightCapture = true
    let requestID = Clocks.nowMs()
    let settings = AVCapturePhotoSettings()
    settings.isHighResolutionPhotoEnabled = false

    let processor = PhotoCaptureProcessor { [weak self] result in
      Task { @MainActor [weak self] in
        guard let self else { return }
        self.activePhotoProcessors[requestID] = nil
        self.inFlightCapture = false

        switch result {
        case .success(let data):
          guard let image = UIImage(data: data) else {
            self.snapshot.phase = .failed
            self.snapshot.errorMessage = "Unable to decode the captured phone camera frame."
            self.publishSnapshot()
            return
          }
          self.snapshot.phase = .capturing
          self.snapshot.errorMessage = nil
          self.publishSnapshot()
          self.onPhotoCaptured?(image, Clocks.nowMs())

        case .failure(let error):
          self.snapshot.phase = .failed
          self.snapshot.errorMessage = error.localizedDescription
          self.publishSnapshot()
        }
      }
    }

    activePhotoProcessors[requestID] = processor
    photoOutput.capturePhoto(with: settings, delegate: processor)
  }

  private func publishSnapshot() {
    onSnapshotUpdated?(snapshot)
  }
}

private enum PhonePhotoCaptureError: LocalizedError {
  case permissionDenied
  case cameraUnavailable
  case configurationFailed(String)

  var errorDescription: String? {
    switch self {
    case .permissionDenied:
      return "Camera permission denied. Grant access in Settings to test phone vision uploads."
    case .cameraUnavailable:
      return "No phone camera is available for debug vision capture."
    case .configurationFailed(let message):
      return message
    }
  }
}

private final class PhotoCaptureProcessor: NSObject, AVCapturePhotoCaptureDelegate {
  private let onComplete: (Result<Data, Error>) -> Void

  init(onComplete: @escaping (Result<Data, Error>) -> Void) {
    self.onComplete = onComplete
  }

  func photoOutput(
    _ output: AVCapturePhotoOutput,
    didFinishProcessingPhoto photo: AVCapturePhoto,
    error: Error?
  ) {
    if let error {
      onComplete(.failure(error))
      return
    }

    guard let data = photo.fileDataRepresentation(), !data.isEmpty else {
      onComplete(.failure(PhonePhotoCaptureError.configurationFailed("Captured phone camera frame is empty.")))
      return
    }

    onComplete(.success(data))
  }
}

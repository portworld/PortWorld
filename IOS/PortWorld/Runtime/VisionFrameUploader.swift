import Foundation
import UIKit

struct VisionFrameUploadResult {
  let frameId: String
  let captureTimestampMs: Int64
  let latencyMs: Int64
  let payloadBytes: Int
  let httpStatusCode: Int?
  let attemptCount: Int
  let success: Bool
  let errorCode: String?
  let errorDescription: String?
}

enum VisionFrameUploaderError: LocalizedError {
  case noSessionID
  case imageEncodingFailed

  var errorDescription: String? {
    switch self {
    case .noSessionID:
      return "Cannot upload vision frame without a session ID"
    case .imageEncodingFailed:
      return "Unable to JPEG-encode frame"
    }
  }
}

enum VisionFrameUploadErrorCode: String {
  case noSessionID = "PHOTO_UPLOAD_NO_SESSION_ID"
  case imageEncodingFailed = "PHOTO_UPLOAD_IMAGE_ENCODING_FAILED"
  case timeout = "PHOTO_UPLOAD_TIMEOUT"
  case network = "PHOTO_UPLOAD_NETWORK_ERROR"
  case server = "PHOTO_UPLOAD_HTTP_ERROR"
  case unknown = "PHOTO_UPLOAD_UNKNOWN_ERROR"
}

final class VisionFrameUploader: VisionFrameUploaderProtocol {
  typealias UploadResultHandler = (VisionFrameUploadResult) -> Void

  private var onUploadResult: UploadResultHandler?

  private struct PendingFrame {
    let image: UIImage
    let captureTimestampMs: Int64
  }

  private let endpointURL: URL
  private let defaultHeaders: [String: String]
  private var sessionIDProvider: () -> String?
  private let uploadIntervalMs: Int64
  private let jpegCompression: CGFloat
  private let requestTimeoutMs: Int64
  private let maxRetryCount: Int
  private let baseRetryDelayMs: Int64
  private let maxRetryDelayMs: Int64
  private let urlSession: URLSession
  private let queue = DispatchQueue(label: "Runtime.VisionFrameUploader")
  private let queueKey = DispatchSpecificKey<Void>()
  private let callbackQueue: DispatchQueue

  private var timer: DispatchSourceTimer?
  private var latestFrame: PendingFrame?
  private var isRunning = false
  private var uploadInFlight = false

  init(
    endpointURL: URL,
    defaultHeaders: [String: String] = [:],
    sessionIDProvider: @escaping () -> String?,
    uploadIntervalMs: Int64 = 1_000,
    jpegCompression: CGFloat = 0.6,
    requestTimeoutMs: Int64 = 3_000,
    maxRetryCount: Int = 2,
    baseRetryDelayMs: Int64 = 250,
    maxRetryDelayMs: Int64 = 2_000,
    urlSession: URLSession = .shared,
    callbackQueue: DispatchQueue = .main
  ) {
    self.endpointURL = endpointURL
    self.defaultHeaders = defaultHeaders
    self.sessionIDProvider = sessionIDProvider
    self.uploadIntervalMs = max(100, uploadIntervalMs)
    self.jpegCompression = min(max(jpegCompression, 0.1), 1.0)
    self.requestTimeoutMs = max(500, requestTimeoutMs)
    self.maxRetryCount = max(0, maxRetryCount)
    self.baseRetryDelayMs = max(100, baseRetryDelayMs)
    self.maxRetryDelayMs = max(self.baseRetryDelayMs, maxRetryDelayMs)
    self.urlSession = urlSession
    self.callbackQueue = callbackQueue
    self.queue.setSpecific(key: queueKey, value: ())
  }

  deinit {
    if DispatchQueue.getSpecific(key: queueKey) != nil {
      stopLocked()
    } else {
      queue.sync {
        stopLocked()
      }
    }
  }

  func start() {
    queue.async {
      guard !self.isRunning else {
        return
      }
      self.isRunning = true
      self.startTimerLocked()
    }
  }

  func stop() {
    queue.async {
      self.stopLocked()
    }
  }

  func submitLatestFrame(_ image: UIImage, captureTimestampMs: Int64 = Clocks.nowMs()) {
    queue.async {
      self.latestFrame = PendingFrame(image: image, captureTimestampMs: captureTimestampMs)
    }
  }

  func bindHandlers(
    sessionIDProvider: @escaping VisionFrameSessionIDProvider,
    onUploadResult: UploadResultHandler?
  ) {
    queue.async {
      self.sessionIDProvider = sessionIDProvider
      self.onUploadResult = onUploadResult
    }
  }

  private func startTimerLocked() {
    let timer = DispatchSource.makeTimerSource(queue: queue)
    timer.schedule(
      deadline: .now() + .milliseconds(Int(uploadIntervalMs)),
      repeating: .milliseconds(Int(uploadIntervalMs))
    )
    timer.setEventHandler { [weak self] in
      self?.tickLocked()
    }
    self.timer = timer
    timer.resume()
  }

  private func stopLocked() {
    isRunning = false
    timer?.setEventHandler {}
    timer?.cancel()
    timer = nil
    latestFrame = nil
  }

  private func tickLocked() {
    guard isRunning else {
      return
    }

    guard !uploadInFlight else {
      return
    }

    guard let pending = latestFrame else {
      return
    }

    latestFrame = nil
    uploadInFlight = true

    do {
      let requestPayload = try makeRequestPayload(pending)
      upload(payload: requestPayload, captureTimestampMs: pending.captureTimestampMs, attempt: 1)
    } catch {
      uploadInFlight = false
      let frameId = "frame_\(pending.captureTimestampMs)"
      let errorCode: VisionFrameUploadErrorCode = (error as? VisionFrameUploaderError) == .noSessionID
        ? .noSessionID
        : .imageEncodingFailed
      publishResult(
        VisionFrameUploadResult(
          frameId: frameId,
          captureTimestampMs: pending.captureTimestampMs,
          latencyMs: 0,
          payloadBytes: 0,
          httpStatusCode: nil,
          attemptCount: 1,
          success: false,
          errorCode: errorCode.rawValue,
          errorDescription: error.localizedDescription
        )
      )
    }
  }

  private func makeRequestPayload(_ pending: PendingFrame) throws -> (frameId: String, data: Data) {
    guard let sessionID = sessionIDProvider(), !sessionID.isEmpty else {
      throw VisionFrameUploaderError.noSessionID
    }

    guard let jpegData = pending.image.jpegData(compressionQuality: jpegCompression) else {
      throw VisionFrameUploaderError.imageEncodingFailed
    }

    let now = Clocks.nowMs()
    let frameId = "frame_\(now)"
    let dimensions = pending.image.normalizedPixelSize

    let body = VisionFrameRequest(
      sessionID: sessionID,
      tsMs: now,
      frameID: frameId,
      captureTsMs: pending.captureTimestampMs,
      width: dimensions.width,
      height: dimensions.height,
      frameB64: jpegData.base64EncodedString()
    )

    return (frameId, try JSONEncoder().encode(body))
  }

  private func upload(payload: (frameId: String, data: Data), captureTimestampMs: Int64, attempt: Int) {
    var request = URLRequest(url: endpointURL)
    request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    for (name, value) in defaultHeaders {
      request.setValue(value, forHTTPHeaderField: name)
    }
    request.httpBody = payload.data
    request.timeoutInterval = TimeInterval(requestTimeoutMs) / 1000.0

    let startedAt = Date()
    urlSession.dataTask(with: request) { [weak self] _, response, error in
      guard let self else {
        return
      }

      let latencyMs = Int64(Date().timeIntervalSince(startedAt) * 1000)
      let statusCode = (response as? HTTPURLResponse)?.statusCode
      let success = error == nil && (statusCode.map { 200..<300 ~= $0 } ?? false)
      let errorCode = self.classifyError(error: error, statusCode: statusCode)
      let shouldRetry = self.shouldRetry(error: error, statusCode: statusCode, attempt: attempt)

      self.queue.async {
        if success {
          self.uploadInFlight = false
          self.publishResult(
            VisionFrameUploadResult(
              frameId: payload.frameId,
              captureTimestampMs: captureTimestampMs,
              latencyMs: latencyMs,
              payloadBytes: payload.data.count,
              httpStatusCode: statusCode,
              attemptCount: attempt,
              success: true,
              errorCode: nil,
              errorDescription: nil
            )
          )
          return
        }

        if shouldRetry {
          let delayMs = self.retryDelayMs(forAttempt: attempt)
          self.queue.asyncAfter(deadline: .now() + .milliseconds(Int(delayMs))) { [weak self] in
            guard let self, self.isRunning else {
              self?.uploadInFlight = false
              return
            }
            self.upload(payload: payload, captureTimestampMs: captureTimestampMs, attempt: attempt + 1)
          }
          return
        }

        self.uploadInFlight = false
        self.publishResult(
          VisionFrameUploadResult(
            frameId: payload.frameId,
            captureTimestampMs: captureTimestampMs,
            latencyMs: latencyMs,
            payloadBytes: payload.data.count,
            httpStatusCode: statusCode,
            attemptCount: attempt,
            success: false,
            errorCode: errorCode.rawValue,
            errorDescription: error?.localizedDescription ?? "HTTP \(statusCode ?? -1)"
          )
        )
      }
    }.resume()
  }

  private func shouldRetry(error: Error?, statusCode: Int?, attempt: Int) -> Bool {
    guard attempt <= maxRetryCount else { return false }

    if let urlError = error as? URLError {
      switch urlError.code {
      case .timedOut, .cannotConnectToHost, .networkConnectionLost, .notConnectedToInternet:
        return true
      default:
        return false
      }
    }

    guard let statusCode else { return false }
    return statusCode == 429 || (500...599).contains(statusCode)
  }

  private func classifyError(error: Error?, statusCode: Int?) -> VisionFrameUploadErrorCode {
    if let urlError = error as? URLError {
      if urlError.code == .timedOut {
        return .timeout
      }
      return .network
    }

    if let statusCode, !(200..<300).contains(statusCode) {
      return .server
    }

    return .unknown
  }

  private func retryDelayMs(forAttempt attempt: Int) -> Int64 {
    let bounded = min(max(attempt - 1, 0), 6)
    let multiplier = Int64(1 << bounded)
    let scaled = min(baseRetryDelayMs * multiplier, maxRetryDelayMs)
    let jitter = Double.random(in: 0.85...1.15)
    return Int64(Double(scaled) * jitter)
  }

  private func publishResult(_ result: VisionFrameUploadResult) {
    callbackQueue.async {
      self.onUploadResult?(result)
    }
  }
}

private extension UIImage {
  var normalizedPixelSize: (width: Int, height: Int) {
    if let cgImage {
      return (cgImage.width, cgImage.height)
    }

    let width = Int(size.width * scale)
    let height = Int(size.height * scale)
    return (max(1, width), max(1, height))
  }
}

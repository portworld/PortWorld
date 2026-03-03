import AVFoundation
import Foundation
import UIKit

struct RollingVideoExportResult {
  let outputURL: URL
  let frameCount: Int
  let durationMs: Int64
  let bytesWritten: Int64
}

enum RollingVideoBufferError: LocalizedError {
  case invalidInterval(startMs: Int64, endMs: Int64)
  case noFramesInInterval(startMs: Int64, endMs: Int64)
  case unableToCreateWriter
  case unableToCreateWriterInput
  case unableToCreatePixelBuffer
  case appendFailed(reason: String)
  case writerFailed(reason: String)

  var errorDescription: String? {
    switch self {
    case .invalidInterval(let startMs, let endMs):
      return "Invalid export interval: start \(startMs) must be <= end \(endMs)"
    case .noFramesInInterval(let startMs, let endMs):
      return "No buffered frames found in interval [\(startMs), \(endMs)]"
    case .unableToCreateWriter:
      return "Failed to create AVAssetWriter"
    case .unableToCreateWriterInput:
      return "Failed to create AVAssetWriterInput for video"
    case .unableToCreatePixelBuffer:
      return "Failed to create pixel buffer from frame"
    case .appendFailed(let reason):
      return "Failed to append frame to MP4 writer: \(reason)"
    case .writerFailed(let reason):
      return "Failed to finalize MP4 writer: \(reason)"
    }
  }
}

final class RollingVideoBuffer: RollingVideoBufferProtocol {
  private struct BufferedFrame {
    let timestampMs: Int64
    let image: UIImage
  }

  private let queue = DispatchQueue(label: "Runtime.RollingVideoBuffer")
  private let workerQueue = DispatchQueue(label: "Runtime.RollingVideoBuffer.Writer", qos: .userInitiated)
  private let maxDurationMs: Int64

  private var frames: [BufferedFrame] = []

  init(maxDurationMs: Int64 = 30_000) {
    self.maxDurationMs = max(1_000, maxDurationMs)
  }

  var bufferedFrameCount: Int {
    queue.sync { frames.count }
  }

  var bufferedDurationMs: Int64 {
    queue.sync {
      guard let first = frames.first, let last = frames.last else {
        return 0
      }
      return max(0, last.timestampMs - first.timestampMs)
    }
  }

  func append(frame: UIImage, timestampMs: Int64 = Clocks.nowMs()) {
    queue.async {
      self.frames.append(BufferedFrame(timestampMs: timestampMs, image: frame))
      self.evictOldFramesLocked(referenceTimestampMs: timestampMs)
    }
  }

  func clear() {
    queue.async {
      self.frames.removeAll(keepingCapacity: false)
    }
  }

  func exportInterval(
    startTimestampMs: Int64,
    endTimestampMs: Int64,
    outputURL: URL? = nil,
    bitrate: Int = 2_000_000
  ) async throws -> RollingVideoExportResult {
    guard startTimestampMs <= endTimestampMs else {
      throw RollingVideoBufferError.invalidInterval(startMs: startTimestampMs, endMs: endTimestampMs)
    }

    let exportFrames: [BufferedFrame] = queue.sync {
      frames.filter { $0.timestampMs >= startTimestampMs && $0.timestampMs <= endTimestampMs }
    }

    guard !exportFrames.isEmpty else {
      throw RollingVideoBufferError.noFramesInInterval(startMs: startTimestampMs, endMs: endTimestampMs)
    }

    let resolvedURL = outputURL ?? Self.makeDefaultOutputURL()

    return try await withCheckedThrowingContinuation { continuation in
      workerQueue.async {
        do {
          let result = try Self.writeMP4(
            frames: exportFrames,
            startTimestampMs: startTimestampMs,
            endTimestampMs: endTimestampMs,
            outputURL: resolvedURL,
            bitrate: bitrate
          )
          continuation.resume(returning: result)
        } catch {
          continuation.resume(throwing: error)
        }
      }
    }
  }

  private func evictOldFramesLocked(referenceTimestampMs: Int64) {
    let minAllowedTimestampMs = referenceTimestampMs - maxDurationMs

    while let first = frames.first, first.timestampMs < minAllowedTimestampMs {
      frames.removeFirst()
    }
  }

  private static func writeMP4(
    frames: [BufferedFrame],
    startTimestampMs: Int64,
    endTimestampMs: Int64,
    outputURL: URL,
    bitrate: Int
  ) throws -> RollingVideoExportResult {
    if FileManager.default.fileExists(atPath: outputURL.path) {
      try? FileManager.default.removeItem(at: outputURL)
    }

    guard let firstSize = frames.first?.image.pixelSize else {
      throw RollingVideoBufferError.noFramesInInterval(startMs: startTimestampMs, endMs: endTimestampMs)
    }

    guard let writer = try? AVAssetWriter(outputURL: outputURL, fileType: .mp4) else {
      throw RollingVideoBufferError.unableToCreateWriter
    }

    let compressionProps: [String: Any] = [
      AVVideoAverageBitRateKey: bitrate,
      AVVideoExpectedSourceFrameRateKey: 24,
      AVVideoMaxKeyFrameIntervalKey: 24,
      AVVideoProfileLevelKey: AVVideoProfileLevelH264MainAutoLevel
    ]

    let settings: [String: Any] = [
      AVVideoCodecKey: AVVideoCodecType.h264,
      AVVideoWidthKey: Int(firstSize.width),
      AVVideoHeightKey: Int(firstSize.height),
      AVVideoCompressionPropertiesKey: compressionProps
    ]

    let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
    input.expectsMediaDataInRealTime = false

    let attributes: [String: Any] = [
      kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA),
      kCVPixelBufferWidthKey as String: Int(firstSize.width),
      kCVPixelBufferHeightKey as String: Int(firstSize.height),
      kCVPixelBufferCGImageCompatibilityKey as String: true,
      kCVPixelBufferCGBitmapContextCompatibilityKey as String: true
    ]

    let adaptor = AVAssetWriterInputPixelBufferAdaptor(
      assetWriterInput: input,
      sourcePixelBufferAttributes: attributes
    )

    guard writer.canAdd(input) else {
      throw RollingVideoBufferError.unableToCreateWriterInput
    }
    writer.add(input)

    guard writer.startWriting() else {
      throw RollingVideoBufferError.writerFailed(reason: writer.error?.localizedDescription ?? "startWriting failed")
    }
    writer.startSession(atSourceTime: .zero)

    var lastPresentationTime = CMTime.zero
    var appendedFrames = 0

    for frame in frames {
      while !input.isReadyForMoreMediaData {
        Thread.sleep(forTimeInterval: 0.002)
      }

      guard let pixelBuffer = makePixelBuffer(
        from: frame.image,
        width: Int(firstSize.width),
        height: Int(firstSize.height),
        pool: adaptor.pixelBufferPool
      ) else {
        throw RollingVideoBufferError.unableToCreatePixelBuffer
      }

      let rawMs = max(0, frame.timestampMs - startTimestampMs)
      var presentationTime = CMTime(value: rawMs, timescale: 1000)
      if presentationTime <= lastPresentationTime {
        presentationTime = CMTimeAdd(lastPresentationTime, CMTime(value: 1, timescale: 1000))
      }

      guard adaptor.append(pixelBuffer, withPresentationTime: presentationTime) else {
        let reason = writer.error?.localizedDescription ?? "append returned false"
        throw RollingVideoBufferError.appendFailed(reason: reason)
      }

      lastPresentationTime = presentationTime
      appendedFrames += 1
    }

    if let lastFrame = frames.last {
      let targetDurationMs = max(33, endTimestampMs - startTimestampMs)
      let targetTime = CMTime(value: targetDurationMs, timescale: 1000)
      if targetTime > lastPresentationTime {
        while !input.isReadyForMoreMediaData {
          Thread.sleep(forTimeInterval: 0.002)
        }

        if let pixelBuffer = makePixelBuffer(
          from: lastFrame.image,
          width: Int(firstSize.width),
          height: Int(firstSize.height),
          pool: adaptor.pixelBufferPool
        ) {
          _ = adaptor.append(pixelBuffer, withPresentationTime: targetTime)
          appendedFrames += 1
        }
      }
    }

    input.markAsFinished()
    let semaphore = DispatchSemaphore(value: 0)
    writer.finishWriting {
      semaphore.signal()
    }
    semaphore.wait()

    guard writer.status == .completed else {
      throw RollingVideoBufferError.writerFailed(reason: writer.error?.localizedDescription ?? "finishWriting did not complete")
    }

    let bytes = (try? FileManager.default.attributesOfItem(atPath: outputURL.path)[.size] as? NSNumber)?.int64Value ?? 0

    return RollingVideoExportResult(
      outputURL: outputURL,
      frameCount: appendedFrames,
      durationMs: max(0, endTimestampMs - startTimestampMs),
      bytesWritten: bytes
    )
  }

  private static func makePixelBuffer(
    from image: UIImage,
    width: Int,
    height: Int,
    pool: CVPixelBufferPool?
  ) -> CVPixelBuffer? {
    guard let cgImage = image.cgImage else {
      return nil
    }

    var maybePixelBuffer: CVPixelBuffer?
    let creationStatus: CVReturn

    if let pool {
      creationStatus = CVPixelBufferPoolCreatePixelBuffer(nil, pool, &maybePixelBuffer)
    } else {
      let attrs: [String: Any] = [
        kCVPixelBufferCGImageCompatibilityKey as String: true,
        kCVPixelBufferCGBitmapContextCompatibilityKey as String: true,
        kCVPixelBufferWidthKey as String: width,
        kCVPixelBufferHeightKey as String: height,
        kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA)
      ]
      creationStatus = CVPixelBufferCreate(
        kCFAllocatorDefault,
        width,
        height,
        kCVPixelFormatType_32BGRA,
        attrs as CFDictionary,
        &maybePixelBuffer
      )
    }

    guard creationStatus == kCVReturnSuccess, let pixelBuffer = maybePixelBuffer else {
      return nil
    }

    CVPixelBufferLockBaseAddress(pixelBuffer, [])
    defer {
      CVPixelBufferUnlockBaseAddress(pixelBuffer, [])
    }

    guard let baseAddress = CVPixelBufferGetBaseAddress(pixelBuffer) else {
      return nil
    }

    let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
    let colorSpace = CGColorSpaceCreateDeviceRGB()
    let bitmapInfo = CGImageAlphaInfo.noneSkipFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue

    guard let context = CGContext(
      data: baseAddress,
      width: width,
      height: height,
      bitsPerComponent: 8,
      bytesPerRow: bytesPerRow,
      space: colorSpace,
      bitmapInfo: bitmapInfo
    ) else {
      return nil
    }

    context.clear(CGRect(x: 0, y: 0, width: width, height: height))
    context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))

    return pixelBuffer
  }

  private static func makeDefaultOutputURL() -> URL {
    FileManager.default.temporaryDirectory
      .appendingPathComponent("query_video_\(UUID().uuidString)")
      .appendingPathExtension("mp4")
  }
}

private extension UIImage {
  var pixelSize: CGSize? {
    if let cgImage {
      return CGSize(width: cgImage.width, height: cgImage.height)
    }

    let width = size.width * scale
    let height = size.height * scale
    guard width > 0, height > 0 else {
      return nil
    }
    return CGSize(width: width, height: height)
  }
}

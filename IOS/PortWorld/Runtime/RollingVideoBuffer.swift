import AVFoundation
import Foundation
import OSLog
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

actor RollingVideoBuffer: RollingVideoBufferProtocol {
  private struct BufferedFrame {
    let timestampMs: Int64
    let image: UIImage
  }

  private let maxDurationMs: Int64
  private let clipsDirectoryURL: URL
  nonisolated private static let logger = Logger(subsystem: "PortWorld", category: "RollingVideoBuffer")

  private var frames: [BufferedFrame] = []
  private var managedTempOutputs: Set<URL> = []

  init(maxDurationMs: Int64 = 30_000) {
    self.maxDurationMs = max(1_000, maxDurationMs)
    self.clipsDirectoryURL = Self.makeClipsDirectoryURL()
    Self.prepareClipsDirectoryAndSweep(at: clipsDirectoryURL)
  }

  deinit {
    Self.cleanupFiles(Array(managedTempOutputs))
  }

  var bufferedFrameCount: Int {
    frames.count
  }

  var bufferedDurationMs: Int64 {
    guard let first = frames.first, let last = frames.last else {
      return 0
    }
    return max(0, last.timestampMs - first.timestampMs)
  }

  func append(frame: UIImage, timestampMs: Int64) {
    frames.append(BufferedFrame(timestampMs: timestampMs, image: frame))
    evictOldFramesLocked(referenceTimestampMs: timestampMs)
  }

  func clear() {
    frames.removeAll(keepingCapacity: false)
    let tempOutputs = Array(managedTempOutputs)
    managedTempOutputs.removeAll()
    Self.cleanupFiles(tempOutputs)
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
    try Task.checkCancellation()

    let exportFrames = frames.filter { $0.timestampMs >= startTimestampMs && $0.timestampMs <= endTimestampMs }

    guard !exportFrames.isEmpty else {
      throw RollingVideoBufferError.noFramesInInterval(startMs: startTimestampMs, endMs: endTimestampMs)
    }

    let resolvedURL = outputURL ?? Self.makeDefaultOutputURL(clipsDirectoryURL: clipsDirectoryURL)
    if Self.isManagedTemporaryOutput(url: resolvedURL, clipsDirectoryURL: clipsDirectoryURL) {
      managedTempOutputs.insert(resolvedURL)
    }
    try Task.checkCancellation()

    return try await Self.writeMP4(
      frames: exportFrames,
      startTimestampMs: startTimestampMs,
      endTimestampMs: endTimestampMs,
      outputURL: resolvedURL,
      bitrate: bitrate
    )
  }

  private func evictOldFramesLocked(referenceTimestampMs: Int64) {
    let minAllowedTimestampMs = referenceTimestampMs - maxDurationMs

    while let first = frames.first, first.timestampMs < minAllowedTimestampMs {
      frames.removeFirst()
    }
  }

  nonisolated private static func writeMP4(
    frames: [BufferedFrame],
    startTimestampMs: Int64,
    endTimestampMs: Int64,
    outputURL: URL,
    bitrate: Int
  ) async throws -> RollingVideoExportResult {
    try Task.checkCancellation()

    if FileManager.default.fileExists(atPath: outputURL.path) {
      do {
        try FileManager.default.removeItem(at: outputURL)
      } catch {
        throw RollingVideoBufferError.writerFailed(
          reason: "Failed to remove existing file at output URL: \(error.localizedDescription)"
        )
      }
    }

    guard let firstSize = frames.first?.image.pixelSize else {
      throw RollingVideoBufferError.noFramesInInterval(startMs: startTimestampMs, endMs: endTimestampMs)
    }

    let writer: AVAssetWriter
    do {
      writer = try AVAssetWriter(outputURL: outputURL, fileType: .mp4)
    } catch {
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
        try Task.checkCancellation()
        try await Task.sleep(for: .milliseconds(2))
      }
      try Task.checkCancellation()

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
          try Task.checkCancellation()
          try await Task.sleep(for: .milliseconds(2))
        }
        try Task.checkCancellation()

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
    try Task.checkCancellation()
    await withCheckedContinuation { continuation in
      writer.finishWriting {
        continuation.resume()
      }
    }
    try Task.checkCancellation()

    guard writer.status == .completed else {
      throw RollingVideoBufferError.writerFailed(reason: writer.error?.localizedDescription ?? "finishWriting did not complete")
    }

    let bytes: Int64
    do {
      let attributes = try FileManager.default.attributesOfItem(atPath: outputURL.path)
      bytes = (attributes[.size] as? NSNumber)?.int64Value ?? 0
    } catch {
      bytes = 0
    }

    return RollingVideoExportResult(
      outputURL: outputURL,
      frameCount: appendedFrames,
      durationMs: max(0, endTimestampMs - startTimestampMs),
      bytesWritten: bytes
    )
  }

  nonisolated private static func makePixelBuffer(
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

  nonisolated private static func makeDefaultOutputURL(clipsDirectoryURL: URL) -> URL {
    clipsDirectoryURL
      .appendingPathComponent("query_video_\(UUID().uuidString)")
      .appendingPathExtension("mp4")
  }

  nonisolated private static func makeClipsDirectoryURL() -> URL {
    FileManager.default.temporaryDirectory
      .appendingPathComponent("clips", isDirectory: true)
  }

  nonisolated private static func prepareClipsDirectoryAndSweep(at clipsDirectoryURL: URL) {
    let fileManager = FileManager.default
    do {
      try fileManager.createDirectory(at: clipsDirectoryURL, withIntermediateDirectories: true)
      let urls = try fileManager.contentsOfDirectory(
        at: clipsDirectoryURL,
        includingPropertiesForKeys: nil,
        options: [.skipsHiddenFiles]
      )
      for url in urls where url.pathExtension.lowercased() == "mp4" {
        do {
          try fileManager.removeItem(at: url)
        } catch {
#if DEBUG
          logger.debug(
            "Failed to remove stale clip \(url.lastPathComponent, privacy: .public): \(error.localizedDescription, privacy: .public)"
          )
#endif
        }
      }
    } catch {
#if DEBUG
      logger.debug(
        "Failed to prepare clips directory: \(error.localizedDescription, privacy: .public)"
      )
#endif
    }
  }

  nonisolated private static func cleanupFiles(_ urls: [URL]) {
    let fileManager = FileManager.default
    for url in urls {
      do {
        if fileManager.fileExists(atPath: url.path) {
          try fileManager.removeItem(at: url)
        }
      } catch {
#if DEBUG
        logger.debug(
          "Failed to clean up clip \(url.lastPathComponent, privacy: .public): \(error.localizedDescription, privacy: .public)"
        )
#endif
      }
    }
  }

  nonisolated private static func isManagedTemporaryOutput(url: URL, clipsDirectoryURL: URL) -> Bool {
    let standardizedURL = url.standardizedFileURL.resolvingSymlinksInPath()
    let standardizedClipsDirectory = clipsDirectoryURL.standardizedFileURL.resolvingSymlinksInPath()
    return standardizedURL.path.hasPrefix(standardizedClipsDirectory.path + "/")
      || standardizedURL.path == standardizedClipsDirectory.path
  }
}

private extension UIImage {
  nonisolated var pixelSize: CGSize? {
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

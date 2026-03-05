import SnapshotTesting
import SwiftUI
import UIKit
import XCTest
@testable import PortWorld

@MainActor
final class SnapshotTests: XCTestCase {
  private let runtimeSnapshotsDirectory = FileManager.default.temporaryDirectory
    .appendingPathComponent("PortWorldTests.__Snapshots__.SnapshotTests", isDirectory: true)

  private var shouldRecordSnapshots: Bool {
    guard ProcessInfo.processInfo.environment["RECORD_SNAPSHOTS"] == "1" else {
      return false
    }
    return isSnapshotDirectoryWritable
  }

  private var snapshotDirectoryPath: String {
    runtimeSnapshotsDirectory.path
  }

  private var isSnapshotDirectoryWritable: Bool {
    let fileManager = FileManager.default
    if fileManager.fileExists(atPath: snapshotDirectoryPath) {
      return fileManager.isWritableFile(atPath: snapshotDirectoryPath)
    }
    return fileManager.isWritableFile(atPath: runtimeSnapshotsDirectory.deletingLastPathComponent().path)
  }

  override func setUpWithError() throws {
    try super.setUpWithError()
    try prepareRuntimeSnapshotDirectory()
  }

  func testCircleButton_iconOnly_lightAndDark() {
    assertLightAndDarkSnapshot(
      name: "CircleButton.iconOnly",
      size: CGSize(width: 140, height: 140)
    ) {
      CircleButton(icon: "camera.fill", text: nil, action: {})
    }
  }

  func testCircleButton_withText_lightAndDark() {
    assertLightAndDarkSnapshot(
      name: "CircleButton.withText",
      size: CGSize(width: 140, height: 140)
    ) {
      CircleButton(icon: "waveform", text: "Wake", action: {})
    }
  }

  func testCustomButton_primaryAndDestructive_lightAndDark() {
    assertLightAndDarkSnapshot(
      name: "CustomButton.styles",
      size: CGSize(width: 360, height: 190)
    ) {
      VStack(spacing: 16) {
        CustomButton(title: "Activate assistant", style: .primary, isDisabled: false, action: {})
        CustomButton(title: "Deactivate assistant", style: .destructive, isDisabled: false, action: {})
        CustomButton(title: "Disabled action", style: .primary, isDisabled: true, action: {})
      }
    }
  }

  func testTipRow_variants_lightAndDark() {
    assertLightAndDarkSnapshot(
      name: "TipRowView.variants",
      size: CGSize(width: 390, height: 220)
    ) {
      VStack(alignment: .leading, spacing: 18) {
        TipRowView(
          resource: .cameraAccessIcon,
          title: "Microphone Access",
          text: "Allow microphone to stream your voice in live sessions.",
          iconColor: .appPrimary,
          titleColor: .primary,
          textColor: .secondary
        )

        TipRowView(
          resource: .smartGlassesIcon,
          text: "Keep glasses connected and internet available for realtime responses.",
          iconColor: .appPrimary,
          titleColor: .primary,
          textColor: .secondary
        )
      }
      .padding(.vertical, 8)
    }
  }

  func testPhotoPreviewView_lightAndDark() {
    let photo = makeSamplePhoto()
    assertLightAndDarkSnapshot(
      name: "PhotoPreviewView.default",
      size: CGSize(width: 390, height: 844)
    ) {
      PhotoPreviewView(photo: photo, onDismiss: {})
    }
  }

  private func assertLightAndDarkSnapshot<V: View>(
    name: String,
    size: CGSize,
    @ViewBuilder content: () -> V,
    file: StaticString = #filePath,
    testName: String = #function,
    line: UInt = #line
  ) {
    let wrapped = content()
      .padding(16)
      .frame(width: size.width, height: size.height, alignment: .topLeading)
      .background(Color(.systemBackground))

    let host = UIHostingController(rootView: wrapped)
    host.view.frame = CGRect(origin: .zero, size: size)

    assertSnapshotAtKnownDirectory(
      of: host.view,
      as: .image(size: size, traits: .init(userInterfaceStyle: .light)),
      named: "\(name).light",
      record: shouldRecordSnapshots,
      file: file,
      testName: testName,
      line: line
    )

    assertSnapshotAtKnownDirectory(
      of: host.view,
      as: .image(size: size, traits: .init(userInterfaceStyle: .dark)),
      named: "\(name).dark",
      record: shouldRecordSnapshots,
      file: file,
      testName: testName,
      line: line
    )
  }

  private func assertSnapshotAtKnownDirectory<Value, Format>(
    of value: @autoclosure () throws -> Value,
    as snapshotting: Snapshotting<Value, Format>,
    named name: String,
    record: Bool,
    file: StaticString,
    testName: String,
    line: UInt
  ) {
    guard assertSnapshotDirectoryAccessible(file: file, line: line) else { return }

    do {
      let failure = verifySnapshot(
        of: try value(),
        as: snapshotting,
        named: name,
        record: record,
        snapshotDirectory: snapshotDirectoryPath,
        file: file,
        testName: testName,
        line: line
      )
      if let failure {
        XCTFail(failure, file: file, line: line)
      }
    } catch {
      XCTFail("Failed to evaluate snapshot value: \(error.localizedDescription)", file: file, line: line)
    }
  }

  private func assertSnapshotDirectoryAccessible(file: StaticString, line: UInt) -> Bool {
    let fileManager = FileManager.default
    let directoryURL = URL(fileURLWithPath: snapshotDirectoryPath, isDirectory: true)
    do {
      try fileManager.createDirectory(at: directoryURL, withIntermediateDirectories: true)
      let probeURL = directoryURL.appendingPathComponent(".write_probe_\(UUID().uuidString)")
      try Data("ok".utf8).write(to: probeURL, options: .atomic)
      try fileManager.removeItem(at: probeURL)
      return true
    } catch {
      XCTFail(
        "Snapshot directory is not writable at path \(directoryURL.path): \(error.localizedDescription)",
        file: file,
        line: line
      )
      return false
    }
  }

  private func prepareRuntimeSnapshotDirectory() throws {
    let fileManager = FileManager.default
    try fileManager.createDirectory(
      at: runtimeSnapshotsDirectory,
      withIntermediateDirectories: true
    )

    guard let resourceDirectory = Bundle(for: Self.self).resourceURL else { return }
    let resourceFiles = try fileManager.contentsOfDirectory(
      at: resourceDirectory,
      includingPropertiesForKeys: nil
    )

    for sourceURL in resourceFiles {
      let fileName = sourceURL.lastPathComponent
      let shouldCopy =
        fileName == "BASELINE_PENDING.md"
        || (fileName.hasPrefix("test") && fileName.hasSuffix(".png"))
      guard shouldCopy else { continue }

      let destinationURL = runtimeSnapshotsDirectory.appendingPathComponent(fileName, isDirectory: false)
      if fileManager.fileExists(atPath: destinationURL.path) {
        continue
      }
      try fileManager.copyItem(at: sourceURL, to: destinationURL)
    }
  }

  private func makeSamplePhoto() -> UIImage {
    let size = CGSize(width: 1200, height: 900)
    let renderer = UIGraphicsImageRenderer(size: size)
    return renderer.image { context in
      let bounds = CGRect(origin: .zero, size: size)
      let colors = [UIColor.systemBlue.cgColor, UIColor.systemTeal.cgColor] as CFArray
      let space = CGColorSpaceCreateDeviceRGB()
      let locations: [CGFloat] = [0, 1]
      if let gradient = CGGradient(colorsSpace: space, colors: colors, locations: locations) {
        context.cgContext.drawLinearGradient(
          gradient,
          start: CGPoint(x: 0, y: 0),
          end: CGPoint(x: size.width, y: size.height),
          options: []
        )
      }

      let inset = bounds.insetBy(dx: 120, dy: 120)
      context.cgContext.setStrokeColor(UIColor.white.withAlphaComponent(0.75).cgColor)
      context.cgContext.setLineWidth(14)
      context.cgContext.stroke(inset)
    }
  }
}

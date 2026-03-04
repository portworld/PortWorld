import UIKit
import XCTest
@testable import PortWorld

@MainActor
final class VisionFrameUploaderTests: XCTestCase {

  override func setUp() {
    super.setUp()
    TestURLProtocolStub.reset()
  }

  override func tearDown() {
    TestURLProtocolStub.reset()
    super.tearDown()
  }

  func testUploadIntervalGatingDelaysSecondUploadUntilNextTick() async throws {
    TestURLProtocolStub.setHandler { _ in
      .success(statusCode: 200)
    }

    let uploader = VisionFrameUploader(
      endpointURL: URL(string: "https://example.invalid/query/frame")!,
      defaultHeaders: [:],
      sessionIDProvider: { "session-123" },
      uploadIntervalMs: 220,
      urlSession: TestURLProtocolStub.makeEphemeralSession()
    )

    await uploader.start()
    await uploader.submitLatestFrame(makeImage(), captureTimestampMs: 100)

    try await AsyncTestWait.until(timeout: 1.5) {
      TestURLProtocolStub.requestCount() == 1
    }

    await uploader.submitLatestFrame(makeImage(), captureTimestampMs: 200)

    try await Task.sleep(nanoseconds: 120_000_000)
    XCTAssertEqual(TestURLProtocolStub.requestCount(), 1)

    try await AsyncTestWait.until(timeout: 1.5) {
      TestURLProtocolStub.requestCount() == 2
    }

    await uploader.stop()
  }

  func testConsumeFrameDropCountTracksReplacedPendingFramesAndResetsDelta() async {
    let uploader = VisionFrameUploader(
      endpointURL: URL(string: "https://example.invalid/query/frame")!,
      defaultHeaders: [:],
      sessionIDProvider: { "session-123" },
      uploadIntervalMs: 500,
      urlSession: TestURLProtocolStub.makeEphemeralSession()
    )

    await uploader.submitLatestFrame(makeImage(), captureTimestampMs: 1)
    await uploader.submitLatestFrame(makeImage(), captureTimestampMs: 2)
    await uploader.submitLatestFrame(makeImage(), captureTimestampMs: 3)

    let firstRead = await uploader.consumeFrameDropCount()
    let secondRead = await uploader.consumeFrameDropCount()

    XCTAssertEqual(firstRead, 2)
    XCTAssertEqual(secondRead, 0)
  }

  func testRetriesHTTP503AndPublishesSuccessWithSecondAttempt() async throws {
    var callCount = 0
    TestURLProtocolStub.setHandler { _ in
      callCount += 1
      if callCount == 1 {
        return .success(statusCode: 503)
      }
      return .success(statusCode: 200)
    }

    let uploader = VisionFrameUploader(
      endpointURL: URL(string: "https://example.invalid/query/frame")!,
      defaultHeaders: [:],
      sessionIDProvider: { "session-123" },
      uploadIntervalMs: 200,
      requestTimeoutMs: 1_000,
      maxRetryCount: 1,
      baseRetryDelayMs: 100,
      maxRetryDelayMs: 100,
      urlSession: TestURLProtocolStub.makeEphemeralSession()
    )

    let resultExpectation = expectation(description: "retry result")
    var captured: VisionFrameUploadResult?

    await uploader.bindHandlers(
      sessionIDProvider: { "session-123" },
      onUploadResult: { result in
        captured = result
        resultExpectation.fulfill()
      }
    )

    await uploader.start()
    await uploader.submitLatestFrame(makeImage(), captureTimestampMs: 42)

    await fulfillment(of: [resultExpectation], timeout: 2.0)

    XCTAssertEqual(TestURLProtocolStub.requestCount(), 2)
    XCTAssertEqual(captured?.success, true)
    XCTAssertEqual(captured?.attemptCount, 2)
    XCTAssertNil(captured?.errorCode)

    await uploader.stop()
  }

  func testSubmitWhenNotRunningDoesNotUpload() async throws {
    TestURLProtocolStub.setHandler { _ in
      .success(statusCode: 200)
    }

    let uploader = VisionFrameUploader(
      endpointURL: URL(string: "https://example.invalid/query/frame")!,
      defaultHeaders: [:],
      sessionIDProvider: { "session-123" },
      uploadIntervalMs: 100,
      urlSession: TestURLProtocolStub.makeEphemeralSession()
    )

    await uploader.submitLatestFrame(makeImage(), captureTimestampMs: 10)
    try await Task.sleep(nanoseconds: 250_000_000)

    XCTAssertEqual(TestURLProtocolStub.requestCount(), 0)
  }

  private func makeImage() -> UIImage {
    UIGraphicsImageRenderer(size: CGSize(width: 4, height: 4)).image { context in
      UIColor.red.setFill()
      context.cgContext.fill(CGRect(x: 0, y: 0, width: 4, height: 4))
    }
  }
}

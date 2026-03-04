import XCTest
@testable import PortWorld

final class AssistantPlaybackEngineTests: XCTestCase {

  func testPendingBufferAccountingOnScheduleAndDrain() {
    var state = AssistantPlaybackQueueState()

    state.recordScheduledBuffer(durationMs: 125, nowMs: 1_000)
    state.recordScheduledBuffer(durationMs: 250, nowMs: 1_100)

    XCTAssertEqual(state.pendingBufferCount, 2)
    XCTAssertEqual(state.pendingBufferDurationMs, 375, accuracy: 0.001)
    XCTAssertEqual(state.lastBufferScheduledAtMs, 1_100)

    state.recordBufferDrained(durationMs: 125, nowMs: 1_200)

    XCTAssertEqual(state.pendingBufferCount, 1)
    XCTAssertEqual(state.pendingBufferDurationMs, 250, accuracy: 0.001)
    XCTAssertEqual(state.lastBufferDrainedAtMs, 1_200)
  }

  func testPendingBufferAccountingClampsOnDrainUnderflow() {
    var state = AssistantPlaybackQueueState()

    state.recordBufferDrained(durationMs: 100, nowMs: 1_000)

    XCTAssertEqual(state.pendingBufferCount, 0)
    XCTAssertEqual(state.pendingBufferDurationMs, 0, accuracy: 0.001)
    XCTAssertEqual(state.lastBufferDrainedAtMs, 1_000)
  }

  func testResetForCancelResponseClearsPendingStateOnly() {
    var state = AssistantPlaybackQueueState()

    state.recordScheduledBuffer(durationMs: 100, nowMs: 1_000)
    _ = state.shouldAttemptRecovery(nowMs: 1_200, thresholdMs: 300, maxConsecutiveChecks: 3)

    XCTAssertEqual(state.consecutiveStuckChecks, 1)
    XCTAssertEqual(state.lastBufferScheduledAtMs, 1_000)

    state.resetForCancelResponse()

    XCTAssertEqual(state.pendingBufferCount, 0)
    XCTAssertEqual(state.pendingBufferDurationMs, 0, accuracy: 0.001)
    XCTAssertEqual(state.consecutiveStuckChecks, 0)
    XCTAssertEqual(state.lastBufferScheduledAtMs, 1_000)
  }

  func testResetForStartResponseResetsPendingAndTimestamps() {
    var state = AssistantPlaybackQueueState()

    state.recordScheduledBuffer(durationMs: 90, nowMs: 1_000)
    state.recordBufferDrained(durationMs: 90, nowMs: 1_100)
    state.recordScheduledBuffer(durationMs: 110, nowMs: 1_200)

    state.resetForStartResponse(nowMs: 2_000)

    XCTAssertEqual(state.pendingBufferCount, 0)
    XCTAssertEqual(state.pendingBufferDurationMs, 0, accuracy: 0.001)
    XCTAssertEqual(state.consecutiveStuckChecks, 0)
    XCTAssertEqual(state.lastBufferScheduledAtMs, 0)
    XCTAssertEqual(state.lastBufferDrainedAtMs, 2_000)
  }

  func testStuckWatchdogTriggersRecoveryAfterConsecutiveChecks() {
    var state = AssistantPlaybackQueueState()
    state.recordScheduledBuffer(durationMs: 100, nowMs: 1_000)

    XCTAssertFalse(state.shouldAttemptRecovery(nowMs: 1_200, thresholdMs: 300, maxConsecutiveChecks: 3))
    XCTAssertFalse(state.shouldAttemptRecovery(nowMs: 1_300, thresholdMs: 300, maxConsecutiveChecks: 3))
    XCTAssertTrue(state.shouldAttemptRecovery(nowMs: 1_400, thresholdMs: 300, maxConsecutiveChecks: 3))
    XCTAssertEqual(state.consecutiveStuckChecks, 3)
  }

  func testStuckWatchdogResetsCounterWhenDrainIsRecent() {
    var state = AssistantPlaybackQueueState()

    state.recordScheduledBuffer(durationMs: 100, nowMs: 1_000)
    state.recordScheduledBuffer(durationMs: 100, nowMs: 1_100)

    XCTAssertFalse(state.shouldAttemptRecovery(nowMs: 1_200, thresholdMs: 300, maxConsecutiveChecks: 3))
    XCTAssertEqual(state.consecutiveStuckChecks, 1)

    state.recordBufferDrained(durationMs: 100, nowMs: 1_250)

    XCTAssertFalse(state.shouldAttemptRecovery(nowMs: 1_300, thresholdMs: 300, maxConsecutiveChecks: 3))
    XCTAssertEqual(state.consecutiveStuckChecks, 0)
  }
}

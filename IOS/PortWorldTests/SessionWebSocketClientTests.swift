import XCTest
@testable import PortWorld

final class SessionWebSocketClientTests: XCTestCase {

  func testSetNetworkUnavailablePreventsConnectAndKeepsDisconnectedState() async {
    let client = SessionWebSocketClient(
      url: URL(string: "wss://example.invalid/ws")!,
      baseReconnectDelayMs: 50,
      maxReconnectDelayMs: 100,
      pingIntervalMs: 1_000
    )

    await client.setNetworkAvailable(false)
    await client.connect()

    let state = await client.currentState()
    let reconnectAttempts = await client.reconnectAttemptCount()

    XCTAssertEqual(state, .disconnected)
    XCTAssertEqual(reconnectAttempts, 0)

    await client.disconnect()
  }

  func testRestoringNetworkAvailabilityAllowsReconnectFlowIntent() async {
    let client = SessionWebSocketClient(
      url: URL(string: "wss://example.invalid/ws")!,
      baseReconnectDelayMs: 50,
      maxReconnectDelayMs: 100,
      pingIntervalMs: 1_000
    )

    await client.setNetworkAvailable(false)
    await client.connect()
    XCTAssertEqual(await client.currentState(), .disconnected)

    await client.setNetworkAvailable(true)

    let stateAfterRestore = await client.currentState()
    switch stateAfterRestore {
    case .connecting, .connected, .reconnecting:
      XCTAssertTrue(true)
    case .idle, .disconnected:
      XCTFail("Expected reconnect flow intent after restoring network, got \(stateAfterRestore)")
    }

    await client.disconnect()
  }
}

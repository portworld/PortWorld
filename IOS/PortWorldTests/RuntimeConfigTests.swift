import XCTest
@testable import PortWorld

final class RuntimeConfigTests: XCTestCase {
  override func setUpWithError() throws {
    try super.setUpWithError()
    try RuntimeConfig.clearStoredAPIKey()
  }

  override func tearDownWithError() throws {
    try RuntimeConfig.clearStoredAPIKey()
    try super.tearDownWithError()
  }

  func testLoadDefaultsRemainSane() {
    let defaults = makeIsolatedDefaults()

    let config = RuntimeConfig.load(from: Bundle(for: Self.self), userDefaults: defaults)

    XCTAssertGreaterThanOrEqual(config.silenceTimeoutMs, 250)
    XCTAssertFalse(config.wakePhrase.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
  }

  func testLoadUsesDefaultURLsWhenConfigKeysAreMissing() throws {
    let defaults = makeIsolatedDefaults()
    let bundle = try makeBundle(infoPlist: [:])

    let config = RuntimeConfig.load(from: bundle, userDefaults: defaults)

    XCTAssertEqual(config.backendBaseURL.absoluteString, "http://127.0.0.1:8080")
    XCTAssertEqual(config.webSocketURL.absoluteString, "ws://127.0.0.1:8080/ws/session")
    XCTAssertEqual(config.visionFrameURL.absoluteString, "http://127.0.0.1:8080/vision/frame")
    XCTAssertEqual(config.queryURL.absoluteString, "http://127.0.0.1:8080/v1/query")
    XCTAssertFalse(config.realtimeDiagnosticsEnabled)
  }

  func testLoadDerivesWebSocketURLFromBackendBaseURLScheme() throws {
    let defaults = makeIsolatedDefaults()

    let httpBundle = try makeBundle(infoPlist: [
      "SON_BACKEND_BASE_URL": "http://api.example.com:8082",
      "SON_WS_PATH": "/ws/session",
    ])
    let httpConfig = RuntimeConfig.load(from: httpBundle, userDefaults: defaults)
    XCTAssertEqual(httpConfig.webSocketURL.absoluteString, "ws://api.example.com:8082/ws/session")

    let httpsBundle = try makeBundle(infoPlist: [
      "SON_BACKEND_BASE_URL": "https://api.example.com",
      "SON_WS_PATH": "/ws/session",
    ])
    let httpsConfig = RuntimeConfig.load(from: httpsBundle, userDefaults: defaults)
    XCTAssertEqual(httpsConfig.webSocketURL.absoluteString, "wss://api.example.com/ws/session")
  }

  func testLoadPrefersExplicitEndpointURLsOverDerivedPaths() throws {
    let defaults = makeIsolatedDefaults()
    let bundle = try makeBundle(infoPlist: [
      "SON_BACKEND_BASE_URL": "https://api.example.com",
      "SON_WS_PATH": "/from-path",
      "SON_VISION_PATH": "/vision/from-path",
      "SON_QUERY_PATH": "/query/from-path",
      "SON_WS_URL": "wss://override.example.com/ws/explicit",
      "SON_VISION_URL": "https://override.example.com/vision/explicit",
      "SON_QUERY_URL": "https://override.example.com/query/explicit",
    ])

    let config = RuntimeConfig.load(from: bundle, userDefaults: defaults)

    XCTAssertEqual(config.webSocketURL.absoluteString, "wss://override.example.com/ws/explicit")
    XCTAssertEqual(config.visionFrameURL.absoluteString, "https://override.example.com/vision/explicit")
    XCTAssertEqual(config.queryURL.absoluteString, "https://override.example.com/query/explicit")
  }

  func testLoadUsesUserDefaultsOverridesForSilenceTimeoutAndWakePhrase() {
    let defaults = makeIsolatedDefaults()
    defaults.set("1200", forKey: "portworld.silenceTimeoutMs")
    defaults.set("  hey from defaults  ", forKey: "portworld.wakePhrase")

    let config = RuntimeConfig.load(from: Bundle(for: Self.self), userDefaults: defaults)

    XCTAssertEqual(config.silenceTimeoutMs, 1_200)
    XCTAssertEqual(config.wakePhrase, "hey from defaults")
  }

  func testLoadRealtimeDiagnosticsDefaultsToFalse() throws {
    let defaults = makeIsolatedDefaults()
    let bundle = try makeBundle(infoPlist: [:])

    let config = RuntimeConfig.load(from: bundle, userDefaults: defaults)

    XCTAssertFalse(config.realtimeDiagnosticsEnabled)
  }

  func testLoadRealtimeDiagnosticsUsesInfoPlistValue() throws {
    let defaults = makeIsolatedDefaults()
    let bundle = try makeBundle(infoPlist: [
      "SON_REALTIME_DIAGNOSTICS_ENABLED": true
    ])

    let config = RuntimeConfig.load(from: bundle, userDefaults: defaults)

    XCTAssertTrue(config.realtimeDiagnosticsEnabled)
  }

  func testLoadRealtimeDiagnosticsUserDefaultsOverrideTakesPrecedence() throws {
    let defaults = makeIsolatedDefaults()
    defaults.set("false", forKey: "portworld.realtimeDiagnosticsEnabled")
    let bundle = try makeBundle(infoPlist: [
      "SON_REALTIME_DIAGNOSTICS_ENABLED": true
    ])

    let config = RuntimeConfig.load(from: bundle, userDefaults: defaults)

    XCTAssertFalse(config.realtimeDiagnosticsEnabled)
  }

  func testLoadFallsBackWhenSilenceTimeoutOverrideIsMalformedOrInvalid() {
    let defaults = makeIsolatedDefaults()
    let baseline = RuntimeConfig.load(from: Bundle(for: Self.self), userDefaults: defaults)

    defaults.set("not-a-number", forKey: "portworld.silenceTimeoutMs")
    let malformed = RuntimeConfig.load(from: Bundle(for: Self.self), userDefaults: defaults)
    XCTAssertEqual(malformed.silenceTimeoutMs, baseline.silenceTimeoutMs)

    defaults.set(0, forKey: "portworld.silenceTimeoutMs")
    let invalidZero = RuntimeConfig.load(from: Bundle(for: Self.self), userDefaults: defaults)
    XCTAssertEqual(invalidZero.silenceTimeoutMs, baseline.silenceTimeoutMs)

    defaults.set(-100, forKey: "portworld.silenceTimeoutMs")
    let invalidNegative = RuntimeConfig.load(from: Bundle(for: Self.self), userDefaults: defaults)
    XCTAssertEqual(invalidNegative.silenceTimeoutMs, baseline.silenceTimeoutMs)
  }

  func testLoadFallsBackWhenWakePhraseOverrideIsMalformedOrInvalid() {
    let defaults = makeIsolatedDefaults()
    let baseline = RuntimeConfig.load(from: Bundle(for: Self.self), userDefaults: defaults)

    defaults.set("   ", forKey: "portworld.wakePhrase")
    let whitespaceOnly = RuntimeConfig.load(from: Bundle(for: Self.self), userDefaults: defaults)
    XCTAssertEqual(whitespaceOnly.wakePhrase, baseline.wakePhrase)

    defaults.set(12345, forKey: "portworld.wakePhrase")
    let nonString = RuntimeConfig.load(from: Bundle(for: Self.self), userDefaults: defaults)
    XCTAssertEqual(nonString.wakePhrase, baseline.wakePhrase)
  }

  func testRequestHeadersOmitsAPIKeyWhenEmptyOrWhitespace() {
    let configEmpty = RuntimeConfig(
      backendBaseURL: URL(string: "https://example.com")!,
      webSocketURL: URL(string: "wss://example.com/ws")!,
      visionFrameURL: URL(string: "https://example.com/vision")!,
      queryURL: URL(string: "https://example.com/query")!,
      apiKey: "",
      bearerToken: ""
    )

    let configWhitespace = RuntimeConfig(
      backendBaseURL: URL(string: "https://example.com")!,
      webSocketURL: URL(string: "wss://example.com/ws")!,
      visionFrameURL: URL(string: "https://example.com/vision")!,
      queryURL: URL(string: "https://example.com/query")!,
      apiKey: "   ",
      bearerToken: ""
    )

    XCTAssertNil(configEmpty.requestHeaders["X-API-Key"])
    XCTAssertNil(configWhitespace.requestHeaders["X-API-Key"])
  }

  func testRequestHeadersIncludesTrimmedAPIKeyWhenPresent() {
    let config = RuntimeConfig(
      backendBaseURL: URL(string: "https://example.com")!,
      webSocketURL: URL(string: "wss://example.com/ws")!,
      visionFrameURL: URL(string: "https://example.com/vision")!,
      queryURL: URL(string: "https://example.com/query")!,
      apiKey: "  test-key  ",
      bearerToken: ""
    )

    XCTAssertEqual(config.requestHeaders["X-API-Key"], "test-key")
  }

  func testBackendSummaryDoesNotIncludeLegacyQueryEndpoint() {
    let config = RuntimeConfig(
      backendBaseURL: URL(string: "https://example.com")!,
      webSocketURL: URL(string: "wss://example.com/ws")!,
      visionFrameURL: URL(string: "https://example.com/vision")!,
      queryURL: URL(string: "https://example.com/query")!,
      apiKey: "",
      bearerToken: ""
    )

    XCTAssertFalse(config.backendSummary.contains("query="))
    XCTAssertTrue(config.backendSummary.contains("base=https://example.com"))
    XCTAssertTrue(config.backendSummary.contains("ws=wss://example.com/ws"))
    XCTAssertTrue(config.backendSummary.contains("vision=https://example.com/vision"))
  }

  func testLoadBootstrapsAPIKeyFromPlistOnlyOnceOnFirstLoad() throws {
    let defaults = makeIsolatedDefaults()
    let bundle = try makeBundle(infoPlist: ["SON_API_KEY": "  seeded-key  "])

    let config = RuntimeConfig.load(from: bundle, userDefaults: defaults)

    XCTAssertEqual(config.apiKey, "seeded-key")
    XCTAssertEqual(
      defaults.object(forKey: RuntimeConfig.apiKeyBootstrapMarkerUserDefaultsKey) as? Bool,
      true
    )
  }

  func testLoadDoesNotReseedFromPlistAfterClearWhenBootstrapMarkerIsSet() throws {
    let defaults = makeIsolatedDefaults()
    let bundle = try makeBundle(infoPlist: ["SON_API_KEY": "seeded-key"])

    let firstLoad = RuntimeConfig.load(from: bundle, userDefaults: defaults)
    XCTAssertEqual(firstLoad.apiKey, "seeded-key")
    XCTAssertEqual(
      defaults.object(forKey: RuntimeConfig.apiKeyBootstrapMarkerUserDefaultsKey) as? Bool,
      true
    )

    try RuntimeConfig.clearStoredAPIKey()

    let secondLoad = RuntimeConfig.load(from: bundle, userDefaults: defaults)
    XCTAssertEqual(secondLoad.apiKey, "")
    XCTAssertEqual(
      defaults.object(forKey: RuntimeConfig.apiKeyBootstrapMarkerUserDefaultsKey) as? Bool,
      true
    )
  }

  private func makeIsolatedDefaults() -> UserDefaults {
    let suiteName = "RuntimeConfigTests.\(UUID().uuidString)"
    let defaults = UserDefaults(suiteName: suiteName)!
    defaults.removePersistentDomain(forName: suiteName)
    return defaults
  }

  private func makeBundle(infoPlist: [String: Any]) throws -> Bundle {
    let fileManager = FileManager.default
    let tempRoot = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
      .appendingPathComponent("RuntimeConfigTests-\(UUID().uuidString)", isDirectory: true)
    let bundleURL = tempRoot.appendingPathComponent("Fixture.bundle", isDirectory: true)

    try fileManager.createDirectory(at: bundleURL, withIntermediateDirectories: true)
    let plistURL = bundleURL.appendingPathComponent("Info.plist")
    let plistData = try PropertyListSerialization.data(
      fromPropertyList: infoPlist,
      format: .xml,
      options: 0
    )
    try plistData.write(to: plistURL, options: .atomic)

    guard let bundle = Bundle(url: bundleURL) else {
      throw NSError(domain: "RuntimeConfigTests", code: 1)
    }
    return bundle
  }
}

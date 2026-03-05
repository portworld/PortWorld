import AVFAudio
import XCTest
@testable import PortWorld

@MainActor
final class AudioCollectionManagerTests: XCTestCase {
    func testIdleToRecordingToIdle() async {
        let audioSession = MockAudioSession()
        audioSession.permissionGranted = true
        audioSession.hasBluetoothInput = true
        let observerCenter = MockObserverCenter()
        let tapController = MockTapController()
        let processor = MockAudioChunkProcessor()

        var manager: AudioCollectionManager? = AudioCollectionManager(
            audioSessionClient: audioSession,
            observerCenter: observerCenter,
            tapControllerFactory: { _ in tapController },
            processor: processor
        )

        await manager?.prepareAudioSession()
        XCTAssertEqual(manager?.state, .idle)
        XCTAssertTrue(manager?.isAudioSessionReady == true)

        await manager?.start()
        XCTAssertEqual(manager?.state, .recording)

        await manager?.stop()
        XCTAssertEqual(manager?.state, .idle)

        XCTAssertEqual(tapController.installTapCallCount, 1)
        XCTAssertEqual(tapController.removeTapCallCount, 1)
        XCTAssertEqual(processor.stopAndFlushCallCount, 1)
        XCTAssertEqual(observerCenter.addObserverCallCount, 2)

        manager = nil
        XCTAssertEqual(observerCenter.removeObserverCallCount, 2)
    }

    func testFailedStateStopResetsErrorAndReturnsToIdle() async {
        let manager = AudioCollectionManager(
            audioSessionClient: MockAudioSession(),
            observerCenter: MockObserverCenter(),
            tapControllerFactory: { _ in MockTapController() },
            processor: MockAudioChunkProcessor()
        )

        await manager.start()

        guard case .failed = manager.state else {
            XCTFail("Expected failed state before stop")
            return
        }
        XCTAssertNotNil(manager.stats.lastError)

        await manager.stop()

        XCTAssertEqual(manager.state, .idle)
        XCTAssertNil(manager.stats.lastError)
    }

    func testRealtimeSinkBypassesChunkProcessorEnqueue() async {
        let audioSession = MockAudioSession()
        audioSession.permissionGranted = true
        audioSession.hasBluetoothInput = true
        let tapController = MockTapController()
        let processor = MockAudioChunkProcessor()
        let manager = AudioCollectionManager(
            audioSessionClient: audioSession,
            observerCenter: MockObserverCenter(),
            tapControllerFactory: { _ in tapController },
            processor: processor
        )

        let sinkExpectation = expectation(description: "realtime sink receives payload")
        let payloadStore = ReceivedPayloadStore()
        manager.onRealtimePCMFrame = { payload, _ in
            Task {
                await payloadStore.set(payload)
                sinkExpectation.fulfill()
            }
        }

        await manager.prepareAudioSession()
        await manager.start()
        XCTAssertEqual(manager.state, .recording)

        let buffer = makePCMBuffer(samples: [0.1, -0.2, 0.3, -0.4])
        tapController.emit(buffer: buffer)

        await fulfillment(of: [sinkExpectation], timeout: 1.0)

        let receivedPayload = await payloadStore.get()
        XCTAssertNotNil(receivedPayload)
        XCTAssertEqual(processor.enqueueCallCount, 0)

        await manager.stop()
    }

    private func makePCMBuffer(samples: [Float]) -> AVAudioPCMBuffer {
        let format = AVAudioFormat(standardFormatWithSampleRate: 16_000, channels: 1)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count))!
        buffer.frameLength = AVAudioFrameCount(samples.count)
        guard let channelData = buffer.floatChannelData?[0] else {
            fatalError("Failed to allocate float channel data")
        }

        for (index, sample) in samples.enumerated() {
            channelData[index] = sample
        }
        return buffer
    }
}

private actor ReceivedPayloadStore {
    private var payload: Data?

    func set(_ payload: Data) {
        self.payload = payload
    }

    func get() -> Data? {
        payload
    }
}

private final class MockAudioSession: AudioSessionControlling {
    var permissionGranted = true
    var hasBluetoothInput = true
    var setCategoryCallCount = 0
    var setActiveCallCount = 0
    let notificationObject: AnyObject? = NSObject()

    func requestRecordPermission() async -> Bool {
        permissionGranted
    }

    func setCategory(
        _ category: AVAudioSession.Category,
        mode: AVAudioSession.Mode,
        options: AVAudioSession.CategoryOptions
    ) throws {
        setCategoryCallCount += 1
    }

    func setActive(_ active: Bool, options: AVAudioSession.SetActiveOptions) throws {
        setActiveCallCount += 1
    }

    func hasBluetoothHFPInput() -> Bool {
        hasBluetoothInput
    }
}

private final class MockObserverCenter: NotificationObserving {
    private final class Token: NSObject { }

    var addObserverCallCount = 0
    var removeObserverCallCount = 0

    func addObserver(
        forName name: NSNotification.Name?,
        object obj: Any?,
        queue: OperationQueue?,
        using block: @escaping (Notification) -> Void
    ) -> NSObjectProtocol {
        addObserverCallCount += 1
        return Token()
    }

    func removeObserver(_ observer: NSObjectProtocol) {
        removeObserverCallCount += 1
    }
}

private final class MockTapController: AudioTapControlling {
    private var tapBlock: AVAudioNodeTapBlock?
    var installTapCallCount = 0
    var removeTapCallCount = 0

    func inputFormat() -> AVAudioFormat {
        AVAudioFormat(standardFormatWithSampleRate: 16_000, channels: 1)!
    }

    func installTap(format: AVAudioFormat, block: @escaping AVAudioNodeTapBlock) {
        installTapCallCount += 1
        tapBlock = block
    }

    func removeTap() {
        removeTapCallCount += 1
        tapBlock = nil
    }

    func prepareEngine() { }

    func startEngine() throws { }

    func stopEngine() { }

    func emit(buffer: AVAudioPCMBuffer, when: AVAudioTime = AVAudioTime(sampleTime: 0, atRate: 16_000)) {
        tapBlock?(buffer, when)
    }
}

private final class MockAudioChunkProcessor: AudioChunkProcessing, @unchecked Sendable {
    private let lock = NSLock()
    private(set) var enqueueCallCount = 0
    private(set) var stopAndFlushCallCount = 0

    func configure(
        sessionId: String,
        sessionDirectory: URL,
        indexFileURL: URL,
        inputFormat: AVAudioFormat,
        chunkTargetDurationMs: Int,
        startTimestampMs: Int64,
        onChunkWritten: @escaping @Sendable (AudioChunkMetadata, Int64) -> Void,
        onError: @escaping @Sendable (String) -> Void
    ) throws {
    }

    func enqueue(buffer: AVAudioPCMBuffer) {
        lock.lock()
        enqueueCallCount += 1
        lock.unlock()
    }

    func stopAndFlush() {
        lock.lock()
        stopAndFlushCallCount += 1
        lock.unlock()
    }

    func flushPartialChunk() {
    }

    func enqueueError(_ message: String) {
    }
}

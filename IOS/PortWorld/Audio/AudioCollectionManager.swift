// Coordinates the shared audio engine, microphone capture, and realtime frame delivery for the assistant runtime.

@preconcurrency import AVFAudio
import Combine
import Foundation
import OSLog

@MainActor
final class AudioCollectionManager: ObservableObject {
    private enum StartError: LocalizedError {
        case invalidInputFormat(String)

        var errorDescription: String? {
            switch self {
            case .invalidInputFormat(let detail):
                return "Invalid recording tap format: \(detail)"
            }
        }
    }

    struct HFPRouteAvailability: Equatable {
        let isSelectable: Bool
        let isActive: Bool
    }

    @Published private(set) var state: AudioCollectionState = .idle
    @Published private(set) var stats: AudioCollectionStats = .default
    @Published private(set) var isAudioSessionReady: Bool = false
    @Published private(set) var lastSpeechActivityTimestampMs: Int64?
    var onWakePCMFrame: ((WakeWordPCMFrame) -> Void)?
    var onRealtimePCMFrame: (@Sendable (Data, Int64) -> Void)? {
        didSet {
            realtimePCMSinkRelay.setSink(onRealtimePCMFrame)
        }
    }
    var isPlaybackPendingProvider: (() -> Bool)?

    /// Shared audio engine for both capture and playback. Exposed so that
    /// AssistantPlaybackEngine can attach its player node to the same engine.
    let sharedAudioEngine: AVAudioEngine

    private let audioSessionClient: AudioSessionControlling
    private let observerCenter: NotificationObserving
    private let tapController: AudioTapControlling
    private let realtimePCMSinkRelay = RealtimePCMSinkRelay()
    private let realtimePCMChunkDurationMs = 40
    private let realtimePCMMaximumChunkBytes = 4_080
    private let speechRMSActivityThreshold: Float
    private let speechActivityDebounceMs: Int64
    private let preferSpeakerOutput: Bool
    private let allowBuiltInMicInput: Bool

    private var routeObserver: NSObjectProtocol?
    private var interruptionObserver: NSObjectProtocol?
    private var isTapInstalled = false
    private var lastSpeechEmissionTimestampMs: Int64 = 0
    private let logger = Logger(
        subsystem: Bundle.main.bundleIdentifier ?? "PortWorld",
        category: "AudioCollectionManager"
    )

    init(
        speechRMSThreshold: Float = 0.02,
        speechActivityDebounceMs: Int64 = 250,
        preferSpeakerOutput: Bool = false,
        allowBuiltInMicInput: Bool = true,
        audioSessionClient: AudioSessionControlling? = nil,
        observerCenter: NotificationObserving? = nil,
        sharedAudioEngine: AVAudioEngine? = nil,
        tapControllerFactory: ((AVAudioEngine) -> AudioTapControlling)? = nil
    ) {
        self.speechRMSActivityThreshold = speechRMSThreshold
        self.speechActivityDebounceMs = speechActivityDebounceMs
        self.preferSpeakerOutput = preferSpeakerOutput
        self.allowBuiltInMicInput = allowBuiltInMicInput
        self.audioSessionClient = audioSessionClient ?? SystemAudioSessionClient()
        self.observerCenter = observerCenter ?? SystemNotificationCenter()
        self.sharedAudioEngine = sharedAudioEngine ?? AVAudioEngine()
        let resolvedTapFactory = tapControllerFactory ?? { engine in
            EngineAudioTapController(engine: engine)
        }
        self.tapController = resolvedTapFactory(self.sharedAudioEngine)
        self.realtimePCMSinkRelay.configureChunking(
            minimumChunkSizeBytes: min(
                Self.realtimePCMChunkSizeBytes(
                    durationMs: realtimePCMChunkDurationMs,
                    sampleRate: 24_000,
                    channels: 1
                ),
                realtimePCMMaximumChunkBytes
            ),
            logChunkEmission: { _, _, _ in }
        )
        interruptionObserver = self.observerCenter.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: self.audioSessionClient.notificationObject,
            queue: .main
        ) { [weak self] notification in
            let interruptionType = Self.interruptionType(from: notification)
            MainActor.assumeIsolated {
                self?.handleInterruption(interruptionType)
            }
        }
    }

    deinit {
        MainActor.assumeIsolated {
            if let routeObserver {
                observerCenter.removeObserver(routeObserver)
            }
            if let interruptionObserver {
                observerCenter.removeObserver(interruptionObserver)
            }
        }
    }

    func prepareAudioSession() async {
        guard state != .recording, state != .stopping else { return }
        state = .preparingAudioSession

        let granted = await requestRecordPermission()
        guard granted else {
            markFailed("Microphone permission denied.")
            return
        }

        do {
            var categoryOptions: AVAudioSession.CategoryOptions = [.allowBluetoothHFP]
            if allowBuiltInMicInput || preferSpeakerOutput {
                categoryOptions.insert(.defaultToSpeaker)
            }
            let sessionMode: AVAudioSession.Mode = preferSpeakerOutput ? .voiceChat : .default
            try audioSessionClient.setCategory(.playAndRecord, mode: sessionMode, options: categoryOptions)
            try audioSessionClient.setActive(true, options: [.notifyOthersOnDeactivation])
            registerRouteObserverIfNeeded()
            isAudioSessionReady = true
            refreshDeviceAvailabilityState()
        } catch {
            markFailed("Failed to prepare audio session: \(error.localizedDescription)")
        }
    }

    func start() async {
        guard state != .recording, state != .stopping else { return }
        guard isAudioSessionReady else {
            markFailed("Audio session is not prepared. Call prepareAudioSession() first.")
            return
        }

        if hasRequiredInputRoute() == false {
            state = .waitingForDevice
            return
        }

        do {
            configureVoiceProcessingIfNeeded()

            guard let inputFormat = tapController.inputFormat() else {
                throw StartError.invalidInputFormat("audio input format is unavailable")
            }
            guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0 else {
                throw StartError.invalidInputFormat(
                    "sampleRate=\(inputFormat.sampleRate) channelCount=\(inputFormat.channelCount)"
                )
            }

            if isTapInstalled {
                tapController.removeTap()
                isTapInstalled = false
            }

            let realtimePCMSinkRelay = self.realtimePCMSinkRelay
            tapController.installTap(format: inputFormat) { [weak self] buffer, _ in
                let rms = Self.computeRMS(buffer)
                let timestampMs = Self.nowMs()
                Task { @MainActor in
                    self?.handleSpeechEnergySample(rms: rms, timestampMs: timestampMs)
                    if let frame = Self.makeWakePCMFrame(from: buffer, timestampMs: timestampMs) {
                        self?.onWakePCMFrame?(frame)
                    }
                }

                guard realtimePCMSinkRelay.hasSink else { return }

                guard let payload = Self.copyRealtimePCMPayload(buffer) else {
                    Task { @MainActor [weak self] in
                        self?.markFailed("Failed to convert realtime audio payload to pcm_s16le mono 24kHz.")
                    }
                    return
                }

                realtimePCMSinkRelay.emit(payload: payload, timestampMs: timestampMs)
            }
            isTapInstalled = true

            tapController.prepareEngine()
            try tapController.startEngine()

            stats = .default
            lastSpeechActivityTimestampMs = nil
            lastSpeechEmissionTimestampMs = 0
            state = .recording
        } catch {
            teardownEngineIfNeeded()
            realtimePCMSinkRelay.flush()
            markFailed("Failed to start audio capture: \(error.localizedDescription)")
        }
    }

    func stop() async {
        switch state {
        case .recording, .failed:
            break
        case .idle, .preparingAudioSession, .waitingForDevice, .stopping:
            return
        }

        if state == .recording {
            state = .stopping
        }

        teardownEngineIfNeeded()
        realtimePCMSinkRelay.flush()

        if case .failed = state {
            stats.lastError = nil
        }

        lastSpeechActivityTimestampMs = nil
        lastSpeechEmissionTimestampMs = 0
        state = .idle
    }

    func hfpRouteAvailability() -> HFPRouteAvailability {
        let routeDiagnostics = audioSessionClient.routeDiagnostics()
        return HFPRouteAvailability(
            isSelectable: routeDiagnostics.hasSelectableBluetoothHFPInput,
            isActive: routeDiagnostics.isCurrentRouteBluetoothHFP
        )
    }

    func selectBluetoothHFPInputIfAvailable() throws -> Bool {
        try audioSessionClient.selectBluetoothHFPInputIfAvailable()
    }

    func logRouteDiagnostics(context: String) {
        let routeDiagnostics = audioSessionClient.routeDiagnostics()
        let preferredInput = routeDiagnostics.preferredInput ?? "-"
        let currentInputs = routeDiagnostics.currentInputs.joined(separator: ", ")
        let currentOutputs = routeDiagnostics.currentOutputs.joined(separator: ", ")
        let availableInputs = routeDiagnostics.availableInputs.joined(separator: ", ")
        logger.debug(
            "Route diagnostics (\(context, privacy: .public)): category=\(routeDiagnostics.category, privacy: .public), mode=\(routeDiagnostics.mode, privacy: .public), selectable=\(routeDiagnostics.hasSelectableBluetoothHFPInput, privacy: .public), active=\(routeDiagnostics.isCurrentRouteBluetoothHFP, privacy: .public), preferredInput=\(preferredInput, privacy: .public), currentInputs=[\(currentInputs, privacy: .public)], currentOutputs=[\(currentOutputs, privacy: .public)], availableInputs=[\(availableInputs, privacy: .public)]"
        )
    }

    private func configureVoiceProcessingIfNeeded() {
        guard preferSpeakerOutput else { return }

        do {
            if sharedAudioEngine.inputNode.isVoiceProcessingEnabled == false {
                try sharedAudioEngine.inputNode.setVoiceProcessingEnabled(true)
                logger.debug("Enabled voice processing on shared input node")
            }
        } catch {
            logger.error("Failed to enable input voice processing: \(error.localizedDescription, privacy: .public)")
        }

        do {
            if sharedAudioEngine.outputNode.isVoiceProcessingEnabled == false {
                try sharedAudioEngine.outputNode.setVoiceProcessingEnabled(true)
                logger.debug("Enabled voice processing on shared output node")
            }
        } catch {
            logger.error("Failed to enable output voice processing: \(error.localizedDescription, privacy: .public)")
        }
    }

    private func teardownEngineIfNeeded() {
        if isTapInstalled {
            tapController.removeTap()
            isTapInstalled = false
        }

        if isPlaybackPendingProvider?() == true {
            return
        }

        tapController.stopEngine()
    }

    private func registerRouteObserverIfNeeded() {
        guard routeObserver == nil else { return }
        routeObserver = observerCenter.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: audioSessionClient.notificationObject,
            queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.refreshDeviceAvailabilityState()
            }
        }
    }

    private func refreshDeviceAvailabilityState() {
        guard isAudioSessionReady else {
            state = .idle
            return
        }

        if state == .recording || state == .stopping {
            return
        }

        state = hasRequiredInputAvailable() ? .idle : .waitingForDevice
    }

    private func handleInterruption(_ interruptionType: AVAudioSession.InterruptionType?) {
        guard let interruptionType else { return }

        switch interruptionType {
        case .began:
            if state == .recording {
                Task { @MainActor in
                    await stop()
                }
            }
        case .ended:
            do {
                try audioSessionClient.setActive(true, options: [])
                refreshDeviceAvailabilityState()
            } catch {
                markFailed("Failed to reactivate audio session after interruption: \(error.localizedDescription)")
            }
        @unknown default:
            break
        }
    }

    private func markFailed(_ message: String) {
        stats.lastError = message
        state = .failed(message)
    }

    private func handleSpeechEnergySample(rms: Float, timestampMs: Int64) {
        guard rms >= speechRMSActivityThreshold else { return }
        guard timestampMs - lastSpeechEmissionTimestampMs >= speechActivityDebounceMs else { return }

        lastSpeechEmissionTimestampMs = timestampMs
        lastSpeechActivityTimestampMs = timestampMs
    }

    private func hasBluetoothHFPInput() -> Bool {
        audioSessionClient.hasCurrentBluetoothHFPInput()
    }

    private func hasRequiredInputAvailable() -> Bool {
        if allowBuiltInMicInput || preferSpeakerOutput {
            return true
        }
        return audioSessionClient.hasSelectableBluetoothHFPInput()
    }

    private func hasRequiredInputRoute() -> Bool {
        if allowBuiltInMicInput || preferSpeakerOutput {
            return true
        }
        return hasBluetoothHFPInput()
    }

    private func requestRecordPermission() async -> Bool {
        await audioSessionClient.requestRecordPermission()
    }

    private nonisolated static func interruptionType(from notification: Notification) -> AVAudioSession.InterruptionType? {
        guard
            let raw = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
            let interruptionType = AVAudioSession.InterruptionType(rawValue: raw)
        else {
            return nil
        }
        return interruptionType
    }

    private nonisolated static func nowMs() -> Int64 {
        Clocks.nowMs()
    }

    private nonisolated static func realtimePCMChunkSizeBytes(
        durationMs: Int,
        sampleRate: Int,
        channels: Int
    ) -> Int {
        let frames = max(1, (sampleRate * durationMs) / 1000)
        return max(2, frames * max(1, channels) * MemoryLayout<Int16>.size)
    }

    private static func computeRMS(_ buffer: AVAudioPCMBuffer) -> Float {
        let frameCount = Int(buffer.frameLength)
        guard frameCount > 0 else { return 0 }

        if let floatData = buffer.floatChannelData {
            let channel = floatData[0]
            var sumSquares: Float = 0
            for i in 0 ..< frameCount {
                let sample = channel[i]
                sumSquares += sample * sample
            }
            return sqrtf(sumSquares / Float(frameCount))
        }

        if let int16Data = buffer.int16ChannelData {
            let channel = int16Data[0]
            var sumSquares: Float = 0
            for i in 0 ..< frameCount {
                let normalized = Float(channel[i]) / Float(Int16.max)
                sumSquares += normalized * normalized
            }
            return sqrtf(sumSquares / Float(frameCount))
        }

        if let int32Data = buffer.int32ChannelData {
            let channel = int32Data[0]
            var sumSquares: Float = 0
            for i in 0 ..< frameCount {
                let normalized = Float(channel[i]) / Float(Int32.max)
                sumSquares += normalized * normalized
            }
            return sqrtf(sumSquares / Float(frameCount))
        }

        return 0
    }

    private static func copyPCMPayload(_ buffer: AVAudioPCMBuffer) -> Data? {
        let sourceList = UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList)
        var totalSize = 0
        for source in sourceList {
            totalSize += Int(source.mDataByteSize)
        }
        guard totalSize > 0 else { return nil }

        var payload = Data()
        payload.reserveCapacity(totalSize)

        for source in sourceList {
            let size = Int(source.mDataByteSize)
            guard size > 0, let sourceData = source.mData else {
                continue
            }
            payload.append(contentsOf: UnsafeRawBufferPointer(start: sourceData, count: size))
        }

        return payload.isEmpty ? nil : payload
    }

    private static func copyRealtimePCMPayload(_ buffer: AVAudioPCMBuffer) -> Data? {
        let targetSampleRate = 24_000.0
        let inputFormat = buffer.format
        let isTargetFormat =
            inputFormat.commonFormat == .pcmFormatInt16 &&
            inputFormat.channelCount == 1 &&
            abs(inputFormat.sampleRate - targetSampleRate) < 0.001
        if isTargetFormat {
            return copyPCMPayload(buffer)
        }

        guard let outputFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: targetSampleRate,
            channels: 1,
            interleaved: false
        ) else {
            return nil
        }

        guard let converter = AVAudioConverter(from: inputFormat, to: outputFormat) else {
            return nil
        }

        let ratio = outputFormat.sampleRate / max(inputFormat.sampleRate, 1)
        let capacity = AVAudioFrameCount(max(1, Int((Double(buffer.frameLength) * ratio).rounded(.up)) + 32))
        guard let convertedBuffer = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else {
            return nil
        }

        var consumed = false
        var convertedPayload = Data()

        while true {
            var conversionError: NSError?
            let status = converter.convert(to: convertedBuffer, error: &conversionError) { _, outStatus in
                if consumed {
                    outStatus.pointee = .noDataNow
                    return nil
                }
                consumed = true
                outStatus.pointee = .haveData
                return buffer
            }

            if conversionError != nil {
                return nil
            }

            let producedFrames = Int(convertedBuffer.frameLength)
            if producedFrames > 0 {
                guard let channelData = convertedBuffer.int16ChannelData else {
                    return nil
                }
                let byteCount = producedFrames * MemoryLayout<Int16>.size
                convertedPayload.append(contentsOf: UnsafeRawBufferPointer(start: channelData[0], count: byteCount))
            }

            switch status {
            case .haveData:
                continue
            case .inputRanDry, .endOfStream:
                return convertedPayload.isEmpty ? nil : convertedPayload
            case .error:
                return nil
            @unknown default:
                return convertedPayload.isEmpty ? nil : convertedPayload
            }
        }
    }

    private static func makeWakePCMFrame(from buffer: AVAudioPCMBuffer, timestampMs: Int64) -> WakeWordPCMFrame? {
        let frameCount = Int(buffer.frameLength)
        guard frameCount > 0 else { return nil }

        var mono = [Int16](repeating: 0, count: frameCount)
        let channels = max(1, Int(buffer.format.channelCount))

        if let floatData = buffer.floatChannelData {
            for i in 0 ..< frameCount {
                var accum: Float = 0
                for c in 0 ..< channels {
                    accum += floatData[c][i]
                }
                let avg = max(-1.0, min(1.0, accum / Float(channels)))
                mono[i] = Int16(avg * Float(Int16.max))
            }
        } else if let int16Data = buffer.int16ChannelData {
            for i in 0 ..< frameCount {
                var accum: Int = 0
                for c in 0 ..< channels {
                    accum += Int(int16Data[c][i])
                }
                let avg = accum / channels
                mono[i] = Int16(max(Int(Int16.min), min(Int(Int16.max), avg)))
            }
        } else if let int32Data = buffer.int32ChannelData {
            for i in 0 ..< frameCount {
                var accum: Int64 = 0
                for c in 0 ..< channels {
                    accum += Int64(int32Data[c][i])
                }
                let avg = accum / Int64(channels)
                let normalized = Float(avg) / Float(Int32.max)
                mono[i] = Int16(max(-1.0, min(1.0, normalized)) * Float(Int16.max))
            }
        } else {
            return nil
        }

        return WakeWordPCMFrame(
            samples: mono,
            sampleRateHz: buffer.format.sampleRate,
            channelCount: 1,
            timestampMs: timestampMs
        )
    }
}

protocol AudioSessionControlling {
    var notificationObject: AnyObject? { get }
    func requestRecordPermission() async -> Bool
    func setCategory(
        _ category: AVAudioSession.Category,
        mode: AVAudioSession.Mode,
        options: AVAudioSession.CategoryOptions
    ) throws
    func setActive(_ active: Bool, options: AVAudioSession.SetActiveOptions) throws
    func hasCurrentBluetoothHFPInput() -> Bool
    func hasSelectableBluetoothHFPInput() -> Bool
    func selectBluetoothHFPInputIfAvailable() throws -> Bool
    func routeDiagnostics() -> AudioSessionRouteDiagnostics
}

struct AudioSessionRouteDiagnostics {
    let category: String
    let mode: String
    let currentInputs: [String]
    let currentOutputs: [String]
    let availableInputs: [String]
    let preferredInput: String?
    let hasSelectableBluetoothHFPInput: Bool
    let isCurrentRouteBluetoothHFP: Bool
}

private final class SystemAudioSessionClient: AudioSessionControlling {
    private let session: AVAudioSession

    init(session: AVAudioSession? = nil) {
        self.session = session ?? .sharedInstance()
    }

    var notificationObject: AnyObject? {
        session
    }

    func requestRecordPermission() async -> Bool {
        await AVAudioApplication.requestRecordPermission()
    }

    func setCategory(
        _ category: AVAudioSession.Category,
        mode: AVAudioSession.Mode,
        options: AVAudioSession.CategoryOptions
    ) throws {
        try session.setCategory(category, mode: mode, options: options)
    }

    func setActive(_ active: Bool, options: AVAudioSession.SetActiveOptions) throws {
        try session.setActive(active, options: options)
    }

    func hasCurrentBluetoothHFPInput() -> Bool {
        session.currentRoute.inputs.contains { input in
            input.portType == .bluetoothHFP
        }
    }

    func hasSelectableBluetoothHFPInput() -> Bool {
        guard let availableInputs = session.availableInputs else { return false }
        return availableInputs.contains { input in
            input.portType == .bluetoothHFP
        }
    }

    func selectBluetoothHFPInputIfAvailable() throws -> Bool {
        guard let availableInputs = session.availableInputs else { return false }
        guard let bluetoothInput = availableInputs.first(where: { $0.portType == .bluetoothHFP }) else {
            return false
        }

        if session.preferredInput?.uid != bluetoothInput.uid {
            try session.setPreferredInput(bluetoothInput)
        }

        return true
    }

    func routeDiagnostics() -> AudioSessionRouteDiagnostics {
        let currentRoute = session.currentRoute
        let currentInputs = currentRoute.inputs.map { "\($0.portType.rawValue):\($0.portName)" }
        let currentOutputs = currentRoute.outputs.map { "\($0.portType.rawValue):\($0.portName)" }
        let availableInputs = session.availableInputs ?? []
        let availableInputDescriptions = availableInputs.map { "\($0.portType.rawValue):\($0.portName)" }
        let preferredInput = session.preferredInput.map { "\($0.portType.rawValue):\($0.portName)" }
        let hasSelectableBluetoothHFPInput =
            availableInputs.contains(where: { $0.portType == .bluetoothHFP })
        let isCurrentRouteBluetoothHFP =
            currentRoute.inputs.contains(where: { $0.portType == .bluetoothHFP }) &&
            currentRoute.outputs.contains(where: { $0.portType == .bluetoothHFP })

        return AudioSessionRouteDiagnostics(
            category: session.category.rawValue,
            mode: session.mode.rawValue,
            currentInputs: currentInputs,
            currentOutputs: currentOutputs,
            availableInputs: availableInputDescriptions,
            preferredInput: preferredInput,
            hasSelectableBluetoothHFPInput: hasSelectableBluetoothHFPInput,
            isCurrentRouteBluetoothHFP: isCurrentRouteBluetoothHFP
        )
    }
}

protocol NotificationObserving {
    func addObserver(
        forName name: NSNotification.Name?,
        object obj: Any?,
        queue: OperationQueue?,
        using block: @escaping (Notification) -> Void
    ) -> NSObjectProtocol
    func removeObserver(_ observer: NSObjectProtocol)
}

private final class SystemNotificationCenter: NotificationObserving {
    private let center: NotificationCenter

    init(center: NotificationCenter? = nil) {
        self.center = center ?? .default
    }

    func addObserver(
        forName name: NSNotification.Name?,
        object obj: Any?,
        queue: OperationQueue?,
        using block: @escaping (Notification) -> Void
    ) -> NSObjectProtocol {
        center.addObserver(forName: name, object: obj, queue: queue, using: block)
    }

    func removeObserver(_ observer: NSObjectProtocol) {
        center.removeObserver(observer)
    }
}

protocol AudioTapControlling {
    func inputFormat() -> AVAudioFormat?
    func installTap(format: AVAudioFormat, block: @escaping AVAudioNodeTapBlock)
    func removeTap()
    func prepareEngine()
    func startEngine() throws
    func stopEngine()
}

private final class EngineAudioTapController: AudioTapControlling {
    private let engine: AVAudioEngine

    init(engine: AVAudioEngine) {
        self.engine = engine
    }

    func inputFormat() -> AVAudioFormat? {
        let format = engine.inputNode.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            return nil
        }
        return format
    }

    func installTap(format: AVAudioFormat, block: @escaping AVAudioNodeTapBlock) {
        engine.inputNode.installTap(onBus: 0, bufferSize: 1024, format: format, block: block)
    }

    func removeTap() {
        engine.inputNode.removeTap(onBus: 0)
    }

    func prepareEngine() {
        engine.prepare()
    }

    func startEngine() throws {
        try engine.start()
    }

    func stopEngine() {
        engine.stop()
    }
}

// SAFETY: `RealtimePCMSinkRelay` is manually marked `@unchecked Sendable` because
// mutable state is protected with `lock`, and sink invocation is dispatched on the
// private serial `callbackQueue`.
private final class RealtimePCMSinkRelay: @unchecked Sendable {
    private let lock = NSLock()
    private let callbackQueue = DispatchQueue(label: "PortWorld.RealtimePCMSinkRelay")
    private var sink: (@Sendable (Data, Int64) -> Void)?
    private var accumulatedPayload = Data()
    private var minimumChunkSizeBytes = 0
    private var nextChunkIndex = 0
    private var logChunkEmission: (@Sendable (Int, Int, Int64) -> Void)?

    var hasSink: Bool {
        lock.lock()
        let hasSink = sink != nil
        lock.unlock()
        return hasSink
    }

    func configureChunking(
        minimumChunkSizeBytes: Int,
        logChunkEmission: (@Sendable (Int, Int, Int64) -> Void)? = nil
    ) {
        lock.lock()
        self.minimumChunkSizeBytes = max(2, minimumChunkSizeBytes)
        self.logChunkEmission = logChunkEmission
        lock.unlock()
    }

    func setSink(_ sink: (@Sendable (Data, Int64) -> Void)?) {
        lock.lock()
        self.sink = sink
        if sink == nil {
            accumulatedPayload.removeAll(keepingCapacity: false)
            nextChunkIndex = 0
        }
        lock.unlock()
    }

    func emit(payload: Data, timestampMs: Int64) {
        let emissions: [(Data, Int64, Int)]
        lock.lock()
        guard sink != nil else {
            lock.unlock()
            return
        }
        accumulatedPayload.append(payload)
        emissions = dequeueReadyChunksLocked(timestampMs: timestampMs)
        lock.unlock()
        dispatch(emissions)
    }

    func flush() {
        let emissions: [(Data, Int64, Int)]
        lock.lock()
        guard sink != nil, !accumulatedPayload.isEmpty else {
            accumulatedPayload.removeAll(keepingCapacity: false)
            lock.unlock()
            return
        }
        let chunkIndex = nextChunkIndex
        nextChunkIndex += 1
        emissions = [(accumulatedPayload, Clocks.nowMs(), chunkIndex)]
        accumulatedPayload = Data()
        lock.unlock()
        dispatch(emissions)
    }

    private func dequeueReadyChunksLocked(timestampMs: Int64) -> [(Data, Int64, Int)] {
        let chunkSize = max(2, minimumChunkSizeBytes)
        guard accumulatedPayload.count >= chunkSize else { return [] }

        var emissions: [(Data, Int64, Int)] = []
        while accumulatedPayload.count >= chunkSize {
            let chunk = accumulatedPayload.prefix(chunkSize)
            let chunkIndex = nextChunkIndex
            nextChunkIndex += 1
            emissions.append((Data(chunk), timestampMs, chunkIndex))
            accumulatedPayload.removeFirst(chunkSize)
        }
        return emissions
    }

    private func dispatch(_ emissions: [(Data, Int64, Int)]) {
        guard !emissions.isEmpty else { return }
        lock.lock()
        let sink = self.sink
        let logChunkEmission = self.logChunkEmission
        lock.unlock()

        guard let sink else { return }
        callbackQueue.async {
            for (payload, timestampMs, chunkIndex) in emissions {
                logChunkEmission?(chunkIndex, payload.count, timestampMs)
                sink(payload, timestampMs)
            }
        }
    }
}

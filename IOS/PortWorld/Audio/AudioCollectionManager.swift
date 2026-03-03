import AVFAudio
import Combine
import Foundation

@MainActor
final class AudioCollectionManager: ObservableObject {
    @Published private(set) var state: AudioCollectionState = .idle
    @Published private(set) var stats: AudioCollectionStats = .default
    @Published private(set) var isAudioSessionReady: Bool = false
    @Published private(set) var currentSessionDirectory: URL?
    @Published private(set) var lastSpeechActivityTimestampMs: Int64?
    var onWakePCMFrame: ((WakeWordPCMFrame) -> Void)?

    /// Shared audio engine for both capture and playback. Exposed so that
    /// AssistantPlaybackEngine can attach its player node to the same engine.
    let sharedAudioEngine = AVAudioEngine()

    private let audioSession = AVAudioSession.sharedInstance()
    private let processor = AudioChunkProcessor()
    private let chunkDurationMs = 500
    private let speechRMSActivityThreshold: Float
    private let speechActivityDebounceMs: Int64

    private var routeObserver: NSObjectProtocol?
    private var interruptionObserver: NSObjectProtocol?
    private var isTapInstalled = false
    private var lastSpeechEmissionTimestampMs: Int64 = 0

    init(speechRMSThreshold: Float = 0.02, speechActivityDebounceMs: Int64 = 250) {
        self.speechRMSActivityThreshold = speechRMSThreshold
        self.speechActivityDebounceMs = speechActivityDebounceMs
        routeObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: audioSession,
            queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.refreshDeviceAvailabilityState()
            }
        }

        interruptionObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: audioSession,
            queue: .main
        ) { [weak self] notification in
            let interruptionType = Self.interruptionType(from: notification)
            MainActor.assumeIsolated {
                self?.handleInterruption(interruptionType)
            }
        }
    }

    deinit {
        if let routeObserver {
            NotificationCenter.default.removeObserver(routeObserver)
        }
        if let interruptionObserver {
            NotificationCenter.default.removeObserver(interruptionObserver)
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
            // Use .default mode and .allowBluetoothHFP per DAT SDK recommendations for HFP.
            // .voiceChat mode can apply aggressive audio processing that interferes with TTS playback.
            try audioSession.setCategory(.playAndRecord, mode: .default, options: [.allowBluetoothHFP])
            try audioSession.setActive(true, options: [.notifyOthersOnDeactivation])
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

        if hasBluetoothHFPInput() == false {
            state = .waitingForDevice
            return
        }

        do {
            let sessionId = UUID().uuidString
            let startedAtMs = Self.nowMs()
            let sessionDirectory = try createSessionDirectory(sessionId: sessionId)
            let indexURL = sessionDirectory.appendingPathComponent("index.jsonl")
            FileManager.default.createFile(atPath: indexURL.path, contents: nil)

            let inputNode = sharedAudioEngine.inputNode
            let inputFormat = inputNode.inputFormat(forBus: 0)

            try processor.configure(
                sessionId: sessionId,
                sessionDirectory: sessionDirectory,
                indexFileURL: indexURL,
                inputFormat: inputFormat,
                chunkTargetDurationMs: chunkDurationMs,
                startTimestampMs: startedAtMs,
                onChunkWritten: { [weak self] metadata, bytesWritten in
                    Task { @MainActor [weak self] in
                        guard let self else { return }
                        self.stats.chunksWritten += 1
                        self.stats.bytesWritten += bytesWritten
                        self.stats.lastChunkDurationMs = metadata.durationMs
                    }
                },
                onError: { [weak self] message in
                    Task { @MainActor [weak self] in
                        guard let self else { return }
                        self.markFailed(message)
                    }
                }
            )

            if isTapInstalled {
                inputNode.removeTap(onBus: 0)
                isTapInstalled = false
            }

            let processor = self.processor
            inputNode.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] buffer, _ in
                let rms = Self.computeRMS(buffer)
                let timestampMs = Self.nowMs()
                Task { @MainActor in
                    self?.handleSpeechEnergySample(rms: rms, timestampMs: timestampMs)
                    if let frame = Self.makeWakePCMFrame(from: buffer, timestampMs: timestampMs) {
                        self?.onWakePCMFrame?(frame)
                    }
                }

                guard let copied = Self.copyPCMBuffer(buffer) else {
                    processor.enqueueError("Failed to copy captured audio buffer.")
                    return
                }
                processor.enqueue(buffer: copied)
            }
            isTapInstalled = true

            sharedAudioEngine.prepare()
            try sharedAudioEngine.start()

            currentSessionDirectory = sessionDirectory
            stats = .default
            stats.startTimestampMs = startedAtMs
            lastSpeechActivityTimestampMs = nil
            lastSpeechEmissionTimestampMs = 0
            state = .recording
        } catch {
            teardownEngineIfNeeded()
            processor.stopAndFlush()
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
        processor.stopAndFlush()

        if case .failed = state {
            return
        }

        lastSpeechActivityTimestampMs = nil
        lastSpeechEmissionTimestampMs = 0
        state = .idle
    }

    /// Flushes any buffered audio data to disk without stopping recording.
    /// Call this before exporting clips to ensure partial chunks are available.
    func flushPendingAudioChunks() {
        processor.flushPartialChunk()
    }

    private func teardownEngineIfNeeded() {
        let inputNode = sharedAudioEngine.inputNode
        if isTapInstalled {
            inputNode.removeTap(onBus: 0)
            isTapInstalled = false
        }
        sharedAudioEngine.stop()
    }

    private func refreshDeviceAvailabilityState() {
        guard isAudioSessionReady else {
            state = .idle
            return
        }

        if state == .recording || state == .stopping {
            return
        }

        state = hasBluetoothHFPInput() ? .idle : .waitingForDevice
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
                try audioSession.setActive(true, options: [])
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
        audioSession.currentRoute.inputs.contains { input in
            input.portType == .bluetoothHFP
        }
    }

    private func requestRecordPermission() async -> Bool {
        await AVAudioApplication.requestRecordPermission()
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

    private func createSessionDirectory(sessionId: String) throws -> URL {
        let documents = try documentsDirectoryURL()
        let root = documents.appendingPathComponent("AudioSessions", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)

        let sessionDirectory = root.appendingPathComponent(sessionId, isDirectory: true)
        try FileManager.default.createDirectory(at: sessionDirectory, withIntermediateDirectories: true)
        return sessionDirectory
    }

    private func documentsDirectoryURL() throws -> URL {
        guard let documentsURL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first else {
            throw NSError(domain: "AudioCollectionManager", code: 1, userInfo: [NSLocalizedDescriptionKey: "Could not locate Documents directory."])
        }
        return documentsURL
    }

    private static func nowMs() -> Int64 {
        Clocks.nowMs()
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

    private static func copyPCMBuffer(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard let copied = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameLength) else {
            return nil
        }

        copied.frameLength = buffer.frameLength

        let srcList = UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList)
        let dstList = UnsafeMutableAudioBufferListPointer(copied.mutableAudioBufferList)
        let count = min(srcList.count, dstList.count)

        for i in 0 ..< count {
            let src = srcList[i]
            let maxSize = Int(dstList[i].mDataByteSize)
            let size = min(Int(src.mDataByteSize), maxSize)
            guard let srcData = src.mData, let dstData = dstList[i].mData else {
                continue
            }
            memcpy(dstData, srcData, size)
            dstList[i].mDataByteSize = UInt32(size)
        }

        return copied
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

    func exportWAVClip(
        window: AudioClipExportWindow,
        from sessionDirectory: URL? = nil
    ) throws -> URL {
        guard window.endTimestampMs > window.startTimestampMs else {
            throw AudioClipExportError.invalidWindow
        }

        let targetDirectory = sessionDirectory ?? currentSessionDirectory
        guard let targetDirectory else {
            throw AudioClipExportError.sessionDirectoryUnavailable
        }

        let chunks = try loadChunkIndex(from: targetDirectory)
        var mergedPCM = Data()
        var clipSampleRate: Int?
        var clipChannels: Int?

        for chunk in chunks {
            let chunkStart = chunk.startedAtMs
            let chunkEnd = chunk.startedAtMs + Int64(chunk.durationMs)
            if chunkEnd <= window.startTimestampMs || chunkStart >= window.endTimestampMs {
                continue
            }

            let chunkURL = targetDirectory.appendingPathComponent(chunk.fileName)
            guard FileManager.default.fileExists(atPath: chunkURL.path) else {
                continue
            }

            let chunkPCM: Data
            do {
                chunkPCM = try Self.readPCM16Payload(from: chunkURL)
            } catch {
                continue
            }

            if chunkPCM.isEmpty {
                continue
            }

            if clipSampleRate == nil {
                clipSampleRate = chunk.sampleRate
                clipChannels = chunk.channels
            } else if clipSampleRate != chunk.sampleRate || clipChannels != chunk.channels {
                // Best effort: skip incompatible chunks from mixed-format captures.
                continue
            }

            let overlapStart = max(window.startTimestampMs, chunkStart)
            let overlapEnd = min(window.endTimestampMs, chunkEnd)
            if overlapEnd <= overlapStart {
                continue
            }

            let bytesPerFrame = max(1, chunk.channels * 2)
            let startOffsetMs = Double(overlapStart - chunkStart)
            let endOffsetMs = Double(overlapEnd - chunkStart)
            let startFrame = Int((startOffsetMs * Double(chunk.sampleRate) / 1000.0).rounded(.down))
            let endFrame = Int((endOffsetMs * Double(chunk.sampleRate) / 1000.0).rounded(.up))

            let startByte = max(0, min(chunkPCM.count, startFrame * bytesPerFrame))
            let endByte = max(startByte, min(chunkPCM.count, endFrame * bytesPerFrame))
            if endByte > startByte {
                mergedPCM.append(chunkPCM.subdata(in: startByte ..< endByte))
            }
        }

        guard !mergedPCM.isEmpty else {
            #if DEBUG
            // Diagnostic logging: print requested window vs available chunks
            let chunkRanges = chunks.map { "[\($0.startedAtMs)-\($0.startedAtMs + Int64($0.durationMs))]" }.joined(separator: ", ")
            print("[AudioCollectionManager] No audio data in window. Requested: [\(window.startTimestampMs)-\(window.endTimestampMs)], Available chunks: \(chunkRanges.isEmpty ? "none" : chunkRanges)")
            #endif
            throw AudioClipExportError.noAudioDataInWindow
        }

        guard let clipSampleRate, let clipChannels else {
            throw AudioClipExportError.noAudioDataInWindow
        }

        let fileName = "clip_\(window.startTimestampMs)_\(window.endTimestampMs).wav"
        let outputURL = targetDirectory.appendingPathComponent(fileName)
        _ = try WavFileWriter.writePCM16(
            samples: mergedPCM,
            sampleRate: clipSampleRate,
            channels: clipChannels,
            to: outputURL
        )
        return outputURL
    }

    private func loadChunkIndex(from sessionDirectory: URL) throws -> [AudioChunkMetadata] {
        let indexURL = sessionDirectory.appendingPathComponent("index.jsonl")
        guard FileManager.default.fileExists(atPath: indexURL.path) else {
            throw AudioClipExportError.indexFileUnavailable
        }

        let indexContents = try String(contentsOf: indexURL, encoding: .utf8)
        let decoder = JSONDecoder()

        let chunks: [AudioChunkMetadata] = indexContents
            .split(whereSeparator: \.isNewline)
            .compactMap { line in
                guard !line.isEmpty else { return nil }
                return try? decoder.decode(AudioChunkMetadata.self, from: Data(line.utf8))
            }
            .sorted(by: { $0.startedAtMs < $1.startedAtMs })

        return chunks
    }

    private static func readPCM16Payload(from wavURL: URL) throws -> Data {
        let wavData = try Data(contentsOf: wavURL)
        guard wavData.count > 44 else { return Data() }
        return wavData.subdata(in: 44 ..< wavData.count)
    }
}

private enum AudioChunkProcessorError: Error {
    case invalidInputFormat
    case converterInitializationFailed
    case outputBufferAllocationFailed
    case missingConvertedChannelData
}

private final class AudioChunkProcessor: @unchecked Sendable {
    private let queue = DispatchQueue(label: "PortWorld.AudioChunkProcessor")
    private let encoder = JSONEncoder()

    private let outputSampleRate = 8_000
    private let outputChannels: AVAudioChannelCount = 1
    private let bitsPerSample = 16

    private var targetFramesPerChunk = 4_000
    private var converter: AVAudioConverter?
    private var outputFormat: AVAudioFormat?

    private var sessionId = ""
    private var sessionDirectory: URL?
    private var indexFileHandle: FileHandle?

    private var chunkSequence = 0
    private var nextChunkStartMs: Int64 = 0
    private var accumulatedPCMData = Data()
    private var accumulatedFrames = 0

    private var onChunkWritten: (@Sendable (AudioChunkMetadata, Int64) -> Void)?
    private var onError: (@Sendable (String) -> Void)?
    private var hasFailed = false

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
        try queue.sync {
            try resetStateUnsafe()

            guard inputFormat.sampleRate > 0 else {
                throw AudioChunkProcessorError.invalidInputFormat
            }

            guard let outputFormat = AVAudioFormat(
                commonFormat: .pcmFormatInt16,
                sampleRate: Double(outputSampleRate),
                channels: outputChannels,
                interleaved: false
            ) else {
                throw AudioChunkProcessorError.invalidInputFormat
            }

            guard let converter = AVAudioConverter(from: inputFormat, to: outputFormat) else {
                throw AudioChunkProcessorError.converterInitializationFailed
            }

            self.converter = converter
            self.outputFormat = outputFormat
            self.sessionId = sessionId
            self.sessionDirectory = sessionDirectory
            self.indexFileHandle = try FileHandle(forWritingTo: indexFileURL)
            try self.indexFileHandle?.seekToEnd()
            self.targetFramesPerChunk = max(1, Int((Double(outputSampleRate) * Double(chunkTargetDurationMs)) / 1000.0))
            self.nextChunkStartMs = startTimestampMs
            self.chunkSequence = 0
            self.accumulatedPCMData = Data()
            self.accumulatedFrames = 0
            self.onChunkWritten = onChunkWritten
            self.onError = onError
            self.hasFailed = false
        }
    }

    func enqueue(buffer: AVAudioPCMBuffer) {
        queue.async {
            guard !self.hasFailed, self.converter != nil else { return }

            do {
                let (convertedBytes, frames) = try self.convertToPCM16Mono8k(buffer)
                guard frames > 0 else { return }

                self.accumulatedPCMData.append(convertedBytes)
                self.accumulatedFrames += frames

                let bytesPerFrame = self.bitsPerSample / 8
                while self.accumulatedFrames >= self.targetFramesPerChunk {
                    let chunkBytes = self.targetFramesPerChunk * bytesPerFrame
                    let chunkData = self.accumulatedPCMData.prefix(chunkBytes)
                    self.accumulatedPCMData.removeSubrange(0 ..< chunkBytes)
                    try self.writeChunk(Data(chunkData), frameCount: self.targetFramesPerChunk)
                    self.accumulatedFrames -= self.targetFramesPerChunk
                }
            } catch {
                self.handleError("Audio chunk processing failed: \(error.localizedDescription)")
            }
        }
    }

    func stopAndFlush() {
        queue.sync {
            guard !self.hasFailed else {
                try? self.resetStateUnsafe()
                return
            }

            do {
                if self.accumulatedFrames > 0 {
                    try self.writeChunk(self.accumulatedPCMData, frameCount: self.accumulatedFrames)
                }
                try self.resetStateUnsafe()
            } catch {
                self.handleError("Failed while flushing audio chunks: \(error.localizedDescription)")
                try? self.resetStateUnsafe()
            }
        }
    }

    /// Flushes any buffered audio as a partial chunk without stopping recording.
    /// Call this before exporting clips to ensure all captured audio is available.
    func flushPartialChunk() {
        queue.sync {
            guard !self.hasFailed, self.accumulatedFrames > 0 else { return }
            do {
                try self.writeChunk(self.accumulatedPCMData, frameCount: self.accumulatedFrames)
                self.accumulatedPCMData = Data()
                self.accumulatedFrames = 0
            } catch {
                self.handleError("Failed to flush partial audio chunk: \(error.localizedDescription)")
            }
        }
    }

    func enqueueError(_ message: String) {
        queue.async {
            self.handleError(message)
        }
    }

    private func handleError(_ message: String) {
        guard !hasFailed else { return }
        hasFailed = true
        onError?(message)
    }

    private func convertToPCM16Mono8k(_ inputBuffer: AVAudioPCMBuffer) throws -> (Data, Int) {
        guard let converter, let outputFormat else {
            return (Data(), 0)
        }

        let ratio = outputFormat.sampleRate / max(inputBuffer.format.sampleRate, 1)
        let capacity = AVAudioFrameCount(max(1, Int((Double(inputBuffer.frameLength) * ratio).rounded(.up)) + 32))

        guard let convertedBuffer = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else {
            throw AudioChunkProcessorError.outputBufferAllocationFailed
        }

        var convertedData = Data()
        var totalFrames = 0
        var consumed = false

        while true {
            var localError: NSError?
            let status = converter.convert(to: convertedBuffer, error: &localError) { _, outStatus in
                if consumed {
                    outStatus.pointee = .noDataNow
                    return nil
                }
                consumed = true
                outStatus.pointee = .haveData
                return inputBuffer
            }

            if let localError {
                throw localError
            }

            let producedFrames = Int(convertedBuffer.frameLength)
            if producedFrames > 0 {
                guard let channelData = convertedBuffer.int16ChannelData else {
                    throw AudioChunkProcessorError.missingConvertedChannelData
                }
                let bytes = producedFrames * (bitsPerSample / 8)
                convertedData.append(contentsOf: UnsafeRawBufferPointer(start: channelData[0], count: bytes))
                totalFrames += producedFrames
            }

            switch status {
            case .haveData:
                continue
            case .inputRanDry, .endOfStream, .error:
                return (convertedData, totalFrames)
            @unknown default:
                return (convertedData, totalFrames)
            }
        }
    }

    private func writeChunk(_ pcmData: Data, frameCount: Int) throws {
        guard let sessionDirectory, let indexFileHandle else {
            throw NSError(domain: "AudioChunkProcessor", code: 2, userInfo: [NSLocalizedDescriptionKey: "Session directory or index file is unavailable."])
        }

        let startedAtMs = nextChunkStartMs
        let durationMs = Int((Double(frameCount) / Double(outputSampleRate) * 1000.0).rounded())
        let fileName = "chunk_\(chunkSequence)_\(startedAtMs).wav"
        let fileURL = sessionDirectory.appendingPathComponent(fileName)

        let bytesWritten = try WavFileWriter.writePCM16(
            samples: pcmData,
            sampleRate: outputSampleRate,
            channels: Int(outputChannels),
            to: fileURL
        )

        let metadata = AudioChunkMetadata(
            chunkId: "\(sessionId)-\(chunkSequence)",
            sessionId: sessionId,
            startedAtMs: startedAtMs,
            durationMs: durationMs,
            sampleRate: outputSampleRate,
            channels: Int(outputChannels),
            codec: "wav_pcm_s16le",
            fileName: fileName
        )

        let metadataLine = try encoder.encode(metadata)
        indexFileHandle.write(metadataLine)
        indexFileHandle.write(Data([0x0A]))

        onChunkWritten?(metadata, bytesWritten)

        chunkSequence += 1
        nextChunkStartMs += Int64(durationMs)
    }

    private func resetStateUnsafe() throws {
        if let indexFileHandle {
            try indexFileHandle.close()
        }

        indexFileHandle = nil
        converter = nil
        outputFormat = nil
        sessionId = ""
        sessionDirectory = nil
        chunkSequence = 0
        nextChunkStartMs = 0
        accumulatedPCMData = Data()
        accumulatedFrames = 0
        onChunkWritten = nil
        onError = nil
    }
}

// Shared audio collection state, statistics, and frame types used by the assistant runtime capture pipelines.

import Foundation

enum AudioCollectionState: Equatable {
    case idle
    case preparingAudioSession
    case waitingForDevice
    case recording
    case stopping
    case failed(String)
}

struct AudioChunkMetadata: Codable {
    let chunkId: String
    let sessionId: String
    let startedAtMs: Int64
    let durationMs: Int
    let sampleRate: Int
    let channels: Int
    let codec: String
    let fileName: String
}

struct AudioCollectionStats {
    var chunksWritten: Int
    var bytesWritten: Int64
    var lastChunkDurationMs: Int
    var startTimestampMs: Int64?
    var lastError: String?

    static var `default`: AudioCollectionStats {
        AudioCollectionStats(
            chunksWritten: 0,
            bytesWritten: 0,
            lastChunkDurationMs: 0,
            startTimestampMs: nil,
            lastError: nil
        )
    }
}

struct AudioClipExportWindow {
    let startTimestampMs: Int64
    let endTimestampMs: Int64
}

enum AudioClipExportError: LocalizedError {
    case invalidWindow
    case sessionDirectoryUnavailable
    case indexFileUnavailable
    case noAudioDataInWindow

    var errorDescription: String? {
        switch self {
        case .invalidWindow:
            return "Invalid clip window. endTimestampMs must be greater than startTimestampMs."
        case .sessionDirectoryUnavailable:
            return "No audio session directory is available for clip export."
        case .indexFileUnavailable:
            return "Audio chunk index file is unavailable."
        case .noAudioDataInWindow:
            return "No audio data found for the requested clip window."
        }
    }
}

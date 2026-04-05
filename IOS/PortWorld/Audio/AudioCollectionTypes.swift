// Shared audio collection state and runtime statistics for the active assistant capture pipeline.

import Foundation

enum AudioCollectionState: Equatable {
    case idle
    case preparingAudioSession
    case waitingForDevice
    case recording
    case stopping
    case failed(String)
}

struct AudioCollectionStats {
    var lastError: String?

    static var `default`: AudioCollectionStats {
        AudioCollectionStats(lastError: nil)
    }
}

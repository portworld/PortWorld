import Foundation

/// Coordinates exclusive audio-session intent across subsystems.
///
/// The arbiter models access as short-lived leases. Multiple concurrent leases are
/// allowed only when they request the same configuration.
actor AudioSessionArbiter {
    enum Configuration: Sendable, Equatable {
        case playAndRecordHFP
    }

    struct Lease: Sendable, Hashable {
        fileprivate let id: UUID
        let configuration: Configuration
    }

    enum ArbiterError: Error, LocalizedError, Sendable {
        case conflictingLease(requested: Configuration, active: Configuration)
        case unknownLease

        var errorDescription: String? {
            switch self {
            case let .conflictingLease(requested, active):
                return "Cannot acquire \(requested) while \(active) is active."
            case .unknownLease:
                return "Cannot release an unknown or already released audio session lease."
            }
        }
    }

    private var activeConfiguration: Configuration?
    private var activeLeaseIDs: Set<UUID> = []

    /// Acquires a lease for the requested configuration.
    /// - Throws: `ArbiterError.conflictingLease` when another configuration is active.
    /// - Returns: A lease token that must be released when work completes.
    func acquire(_ configuration: Configuration) throws -> Lease {
        if let activeConfiguration, activeConfiguration != configuration {
            throw ArbiterError.conflictingLease(requested: configuration, active: activeConfiguration)
        }

        let leaseID = UUID()
        activeConfiguration = configuration
        activeLeaseIDs.insert(leaseID)
        return Lease(id: leaseID, configuration: configuration)
    }

    /// Releases a previously acquired lease.
    /// - Throws: `ArbiterError.unknownLease` when the token is invalid or already released.
    func release(_ lease: Lease) throws {
        guard activeLeaseIDs.remove(lease.id) != nil else {
            throw ArbiterError.unknownLease
        }

        if activeLeaseIDs.isEmpty {
            activeConfiguration = nil
        }
    }

    /// Returns the currently active configuration, or `nil` when no lease is held.
    func currentConfiguration() -> Configuration? {
        activeConfiguration
    }
}

/// Owns a single arbiter lease for one runtime pipeline.
/// Provides idempotent acquire/release calls for coordinator wiring.
actor AudioSessionLeaseManager {
    private let arbiter: AudioSessionArbiter
    private var activeLease: AudioSessionArbiter.Lease?

    init(arbiter: AudioSessionArbiter) {
        self.arbiter = arbiter
    }

    /// Acquires a lease if one is not already held.
    /// - Throws: `AudioSessionArbiter.ArbiterError` for conflicts.
    func acquire(configuration: AudioSessionArbiter.Configuration) async throws {
        if let activeLease {
            if activeLease.configuration == configuration {
                return
            }
            throw AudioSessionArbiter.ArbiterError.conflictingLease(
                requested: configuration,
                active: activeLease.configuration
            )
        }

        activeLease = try await arbiter.acquire(configuration)
    }

    /// Releases the active lease, if any.
    /// - Throws: `AudioSessionArbiter.ArbiterError` when release validation fails.
    func releaseIfNeeded() async throws {
        guard let activeLease else { return }
        try await arbiter.release(activeLease)
        self.activeLease = nil
    }
}

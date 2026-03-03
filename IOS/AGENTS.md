# IOS/AGENTS.md

iOS-specific implementation guide. Applies to all work inside the `IOS/` directory.

---

## Documentation Map

For **non-trivial architecture, runtime, transport, or SDK changes**, read the relevant files below before implementing:

| File | Read when… |
|---|---|
| `IOS/docs/ARCHITECTURE.md` | Touching module boundaries, data flows, concurrency model, or design system |
| `IOS/docs/PRD.md` | Verifying functional requirements, transport contracts, or failure modes |
| `IOS/docs/IMPLEMENTATION_PLAN.md` | Checking which tasks belong to the current phase |
| `IOS/docs/TESTING.md` | Adding or modifying tests, or before a phase gate |
| `IOS/docs/Wearables DAT SDK.md` | Any code that touches the DAT SDK |

For **small, localised fixes** (single file, no API surface or concurrency change), read only the directly relevant file(s).

> **Archived docs** in `IOS/PortWorld/docs/` are hackathon v4 documents and are not authoritative.
> See `IOS/PortWorld/docs/ARCHIVE_NOTICE.md` for the mapping to current docs.

---





### Simulator Launch Guard (Mandatory)

To prevent accidental multi-simulator launches during sub-agent parallelization:

- Do not boot/install/launch Simulator by default.
- Simulator boot/install/launch is allowed only when the user explicitly requests UI smoke validation.
- Sub-agents must not run simulator launch commands.
- Only the coordinator agent may run simulator launch commands, and only once per verification cycle.
- Default verification in parallel work is build + tests.

### Session setup (run once per session)

```
1. session_show_defaults          — check any saved configuration
2. Locate the project in IOS/
3. Confirm available schemes
4. Discover available simulators (only if a user-requested UI smoke run is planned)
```

Discover the project path, scheme, and simulator each session. If a local machine override file exists at the repo root (e.g. `local.xcconfig` or `.local-defaults.json`), prefer its values.

Do **not** assume a fixed simulator ID or absolute local path — these change across machines and Xcode versions. The scheme is `PortWorld`.

### Common operations

```
Build app:        xcodebuild build (scheme: PortWorld)
Run unit tests:   xcodebuild test (target: PortWorldTests)
Boot simulator:   xcodebuild boot → install → launch  (manual-only, user-requested)
Screenshot:       xcodebuild screenshot  (only during explicit UI smoke validation)
UI automation:    snapshot/tap/type tools to verify UI states
```

---

## Documentation Lookup

Use **Ref MCP** and **Apple Docs MCP** when available:

```swift
// Before using an unfamiliar API:
mcp_ref_ref_search_documentation(query: "AVAudioSession allowBluetoothHFP iOS 17")
mcp_ref_ref_read_url(url: "<url from result>")

// For Apple framework reference:
mcp_apple_docs_search_apple_docs(query: "AVAudioPlayerNode scheduleBuffer")
mcp_apple_docs_get_platform_compatibility(apiUrl: "https://developer.apple.com/documentation/...")
mcp_apple_docs_get_related_apis(...)  // useful when exploring deprecated API alternatives
```

Key Apple frameworks for this project: `AVFoundation`, `AVAudioEngine`, `AVAudioSession`, `AVAssetWriter`, `URLSession`, `SwiftUI`, `@Observable`, `SFSpeechRecognizer`, `NWPathMonitor`.

Always call `get_platform_compatibility` before using any new Apple API to verify the iOS 17.0 minimum deployment target.

---

## Meta Wearables DAT SDK Rules

When writing any code that touches the DAT SDK:

1. **State the module:** `MWDATCore`, `MWDATCamera`, or `MWDATMockDevice`.
2. **Read the local SDK doc first:** `IOS/docs/Wearables DAT SDK.md`.
3. **Fetch the current API surface** if the local doc is insufficient — use `mcp_ref_ref_search_documentation` with the MWDAT SDK endpoint.
4. **iOS lifecycle constraints to respect:**
   - DAT camera streams are session-state driven; handle via observed stream/session transitions.
   - DAT stream quality is Bluetooth-bandwidth constrained; requested quality is not guaranteed.
   - HFP audio route must be configured before starting any audio workflow.
   - DAT microphone input is 8kHz mono.
5. **Name the source doc/path used** in your response (e.g. `IOS/docs/Wearables DAT SDK.md §3.2`). Do not generate SDK usage code without stating which section or doc informed it.
6. **If required SDK details are missing,** stop and fetch the exact MWDAT doc link before continuing.

---

## Preferred Code Patterns

### ViewModel (`@MainActor` + `@Observable`)

```swift
@MainActor
@Observable
final class QueryViewModel {
    var state: QueryState = .idle

    private let service: QueryService  // injected actor

    func submit(query: String) async {
        state = .loading
        do {
            let result = try await service.process(query)
            state = .success(result)
        } catch {
            state = .failure(error)
        }
    }
}
```

### Actor service

```swift
actor QueryService {
    private var session: URLSession = .shared

    func process(_ query: String) async throws -> QueryResult {
        // All mutable state is actor-isolated.
        // Never call DispatchQueue.sync here.
        let request = try buildRequest(query)
        let (data, _) = try await session.data(for: request)
        return try JSONDecoder().decode(QueryResult.self, from: data)
    }
}
```

### Error handling (no silent discards)

```swift
// ✅ Correct — propagate or log explicitly
do {
    try await uploader.upload(file)
} catch {
    logger.error("Upload failed: \(error)")
    throw error  // or handle intentionally
}

// ❌ Banned on I/O paths
try? uploader.upload(file)
```

### Logging

```swift
import OSLog
private let logger = Logger(subsystem: "com.portworld", category: "QueryService")

// ✅ Always use os_log in production paths
logger.info("Session started")

// ❌ Banned outside #if DEBUG
print("debug message")
```

---

## Concurrency Quick Reference

| Context | Primitive |
|---|---|
| UI state, ViewModels, Coordinators, SessionOrchestrator | `@MainActor` |
| Thread-isolated services | `actor` |
| AVAudioEngine tap callback | dedicated `DispatchQueue` only |
| Network calls | `async/await` + `URLSession` |

**Banned everywhere:**

- `DispatchQueue.sync` (except AVAudioEngine tap)
- `@unchecked Sendable` without an explanatory comment

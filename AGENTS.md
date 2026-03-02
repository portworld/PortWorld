# AGENTS.md

## Purpose

This file defines the minimum documentation context and tooling that coding agents must load before proposing architecture, writing code, or reviewing changes in this repository.

---

## Platform Scope (Mandatory)

- Primary platform: **iOS**
- Minimum deployment target: **iOS 17.0**
- Target device: iPhone + Meta Ray-Ban Gen 2 smart glasses
- Assume iOS-first decisions unless a task explicitly asks for Android.

---

## Current State

The codebase is a **hackathon MVP (v4) being refactored to a consumer-quality v1.0**.

- The runtime pipeline (WebSocket, audio capture, query bundle, playback) is fully implemented.
- The refactor is tracked in phases in `IOS/docs/IMPLEMENTATION_PLAN.md`.
- Do not add features until the phase they belong to is reached.
- Always leave the app compilable after every change.

---

## Always-Read Documentation Map

Read these files **in order** before any implementation work:

1. **Architecture:** `IOS/docs/ARCHITECTURE.md` — module map, data flows, concurrency model, design system
2. **Product requirements:** `IOS/docs/PRD.md` — functional requirements, transport contracts, failure modes
3. **Implementation plan:** `IOS/docs/IMPLEMENTATION_PLAN.md` — phase-by-phase tasks, per-file instructions
4. **Test strategy:** `IOS/docs/TESTING.md` — test inventory, manual acceptance tests, release gate
5. **Meta Wearables SDK:** `IOS/docs/Wearables DAT SDK.md` — DAT SDK integration reference

> **Old docs** in `IOS/PortWorld/docs/` are archived hackathon v4 documents.
> They are not authoritative. See `IOS/PortWorld/docs/ARCHIVE_NOTICE.md` for the mapping.

---

## Available MCP Tools — Use Them

### XcodeBuildMCP — iOS build, run, test, UI automation

XcodeBuildMCP is available and **must be used** for all Xcode-related tasks instead of running raw shell commands.

Before any build or test operation:

1. Call `session_show_defaults` to check current configuration.
2. Call `mcp_xcodebuildmcp_discover_projs` with `workspaceRoot: IOS/` to locate the project.
3. Call `mcp_xcodebuildmcp_list_schemes` to confirm available schemes.

Use cases:

- **Build:** use XcodeBuildMCP build tools, not `xcodebuild` in terminal.
- **Test:** use XcodeBuildMCP test tools to run `PortWorldTests`.
- **Simulator:** use XcodeBuildMCP to boot, install, and launch on simulator.
- **UI automation:** use snapshot/tap/type tools to verify UI states without manual steps.
- **Screenshots:** use `mcp_xcodebuildmcp_screenshot` when verifying UI changes.

### Ref MCP — Search up-to-date documentation

Use `mcp_ref_ref_search_documentation` and `mcp_ref_ref_read_url` to look up:

- Third-party library docs (SDKs, Swift packages)
- Any API where the local docs may be outdated or incomplete
- Stack Overflow / GitHub issues for specific error messages

Always search before assuming behaviour of an unfamiliar API.

```
// Example usage pattern:
mcp_ref_ref_search_documentation(query: "AVAudioSession allowBluetoothHFP iOS 17")
mcp_ref_ref_read_url(url: "<url from search result>")
```

### Apple Docs MCP — iOS and Swift API reference

Use `mcp_apple-docs_search_apple_docs` for all Apple framework questions:

- `AVFoundation`, `AVAudioEngine`, `AVAudioSession` — audio pipeline
- `URLSession` async/await — networking
- `SwiftUI`, `@Observable`, `@MainActor` — UI and concurrency
- `SFSpeechRecognizer` — wake word detection
- `NWPathMonitor` — network reachability
- `AVAssetWriter` — video encoding

Use `mcp_apple-docs_get_platform_compatibility` to verify minimum iOS version before using any API.

Use `mcp_apple-docs_get_related_apis` when exploring alternatives to deprecated APIs.

```
// Example usage pattern:
mcp_apple-docs_search_apple_docs(query: "AVAudioPlayerNode scheduleBuffer")
mcp_apple-docs_get_platform_compatibility(apiUrl: "https://developer.apple.com/documentation/avfoundation/avaudioplayernode")
```

---

## Meta Wearables SDK Rules

When writing any code that touches the DAT SDK:

1. **State the module explicitly:** `MWDATCore`, `MWDATCamera`, or `MWDATMockDevice`.
2. **Read the local SDK doc first:** `IOS/docs/Wearables DAT SDK.md`.
3. **Fetch current API surface** if local doc is insufficient — use `mcp_ref_ref_search_documentation` with the MWDAT SDK endpoint.
4. **iOS lifecycle constraints to respect:**
   - DAT camera streams are session-state driven; handle via observed stream/session transitions.
   - DAT stream quality is Bluetooth-bandwidth constrained; requested quality is not guaranteed.
   - HFP audio route must be configured before starting any audio workflow.
   - DAT microphone input is 8kHz mono.
5. **Never generate SDK usage code** without citing the relevant module and iOS integration constraints from the docs.
6. **If required SDK details are missing,** stop and fetch the exact MWDAT doc link before continuing.

---

## Concurrency Rules (Mandatory)

The project uses a strict two-primitive concurrency model. Do not deviate.

| Where                                                           | Use                                                         |
| --------------------------------------------------------------- | ----------------------------------------------------------- |
| UI state, ViewModels, Coordinators, SessionOrchestrator         | `@MainActor`                                                |
| Thread-isolated services (WebSocket, uploader, buffer, arbiter) | `actor`                                                     |
| AVAudioEngine tap callback (AVFoundation requirement)           | dedicated `DispatchQueue` — no other use of `DispatchQueue` |
| All network calls                                               | `async/await` with `URLSession`                             |

**Banned patterns:**

- `DispatchQueue.sync` outside the audio engine tap
- Bare `print()` outside `#if DEBUG`
- `try?` that silently discards errors on I/O paths
- `@unchecked Sendable` without a comment explaining the exception

---

## Implementation Policy

- Keep every change aligned with `IOS/docs/PRD.md` + `IOS/docs/ARCHITECTURE.md`.
- If a proposed change conflicts with either, flag it explicitly before proceeding.
- Each phase in `IMPLEMENTATION_PLAN.md` has a completion criterion — verify it before moving to the next phase.
- No secrets (API keys, tokens, IP addresses) in source; use xcconfig injection.

---

## Output Expectations

For each non-trivial code change, state:

1. Which docs were consulted (file paths).
2. Which MWDAT module was used (if DAT SDK touched) and why.
3. Which MCP tools were used for research (Apple Docs, Ref, XcodeBuildMCP).
4. Any iOS lifecycle or integration assumptions made.
5. Which phase of `IMPLEMENTATION_PLAN.md` the change belongs to.

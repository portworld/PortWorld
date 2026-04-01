# IOS/AGENTS.md

iOS-specific implementation guide for work under `IOS/`.

---

## Current Reality

- `IOS/PortWorld/` is the only active iOS runtime source tree.
- The app is phone-first. Active focus is clean UI/UX polish.
- Next phases: code cleanup, removal of unused features (phone-specific features, mock device kit), and App Store publishing prep.
- Ray-Ban Meta Gen 2 hardware testing is deferred until the app is near publishing.
- The active DAT / wearables runtime lives under `IOS/PortWorld/Runtime/Glasses/`.
- Any leftover `IOS/PortWorld/FutureHardware/` content should be treated as cleanup debt, not implementation authority.
- Historical iOS runtime context lives in git history.

---

## Source Tree Mental Model

- `IOS/PortWorld/`
  Active app shell and phone runtime
- `IOS/PortWorld/Runtime/Assistant/`
  Assistant orchestration and runtime-owned UI state
- `IOS/PortWorld/Runtime/Transport/`
  Backend websocket client, wire types, transport support
- `IOS/PortWorld/Runtime/Playback/`
  Assistant playback engine and route/interruption handling
- `IOS/PortWorld/Runtime/Wake/`
  Wake/sleep detection and speech recognizer-backed wake engine
- `IOS/PortWorld/Runtime/AudioIO/`
  Phone audio bridge
- `IOS/PortWorld/Runtime/Glasses/`
  Active DAT / wearables lifecycle, session, and vision capture runtime
- git history
  Historical runtime context — reference only

---

## Verification Policy

### Standard order

```text
1. Build:       xcodebuild build
2. Tests:       xcodebuild test
3. UI smoke:    manual-only, user-requested
```

### Practical rule

- Build after any non-trivial iOS change.
- Run `xcodebuild test` only when tests are relevant and the user expects test execution.
- For small, isolated code or docs changes, build-only verification is enough.

### Simulator guard

- Do not boot/install/launch Simulator unless the user explicitly asks for UI smoke validation.
- Sub-agents must never run simulator commands.
- In parallel work, default verification is build only.
- `test_sim` is banned.

### Xcode project defaults

- Scheme: `PortWorld`
- Discover project path, scheme, and simulator availability each session if needed.
- Do not assume fixed simulator IDs or machine-specific local overrides.

---

## Documentation Lookup

Use Apple Docs MCP and Ref MCP when local knowledge might be stale or the API is unfamiliar.

Good cases:

- AVFoundation / AVAudioSession behavior
- speech recognition / SFSpeech APIs
- SwiftUI / observation / lifecycle APIs
- URLSession / networking behavior

Always verify Apple API availability against the iOS 17.0 minimum deployment target before introducing new framework usage.

---

## Concurrency Rules

| Context | Primitive |
|---|---|
| UI state, ViewModels, Coordinators, SessionOrchestrator | `@MainActor` |
| Thread-isolated services | `actor` |
| AVAudioEngine tap callback | dedicated `DispatchQueue` only |
| Network calls | `async/await` + `URLSession` |

Banned:

- `DispatchQueue.sync` outside the AVAudioEngine tap context
- silent `try?` on I/O paths
- `@unchecked Sendable` without an explanatory comment

---

## Preferred iOS Patterns

### State ownership

- controller/runtime owns mutable runtime state
- view model is a thin observation/action bridge
- views render the published status model

### Logging

- use high-signal logs only
- keep lifecycle, interruption, queue-pressure, playback-control, and real error logs
- avoid hot-path spam in stable codepaths
- use `print()` only inside `#if DEBUG`

### UI/UX focus (active phase)

- prefer clean, minimal SwiftUI views
- keep view logic thin; push logic into the runtime or view model
- do not add UI features unless explicitly requested

---

## Common Decisions

When unsure:

- prefer the active `IOS/PortWorld/` path over legacy code
- prefer small, conservative refactors over broad rewrites
- prefer one obvious ownership boundary over layered duplicate state
- prefer the active `Runtime/Glasses/` path for DAT / wearables work
- do not revive `FutureHardware/` or archived historical runtime code unless explicitly asked

---

## Response Requirements

For non-trivial iOS changes, include:

1. **Files / areas changed**
2. **MCP tools used**
3. **Assumptions made**

# IOS/AGENTS.md

iOS-specific implementation guide for work under `IOS/`.

---

## Current Reality

- `IOS/PortWorld/` is the only active iOS runtime source tree.
- The app is currently phone-first.
- Future hardware work should layer on top of the cleaned phone runtime, not revive legacy runtime architecture.
- Historical / compatibility code lives under `IOS/Legacy/` and should be treated as reference or migration context unless the task explicitly says otherwise.

---

## Documentation Map

Read the relevant docs before non-trivial iOS work:

| File | Read when… |
|---|---|
| `docs/IOS_PHONEONLY_TO_GLASSES_ROADMAP.md` | Checking current phase status, forward sequencing, or how glasses work should build onto the cleaned phone runtime |
| `docs/intermediary/PHASE1_IMPLEMENTATION.md` | You need the detailed record of the completed phone-runtime cleanup and stabilization work |
| `IOS/docs/Wearables DAT SDK.md` | Any code that touches the DAT SDK |
| `IOS/docs/archived/ARCHITECTURE.md` | Looking up legacy architecture rationale |
| `IOS/docs/archived/PRD.md` | Looking up historical requirements or old compatibility assumptions |
| `IOS/docs/archived/IMPLEMENTATION_PLAN.md` | Tracing earlier refactor intent or old cleanup plans |
| `IOS/docs/archived/TESTING.md` | Reusing historical test ideas selectively, not as the active gate |

For small, localized fixes, read only the directly relevant files.

Rules of authority:

1. `docs/IOS_PHONEONLY_TO_GLASSES_ROADMAP.md`
2. current active code in `IOS/PortWorld/`
3. `docs/intermediary/PHASE1_IMPLEMENTATION.md` for historical Phase 1 detail
4. historical docs only for context

If archived docs conflict with the active runtime plan or current code, the active runtime plan wins unless the task explicitly concerns migration/history.

---

## Source Tree Mental Model

Use this mental model when navigating the iOS app:

- `IOS/PortWorld/`
  Active app shell, active phone runtime, active future-hardware path
- `IOS/PortWorld/Runtime/Assistant/`
  Assistant orchestration and runtime-owned UI state
- `IOS/PortWorld/Runtime/Transport/`
  Backend websocket client, wire types, transport support
- `IOS/PortWorld/Runtime/Playback/`
  Assistant playback engine and route/interruption handling
- `IOS/PortWorld/Runtime/Wake/`
  Wake/sleep detection and speech recognizer-backed wake engine
- `IOS/PortWorld/Runtime/AudioIO/`
  Phone microphone/playback bridge
- `IOS/PortWorld/FutureHardware/`
  Secondary DAT / wearables / mock-device path
- `IOS/Legacy/`
  Historical runtime and compatibility code, not the default implementation surface

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
- DAT SDK references when the local DAT doc is insufficient

Always verify Apple API availability against the iOS 17.0 minimum deployment target before introducing new framework usage.

---

## DAT SDK Rules

When touching the DAT SDK:

1. State the module: `MWDATCore`, `MWDATCamera`, or `MWDATMockDevice`.
2. Read `IOS/docs/Wearables DAT SDK.md` first.
3. If the local DAT doc is insufficient, fetch the current API/docs before implementing.
4. Respect these constraints:
   - DAT camera streams are session-state driven
   - requested stream quality is bandwidth-constrained and not guaranteed
   - HFP audio route must be configured before DAT audio workflows
   - DAT microphone input is 8kHz mono
5. Name the doc/path used in the response.

Do not write DAT code from memory alone when exact API details are uncertain.

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

### Runtime evolution

- add new behavior on top of the cleaned runtime
- do not reintroduce old generic transport/runtime abstractions without a clear need
- keep future-hardware code from reshaping the phone-first path until that phase explicitly starts

---

## Common Decisions

When unsure:

- prefer the active `IOS/PortWorld/` path over legacy code
- prefer small, conservative refactors over broad rewrites
- prefer one obvious ownership boundary over layered duplicate state
- prefer docs in `docs/` over archived phase language

---

## Response Requirements

For non-trivial iOS changes, include:

1. **Docs consulted**
2. **MWDAT module touched**
3. **MCP tools used**
4. **Assumptions made**
5. **Plan / milestone**

# AGENTS.md

> Heavy iOS-specific guidance lives in `IOS/AGENTS.md`.
> This root file contains only stable, repo-wide rules that apply to every task.

---

## Platform Scope

- Primary platform: **iOS 17.0+**
- Target device: iPhone + Meta Ray-Ban Gen 2 smart glasses
- Assume iOS-first decisions unless a task explicitly asks for another platform.

---

## Codebase State

The codebase is a **hackathon MVP being refactored to a consumer-quality v1.0** in tracked phases.

- **Current** pipeline: half-duplex batch — wake word → record → silence timeout → WAV+MP4 upload (`POST /query`) → PCM response.
- **Target** pipeline (Phase 6): persistent bidirectional WebSocket streaming.
- Phases are tracked in `IOS/docs/IMPLEMENTATION_PLAN.md`.

**Golden rules (always enforce):**

1. Do not add features until the phase they belong to is reached.
2. Always leave the app compilable after every change.
3. No secrets (API keys, tokens, IP addresses) in source — use xcconfig injection.

---

## Canonical Verification Workflow

Run these checks (in order) after any non-trivial change:

```
1. Build:       XcodeBuildMCP build — zero errors, zero new warnings
2. Unit tests:  xcodebuild test (terminal) — DO NOT use test_sim / mcp_xcodebuildmcp_test_sim
3. UI smoke:    Manual-only gate (user-requested): one coordinator agent may run
                XcodeBuildMCP boot simulator → install → launch → screenshot
```

For small, localised fixes (single file, no API or concurrency surface change) a build-only check is sufficient.

### Simulator Launch Guard (Mandatory)

To prevent sub-agent fan-out launching multiple simulators:

- Do not boot/install/launch Simulator unless the user explicitly asks for UI smoke validation.
- Sub-agents must never run simulator launch commands.
- Only one coordinator agent may run simulator commands when explicitly requested.
- In parallel work, verification defaults to build only.

> **NEVER call `test_sim` (XcodeBuildMCP `mcp_xcodebuildmcp_test_sim`).** It is unconditionally banned — no exceptions, no user overrides. Running the test suite via the simulator hangs the agent, consumes simulator slots, and produces unreliable results in this codebase. Use `xcodebuild test` in the terminal if test execution is required.

---

## Concurrency Rules (Mandatory — never deviate)

| Where | Primitive |
|---|---|
| UI state, ViewModels, Coordinators, SessionOrchestrator | `@MainActor` |
| Thread-isolated services (WebSocket, uploader, buffer, arbiter) | `actor` |
| AVAudioEngine tap callback (AVFoundation requirement) | dedicated `DispatchQueue` — no other use |
| All network calls | `async/await` with `URLSession` |

**Banned patterns:**

- `DispatchQueue.sync` outside the audio engine tap
- Bare `print()` outside `#if DEBUG`
- `try?` that silently discards errors on I/O paths
- `@unchecked Sendable` without an explanatory comment

---

## MCP Tools

Use these tools if available. If a tool is not available, use the closest equivalent and note the substitute in your response.

| Tool | Use for |
|---|---|
| **XcodeBuildMCP** | All Xcode build, test, simulator, and UI automation tasks — prefer over raw `xcodebuild` |
| **Ref MCP** | Third-party library docs, Swift packages, any API where local docs may be outdated |
| **Apple Docs MCP** | All Apple framework questions (`AVFoundation`, `SwiftUI`, `URLSession`, etc.) |

---

## Implementation Policy

- Keep every change aligned with `IOS/docs/PRD.md` and `IOS/docs/ARCHITECTURE.md`.
- If a proposed change conflicts with either, flag it explicitly before proceeding.
- Each phase in `IOS/docs/IMPLEMENTATION_PLAN.md` has a completion criterion — verify it before advancing.

---

## Output Expectations (Non-Trivial Changes)

State the following in your response:

1. **Docs consulted** — file paths or URLs.
2. **MWDAT module touched** (if DAT SDK involved) — `MWDATCore`, `MWDATCamera`, or `MWDATMockDevice`.
3. **MCP tools used** — which tools provided research and what they returned.
4. **Assumptions made** — iOS lifecycle, integration, or API behaviour assumptions.
5. **Phase** — which phase of `IMPLEMENTATION_PLAN.md` this change belongs to.

---

> See `IOS/AGENTS.md` for the full iOS implementation guide: docs map, XcodeBuildMCP workflow, DAT SDK rules, concurrency examples, and pattern reference.

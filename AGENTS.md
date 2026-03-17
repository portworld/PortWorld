# AGENTS.md

> Detailed iOS implementation guidance lives in `IOS/AGENTS.md`.
> This root file contains stable repo-wide rules.

---

## Platform Scope

- Primary platform: **iOS 17.0+**
- Target device: iPhone (primary); Meta Ray-Ban Gen 2 (future — deferred until near publishing)
- Default to iOS-first decisions unless the task explicitly targets another platform

---

## Current Repo State

The codebase is a hackathon MVP being cleaned up and stabilized for production.

**Backend** — active focus: removing non-production code, fixing gaps and reliability issues, making the backend deployable. Next: a developer CLI for easy self-hosting and deployment.

**iOS app** — active focus: clean UI/UX polish. Next: code cleanup and App Store publishing prep. Glasses / mock-device / phone-specific features will be removed or deferred as the app approaches publishing; Ray-Ban Meta Gen 2 hardware testing will happen once the app is close to ready.

Historical docs:

- `docs/archived/ios-history/`
- `docs/archived/maintainer-roadmaps/`

Use archived docs only for migration context, historical rationale, or explicit user-requested research.

Golden rules:

1. Do not add features unless the user explicitly requests them.
2. Always leave the app compilable after every change.
3. No secrets in source. Use xcconfig or environment-based injection.

---

## Active Source Of Truth

- Active iOS runtime: `IOS/PortWorld/`
- Historical iOS runtime / compatibility code: `IOS/Legacy/`
- Active backend: `backend/`

Do not treat legacy code or archived docs as implementation authority over the active runtime.

---

## Verification Workflow

Run these checks after any non-trivial change:

```text
1. Build:       xcodebuild build — zero errors, zero new warnings
2. Unit tests:  xcodebuild test (terminal) — DO NOT use test_sim
3. UI smoke:    Manual-only gate, and only when the user explicitly asks for it
```

For small, localized fixes with no API or concurrency surface change, build-only verification is sufficient.

### Backend Test Policy

- Do not add backend pytest files by default.
- Do not run backend pytest by default.
- Backend regression tests are deferred unless the user explicitly asks for them.
- For backend work, prefer implementation, source inspection, and manual/runtime validation over speculative pytest maintenance.

### Simulator Guard

- Do not boot/install/launch Simulator unless the user explicitly asks for UI smoke validation.
- Sub-agents must never run simulator launch commands.
- In parallel work, default verification is build only.
- `test_sim` is banned with no exceptions.

---

## Concurrency Rules

| Where | Primitive |
|---|---|
| UI state, ViewModels, Coordinators, SessionOrchestrator | `@MainActor` |
| Thread-isolated services | `actor` |
| AVAudioEngine tap callback | dedicated `DispatchQueue` only |
| Network calls | `async/await` with `URLSession` |

Banned patterns:

- `DispatchQueue.sync` outside the AVAudioEngine tap context
- bare `print()` outside `#if DEBUG`
- `try?` that silently discards I/O errors
- `@unchecked Sendable` without an explanatory comment

---

## MCP Tools

Use these tools when available:

| Tool | Use for |
|---|---|
| **xcodebuild / Xcode MCP** | Xcode build, test, simulator, and project inspection tasks |
| **Ref MCP** | Third-party docs, package docs, and non-Apple APIs |
| **Apple Docs MCP** | Apple framework/API documentation |

If a preferred tool is unavailable, use the closest substitute and note that in the response.

---

## Output Expectations

For non-trivial changes, state:

1. **Files / areas changed**
2. **MCP tools used**
3. **Assumptions made**

---

> See `IOS/AGENTS.md` for the iOS-specific operational guide.

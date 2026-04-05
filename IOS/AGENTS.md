# IOS/AGENTS.md

iOS-specific implementation guide for work under `IOS/`.

---

## Current Reality

- `IOS/PortWorld/` is the only active iOS runtime source tree.
- The app is an iPhone-hosted SwiftUI shell with active Meta glasses, backend, wake-word, audio, and vision runtime surfaces.
- `PortWorldApp` owns the shared `WearablesRuntimeManager`.
- `MainAppView` and `MainAppRouteResolver` are the shell authority for splash, onboarding, and post-onboarding routing.
- Wake practice is active code. Do not treat it as dead flow or deferred work.
- `IOS/PortWorld/FutureHardware/` is not part of the current source tree and must not be treated as implementation authority.
- Historical iOS context lives in git history and `docs/archived/`. Use it only for migration context or explicit historical research.

---

## Source Tree Mental Model

- `IOS/PortWorld/PortWorldApp.swift`
  App entrypoint. Boots the wearables runtime and forwards incoming URLs.
- `IOS/PortWorld/Views/MainAppView.swift`
  Root route shell for splash, onboarding, and the post-onboarding shell.
- `IOS/PortWorld/App/`
  Onboarding steps, post-onboarding tabs, settings flows, backend setup, wake practice, profile interview, and persistence-backed app stores.
- `IOS/PortWorld/ViewModels/`
  SwiftUI bridge layer. `AssistantRuntimeViewModel` coordinates assistant lifecycle plus glasses readiness for the UI.
- `IOS/PortWorld/Runtime/Assistant/`
  Assistant orchestration, conversation lifecycle, runtime state, and backend readiness handling.
- `IOS/PortWorld/Runtime/Transport/`
  WebSocket transport, wire types, runtime support, and vision frame upload.
- `IOS/PortWorld/Runtime/Wake/`
  Wake phrase facade plus concrete wake engines.
- `IOS/PortWorld/Runtime/Playback/`
  Assistant playback graph, playback state, route handling, and interruption behavior.
- `IOS/PortWorld/Runtime/AudioIO/`
  Glasses audio bridge used by the assistant runtime.
- `IOS/PortWorld/Runtime/Glasses/`
  DAT configuration, Meta registration/discovery, glasses session lifecycle, photo capture, and wearables runtime ownership.
- `IOS/PortWorld/Audio/`
  Shared audio session, capture, PCM relay, and engine-level primitives.
- `IOS/PortWorld/Utilities/`
  Small support utilities only. Do not move runtime ownership here.

Default to these active paths. Do not resurrect removed or historical folders as design authority.

---

## Onboarding And Routing

The live route chain is:

`PortWorldApp` -> `MainAppView` -> `MainAppRouteResolver` -> `AppRoute`

Current onboarding order:

1. Welcome
2. Feature highlights
3. Backend intro
4. Backend setup / validation
5. Meta connection
6. Wake practice
7. Profile interview
8. Home shell

Route rules:

- While `WearablesRuntimeManager.configurationState` is `.idle` or `.configuring`, the app stays on splash.
- Meta completion keeps the user on the onboarding path through wake practice and profile interview.
- Meta skip is a real path and can send the user directly to home.
- `PostOnboardingShellView` is the steady-state shell and owns the Home, Agent, and Settings tabs.

Persistence rules:

- `OnboardingStore` is the source of truth for onboarding progress.
- If you change route order or gating, update `MainAppRouteResolver` and `OnboardingStore` together.
- Keep `OnboardingStore.normalize` aligned with any onboarding changes. `profileCompleted` currently backfills `metaCompleted` and `wakePracticeCompleted`.

---

## Verification Workflow

Current project reality:

- One app target: `PortWorld`
- Two shared schemes: `PortWorld`, `PortWorldDev`
- Deployment target: iOS 17.0
- No test target is currently configured in the shared schemes

Default verification order:

```text
1. Build:       xcodebuild build
2. Tests:       only if a real test target exists or the user explicitly asks
3. UI smoke:    manual-only and only when the user explicitly asks
```

Practical rules:

- Build after any non-trivial iOS change.
- For docs-only or tiny metadata changes, lightweight verification is enough.
- Do not present `xcodebuild test` as routine verification while the project has no active test bundle.
- When behavior validation matters, prefer focused manual checks against the real backend/runtime flow over speculative simulator automation.

Simulator guardrails:

- Do not boot, install, or launch Simulator unless the user explicitly asks for UI smoke validation.
- Sub-agents must not run simulator commands.
- Do not assume fixed simulator IDs or machine-local simulator state.
- `test_sim` is banned.

Tool preference:

- Prefer Xcode MCP for project inspection and builds.
- Use raw `xcodebuild` only if MCP is unavailable.

---

## Concurrency Rules

Use the concurrency model already established in the codebase:

| Context | Primitive |
|---|---|
| App stores, view models, runtime coordinators, wearables/session owners | `@MainActor` |
| Transport and upload services with independent mutable state | `actor` |
| Framework callbacks that are already guaranteed on main | `MainActor.assumeIsolated` sparingly |
| Cross-boundary callback handoff back to owned state | `Task { @MainActor ... }` |

Implementation rules:

- Keep stateful runtime owners on `@MainActor` unless there is a strong isolation reason not to.
- Keep `BackendSessionClient` and `VisionFrameUploader` actor-isolated.
- In audio and framework callback paths, do minimal work before hopping back to owned state.
- Treat transport connection state and protocol readiness as different things. Socket connection alone is not assistant readiness.

Banned:

- `DispatchQueue.sync` outside tightly scoped low-level audio internals
- silent `try?` on I/O or network paths
- `@unchecked Sendable` without an inline safety explanation
- bare `print()` outside `#if DEBUG`

---

## Documentation Lookup

Use Apple Docs MCP when local knowledge may be stale, especially for:

- AVFAudio / AVAudioSession / AVAudioEngine behavior
- Speech framework APIs and authorization behavior
- SwiftUI scene phase and lifecycle behavior
- URLSession / WebSocket behavior
- iOS 17 availability questions

Use Ref MCP for third-party dependencies when needed.

---

## Response Requirements

For non-trivial iOS work, include:

1. **Files / areas changed**
2. **MCP tools used**
3. **Assumptions made**

If verification was limited, manual-only, or skipped because the change was docs-only, say so explicitly.

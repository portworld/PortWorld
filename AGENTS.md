# AGENTS.md

> Detailed iOS implementation guidance lives in `IOS/AGENTS.md`.
> This root file contains stable repo-wide rules and current repo boundaries.

---

## Product Surface

PortWorld currently has four real product/code surfaces:

- `IOS/PortWorld/`
  Active iOS app runtime, including phone and Meta glasses routes
- `backend/`
  Active Python backend runtime, operator entrypoints, and deploy-facing server code
- `portworld_cli/`
  Active public CLI package published as `portworld`
- `portworld_shared/`
  Shared Python contract layer used by the CLI and backend

Default to the subsystem the user is actually touching. Do not treat the repo as iOS-only.

---

## Current Reality

- The repo is close to a release-shaped state, not just a hackathon prototype cleanup pass.
- Meta glasses support is implemented in the iOS app and should be treated as active product surface, not deferred-only roadmap work.
- The public CLI is a first-class release target. It is intended to be installable from PyPI and usable without cloning the repo.
- The backend is a first-class release target. Published/container workflows are expected to use GHCR backend images.
- `portworld_shared/` is the shared contract boundary. Do not recreate `backend` imports inside `portworld_cli` for metadata, versioning, or packaging glue.
- Archived docs and git history are historical context only. They are not implementation authority for new work.

Golden rules:

1. Do not add features unless the user explicitly requests them.
2. Keep the touched subsystem working after every change.
3. No secrets in source, examples, screenshots, or generated release artifacts.
4. Prefer stable package/runtime boundaries over cross-import shortcuts.

---

## Source Of Truth

- Active iOS runtime: `IOS/PortWorld/`
- Active backend runtime: `backend/`
- Active public CLI: `portworld_cli/`
- Active shared Python contracts: `portworld_shared/`
- CLI agent skill (Codex, Cursor, etc.): `skills/portworld-cli-autopilot/` — install for end users with `npx skills add` ([skills/README.md](skills/README.md))
- Historical docs: `docs/archived/`
- Historical runtime context: git history

Use archived docs or old code only for migration context, rationale lookup, or explicit user-requested research.

---

## Verification Workflow

Choose verification based on the subsystem you changed.

### iOS changes

```text
1. Build:       xcodebuild build
2. Unit tests:  xcodebuild test (terminal only)
3. UI smoke:    manual-only and only when the user explicitly asks
```

- Build after any non-trivial iOS change.
- Do not boot/install/launch Simulator unless the user explicitly asks for UI smoke validation.
- `test_sim` is banned.

### Backend / CLI / shared Python changes

- Run the smallest verification that proves the changed path still works.
- Preferred checks:
  - `python -m py_compile` on touched Python files
  - `python -m build --sdist --wheel` when packaging, release, or install behavior changes
  - targeted CLI smoke checks such as `portworld --help`, `portworld --version`, `portworld providers list`, `portworld ops ...`, or other directly affected commands
  - targeted runtime/manual checks when touching backend behavior

### Backend pytest policy

- Existing backend tests live under `tests/backend/`.
- Do not add backend pytest files by default.
- Do not run the broad backend pytest suite by default.
- Use targeted pytest only when the user explicitly asks for it or when a change clearly depends on a specific existing test-covered contract.
- For most backend work, prefer implementation, source inspection, packaging checks, and focused runtime validation over speculative test churn.

For small, localized docs or metadata changes, lightweight verification is enough.

---

## Release Hygiene

- Treat `portworld` PyPI packaging as a public interface.
- The CLI wheel/sdist must not accidentally ship repo-local junk, generated artifacts, or backend source when the package boundary says it should not.
- Published CLI workflows should work without a repo checkout.
- Source-checkout workflows may depend on the local repo, but that boundary should stay explicit.
- Do not commit:
  - `.env` files with real values
  - build outputs
  - `__pycache__`
  - `.DS_Store`
  - local run logs or exported artifacts unless the user explicitly asks

When touching release-sensitive files such as `pyproject.toml`, installer flows, package discovery, or build inputs, verify the resulting artifacts, not just the source diff.

---

## Concurrency And Code Quality

### Swift / iOS

| Context | Primitive |
|---|---|
| UI state, ViewModels, Coordinators, session orchestration | `@MainActor` |
| Thread-isolated services | `actor` |
| AVAudioEngine tap callback | dedicated `DispatchQueue` only |
| Network calls | `async/await` with `URLSession` |

Banned:

- `DispatchQueue.sync` outside the AVAudioEngine tap context
- bare `print()` outside `#if DEBUG`
- `try?` that silently discards I/O errors
- `@unchecked Sendable` without an explanatory comment

### Python / backend / CLI

- Prefer explicit package boundaries over hidden runtime coupling.
- Keep `portworld_cli` repo-free for public install flows.
- Keep shared contract logic in `portworld_shared`, not duplicated across backend and CLI.
- Avoid broad re-export surfaces unless they provide real user value.

---

## Tool Preference

Use these tools when available:

| Tool | Use for |
|---|---|
| Xcode / xcodebuild MCP | iOS build, test, project inspection |
| Apple Docs MCP | Apple framework/API documentation |
| Ref MCP | third-party package/framework documentation |

If a preferred tool is unavailable, use the closest substitute and note that in the response.

---

## Output Expectations

For non-trivial changes, state:

1. **Files / areas changed**
2. **MCP tools used**
3. **Assumptions made**

If verification was limited or a release-sensitive path was not fully tested, say so explicitly.

---

> See `IOS/AGENTS.md` for iOS-specific operational guidance.

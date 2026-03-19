# Framework Backend Gap Audit

## Summary

`framework/` is an old hackathon-era backend stack. It is not imported by the active runtime code, and it should not be treated as an implementation authority for the current `backend/`.

The purpose of this audit is to identify the small subset of ideas in `framework/` that are still worth considering before the folder is eventually deleted. The result is intentionally narrow: "implemented in `framework/` but not in `backend/`" is not enough by itself to justify migration.

Current grounded facts:

- `framework/` is not imported by active backend or CLI runtime code.
- The most plausible carryover value is in observability and diagnostics, not in the old provider pipeline or framework product shape.
- The root `README.md` still advertises framework-era API endpoints, so deleting `framework/` later will require doc cleanup outside the folder.

## What `framework/` Implemented

At a high level, `framework/` provided:

- a generic multimodal "framework" API surface with pipeline, debug, config, agent, and run-inspection endpoints
- a separate provider stack centered on Voxtral, Nemotron, ElevenLabs, Mistral, and optional Strands/Weave integrations
- runtime agent preset and plugin-style configuration
- a tracing subsystem with pluggable backends
- persistent run logging and read APIs for recent runs
- richer debug capture helpers for request/response inspection

Most of that implementation is tied to the old hackathon architecture and should not be revived directly.

## Already Superseded By `backend/`

The active `backend/` already replaces the following areas well enough:

- **Core runtime execution**
  - `backend/` has its own realtime, vision, tooling, and session runtime architecture.
  - The framework-era `/v1/pipeline*` flows and old provider orchestration do not match the current backend shape.

- **Auth, health, and basic configuration**
  - `backend/` already owns HTTP/WebSocket auth, health/live/ready checks, provider validation, and operator configuration.
  - The framework config-template and agent-template APIs are not aligned with the current product direction.

- **Operator workflow**
  - `portworld_cli/` and `backend/cli.py` now define the active operator and deploy workflow.
  - The framework-era backend behaved like a general developer framework; the current backend is a product backend with an operator CLI.

- **Provider-specific hackathon stack**
  - The old Voxtral/Nemotron/ElevenLabs orchestration is not the source of truth for current realtime or vision behavior.
  - The current provider model is already centered on provider requirements and selected provider IDs inside `backend/`.

## Worth Considering For `backend/`

The following ideas remain plausibly useful and are the only areas that look worth carrying forward.

### Priority 1: Tracing Foundation

**What `framework/` had**

- a `TraceManager` that emitted named runtime events through a collector
- pluggable trace backends
- a simple `console` backend
- optional `weave` backend
- historical `strands` backend support
- sanitized event payload capture before export

**Why it is still useful**

- The active backend has local debug logging and websocket tracing, but no explicit provider-agnostic event collector for runtime stages.
- Observability is currently fragmented across logger calls, request handlers, and provider-specific debug output.
- A tracing abstraction could make request/session/vision/tool execution easier to inspect without exposing raw secrets or reviving public debug endpoints.

**Where the current backend has a gap**

- no single event collector abstraction spanning realtime, vision, tools, and session lifecycle
- no optional trace sink model for structured event export
- no explicit sanitized trace payload model beyond ad hoc logging and local helper functions

**What a modern backend version should look like**

- provider-agnostic runtime events tied to current backend flows, not the framework pipeline schema
- optional backends with `console` first
- `weave` as the main external tracing sink worth reconsidering
- internal capture scoped to current session, vision, and tool execution paths
- strict secret redaction and bounded payload capture

**Why the framework version should not be copied directly**

- it is wired to the old runtime config and provider model
- it assumes a different API surface and different orchestration boundaries
- it includes historical `strands` support that should be treated as context, not a default recommendation

### Priority 2: Run Inspection

**What `framework/` had**

- JSONL-backed persistent run logs
- in-memory recent-run buffer
- `/v1/runs` and `/v1/runs/{query_id}` inspection endpoints
- run records containing STT, video, main LLM, TTS, tool, and metadata fields

**Why it is still useful**

- The active backend has health/readiness and Cloud Run log access, but not a first-class notion of persisted per-run inspection for current backend flows.
- A compact run log would help diagnose real session failures, provider regressions, and tool/runtime behavior over time.

**Where the current backend has a gap**

- no persistent run ledger for selected session/vision/tool executions
- no operator-oriented "show me the last N runs" workflow
- no normalized summary of what happened in a single backend interaction

**What a modern backend version should look like**

- JSONL or equivalently simple append-only logging
- schema based on current backend runtime concepts: session activation, realtime provider events, vision analysis, tool execution, readiness or failure outcomes
- authenticated internal API and/or CLI-first inspection path
- retention and redaction rules defined for operator use, not public debug use

**Why the framework version should not be copied directly**

- its schema is built around the old pipeline, STT/video/TTS stages, and query bundle model
- its endpoint shape is too tied to the framework-era public API surface
- the active backend should prefer operator or internal inspection paths over broad public debug endpoints

### Priority 3: Internal Debug Capture

**What `framework/` had**

- sanitization helpers for debug payloads
- redaction of sensitive headers
- truncation and summarization helpers for large payloads and data URLs
- rich end-to-end debug captures for simulated flows

**Why it is still useful**

- The current backend has targeted debug logging and some local telemetry helpers, but not a consolidated reusable debug-capture toolkit for internal diagnostics.
- The sanitization primitives are still useful as a pattern for future operator-facing diagnostic output.

**Where the current backend has a gap**

- no single reusable library for bounded, redacted request/response/provider payload snapshots
- no unified internal diagnostic report format across vision, realtime, and tool execution

**What a modern backend version should look like**

- internal-only diagnostic helpers reusable by CLI checks, protected admin routes, or test harnesses
- redacted and size-bounded captures for headers, payload excerpts, binary summaries, and provider error bodies
- tightly scoped to current backend subsystems

**Why the framework version should not be copied directly**

- it is bundled with framework-era public debug endpoints and old pipeline assumptions
- it would need to be reoriented around current provider/runtime flows and the current auth surface

## Not Recommended To Port

The following should be treated as historical artifacts, not migration candidates:

- the generic "open framework" product model
- agent preset catalog, runtime template API, and plugin-style agent/module loading
- framework-era `/v1/pipeline`, `/v1/pipeline/tts-stream`, `/v1/elevenlabs/stream`, `/v1/agents`, `/v1/config/*`, and `/v1/debug/*` surface
- legacy iOS query pipeline and simulation/debug workflow
- the old Voxtral/Nemotron/ElevenLabs orchestration model
- provider-specific integrations that do not fit the current provider-scoped backend architecture
- abstractions that would push `backend/` back toward being a general multimodal framework instead of the current product backend

This point matters: `framework/` contains a lot of code, but most of it solves the wrong problem for the current repository.

## Nice To Add To `backend/`

If anything is carried over from `framework/`, it should be limited to this short list:

1. **Tracing foundation**
   - Add a provider-agnostic event collector for current backend runtime stages.
   - Start with console export.
   - Consider Weave as the main optional external sink.
   - Keep payload capture sanitized and bounded.

2. **Run inspection**
   - Add persistent run logging for selected current-backend requests or sessions.
   - Expose it via an authenticated internal path and/or CLI-first operator workflow.
   - Use a schema based on current backend session, vision, and tool execution, not the framework pipeline schema.

3. **Internal debug capture**
   - Add reusable sanitization helpers for headers, payload excerpts, and binary/data-url summaries.
   - Use them for internal diagnostics only.
   - Avoid reviving broad public debug endpoints by default.

## Retirement Checklist For Later Deletion

Do not delete `framework/` until the following follow-up cleanup is done:

- confirm again that no active imports or runtime references remain
- update stale docs that still advertise framework-era API surface, especially the root `README.md`
- remove any framework-only requirement, setup, or usage references that remain in docs
- verify that no iOS or backend instructions still depend on framework endpoints or behavior
- delete `framework/` only after this audit remains available as the retained summary of useful leftovers

## Conclusion

`framework/` should be treated as a historical experiment, not a shadow backend. The only realistic carryover value is in observability and diagnostics, especially:

- structured tracing with optional Weave support
- persistent run inspection
- sanitized internal debug capture

Everything else is either already superseded by `backend/` or mismatched to the current product direction.

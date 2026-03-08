# Backend Productization Roadmap

## Purpose

This document captures the intended Step 4 backend work after the landed Phase 3 vision-upload slice.

It is the active execution-facing plan for turning the current PortWorld backend into a high-quality self-hostable open-source backend while adding the first bounded multimodal memory and tool-use capabilities.

It is not a low-level implementation spec and it should not contain code.

## Current Position

- Steps 1 to 3 from `docs/IOS_PHONEONLY_TO_GLASSES_ROADMAP.md` are effectively landed.
- The active iOS runtime now supports:
  - phone route
  - glasses route
  - bounded still-photo upload at `1 photo / second`
- The backend currently remains centered on:
  - one websocket conversation loop for realtime audio
  - one bounded `/vision/frame` upload path for still images
- The backend is still primarily an OpenAI Realtime bridge rather than a productized multimodal backend.
- The next phase is to clean up and productize the backend around the working PortWorld app contract rather than grow more capability on top of the current hackathon-era shape.

## Relationship To Other Docs

Authority and intent should be read in this order for backend planning:

1. `docs/IOS_PHONEONLY_TO_GLASSES_ROADMAP.md`
2. `docs/BACKEND_PRODUCTIZATION_ROADMAP.md`
3. `docs/intermediary/PHASE3_IMPLEMENTATION.md`
4. `docs/AGENTIC_INTEGRATION.md`

Meaning:

- `docs/IOS_PHONEONLY_TO_GLASSES_ROADMAP.md` remains the concise product-phase map.
- `docs/BACKEND_PRODUCTIZATION_ROADMAP.md` is the active backend planning document for Step 4.
- `docs/intermediary/PHASE3_IMPLEMENTATION.md` remains the landed trace for the first bounded vision-upload slice.
- `docs/AGENTIC_INTEGRATION.md` remains useful long-horizon architecture context, but it is not the active implementation plan for the next backend milestone.

## Product Intent

The backend should become a PortWorld-specific open-source backend that is:

- easy for a single user to self-host
- easy for contributors to understand and extend
- tightly aligned with the current PortWorld iOS contracts
- ready to support the first practical visual-memory and tool-access workflows
- shaped so additional providers can be added later without forcing a structural rewrite

The first operating model is:

- one deployed backend
- one user profile
- many sessions underneath that profile

This is not the phase where PortWorld becomes a hosted multi-tenant platform.

## Locked Decisions

- The backend stays tightly scoped to PortWorld and its current app contracts.
- The first supported deployment target is self-hosted OSS for technical users.
- The default deployment path is `Docker Compose`.
- The default persistence model is:
  - `SQLite` for metadata and indexes
  - mounted filesystem artifacts for memory documents and optional debug outputs
- The first realtime provider remains OpenAI.
- The vision / memory layer should be provider-agnostic in shape, but implemented first with Mistral.
- The first vision / memory model target is `ministral-3b-2512`.
- The first built-in search provider is Tavily.
- The backend should not retain raw frames by default.
- The realtime model should read short-term and session visual context through tool calls instead of receiving it on every turn.
- Stable user-profile facts may be injected into the realtime system prompt.
- MCP-backed tooling is not an MVP deliverable, but the backend should leave a clear extension seam for it.
- Long-running async reasoning jobs are not part of the first Step 4 MVP.

## Out Of Scope For This Step

- multi-user hosted backend architecture
- managed PortWorld cloud product
- Gemini Live integration
- ElevenLabs Voice Agents integration
- rich backend administration UI
- bundled sample document corpora
- sophisticated frame-selection or scene-understanding pipelines
- long-running async research jobs
- final MCP setup UX
- direct provider selection controls in the iOS app

## Planned Sequence

### Step 4A. Backend Foundation And Productization

Goal:

- make the backend easy to understand, easy to deploy, and clearly organized around PortWorld runtime behavior

Work:

- reorganize the backend around stable ownership seams for:
  - realtime conversation handling
  - vision ingestion
  - memory construction and retrieval
  - tool execution
  - provider adapters
  - lightweight admin / operator tasks
- preserve the working realtime websocket/audio contract unless a simplification is clearly beneficial and low-risk
- standardize persistence around one explicit storage model:
  - `user/user_profile.md`
  - `user/user_profile.json`
  - `session/<session_id>/session_memory.md`
  - `session/<session_id>/session_memory.json`
  - `SQLite` metadata and indexes
- make the self-hosted Docker-based deployment path the primary supported operating model
- remove or isolate hackathon-era backend structure that no longer serves the active app path

Expected result:

- the backend reads as one coherent PortWorld backend instead of a narrow OpenAI bridge with add-on endpoints

Acceptance:

- a new contributor can explain the backend ownership model without relying on hackathon history
- the storage model is explicit and stable
- the backend can be brought up locally through the documented self-hosted path
- the design does not assume multiple users or hosted tenancy

Status:

- planned

### Step 4B. Visual Memory MVP

Goal:

- turn bounded image uploads into useful visual context without over-processing every frame

Work:

- keep the current `1 photo / second` ingestion model
- run cheap gating before calling the VLM so the backend does not analyze every received frame blindly
- keep the first gating policy intentionally simple:
  - minimum time spacing
  - visual-change threshold
  - forced analysis when the current conversation strongly suggests a visual question
- build three memory outputs:
  - short-term visual context covering roughly the last `30` seconds
  - per-session memory that accumulates the session-level picture of what the user has been doing
  - persistent user profile memory for cross-session facts
- store memory in a hybrid format:
  - concise markdown for human readability
  - structured JSON for reliable retrieval and selective prompt/tool usage
- keep raw-frame retention disabled by default and reserve it for debug or explicit operator opt-in

Expected result:

- the backend can describe recent visual context, evolving session context, and a small stable user profile without becoming a video-processing system

Acceptance:

- accepted frames can produce usable short-term and session memory artifacts
- memory artifacts remain compact and readable
- the default path retains derived memory only, not raw frames
- the design does not require streaming video or continuous heavy analysis

Status:

- planned

### Step 4C. Realtime Tooling MVP

Goal:

- let the realtime model fetch the context it needs when it needs it, without bloating the default context window

Work:

- keep memory access model-driven rather than injected on every turn
- provide a deliberately small first built-in tool catalog:
  - `get_short_term_visual_context`
  - `get_session_visual_context`
  - `web_search`
- keep the tool layer small enough that it remains easy to understand and validate end-to-end
- inject only stable user-profile facts into the realtime system prompt by default
- define a clean future extension seam for MCP-backed tools without making MCP part of the first deliverable
- keep tool execution synchronous and low-latency in this milestone

Expected result:

- the realtime model can ask for missing visual or web context without turning the backend into an async agent platform

Acceptance:

- realtime can fetch short-term or session context on demand
- search is available through one backend-owned interface
- the default conversation loop is not preloaded with unnecessary visual history
- the tool catalog remains intentionally small and stable

Status:

- planned

### Step 4D. Profile Capture And Memory Lifecycle

Goal:

- establish how persistent user facts and session memory are created, updated, retained, exported, and reset

Work:

- introduce a first-session onboarding flow focused on collecting initial user-profile facts
- keep persistent profile memory separate from per-session memory
- allow later profile enrichment from completed conversations, but keep that extraction path conservative and bounded
- start with a small allowlisted set of promoted profile facts such as:
  - name
  - job
  - company
  - stable preferences
  - recurring projects or domains
- define retention defaults:
  - session memory retained for a bounded period such as `7` or `30` days
  - user profile retained until explicit reset
- provide minimal operator/admin controls for:
  - exporting memory artifacts
  - resetting one session memory set
  - resetting persistent user profile memory

Expected result:

- memory lifecycle behavior is deliberate and understandable rather than emerging implicitly from prompts or ad hoc background logic

Acceptance:

- onboarding can populate the persistent profile store
- session memory and user profile have distinct lifecycles
- retention and reset behavior are explicit
- the realtime model is not responsible for directly writing long-term memory during live turns

Status:

- planned

### Step 4E. OSS Deployment And Operator Experience

Goal:

- make the backend straightforward for technical users to self-host and operate

Work:

- make `Docker Compose` the canonical deployment story
- document env-based configuration for:
  - OpenAI realtime
  - Mistral vision / memory
  - Tavily search
- keep setup focused on a small number of required secrets and runtime settings
- document lightweight operator workflows for:
  - first launch
  - provider configuration
  - memory export
  - memory reset
  - debug-mode raw-frame retention
- treat AWS as the first documented cloud deployment follow-on, but keep the runtime shape container-first rather than AWS-specific

Expected result:

- external technical users can self-host PortWorld with minimal backend friction and without reading deep repo history first

Acceptance:

- the self-host deployment path is clear and practical
- required secrets and provider settings are explicit
- the backend can be open-sourced in this shape without architectural apology
- AWS guidance can build on the same container shape instead of requiring a second architecture

Status:

- planned

## Deferred Next

The following items should be treated as deliberate follow-on work rather than silent scope creep inside the Step 4 MVP:

- Gemini Live realtime adapter
- ElevenLabs Voice Agents adapter
- richer provider-selection controls in the iOS app
- long-running async reasoning jobs
- bundled MCP setup and example servers
- broader provider matrix for vision, search, and realtime
- hosted backend offering
- richer operator UX beyond minimal reset/export flows

## Open Questions / Exploration Areas

These questions are worth exploring during implementation, but they should not block the first Step 4 roadmap slice:

- what exact lightweight heuristic is sufficient for the first frame-gating policy
- whether session-memory extraction should happen continuously, at periodic checkpoints, or only at session end
- how conservative the first profile-fact promotion pass should be after onboarding
- what exact future MCP integration surface should be exposed once MCP becomes in-scope
- whether session-memory retention should default closer to `7` days or `30` days

## Success Criteria

We are on the right path if:

- the backend is easy to explain from top to bottom
- the backend is easy for one technical user to self-host
- the backend remains clearly PortWorld-specific instead of turning into generic infrastructure
- visual input produces useful short-term and session memory without destabilizing the realtime loop
- the realtime model can fetch visual and web context only when needed
- the backend is in a shape that can be open-sourced confidently and extended later without structural rework

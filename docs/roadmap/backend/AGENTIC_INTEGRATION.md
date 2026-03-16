# Realtime Glasses Agent – Architecture Vision

## 1. Mission

Build an open-source, self-hostable realtime voice + vision agent stack for smart glasses (e.g., Ray-Ban Meta) and mobile devices.

The system must:

- Support realtime speech-to-speech interactions.
- Integrate vision (photos / frames / short clips).
- Allow async tool execution (Loop B).
- Be fully self-hostable by technical users.
- Support BYOK (Bring Your Own Key).
- Eventually support a fully local Loop A runtime on iOS (later phase).

The project is designed to be:

- Open-source first.
- Privacy-conscious.
- Modular and provider-agnostic.
- Extensible via tool gateways and adapters.

---

# 2. Core Architectural Concept

The system is built around two logical loops:

## Loop A — Realtime Conversational Agent (Fast Loop)

Responsibilities:

- Manage realtime audio input/output.
- Handle turn-taking and interruptions (barge-in).
- Maintain short-term conversational state.
- Decide when to call tools (Loop B).
- Inject contextual updates (e.g., vision summaries).
- Stream audio responses back to the user.

Loop A is the "front brain" of the system.

It must feel:

- Immediate
- Conversational
- Low-latency
- Interruptible

In the initial architecture, Loop A runs on the backend server (Gateway).

A fully local Loop A runtime on iOS will be implemented later.

---

## Loop B — Tooling & Deep Reasoning (Async Loop)

Responsibilities:

- Execute long-running reasoning tasks.
- Call external tools.
- Perform vision analysis if needed.
- Run retrieval, workflows, structured pipelines.
- Return structured results.
- Stream progress updates when appropriate.

Loop B is accessed via tool calls initiated by Loop A.

Loop B is asynchronous by design:

- Loop A must never block on Loop B.
- Loop A may continue speaking while Loop B processes.
- Loop B results are injected back into the conversation when ready.

Loop B lives on the backend server.

---

# 3. High-Level System Layout (Phase 1: Server-First)

## Client (iOS App)

Responsibilities:

- Pair with smart glasses.
- Capture microphone audio.
- Capture frames / photos when needed.
- Playback assistant audio.
- Provide session UI and settings.
- Manage provider keys (BYOK).
- Connect to the Realtime Agent Gateway.

The iOS app is a thin realtime client.

It does not initially implement Loop A logic locally.

---

## Realtime Agent Gateway (Open-Source Backend)

This is the canonical implementation of Loop A (server-side).

Responsibilities:

- Maintain realtime sessions with model providers.
- Stream audio input/output.
- Handle tool call orchestration.
- Inject contextual updates (e.g., vision summaries).
- Interface with Loop B.
- Manage async job lifecycle.
- Abstract provider-specific APIs.

This gateway is:

- Self-hostable.
- Modular.
- Provider-agnostic via adapters.
- The core open-source artifact of the project.

---

## Provider Adapters

The gateway connects to:

- OpenAI Realtime APIs
- Gemini Live APIs
- Other providers (extensible)

Provider adapters:

- Translate between provider-specific protocols and the neutral internal event schema.
- Keep the rest of the system provider-independent.

---

## Tool Router (Loop B Orchestrator)

Responsibilities:

- Receive tool call requests from Loop A.
- Start async jobs.
- Emit progress events.
- Emit final structured results.
- Support cancellation.
- Interface with:
  - Vision services
  - Retrieval systems
  - External APIs
  - User-hosted tool gateways

Loop B is a structured, async system — not free-form text generation.

---

# 4. Neutral Event & Tool Protocol (Conceptual)

The system must define a provider-agnostic event schema that supports:

- Audio input streaming
- Audio output streaming
- Context updates (e.g., vision text)
- Tool call start
- Tool progress
- Tool result
- Tool cancel
- Barge-in / interruption
- Session state transitions

This protocol must remain stable across:

- Server-based Loop A
- Future local Loop A (iOS runtime)
- Different providers
- Different tool backends

The protocol is the most important architectural contract in the system.

---

# 5. Vision Integration (High-Level)

Vision may be handled via:

- Direct provider multimodal input.
- A dedicated vision service that produces structured scene summaries.
- Loop B-triggered analysis when needed.

Key principles:

- Vision updates must be lightweight.
- Avoid flooding Loop A with large payloads.
- Prefer structured summaries over verbose captions.
- Support context versioning to prevent stale results.

---

# 6. Modes of Operation

The system must support three modes:

## Mode A — Direct-to-Provider (BYOK)

- iOS app connects directly to provider APIs.
- No relay server required.
- Tool calls may go to:
  - User-hosted tool gateways.
  - Local limited tools.

This maximizes privacy and decentralization.

---

## Mode B — Self-Hosted Gateway

- iOS connects to a self-hosted Realtime Agent Gateway.
- Gateway connects to provider.
- Tools run on self-hosted infrastructure.

This is the recommended path for technical users.

---

## Mode C — Managed Gateway (Future Subscription)

- iOS connects to a managed hosted gateway.
- Simplified setup.
- Ideal for non-technical users.

The architecture must support this without structural changes.

---

# 7. Design Principles

## 1. Server-First Implementation

We begin by implementing Loop A on the backend server.

Local Loop A runtime (on-device) is explicitly a later phase.

## 2. Async by Default

Loop B must never block Loop A.

All tool calls are async jobs.

Loop A continues conversation while tools execute.

## 3. Provider-Agnostic Core

No core logic should depend tightly on a specific provider’s tool format.

Adapters isolate provider differences.

## 4. Local-First Vision (Long-Term)

The architecture must not assume permanent server-side Loop A.

Eventually:

- Loop A can run on-device.
- Gateway becomes optional.
- Tool router can remain server-side or user-hosted.

## 5. Extensibility Over Specificity

The project should enable:

- New provider adapters.
- New tool packs.
- New vision pipelines.
- Domain-specific extensions.

We are building infrastructure, not a single-purpose assistant.

---

# 8. Future Phase: Local Loop A Runtime (Explicitly Deferred)

At a later stage:

- Loop A will be implemented as a local runtime in the iOS app.
- The same neutral protocol will be reused.
- The backend gateway will become optional.
- Tool routing may remain server-based or user-hosted.

This is not part of the initial implementation.

Phase 1 focus:

- Server-based Loop A.
- Self-hostable gateway.
- Clean protocol design.
- Tool orchestration stability.

---

# 9. What This Project Is

- A self-hostable realtime multimodal agent stack.
- A modular infrastructure for smart-glasses assistants.
- An extensible tool-calling runtime.
- An open-source foundation for privacy-conscious AI agents.

---

# 10. What This Project Is Not (For Now)

- Not a purely on-device agent system (yet).
- Not tied to a single provider.
- Not a monolithic backend with hardcoded tools.
- Not a closed SaaS-only product.

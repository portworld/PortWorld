# PortWorld Backend Platform Roadmap

## Summary

This document defines the backend roadmap now that the current production-oriented slice is working with:

- OpenAI Realtime for the live conversational loop
- Mistral-compatible vision analysis for visual memory
- a minimal operator CLI for serving, validation, storage bootstrap, and memory export
- a simple local-first persistence model using SQLite plus filesystem artifacts

The next objective is not to add features opportunistically. The objective is to turn the backend into a coherent platform that is:

- easy to deploy
- easy to operate
- easy to extend
- still simple to self-host

The primary implementation order is:

1. Build a real deploy-focused CLI
2. Expand official realtime provider support
3. Expand official tool packs and custom tool extensibility
4. Improve the memory/storage architecture without compromising deployment simplicity

This order is deliberate. The CLI is the adoption lever. Once deployment and operator experience are strong, provider and tool expansion becomes much more valuable because users can actually install and run the platform easily.

## North Star

PortWorld backend should become a Docker-first, self-hostable realtime multimodal agent gateway with:

- a small stable core
- official provider adapters for major realtime and vision services
- official tool packs that can be enabled by configuration or CLI workflows
- a clear extension model for developers who want custom tools and integrations
- a deployment CLI that makes common cloud targets as close as possible to a guided one-command experience

The system should feel like a serious open-source agent runtime rather than a one-off backend for the current app.

## Design Principles

### 1. CLI-first platformization

The backend should not be experienced primarily as “clone repo, edit env, run docker compose”.
It should be experienced as an installable product with a strong command-line interface.

Docker remains the canonical packaging layer, but the CLI becomes the primary operational interface for:

- initialization
- validation
- local setup
- deployment
- upgrade workflows
- memory export and operator tasks

### 2. Small stable core, official packs around it

The backend core should remain opinionated but small.
We should avoid both extremes:

- not a hardcoded monolith with every integration baked directly into the runtime
- not a thin shell that pushes all value into external packages too early

The preferred model is:

- stable core runtime
- official in-repo provider adapters
- official in-repo tool packs
- documented extension contracts for custom integrations

### 3. Provider-agnostic runtime contracts

The current provider abstraction should be strengthened, not replaced.
The backend should have neutral runtime contracts for:

- realtime session lifecycle
- control/event mapping
- tool registration and execution
- vision provider invocation
- storage access

Provider-specific protocol details should stay isolated in adapters.

### 4. Simple self-hosting remains the default

The system should remain easy to self-host by a technical developer with Docker.
This means local-first defaults continue to matter.

We should not redesign the product around a managed-cloud architecture as the main path.
Instead:

- local SQLite plus filesystem artifacts remains the default deployment model
- managed persistence is added only where cloud deployment requires it
- storage abstractions should allow a heavier backend later without forcing it on every user

### 5. Phased delivery over broad ambition

The roadmap spans CLI, cloud deployment, providers, tools, and memory. These are all meaningful projects.
We should not attempt to solve them simultaneously.

The sequencing in this document is part of the architecture.
Each phase exists to unblock the next one and reduce the risk of rework.

## Current State

The backend already contains several useful foundations that the roadmap should build on rather than rewrite.

### Runtime and provider state

Today the realtime architecture already has:

- a provider registry/factory pattern
- a neutral bridge construction path
- one official realtime provider: `openai`

The right conclusion is that the architecture is headed in the correct direction, but the provider surface is not yet a platform. It is an abstraction with only one implementation.

### Tooling state

Today the tooling system already has:

- a realtime tool registry
- tool definitions and executors
- built-in visual memory recall tools
- one optional web search path using Tavily

This is a valid base, but it is still closer to a hardcoded catalog than to a developer platform.

### CLI state

Today the backend already ships a minimal operator CLI with commands for:

- `serve`
- `check-config`
- `bootstrap-storage`
- `export-memory`
- `migrate-storage-layout`

This is useful, but it is not yet the product surface we want. It is an internal operator entrypoint, not a polished deployment CLI.

### Storage and memory state

Today memory and persistence are intentionally simple:

- SQLite stores metadata and indexes
- JSON and Markdown files store profile/session artifacts
- vision artifacts are filesystem-backed
- export/reset flows are already present

This is a strong default for self-hosting, but it creates limitations for managed cloud deployments and for future storage evolution.

## Target Product Shape

The target product shape is:

- `portworld` is an installable CLI
- Docker is the canonical runtime packaging format
- local self-hosting is one good path, not the only path
- managed cloud deployment is guided through official CLI workflows
- the runtime supports multiple official realtime providers out of the box
- the runtime supports multiple official tool/search providers out of the box
- developers can add tools without editing core orchestration code
- storage supports both local-first and managed-cloud backends under the same functional contract

## Roadmap Overview

The roadmap is split into four delivery tracks, but they should be executed sequentially.

### Phase 1

CLI and deployment platformization.

### Phase 2

Official provider expansion.

### Phase 3

Official tool-pack system and developer extensibility.

### Phase 4

Memory and storage evolution.

Phases 2 through 4 should reuse the CLI and deployment contracts established in Phase 1.

## Phase 1: CLI-First Platformization

### Goal

Turn the backend into an installable and deployable product with a guided command-line experience.

### Why this comes first

Without a strong CLI, all subsequent work has lower practical value:

- more providers are harder to configure and validate
- more tools increase setup complexity
- memory improvements create more operational burden

A real CLI reduces deployment friction, creates a stable operator surface, and becomes the natural place to expose future provider and tool workflows.

### Public CLI direction

The backend should graduate from `python -m backend.cli` to a public installable CLI named:

- `portworld`

The first distribution target should be:

- `pipx`

This gives us:

- isolated installation
- a modern operator UX
- low release overhead compared with standalone binaries
- a clean path to later Homebrew or packaged-binary distribution if needed

### CLI v1 scope

CLI v1 should focus on deploy and operator workflows, not on being a general developer framework.

Recommended top-level commands:

- `portworld init`
- `portworld doctor`
- `portworld deploy gcp-cloud-run`
- `portworld ops check-config`
- `portworld ops bootstrap-storage`
- `portworld ops export-memory`
- `portworld ops migrate-storage-layout`

### Responsibilities of the CLI

The CLI should own the guided experience for:

- generating or validating baseline config
- checking local prerequisites
- checking cloud auth and tooling
- building or referencing the backend container image
- preparing deployment parameters
- provisioning the minimum cloud resources required by the selected deployment path
- deploying the backend
- printing the resulting endpoints, next steps, and post-deploy warnings

### What the CLI should not try to be in v1

The CLI should not try to become:

- a generic infrastructure-as-code engine
- a full cloud abstraction layer across every provider at once
- a full lifecycle fleet manager with rollback, advanced log browsing, and secret rotation on day one

These can come later if needed. v1 should be a guided deploy orchestrator with good defaults.

### Deployment target order

#### First deep deployment target

- GCP Cloud Run

Why:

- aligned with the current Docker-first direction
- supports HTTPS and WebSocket-based serving
- integrates naturally with a guided `gcloud`-based workflow
- a reasonable first path for a deploy-focused CLI

#### Next official deployment targets

- Fly.io
- Railway
- then other common cloud providers

The roadmap should explicitly say we want eventually to support 5 to 6 common providers, but we should not block v1 on broad support.

### Cloud Run-specific constraint

Cloud Run is not compatible with the current local-disk persistence model as a durable production story.
That means the roadmap for `deploy gcp-cloud-run` must include a minimal managed persistence design.

Cloud Run support should not be demo-only.
The deployment workflow should preserve meaningful memory behavior.

### Required Cloud Run managed persistence

The first Cloud Run deployment path should provision or require:

- managed SQL for metadata and indexes
- object storage for artifact/blob persistence

At the roadmap stage, this should be defined as a minimal managed-storage backend, not as a complete memory redesign.

### Phase 1 deliverables

#### Deliverable 1: installable CLI package

- package the CLI as `portworld`
- keep the current backend operator commands as implementation building blocks
- define stable user-facing CLI help, command naming, and output conventions

#### Deliverable 2: local bootstrap experience

- `portworld init` creates or guides a valid local configuration
- local path should remain Docker-first
- generated config should reflect the current supported runtime modes cleanly

#### Deliverable 3: environment and readiness diagnostics

- `portworld doctor` checks for missing env vars, invalid combinations, missing CLIs, and unsupported deployment conditions
- diagnostics should be specific and actionable
- output should clearly distinguish local issues from provider issues and cloud issues

#### Deliverable 4: GCP Cloud Run guided deployment

`portworld deploy gcp-cloud-run` should:

- verify local Docker and `gcloud` availability
- verify or prompt for Google auth status
- verify or select project and region
- verify or enable required services
- create or validate required managed persistence resources
- configure secrets and runtime env vars
- deploy the container
- print service URL, readiness checks, and operator follow-up commands

#### Deliverable 5: operator namespace

Existing operator tasks should move under a coherent `ops` namespace.
This keeps the CLI organized as the product grows.

### Phase 1 acceptance criteria

- a user can install the CLI with `pipx`
- a user can initialize a valid local backend config without manually reverse-engineering the env file
- a user can run a diagnostic command and receive actionable failure messages
- a user can deploy to Cloud Run through a guided official workflow
- the CLI produces a clear final summary with deployed URL, required secrets, and post-deploy validation steps

### Explicitly deferred in Phase 1

- multi-cloud parity across 5 to 6 providers
- full rollback and teardown lifecycle management
- packaging as standalone binaries
- provider and tool scaffolding commands
- large memory redesign beyond the minimum needed for managed persistence support

## Phase 2: Official Provider Expansion

### Goal

Turn the current realtime abstraction into a real multi-provider platform.

### Why this comes after the CLI

Provider expansion adds configuration, capability differences, and operational complexity.
The CLI from Phase 1 should become the surface that helps users configure and validate these providers.

### Direction

The existing provider factory/registry pattern should be formalized into a provider adapter contract.
Each official provider adapter should declare:

- provider identity
- configuration requirements
- supported capabilities
- neutral event/session mapping behavior
- tool wiring behavior
- validation behavior
- startup validation policy

### Official provider order

#### 1. OpenAI Realtime

OpenAI remains the reference implementation and baseline contract.

#### 2. Gemini Live

Gemini Live should be the next first-class realtime provider.
Its role in the roadmap is to prove that the neutral session/runtime contract is real, not just aspirational.

#### 3. ElevenLabs voice agents

ElevenLabs should be treated as a full realtime adapter target, not merely a TTS or STT integration.
This should be documented carefully because its protocol and control model may differ from OpenAI and Gemini.

The roadmap should still treat it as a first-class provider objective rather than a side experiment.

### Provider capability model

The roadmap should define a capability model that can express differences such as:

- server-side turn detection support
- tool-calling support
- voice selection support
- streaming modality differences
- interruption behavior
- system-instruction or prompt model differences
- structured output or tool result constraints

This capability model is important because it prevents the core runtime from assuming all providers behave like OpenAI.

### Vision provider expansion

The roadmap should explicitly mention broadening vision provider support beyond the current Mistral-compatible path.
This remains secondary to the realtime provider work, but it should happen under the same adapter philosophy:

- stable internal contract
- provider-specific configuration isolated in adapters
- validation through CLI and runtime checks

### Phase 2 deliverables

- formal provider adapter contract
- capability metadata model
- Gemini Live official adapter
- ElevenLabs official adapter roadmap target or initial implementation slice
- CLI/provider diagnostics that understand provider-specific requirements

### Phase 2 acceptance criteria

- the backend can run with more than one official realtime provider without changing core orchestration code
- provider selection is config-driven and validated
- provider-specific limitations are surfaced clearly in diagnostics and documentation
- the neutral runtime contract remains the center of the design

### Explicitly deferred in Phase 2

- trying to unify every provider into a fake lowest common denominator
- adding many providers with shallow quality
- custom plugin packaging for third-party providers outside the repo

## Phase 3: Tool Pack System And Developer Extensibility

### Goal

Move from a small hardcoded tool catalog to a stable tool-platform model with official packs and a clean custom tool interface.

### Why this comes after providers

Once multiple realtime providers exist, the runtime/tool contract becomes more important and more testable.
The tool system should evolve on top of that clearer provider foundation.

### Current issue to solve

Today the backend effectively exposes:

- memory recall tools
- one web search path backed by Tavily

This is good enough for current use, but it is not yet a platform that developers can extend comfortably.

### Desired model

The roadmap should define three layers:

#### Layer 1: core tool runtime

The core runtime should handle:

- tool registration
- tool execution
- timeouts
- error normalization
- provider-facing tool exposure

#### Layer 2: official tool packs

Official packs should contribute tools in coherent groups.
Initial pack model:

- core memory pack
- core web-search pack

Future packs can include:

- more search providers
- external knowledge and retrieval tools
- utility tools for structured actions

#### Layer 3: custom tool contributions

Developers should be able to add tools through a documented registration contract rather than by patching core runtime code.

### Web search roadmap

Tavily should remain one supported official provider, but the roadmap should explicitly commit to expanding search providers out of the box.

This should be framed as:

- official search-provider expansion
- not a one-provider assumption embedded in the runtime

### Developer extensibility contract

The roadmap should define a minimal public contract for tool contributions:

- tool definition schema
- executor interface
- registration hook or contributor contract
- config-driven enablement
- error and timeout behavior

The goal is not a plugin marketplace in v1.
The goal is to make “I want to add my own tool” straightforward and low-friction.

### CLI relationship

The CLI should eventually become the recommended surface for:

- validating enabled tools
- showing active provider/tool compatibility
- diagnosing missing API keys or unsupported tool combinations

These commands are not required in CLI v1, but the roadmap should reserve this role for the CLI.

### Phase 3 deliverables

- formal tool-pack contribution model
- official pack boundaries documented
- more official search providers out of the box
- custom tool developer contract documented and implemented
- diagnostics for enabled/disabled tool providers

### Phase 3 acceptance criteria

- official packs can be enabled without editing core dispatch logic
- adding a custom tool no longer requires invasive core changes
- the runtime degrades cleanly when a provider credential is missing
- tool exposure remains understandable across multiple realtime providers

### Explicitly deferred in Phase 3

- broad plugin ecosystem design
- remote plugin execution model
- advanced policy sandboxing for arbitrary third-party tools

## Phase 4: Memory And Storage Evolution

### Goal

Improve the memory system so it is more robust, more cloud-compatible, and more future-proof without losing deployment simplicity.

### Why this comes last

The current memory model is simple but workable.
The immediate product bottleneck is deployment and platformization, not memory sophistication.

Memory work should therefore be split into:

- minimum storage abstraction needed to support managed deployment
- later quality improvements to retrieval, retention, and storage lifecycle

### Default principle

Local-first simplicity remains the default recommendation.

The roadmap should explicitly state:

- SQLite plus local artifacts remains the default self-hosted path
- more complex storage should be optional and driven by deployment requirements
- the backend should not require a managed database for normal self-hosting

### Storage architecture direction

Introduce a storage backend contract with two primary implementations:

#### Local backend

- SQLite for metadata and indexes
- filesystem for profile/session/vision artifacts

#### Managed backend

- managed SQL for metadata and indexes
- object storage for durable artifact persistence

The contract should preserve core behaviors such as:

- session memory retention
- memory export
- profile reset
- per-session reset
- artifact indexing and retrieval

### Memory improvements to defer until after abstraction

After the storage backend contract is in place, the roadmap should support future work on:

- cleaner memory lifecycle boundaries
- better retrieval ergonomics
- stronger export/import semantics
- more explicit distinction between short-term, session, and profile memory
- optional higher-scale backends if the product later needs them

### What not to do

The roadmap should push back against an overcomplicated memory redesign at this stage.

We should avoid:

- prematurely introducing a large distributed memory stack
- making deployment meaningfully harder for self-hosters
- coupling memory redesign to provider expansion

### Phase 4 deliverables

- storage backend contract
- local backend preserved as the default
- managed backend sufficient for Cloud Run deployments
- documented parity expectations for reset/export/retention behavior
- follow-up backlog for retrieval and lifecycle improvements

### Phase 4 acceptance criteria

- the backend supports both local and managed persistence models under one functional contract
- self-hosting remains simple
- Cloud Run deployments have durable persistence support
- memory-related operator behavior remains consistent across storage backends where practical

### Explicitly deferred in Phase 4

- full-scale enterprise data platform work
- distributed memory systems by default
- broad multi-region storage design

## Cross-Cutting Contracts To Define

The roadmap should explicitly call out the public contracts that future implementation should protect.

### 1. CLI contract

Stable user-facing command model for `portworld`.

### 2. Realtime provider adapter contract

Stable internal contract for provider registration, capability description, validation, and session bridge construction.

### 3. Tool contribution contract

Stable internal-public contract for official packs and custom tools.

### 4. Storage backend contract

Stable interface for local and managed persistence implementations.

### 5. Deployment contract

Stable expectations for official deploy commands such as `deploy gcp-cloud-run`, including required auth, required secrets, expected outputs, and post-deploy validation behavior.

## Milestones And Exit Criteria

### Milestone A: CLI foundation

Exit criteria:

- installable `portworld` CLI exists
- local bootstrap and diagnostics are solid
- current operator tasks are organized under a coherent command surface

### Milestone B: Cloud Run deployment path

Exit criteria:

- guided official Cloud Run deploy flow exists
- required managed persistence path is defined and functioning
- the CLI can tell the user exactly what was provisioned and where the service is reachable

### Milestone C: multi-provider runtime

Exit criteria:

- OpenAI and Gemini Live both work as official providers under the same core contracts
- ElevenLabs has a defined first-class adapter path and implementation plan or first slice

### Milestone D: tool-pack platform

Exit criteria:

- official tool packs are documented and implemented
- custom tools can be added through a supported contract
- more than one official search provider is supported

### Milestone E: storage abstraction and memory evolution

Exit criteria:

- local and managed storage backends coexist under one contract
- self-hosting remains simple
- managed cloud deployments preserve durable memory behavior

## Implementation Flow

The coherent implementation flow should be:

1. Package and stabilize the CLI surface.
2. Add diagnostics and local bootstrap UX.
3. Ship the first official managed deployment workflow on GCP Cloud Run.
4. Introduce the minimum managed persistence backend needed for Cloud Run durability.
5. Formalize provider capabilities and adapter contracts.
6. Add Gemini Live as the second official realtime provider.
7. Add ElevenLabs as a first-class realtime adapter target.
8. Formalize tool packs and the custom tool contribution contract.
9. Expand official search/tool providers.
10. Continue memory and storage evolution behind the new storage contract.

This order avoids premature abstraction and keeps each phase grounded in a real operator need.

## Non-Goals

This roadmap intentionally does not commit to:

- building every cloud provider in parallel
- replacing Docker as the packaging center
- making the core runtime highly modular at the expense of clarity
- forcing a managed-cloud architecture on all self-hosters
- introducing a complex distributed memory stack early

## Success Criteria For The Overall Roadmap

The roadmap is successful when:

- a developer can install the CLI and get a working deployment with much less friction than today
- the backend supports multiple serious realtime providers without core rewrites
- tools are easier to enable by default and easier to extend customly
- memory and storage remain practical for self-hosting while also supporting managed deployment paths
- the backend feels like a reusable platform, not just the current app’s backend

## Immediate Next Step

The first implementation document that should follow this roadmap is a focused CLI specification covering:

- CLI packaging
- command taxonomy
- Cloud Run deployment workflow
- managed persistence requirements for that workflow
- command outputs and diagnostics conventions

That CLI specification should be concrete enough to implement without re-deciding product scope.

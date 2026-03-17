# Backend Model Providers Implementation Plan

## Summary

This document turns the backend model-provider expansion direction into a concrete implementation sequence.

The goals of this plan are:

- add Gemini Live as the second official realtime provider
- expand official vision-provider support beyond the current Mistral-compatible path
- keep the backend core provider-neutral instead of hardcoding more OpenAI assumptions
- land matching CLI support so provider setup and diagnostics remain coherent

This plan intentionally does **not** try to force all providers through an OpenAI-compatible abstraction. The adapter layer should normalize backend runtime behavior, but each provider should remain native inside its own implementation.

## Target Outcomes

By the end of this implementation slice:

- the backend can run realtime sessions through `openai` or `gemini_live`
- the backend can run visual-memory analysis through `mistral`, `openai`, `azure_openai`, `gemini`, `claude`, `bedrock`, and `groq`
- core websocket orchestration does not need provider-specific branching for each new provider
- `portworld init`, `portworld config edit providers`, `portworld doctor`, and deploy secret handling understand the selected providers
- configuration errors fail early with provider-specific diagnostics

## Current Grounding

The current backend already has the correct high-level seams:

- realtime provider registry/factory in `backend/realtime/factory.py`
- vision provider registry/factory in `backend/vision/factory.py`
- bootstrap-time provider validation in `backend/bootstrap/runtime.py`
- a single official realtime adapter: `openai`
- a single official vision adapter: `mistral`

The main limitation is that the realtime core is still OpenAI-shaped in several places:

- event names
- tool serialization
- tool output submission
- turn-finalization commands
- session initialization/update payloads

The vision side is cleaner, but the env/config surface is still biased toward the existing Mistral path.

## Implementation Principles

1. Keep the current registry/factory pattern and strengthen it.
2. Normalize runtime behavior, not raw provider protocols.
3. Use native provider adapters for Gemini, Claude, Bedrock, and others.
4. Share only low-level helper code where it truly reduces duplication.
5. Expand CLI and runtime together so config drift does not grow.
6. Preserve backward compatibility for current OpenAI + Mistral deployments during migration.

## Step-By-Step Plan

### Step 1: Formalize the provider contracts

Define explicit adapter contracts before adding more providers.

#### Realtime contract changes

- Introduce a provider-neutral upstream client/adapter interface for:
  - connect
  - close
  - initialize session
  - update session
  - append client audio
  - finalize/commit client turn
  - create response
  - cancel response
  - register tools
  - submit tool results
  - iterate normalized events
- Add provider capability metadata to realtime definitions. Minimum capability fields:
  - streaming audio input
  - streaming audio output
  - server VAD / turn detection
  - manual turn commit requirement
  - tool-calling support
  - tool result submission mode
  - voice selection support
  - interruption / cancel semantics
  - startup validation policy
- Keep the existing registry/factory ownership in `backend/realtime/factory.py`, but upgrade provider definitions to carry capability metadata and provider-owned hooks.

#### Vision contract changes

- Keep `VisionAnalyzer` as the main runtime-facing contract.
- Extend vision provider definitions to include:
  - provider identity
  - validation behavior
  - capability metadata
  - optional structured-output support
  - image transport expectations
  - rate-limit / retry hints where useful
- Keep `VisionObservation` and provider-payload normalization as the stable internal contract.

#### Deliverables

- updated provider definition types
- capability metadata types for realtime and vision
- a written mapping of normalized realtime events and commands

### Step 2: Refactor the existing OpenAI realtime path onto the new contract

Use OpenAI as the reference implementation of the new realtime adapter contract before adding Gemini Live.

#### Work

- Split OpenAI-specific protocol logic out of the current bridge assumptions.
- Replace direct OpenAI event-name checks in the core path with normalized event handling.
- Move OpenAI-specific command payload construction into the OpenAI adapter.
- Move OpenAI-specific tool serialization and tool-output submission into provider-owned methods.

#### Core files likely affected

- `backend/realtime/client.py`
- `backend/realtime/bridge.py`
- `backend/realtime/tool_dispatcher.py`
- `backend/realtime/turn_state.py`
- `backend/realtime/providers/openai.py`

#### Required result

- the OpenAI provider still behaves exactly as today from the backend client’s point of view
- core orchestration consumes normalized events rather than raw OpenAI protocol details

### Step 3: Introduce provider-neutral tool rendering and tool-result submission

The current tooling contract is explicitly OpenAI-shaped. Fix that before Gemini Live lands.

#### Work

- replace `ToolDefinition.to_openai_tool()` with a provider-neutral tool definition model
- let each realtime adapter render tool definitions into its own upstream schema
- let each realtime adapter own tool-output submission format
- keep tool execution itself provider-neutral

#### Expected outcome

- realtime tooling stays in one runtime/catalog
- provider adapters translate tool definitions and outputs into provider-specific wire formats

### Step 4: Implement the Gemini Live realtime adapter

Add Gemini Live as the second official realtime provider using a native adapter.

#### Work

- add `gemini_live` provider registration in the realtime registry
- implement Gemini session setup, audio transport, event parsing, interruption behavior, and tool-call mapping inside a dedicated adapter module
- normalize Gemini events into the shared runtime event contract
- handle Gemini-specific capability differences explicitly instead of hiding them

#### Expected capabilities to support

- bidirectional realtime audio
- session initialization
- response lifecycle
- interruption/cancel behavior where supported
- tool calling if the selected Gemini Live path supports it
- provider-specific validation through startup checks and doctor output

#### Required result

- `/ws/session` works with `REALTIME_PROVIDER=gemini_live`
- the same websocket/session orchestration path is used for OpenAI and Gemini

### Step 5: Generalize realtime settings and credential validation

The current settings surface is too tied to OpenAI.

#### Work

- add provider-scoped realtime settings for Gemini Live
- restructure settings so selected-provider configuration is explicit
- preserve current OpenAI env names for backward compatibility
- add validation helpers for provider-specific credential and endpoint requirements

#### Minimum configuration direction

- keep `REALTIME_PROVIDER`
- keep existing OpenAI env vars
- add Gemini-specific env vars for API key, model, optional endpoint/base URL, and provider defaults
- make startup validation depend on the selected provider instead of requiring unrelated secrets

### Step 6: Add shared vision provider helpers without creating a fake universal provider

Vision expansion should share helper code where useful, but not collapse all providers into a misleading compatibility layer.

#### Shared helpers that are worth extracting

- image to data-URL or provider-ready payload conversion
- timeout and retry policy helpers
- HTTP error parsing
- rate-limit parsing
- structured-output fallback behavior
- provider payload JSON extraction and normalization

#### Helpers that should remain provider-specific

- request schema
- authentication model
- endpoint layout
- model/deployment naming
- SDK usage
- provider-specific response parsing

### Step 7: Implement the first native vision adapter tranche

Add official vision providers as separate adapters under the existing vision factory.

#### Providers to add

- `openai`
- `azure_openai`
- `gemini`
- `claude`
- `bedrock`
- `groq`

#### Provider implementation expectations

##### OpenAI

- use the native OpenAI multimodal chat/image path
- support provider-specific model and API key validation

##### Azure OpenAI

- use Azure OpenAI directly, not as a plain OpenAI clone
- support endpoint, deployment/model, API version, and key validation
- keep Azure-specific config naming explicit in settings and CLI

##### Gemini

- implement native Gemini multimodal image analysis
- use provider-native request/response handling

##### Claude

- implement native Anthropic Messages vision flow
- keep Anthropic request structure inside its adapter

##### Bedrock

- implement a Bedrock adapter using AWS-native access patterns
- prefer the AWS SDK path where it materially reduces auth/signing complexity
- validate required AWS region/model configuration explicitly

##### Groq

- implement Groq as its own provider entry
- allow internal request reuse only where it does not blur provider identity or config semantics

#### Required result

- `VISION_MEMORY_PROVIDER` becomes truly multi-provider
- each provider returns the same normalized `VisionObservation`

### Step 8: Redesign the settings and env surface for provider growth

The env surface must stop accreting one-off fields.

#### Work

- reorganize `backend/core/settings.py` around selected provider plus provider-scoped config blocks
- retain backward-compatible aliases for current users
- update `backend/.env.example` to show the supported providers and their required env vars clearly
- document which secrets are required only when the corresponding provider is selected

#### Migration rules

- existing OpenAI realtime envs must continue to work
- existing `VISION_PROVIDER_API_KEY`, `VISION_PROVIDER_BASE_URL`, `MISTRAL_API_KEY`, and `MISTRAL_BASE_URL` flows must continue to work during migration
- new provider-specific vars should take precedence when the matching provider is selected

### Step 9: Expand CLI provider metadata and project config

CLI support must land in the same milestone as runtime support.

#### Work

- expand `portworld_cli/provider_catalog.py` with the new realtime and vision providers
- update `.portworld/project.json` provider selections to support the new ids cleanly
- update `portworld init` to prompt for:
  - realtime provider
  - selected realtime provider credentials/settings
  - visual-memory enablement
  - selected vision provider
  - selected vision provider credentials/settings
- update `portworld config edit providers` so users can switch providers without hand-editing env files
- update the env writer so only the selected provider’s required secrets are surfaced as active requirements

#### Result

- the CLI becomes the canonical way to configure providers
- provider sprawl does not push users back to manual env reverse-engineering

### Step 10: Expand doctor and readiness diagnostics

Provider expansion is only acceptable if failure modes stay actionable.

#### Work

- update backend runtime checks to validate only the selected realtime and vision providers
- update `portworld doctor` local checks to show provider-specific config and capability failures
- update Cloud Run doctor/deploy secret checks to upload only the secrets required by the selected providers
- surface provider capability limits in diagnostics when they affect runtime behavior

#### Examples

- missing Gemini Live key should fail only when `REALTIME_PROVIDER=gemini_live`
- missing AWS region/model should fail only when `VISION_MEMORY_PROVIDER=bedrock`
- unsupported tool-calling or voice-selection combinations should produce explicit warnings or failures

### Step 11: Update deploy and secret-binding logic

Deployment workflows must understand provider-specific secret requirements.

#### Work

- update deploy-time secret discovery to use selected-provider requirements
- update secret-manager bindings for Cloud Run deploys
- avoid hardcoding only `OPENAI_API_KEY` and `VISION_PROVIDER_API_KEY`
- make the deployment summary reflect the actual provider configuration in use

### Step 12: Document provider support and migration

Update docs only after the runtime and CLI shape are settled.

#### Docs to update

- `backend/README.md`
- `docs/operations/BACKEND_SELF_HOSTING.md`
- `backend/.env.example`
- provider roadmap docs where needed

#### Required doc content

- supported realtime and vision providers
- required credentials per provider
- default models per provider
- known capability differences
- migration guidance from the current OpenAI + Mistral configuration

## Suggested Delivery Order

Implement in the following order:

1. provider contracts and capability metadata
2. OpenAI realtime refactor onto the new contract
3. provider-neutral tool rendering and submission
4. Gemini Live realtime adapter
5. realtime settings generalization
6. shared vision helper extraction
7. native vision adapters
8. settings/env migration layer
9. CLI provider expansion
10. doctor and deploy diagnostics
11. docs and migration cleanup

## Verification Plan

Run verification after each non-trivial slice.

### Backend verification

- run local config validation with the selected provider combinations
- verify the existing OpenAI realtime flow still works
- verify Gemini Live works through the same `/ws/session` entrypoint
- verify each new vision provider can produce a normalized `VisionObservation` from one JPEG frame
- verify provider-specific failures are surfaced cleanly

### CLI verification

- `portworld init`
- `portworld config edit providers`
- `portworld doctor --target local`
- Cloud Run secret readiness checks for selected-provider combinations

### Acceptance scenarios

- OpenAI realtime + Mistral vision
- OpenAI realtime + OpenAI vision
- Gemini Live realtime + Gemini vision
- Gemini Live realtime + Claude vision
- Gemini Live realtime + Bedrock vision
- OpenAI realtime + Azure OpenAI vision

## Assumptions

- ElevenLabs is out of scope for this implementation slice.
- Search-provider expansion is out of scope for this slice.
- No plugin packaging or third-party provider-extension system is introduced here.
- Broad vision support is desired, but not through an “everything is OpenAI-compatible” abstraction.
- A hybrid implementation style is acceptable:
  - native adapters by default
  - official SDKs used selectively when they materially reduce complexity or risk

## Completion Criteria

This roadmap item is complete when all of the following are true:

- OpenAI and Gemini Live both work as official realtime providers
- the backend core no longer assumes raw OpenAI event and command names
- the listed vision providers exist as official adapters behind the current vision contract
- CLI setup and diagnostics support the new providers
- current OpenAI + Mistral users can migrate without breakage
- docs and env templates reflect the new provider surface clearly

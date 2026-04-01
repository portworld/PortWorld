# CLI And Backend Provider Testing Plan

This is the step-by-step test runbook for validating the PortWorld CLI and backend provider surface.

Use it when you want to answer two questions:

1. Does the source checkout work correctly?
2. Does the published CLI and published backend runtime still work correctly?

## Recommended Order

Always test in this order:

1. Source checkout first
2. Published workspace and packaged CLI second
3. Deployed target smoke last

Reason:

- Source testing is the fastest way to catch config drift and provider integration bugs.
- Published testing validates packaging, generated workspace files, and the released backend image.
- Deployed smoke should only happen after local and published checks are already green.

Do not start with an older published package if you are validating unreleased provider work. Use the local checkout first, then a release candidate or freshly published package/image.

## Current Repo Note

At the time this plan was written, local source readiness was already showing one concrete blocker:

- `vision_memory` is enabled
- `vision_provider` is `mistral`
- `VISION_MISTRAL_API_KEY` is missing

That means the current source checkout will fail provider readiness until you either:

- add `VISION_MISTRAL_API_KEY`, or
- disable vision for lanes that are not testing vision

## Scope

This plan covers:

- CLI command surface in `portworld_cli/`
- backend runtime surface in `backend/`
- all supported provider families:
  - realtime: `openai`, `gemini_live`
  - vision: `mistral`, `openai`, `azure_openai`, `gemini`, `claude`, `bedrock`, `groq`
  - search: `tavily`

This plan does not try to fully certify every cloud deploy target combination. It focuses on local source, local published, and optional final deployed smoke.

## Prerequisites

Before starting:

- Docker and `docker compose` must be available
- Python 3.11+ must be available
- You must have valid credentials for every provider lane you actually plan to run
- You must have one bearer token available for authenticated backend endpoints
- You should have:
  - one small JPEG fixture for `/vision/frame`
  - one short 24 kHz mono PCM16 audio fixture for `/ws/session`

Important repo note:

- There does not appear to be a repo-provided websocket smoke harness today
- For realtime session testing, use either:
  - the iOS app, or
  - a temporary local websocket harness

## Phase 1: Normalize The Env Contract

Do this before any functional test.

### Step 1. Sync `backend/.env` with the canonical template

Use [backend/.env.example](../../backend/.env.example) as the source of truth.

What to do:

1. Compare `backend/.env` against `backend/.env.example` by key name
2. Add any missing canonical keys as blank placeholders
3. Keep only canonical provider-scoped keys in active use
4. Stop relying on legacy keys

Keys that were present in the local `backend/.env` but are legacy and should not be relied on:

- `VISION_MEMORY_MODEL`
- `VISION_PROVIDER_API_KEY`
- `VISION_PROVIDER_BASE_URL`

### Step 2. Treat source and published envs as the same contract

The published workspace template at [published.env.template](../../portworld_cli/templates/published.env.template) matches `backend/.env.example`.

That means:

- if a key is missing from source env, fix the env contract first
- do not maintain separate provider key conventions for source and published flows

### Step 3. Build lane-specific env values

For each test lane:

- enable only the provider being tested
- disable unrelated optional features
- avoid carrying over secrets from other providers unless that lane explicitly needs them

## Phase 2: Define The Test Matrix

Run these lanes.

### Core lanes

1. Realtime only: `openai`
2. Realtime only: `gemini_live`
3. Realtime + tooling: `openai + tavily`
4. Realtime + tooling: `gemini_live + tavily`
5. Realtime + vision: `openai + mistral`
6. Realtime + vision: `openai + openai`
7. Realtime + vision: `openai + azure_openai`
8. Realtime + vision: `openai + gemini`
9. Realtime + vision: `openai + claude`
10. Realtime + vision: `openai + bedrock`
11. Realtime + vision: `openai + groq`
12. Cross-provider sanity: `gemini_live + mistral`

### Negative lanes

Run one intentional failure per provider family:

1. Realtime negative:
   `REALTIME_PROVIDER=openai` with missing `OPENAI_API_KEY`
2. Vision negative:
   `VISION_MEMORY_PROVIDER=azure_openai` with missing `VISION_AZURE_OPENAI_ENDPOINT`
3. Search negative:
   `REALTIME_TOOLING_ENABLED=true` with missing `TAVILY_API_KEY`
4. Security negative:
   `BACKEND_PROFILE=production` without `BACKEND_BEARER_TOKEN`

The goal of negative lanes is to prove the CLI and backend fail early and clearly.

## Phase 3: Source Checkout Validation

Run all of this from the repo root.

### Step 1. CLI smoke for the source checkout

Run:

```bash
python -m portworld_cli.main --help
python -m portworld_cli.main providers list
python -m portworld_cli.main config show
```

Then run provider details for the distinct provider ids that the CLI exposes directly:

```bash
python -m portworld_cli.main providers show openai
python -m portworld_cli.main providers show gemini_live
python -m portworld_cli.main providers show mistral
python -m portworld_cli.main providers show azure_openai
python -m portworld_cli.main providers show gemini
python -m portworld_cli.main providers show claude
python -m portworld_cli.main providers show bedrock
python -m portworld_cli.main providers show groq
python -m portworld_cli.main providers show tavily
```

Note:

- `openai` is reused across more than one provider kind in the catalog
- use `providers list`, `config show`, `doctor`, and lane-based env validation as the authoritative checks for the full matrix

Pass criteria:

- commands execute successfully
- required env keys shown by the CLI match the lane you are testing

### Step 2. Per-lane readiness checks

For each lane, update `backend/.env` to match that lane, then run:

```bash
python -m portworld_cli.main --json doctor --target local
python -m portworld_cli.main --json doctor --target local --full
python -m portworld_cli.main ops check-config
python -m portworld_cli.main ops check-config --full-readiness
python -m portworld_cli.main --json status
```

Pass criteria:

- valid lanes return green readiness
- negative lanes fail with the expected missing-key or missing-config message

### Step 3. Start the source runtime

Run:

```bash
docker compose up --build -d
docker compose ps
```

Pass criteria:

- backend container is running
- the container does not immediately restart or exit

## Phase 4: Source Runtime Functional Checks

Run these checks for every valid lane after the container is up.

### Step 1. Liveness and readiness

Run:

```bash
curl http://127.0.0.1:8080/livez
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8080/readyz
```

Pass criteria:

- `/livez` returns HTTP 200
- `/readyz` returns HTTP 200 for valid lanes
- `/readyz` returns HTTP 503 for intentional invalid lanes

### Step 2. Profile and storage checks

Run:

```bash
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8080/profile
curl -X PUT \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"Provider Test","preferred_language":"en","preferences":["testing"]}' \
  http://127.0.0.1:8080/profile
curl -X POST \
  -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8080/profile/reset
python -m portworld_cli.main ops export-memory --output /tmp/portworld-memory-export.zip
```

Pass criteria:

- profile GET works
- profile PUT persists and returns updated data
- profile reset clears the profile
- memory export creates a zip file successfully

### Step 3. Vision ingest checks

Only run this for vision-enabled lanes.

Run one `POST /vision/frame` request using a fixed JPEG fixture.

Pass criteria:

- response is HTTP 200
- response contains `status=ok`
- backend logs do not show provider configuration failures

### Step 4. Realtime websocket checks

Run this for every realtime lane.

Open a websocket to:

```text
ws://127.0.0.1:8080/ws/session
```

Authenticate with the configured bearer token.

Then send `session.activate` using:

- `client_audio_format.encoding=pcm_s16le`
- `client_audio_format.channels=1`
- `client_audio_format.sample_rate=24000`

Then send one short PCM audio utterance as a binary client-audio frame.

Pass criteria:

- session activates successfully
- backend returns `session.state=active`
- you receive normal upstream activity for the selected realtime provider
- session deactivates cleanly

For tooling lanes, use a prompt that should force search so Tavily is actually exercised.

Suggested evidence to capture:

- one successful `session.activate`
- one successful upstream response event
- one clean `session.deactivate`

## Phase 5: Published Workspace And Packaged CLI Validation

Only start this after source testing is green.

### Step 1. Install a package candidate

Use one of these:

- a freshly published version
- a release candidate
- a local built package if the provider work is not published yet

Do not use an older unrelated public package to validate new provider changes.

### Step 2. Initialize a published workspace

Run:

```bash
portworld init --runtime-source published
cd ~/.portworld/stacks/default
```

### Step 3. Populate the published workspace env

Use the same lane definitions you used for source validation.

Do not invent a second env format for published mode.

### Step 4. Run published CLI checks

Run:

```bash
portworld doctor --target local
portworld doctor --target local --full
portworld status
docker compose up -d
docker compose ps
```

### Step 5. Run a representative published functional subset

At minimum, run these lanes in published mode:

1. `openai` realtime-only
2. `gemini_live` realtime-only
3. `openai + tavily`
4. `openai + azure_openai`
5. `openai + bedrock`
6. one full-stack lane with realtime + vision + tooling

For each published lane, repeat:

- `/livez`
- authenticated `/readyz`
- profile CRUD
- memory export
- websocket smoke
- vision ingest when enabled

Pass criteria:

- the published workspace behaves the same as the source checkout for the tested lanes
- generated files and released image work without source-checkout assumptions

## Phase 6: Optional Deployed Smoke

Only do this after source and published testing are already green.

For the currently tracked GCP target, run:

```bash
portworld doctor --target gcp-cloud-run --gcp-project <project> --gcp-region <region>
portworld status
```

Then verify against the live URL:

- `/livez`
- authenticated `/readyz`

If you need one final deployed functional pass, run:

- profile GET
- one websocket session

Keep deployed smoke narrow. Do not debug provider integration for the first time in the deployed environment.

## Pass And Fail Rules

A lane is green only if all of these are true:

1. CLI provider inspection matches the intended lane
2. `doctor` passes
3. `ops check-config --full-readiness` passes
4. Docker runtime starts cleanly
5. `/livez` passes
6. `/readyz` passes
7. Required HTTP functional checks pass
8. Realtime session smoke passes when that lane includes realtime
9. Vision ingest passes when that lane includes vision
10. Tooling is actually exercised when that lane includes search

A negative lane is green only if it fails for the expected reason.

## Suggested Run Log

For each lane, capture:

- lane name
- env selections
- CLI doctor result
- CLI check-config result
- docker start result
- `/livez` result
- `/readyz` result
- profile CRUD result
- memory export result
- vision ingest result if applicable
- websocket result if applicable
- notes and failures

That gives you one comparable record for source, published, and deployed smoke.

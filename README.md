# PortWorld

PortWorld is an open-source runtime for voice-and-vision assistants connected to the real world.
The supported public slice today is:

- a FastAPI backend for realtime sessions, memory, and provider routing
- the `portworld` CLI for local bootstrap, self-hosting, and managed deploy workflows
- an iOS app that connects a self-hosted PortWorld backend to Meta smart glasses

## Status

PortWorld is shipped as a stable public `v0.x` project.
The supported surfaces are usable today, but the repository is still under active improvement.

- Stable first-class surfaces: `backend/`, `portworld_cli/`, `portworld_shared/`, `IOS/`
- Supported but still hardening: managed cloud deploy defaults and public-facing operator docs
- Not part of the public supported surface: old experimental or maintainer-only materials that are no longer part of the tracked onboarding path

## Get Started

Start with [docs/operations/GETTING_STARTED.md](docs/operations/GETTING_STARTED.md).

That is the canonical onboarding and setup path for:

- the default operator flow via `install.sh` and `portworld init`
- source-checkout contributor setup
- backend-only contributor setup
- iOS contributor setup

## Who This Repo Is For

- operators who want to run PortWorld locally or on managed cloud targets
- contributors working on the backend, CLI, or iOS app
- teams building a self-hosted assistant flow around PortWorld's runtime and provider integrations

## Supported Workflows

- Local operator path: bootstrap the CLI, initialize a published workspace, and run PortWorld locally through Docker
- Source-checkout development: work on the repo directly and use the CLI in source mode
- Backend self-hosting: run the backend locally with documented health and readiness checks
- Managed deploys: use the CLI to validate and deploy to GCP Cloud Run, AWS ECS/Fargate, and Azure Container Apps
- iOS integration: build the iOS app, connect it to a reachable PortWorld backend, and validate the runtime path

## What Works Today

- local backend self-hosting with documented health/readiness checks
- published-workspace local operator flow through `portworld init`
- managed deploy flows for GCP Cloud Run, AWS ECS/Fargate, and Azure Container Apps
- optional provider integrations documented in `backend/README.md` and `portworld providers`
- iOS onboarding, backend validation, and active Meta/glasses runtime path
- released CLI installation through PyPI/TestPyPI, GitHub Releases, and the bootstrap installer

## Major Limitations

- provider credentials are required for meaningful runtime use; there is no no-key production path
- managed deploy defaults still need explicit operator review before internet-facing production rollout
- iOS runtime validation depends on a reachable backend and, for full product validation, supported Meta hardware/app setup
- the shared iOS schemes do not currently provide a meaningful maintained Xcode test action

## Security And Community

- Security policy: [SECURITY.md](SECURITY.md)
- Contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md)
- Code of Conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- Open-source readiness checklist: [docs/open-source/OPEN_SOURCE_READINESS_CHECKLIST.md](docs/open-source/OPEN_SOURCE_READINESS_CHECKLIST.md)

Do not post secrets, tokens, private URLs, or unredacted production logs in public issues.

## Documentation Map

- Canonical onboarding: [docs/operations/GETTING_STARTED.md](docs/operations/GETTING_STARTED.md)
- Backend runtime reference: [backend/README.md](backend/README.md)
- CLI/operator reference: [portworld_cli/README.md](portworld_cli/README.md)
- iOS app reference: [IOS/README.md](IOS/README.md)
- CLI release process: [docs/operations/CLI_RELEASE_PROCESS.md](docs/operations/CLI_RELEASE_PROCESS.md)

## Releases

- Changelog: [CHANGELOG.md](CHANGELOG.md)
- GitHub Releases: <https://github.com/portworld/PortWorld/releases>

## License

This repository is licensed under the MIT License.
See [LICENSE](LICENSE).

# Contributing To PortWorld

Thanks for your interest in contributing.

This project is currently in an early public phase.
Maintainers welcome high-signal contributions, while keeping roadmap ownership
and scope control with maintainers.

## Before You Start

- Read [README.md](README.md) for current setup paths.
- Read [SECURITY.md](SECURITY.md) for security reporting rules.
- Read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community expectations.

## What Contributions Are Most Helpful

- bug fixes with clear reproduction details
- documentation fixes and onboarding improvements
- reliability and operational hardening
- focused refactors that improve clarity without changing behavior
- tests for existing behavior

## PR Scope Policy

Outside pull requests are accepted, but maintainers are roadmap-gated.

Maintainers may decline or close PRs that:

- introduce roadmap-shaping features without prior alignment
- expand deprecated or legacy surfaces
- create significant maintenance burden relative to project priorities

If you want to propose larger work, open an issue first and align on scope.

## Development Expectations

- Keep changes focused and minimal.
- Use descriptive commit messages and PR descriptions.
- Link related issues in the PR body when relevant.
- Update docs when behavior or setup changes.
- Do not commit secrets, credentials, local env files, or private artifacts.

## Validation Expectations

Choose checks based on the area you changed.

For iOS changes, preferred verification order:

1. `xcodebuild build`
2. `xcodebuild test` when tests are relevant and expected

Do not run simulator UI flows unless explicitly requested.

For backend and CLI changes, use targeted validation for the edited surface and
prefer source inspection plus runtime checks over speculative test additions.

## Pull Request Checklist

- [ ] Change is scoped to one clear purpose
- [ ] Relevant docs were updated
- [ ] Local verification was run for the touched area
- [ ] No secrets or sensitive data were introduced
- [ ] PR description explains what changed and why

## Support Channels

- Bug reports and feature requests: open a GitHub issue.
- Security concerns: follow [SECURITY.md](SECURITY.md).
- Conduct concerns: follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

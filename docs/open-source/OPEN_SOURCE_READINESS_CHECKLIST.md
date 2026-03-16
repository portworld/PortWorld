# Open-Source Readiness Checklist

Use this checklist before making the PortWorld repository public.

This is intentionally repo-wide, not CLI-specific.
The CLI and `install.sh` should be reviewed separately after this baseline is in place.

## 1. Legal And Ownership

- [ ] Choose and add a real root `LICENSE` file.
- [ ] Confirm you have the right to publish all code, assets, diagrams, screenshots, and copied snippets.
- [ ] Remove or replace any third-party assets that cannot be redistributed.
- [ ] Check dependency licenses for anything that would conflict with your chosen license.
- [ ] Decide whether model/provider names in docs are factual references or imply endorsement.

## 2. Secrets And Sensitive Data

- [ ] Search the repo history and current tree for API keys, tokens, passwords, private URLs, and internal IDs.
- [ ] Remove accidental secrets from tracked files, examples, screenshots, and test artifacts.
- [ ] Verify `.env`, `.xcconfig`, local build products, and generated data are gitignored where needed.
- [ ] Check for personal emails, phone numbers, IPs, machine names, and local filesystem paths in docs and logs.
- [ ] Review issue templates, sample configs, and exported JSON for private/internal values.

## 3. Repository Shape And First Impression

- [ ] Rewrite the root `README.md` so it matches the current repo, architecture, and supported workflows.
- [ ] Make the root README the real entrypoint for new users:
  - what this repo is
  - who it is for
  - what works today
  - what is intentionally incomplete
- [ ] Ensure the repo layout described in docs matches the actual filesystem.
- [ ] Remove stale hackathon-only or obsolete setup instructions, or clearly label them as historical context.
- [ ] Add a short status section that distinguishes stable paths from experimental ones.

## 4. Developer Onboarding

- [ ] Document the minimum supported platforms and tool versions.
- [ ] Document the fastest working local path for backend-only contributors.
- [ ] Document the fastest working local path for iOS contributors.
- [ ] Make sure all required environment variables and setup steps are documented in one place.
- [ ] Verify a new contributor can get to a meaningful success state without tribal knowledge.

## 5. Build, Run, And Verification

- [ ] Ensure the default local run path actually works on a clean machine.
- [ ] Ensure the default verification path is documented and reproducible.
- [ ] Confirm the repo can be built without private/internal services unless clearly documented.
- [ ] Remove or label flows that only work in the original hackathon/dev environment.
- [ ] Decide what “supported” means for:
  - local backend
  - managed deploy
  - iOS app
  - optional provider integrations

## 6. Security Posture

- [ ] Add a root `SECURITY.md` with disclosure instructions.
- [ ] Review public docs for unsafe recommendations such as committing secrets, permissive production defaults, or unsafe auth guidance.
- [ ] Make sure example configs are safe-by-default for public readers.
- [ ] Check that any admin, debug, or test-only flows are clearly labeled.
- [ ] Decide whether the repo is safe for public issue reports or needs “do not post secrets/logs” guidance.

## 7. Community And Contribution Hygiene

- [ ] Add `CONTRIBUTING.md`.
- [ ] Add `CODE_OF_CONDUCT.md`.
- [ ] Decide how you want to handle:
  - support questions
  - bug reports
  - feature requests
  - outside pull requests
- [ ] Add issue and PR templates if you want structured inbound contributions.
- [ ] State repo expectations clearly:
  - supported branches
  - review bar
  - testing expectations
  - whether maintainers may decline roadmap-shaping PRs

## 8. Releases And Versioning

- [ ] Decide what constitutes a release.
- [ ] Tag a first public release rather than pointing new users at a moving branch tip.
- [ ] Add a changelog or release-notes convention.
- [ ] Decide whether package/distribution channels are:
  - source-only
  - GitHub releases
  - package registry
  - installer script
- [ ] Ensure docs and installer/update instructions point at a real release strategy, not just current maintainer workflow.

## 9. Repo Cleanup

- [ ] Remove dead code, abandoned experiments, and misleading placeholders that make the repo look less trustworthy.
- [ ] Archive or relocate historical docs if they are useful for maintainers but confusing for new users.
- [ ] Remove generated artifacts and editor/system junk from the tracked tree.
- [ ] Make file and directory names consistent where possible.
- [ ] Decide which internal-only docs should stay private versus public.

## 10. Project Positioning

- [ ] Be explicit about what this project is today:
  - hackathon prototype
  - early alpha
  - experimental framework
  - production-ready for some slice
- [ ] State major limitations honestly.
- [ ] State which parts are maintained first and which parts are secondary.
- [ ] Avoid promising platform/provider support that is not actually tested.

## 11. Final Pre-Public Pass

- [ ] Read the repo as if you are a stranger seeing it for the first time.
- [ ] Click every doc link that appears in the main onboarding path.
- [ ] Follow the main quick start exactly as written.
- [ ] Confirm there is no “maintainer-only” assumption hidden in the happy path.
- [ ] Decide whether the repo should open as public immediately or after one more cleanup pass.

## Suggested Order

1. Legal and secrets
2. Root README and positioning
3. Security and contribution docs
4. Real release/versioning story
5. Installer/CLI polish
6. Nice-to-have community automation

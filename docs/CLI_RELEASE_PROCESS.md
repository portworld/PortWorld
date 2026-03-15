# CLI Release Process

This documents the manual tagged-release flow for the public `portworld` CLI.

## Version Source

- The packaged CLI version is sourced from `backend.__version__`.
- Git tags should use the format `vX.Y.Z`.
- The public installer resolves `latest` from GitHub Releases.

## Release Steps

1. Update `backend/__init__.py`
   - bump `__version__` to the intended release version, for example `0.2.0`
2. Verify local packaging and CLI smoke
   - `python -m py_compile $(find portworld_cli -name '*.py' | sort) backend/cli.py`
   - `python -m portworld_cli.main --help`
   - `python -m portworld_cli.main providers list`
   - `python -m portworld_cli.main update cli --json`
3. Verify installer syntax and non-interactive path
   - `bash -n install.sh`
   - if needed, use `PORTWORLD_INSTALL_SOURCE_URL=. PORTWORLD_NO_INIT=1 PORTWORLD_NON_INTERACTIVE=1 bash install.sh`
4. Commit the version bump and related release notes/docs
5. Create an annotated tag
   - `git tag -a vX.Y.Z -m "Release vX.Y.Z"`
6. Push the branch and tag
   - `git push origin <branch>`
   - `git push origin vX.Y.Z`
7. Draft and publish a GitHub Release for the tag
8. Verify the public install paths against the new release
   - installer: `curl -fsSL --proto '=https' --tlsv1.2 https://openclaw.ai/install.sh | bash`
   - pinned installer: `curl -fsSL --proto '=https' --tlsv1.2 https://openclaw.ai/install.sh | bash -s -- --version vX.Y.Z`
   - manual fallback: `python3 -m pipx install --force "https://github.com/armapidus/PortWorld/archive/refs/tags/vX.Y.Z.zip"`

## Post-Release Smoke

- `portworld --help`
- `portworld init --help`
- `portworld providers list`
- `portworld update cli --json`

## Notes

- Source-checkout developer installs remain `pipx install . --force`.
- This process is intentionally manual for now; GitHub release automation is deferred.

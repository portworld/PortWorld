# Security Policy

## Supported Versions

PortWorld is currently in early beta and moving quickly.

Security fixes are handled on a best-effort basis for:

- `main`
- the latest tagged release

Older tags and historical branches may not receive security updates.

## Reporting A Vulnerability

Report potential security vulnerabilities by opening a GitHub issue in this repository.

Use a clear title prefix such as `security:` and include:

- affected component (`backend`, `IOS`, `portworld_cli`, or docs/process)
- impact and possible abuse scenario
- reproduction steps or proof of concept
- mitigation ideas (if available)

Do not include secrets or private data in issues.
Redact API keys, bearer tokens, internal URLs, personal data, and full production logs before posting.

If you accidentally expose a secret while reporting, rotate/revoke it immediately and update the issue with redacted content.

## What To Report Here

Examples that belong in this security policy path:

- auth or authorization bypass
- token/credential leakage risks
- unsafe default configuration that can expose data or services
- injection, traversal, or code execution vectors
- denial-of-service vectors caused by unbounded or unsafe behavior

Non-security bugs (UI defects, crashes without security impact, feature requests) should use normal issue reporting.

## Response Expectations

Maintainers triage reports on a best-effort basis.

- acknowledgement target: within 7 days
- follow-up target: within 30 days with status or mitigation direction

Timelines may vary depending on maintainer availability and report quality.

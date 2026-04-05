# Security Policy

## Supported Versions

PortWorld is under active development and security fixes are handled on a best-effort basis.

Security fixes are handled on a best-effort basis for:

- `main`
- the latest tagged release

Older tags and historical branches may not receive security updates.

## Reporting A Vulnerability

Report potential security vulnerabilities privately using GitHub's
`Report a vulnerability` flow / private security advisories for this repository.

Do not open a public GitHub issue for suspected vulnerabilities.
Use normal public issues only for non-security bugs, crashes without security impact,
and feature requests.

Use a clear title prefix such as `security:` and include:

- affected component (`backend`, `IOS`, `portworld_cli`, or docs/process)
- impact and possible abuse scenario
- reproduction steps or proof of concept
- mitigation ideas (if available)

Do not include secrets or private data in a report.
Redact API keys, bearer tokens, internal URLs, personal data, and full production logs.
Do not upload screenshots, diagrams, or copied snippets unless you have the right to share them.

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

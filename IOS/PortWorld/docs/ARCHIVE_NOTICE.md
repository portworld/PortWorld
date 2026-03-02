# Archive Notice

> **These documents describe the hackathon v4 MVP and are no longer authoritative.**
>
> The current, active documentation lives in `IOS/docs/`.

---

## What happened

These documents were written during the initial hackathon build to specify and track
the `Port:🌍 v4` MVP. They drove the implementation of:

- WebSocket control plane
- Wake word detection (manual + SFSpeech)
- 1fps vision frame upload
- Query bundle creation and upload
- Assistant audio playback pipeline

That implementation is now complete and in the codebase.

The codebase is being refactored toward a production v1.0. The new docs in `IOS/docs/`
capture the updated architecture, requirements, and implementation plan.

---

## Mapping: old → new

| This file | Status | Superseded by |
|-----------|--------|---------------|
| `PRD.md` | **Archived.** Describes hackathon v4 scope and wire contracts. Wire contracts are mostly still valid but requirements are expanded. | `IOS/docs/PRD.md` |
| `CONTEXT.md` | **Archived.** Historical project context. Useful background reading but describes hackathon scope and team assumptions. | `IOS/docs/PRD.md` §1–2 |
| `IMPLEMENTATION_PLAN.md` | **Archived.** Workstream plan (WS0–WS8) for building the hackathon features. All workstreams are complete. | `IOS/docs/IMPLEMENTATION_PLAN.md` |
| `PRD_ACCEPTANCE.md` | **Archived.** Test matrix T1–T13 for hackathon release gate. Tests mostly passed but never formally signed off. | `IOS/docs/TESTING.md` (T1–T18) |
| `PRD_APPENDIX_INTERFACES.md` | **Partially relevant.** Wire contracts (WS payload schemas, HTTP body formats) are still the normative source until the v1 implementation formally validates them. Cross-reference against `IOS/docs/PRD.md §5`. | `IOS/docs/PRD.md §5` |
| `Wearables DAT SDK.md` | **Still useful.** Meta MWDAT SDK integration reference. Not superseded; the SDK does not change with the refactor. | — (keep as reference) |
| `DEBUG_GREETING_AUDIO.md` | **Developer note.** Debugging guide for playback issues. Relevant during development but not a formal spec. | — (keep as developer note) |

---

## Do not delete these files

They provide useful historical context for understanding why certain design decisions were made.
They also contain wire contract details (`PRD_APPENDIX_INTERFACES.md`) that remain valid
until the v1.0 refactor formally validates and supersedes the transport layer.

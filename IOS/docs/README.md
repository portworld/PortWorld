# PortWorld iOS — Documentation

This directory holds the canonical, current documentation for the PortWorld iOS app.

---

## Documents

| File                                             | Purpose                                                                                              | Read first?                      |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------- | -------------------------------- |
| [ARCHITECTURE.md](ARCHITECTURE.md)               | Target architecture: module map, data flows, concurrency model, design system, storage, navigation   | Yes — start here                 |
| [PRD.md](PRD.md)                                 | Product requirements, functional requirements, transport contracts, failure modes, version roadmap   | Yes                              |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Step-by-step refactoring plan, Phase 0–5, per-file instructions                                      | When implementing                |
| [TESTING.md](TESTING.md)                         | Test strategy, unit test inventory, snapshot inventory, manual acceptance tests T1–T18, release gate | Before any release               |
| [IOS_APP_REALTIME_REVIEW.md](IOS_APP_REALTIME_REVIEW.md) | Review of the current iOS app against the intended realtime wake -> stream -> converse -> sleep flow | When debugging realtime behavior |
| [Wearables DAT SDK.md](Wearables%20DAT%20SDK.md) | Meta DAT SDK v0.4 reference: setup, session lifecycle, HFP audio, Mock Device Kit, API surface       | When touching DAT / glasses code |

---

## Relationship to old docs

`IOS/PortWorld/docs/` contains documentation from the hackathon (v4 MVP, pre-refactor).
Those documents are archived and **no longer authoritative**. They describe a state the codebase is being refactored away from.
See `IOS/PortWorld/docs/ARCHIVE_NOTICE.md` for details on which old documents map to which new ones.

---

## How to keep these docs current

1. **Architecture changes** → update `ARCHITECTURE.md` in the same PR as the code change.
2. **New requirements or scope change** → update `PRD.md`; update the version roadmap if a version boundary shifts.
3. **New implementation steps** (new bugs found, new tasks discovered) → add to `IMPLEMENTATION_PLAN.md` under the relevant phase with a `[NEW]` marker.
4. **New test scenarios** → add to `TESTING.md`; increment the T-number sequentially.

Stale docs are worse than no docs. When in doubt, update.

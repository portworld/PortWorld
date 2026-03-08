# iOS PhoneOnly To Glasses Roadmap

## Purpose

This document captures the intended sequencing for the iOS app from the current working phone-only assistant toward the longer-term Ray-Ban Meta + vision product.

It is not a detailed implementation spec. It is a short process map for how we want to evolve the codebase.

## Current Position

- The phone-only assistant runtime now works end-to-end.
- Wake and spoken sleep behavior are now working in the active runtime.
- the old `IOS/PhoneOnly/` reference slice has now been removed.
- its purpose was to help reduce and clarify the active runtime during Phase 1.
- that historical reasoning now lives in docs rather than in a parallel code tree.

## Why `IOS/PhoneOnly/` Existed

`IOS/PhoneOnly/` gave us a reduced view of the runtime so we could:

- see the minimum set of files involved in the phone-only assistant
- reason about the active path without DAT, camera, or legacy runtime noise
- improve code quality on the working assistant before adding more hardware and media complexity

That cleanup role is now complete.

The source of truth is the active `IOS/PortWorld/` runtime plus the accompanying implementation notes in `docs/intermediary/PHASE1_IMPLEMENTATION.md`.

## Locked Decisions

- The next cleanup priority is:
  - smallest possible active code surface
  - better file and folder ownership
- Refactors should be conservative.
- Archived code stays in an archive folder for reference until the app is deployable in its new form.
- The forward hardware path is glasses-connected runtime support with Meta mock-device support retained for development.
- Backend cleanup should stay aligned to this iOS app and its runtime contract, not become a generic reusable platform.

## Guiding Principle

We should extend the new working phone-only runtime forward.

We should not rebuild the product by re-expanding the old legacy assistant stack.

Every next layer should be added on top of a cleaner, smaller, better-owned codebase.

## Planned Sequence

### 1. Stabilize and clean the phone-only runtime

Goal:

- make the current phone-only runtime easy to read, test, and maintain

Work:

- reduce the active code surface as much as possible
- identify the true active-path files
- split oversized files with mixed responsibilities conservatively
- remove or isolate legacy compatibility code that the phone-only path does not need
- make ownership boundaries explicit across audio, wake detection, backend transport, playback, UI state, and app shell
- add a short file-purpose description at the top of every actively maintained source file

Expected result:

- files have clear ownership
- the phone-only path can be understood without tracing through legacy DAT/runtime layers

Status:

- complete

Evidence / outcome:

- the shipping app is now phone-first and launches into the active assistant path in `IOS/PortWorld/`
- the old `IOS/PhoneOnly/` slice has been removed so there is no second phone-only code tree
- future-hardware / DAT setup is isolated as a secondary path rather than shaping app launch and the main runtime
- the active runtime has been reduced, reorganized by subsystem, and stripped of the main legacy compatibility surfaces
- state ownership is simpler:
  - one runtime-owned UI status model
  - no duplicate `PhoneAssistantRuntimeStore` layer
- active files now carry purpose headers and the main Xcode/runtime logs have been trimmed back to higher-signal lifecycle, interruption, queue-pressure, and error events
- targeted build and runtime verification confirmed that activate, armed listening, wake, realtime mic -> backend -> speaker, sleep, and deactivate behavior still work after the cleanup

Execution trace:

- full Phase 1 step-by-step record lives in `docs/intermediary/PHASE1_IMPLEMENTATION.md`

### 2. Turn the phone-only foundation into a glasses-capable runtime

Goal:

- keep the working assistant lifecycle, but make it compatible with glasses-connected Ray-Ban Meta / DAT integration

Work:

- integrate DAT and glasses lifecycle into the new runtime shape
- retain Meta mock-device support as part of the active development workflow
- decide which responsibilities stay phone-owned versus glasses-owned
- adapt audio/session handling to support the glasses path without reintroducing old architecture drift

Expected result:

- one coherent assistant runtime that supports the glasses-connected path cleanly, with mock-device support available during development

### 3. Add vision input after glasses compatibility is stable

Goal:

- add image or video collection only after audio/runtime behavior is solid

Work:

- add camera/frame collection in a bounded module
- define the minimal upload/streaming path needed for vision
- keep vision separate from the core assistant control loop as much as possible

Expected result:

- vision becomes an additive capability, not a new source of runtime coupling

### 4. Clean up and productize the backend

Goal:

- make the backend easy to understand, modular, and suitable for open-source release

Work:

- simplify backend contracts around the working app behavior
- remove hackathon-era compatibility layers that are no longer needed
- improve structure, documentation, and separability for public release

Expected result:

- a cleaner backend framework that matches the final app architecture and can be open-sourced confidently

Execution detail for this step lives in `docs/BACKEND_PRODUCTIZATION_ROADMAP.md`.

## Code Quality Rules For The Next Phase

- prefer small files with one clear responsibility
- avoid “god files” that mix orchestration, UI state, transport, audio, and policy
- split files conservatively rather than exploding one file into many tiny files at once
- keep app-shell, runtime, hardware integration, and media collection separated
- preserve a single obvious active path
- add a short purpose description at the top of each actively maintained source file
- treat archived and legacy code as reference only unless intentionally migrated

## Success Criteria

We are on the right path if:

- the active assistant path is easy to explain from top to bottom
- each file has a clear owner and reason to exist
- glasses support can be added without reviving the old runtime architecture
- vision can be added without destabilizing the assistant loop
- backend cleanup becomes a follow-through step instead of another reset

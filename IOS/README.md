# PortWorld iOS App

This directory contains the active iOS client for PortWorld.

For first-time setup, start with [../docs/operations/GETTING_STARTED.md](../docs/operations/GETTING_STARTED.md).
This README keeps the iOS-specific runtime, Meta DAT, permissions, and validation details.

The current app centers on:

- onboarding contributors and testers into a working runtime
- connecting PortWorld to Meta smart glasses
- validating a self-hosted backend
- running the assistant through the glasses route once setup is complete

The source of truth for active iOS code is `IOS/PortWorld/`.

## What Contributors Should Know First

- Open `IOS/PortWorld.xcodeproj` in Xcode.
- Use the `PortWorld` scheme by default. `PortWorldDev` is also shared, but both schemes build the same app target.
- The app targets iOS 17.0.
- A reachable PortWorld backend is required for meaningful runtime validation.
- Meta integration is active product surface. You can still inspect code, build the app, and review most onboarding flows without glasses hardware.

## Current App Flow

At launch, the app shows a startup/loading state while shared wearables support initializes.

The onboarding flow then advances through these steps:

1. Welcome
2. Feature overview
3. Backend introduction
4. Backend setup and validation
5. Meta connection
6. Wake practice
7. Profile interview

After onboarding, the app enters a tab-based shell with:

- `Home`
- `Agent`
- `Settings`

From the `Agent` tab, the assistant can be activated once both of these are ready:

- the backend has been validated
- the glasses route is ready for activation

When active, the runtime listens for the configured wake phrase, opens a backend session, and returns to an idle or listening state when the configured sleep phrase ends the interaction.

## Project Layout

The active app code lives under `IOS/PortWorld/`:

```text
IOS/PortWorld/
├── App/                  # onboarding flow, home/settings screens, readiness models
├── Views/                # root app views and shared presentation surfaces
├── ViewModels/           # thin view-model bridge into runtime state
├── Runtime/
│   ├── Assistant/        # assistant state machine and conversation lifecycle
│   ├── Transport/        # backend websocket client and wire types
│   ├── Playback/         # assistant playback engine
│   ├── Wake/             # wake and sleep phrase detection
│   ├── AudioIO/          # audio route control for phone and glasses paths
│   └── Glasses/          # Meta DAT lifecycle, registration, discovery, vision capture
├── Audio/                # shared audio helpers and session coordination
├── Utilities/            # clocks, keychain storage, small support types
├── Assets.xcassets/
└── StartupLaunchScreen.storyboard
```

## Setup

The canonical contributor happy path is in [../docs/operations/GETTING_STARTED.md](../docs/operations/GETTING_STARTED.md).

Use this README for the iOS-specific pieces that remain after that setup:

1. Open `IOS/PortWorld.xcodeproj`.
2. Let Xcode resolve Swift Package dependencies.
3. Review `IOS/Config/Config.xcconfig.template` before changing local build settings.
4. Configure a backend base URL and, if needed, a bearer token for your local environment.
5. Build the `PortWorld` scheme.

Notes:

- Do not copy real local secrets into repo-tracked files or docs.
- The checked-in config template is the reference for expected local values.
- The app reads backend defaults from the preprocessed `IOS/Info.plist`, then lets users override and validate them in the app.
- The bearer token is stored securely in Keychain once entered or loaded.

### Meta DAT Configuration

The project supports two DAT setup modes:

- developer mode
  The default template path. This is the least demanding setup for local development.
- registered-project mode
  Requires `MetaAppID`, `ClientToken`, and `TeamID`.

The app also expects:

- the `portworld` callback URL scheme to stay aligned with DAT callback configuration
- the Meta AI app to be installed for registration and permission handoff

## Configuration And Permissions

### Runtime Configuration

The app currently derives runtime behavior from `Info.plist`, local xcconfig values, and persisted in-app settings.

Key runtime inputs include:

- `SON_BACKEND_BASE_URL`
- optional bearer token
- optional explicit websocket URL
- optional explicit vision upload URL
- wake phrase and sleep phrase settings
- wake detection mode, locale, and cooldown values

If explicit websocket or vision URLs are not supplied, the app derives them from the configured backend base URL using the current runtime defaults.

### Backend Endpoints

During setup, the app validates the configured backend by calling:

- `GET /livez`
- `GET /readyz`

At runtime, the assistant uses the websocket session endpoint derived from the configured backend, currently `/ws/session` by default. Vision uploads are derived from the backend as `/vision/frame` by default.

### Required Permissions

The app currently declares and uses these permissions/capabilities:

- microphone
- speech recognition
- camera
- Bluetooth
- local network access
- photo library add access

It also enables the capabilities needed for local-network backend access, background audio, external accessory support, and Meta app interoperability.

## Build And Validate Changes

Use build-first verification for non-trivial iOS changes.

Recommended baseline:

1. Open `IOS/PortWorld.xcodeproj`
2. Select the `PortWorld` scheme
3. Build the app

For contributor validation:

- Confirm the app still builds cleanly after your change.
- If you changed backend-facing behavior, validate the backend setup flow in-app and confirm backend readiness still succeeds with a reachable deployment.
- If you changed Meta or glasses flows, validate only the paths your setup actually supports.

Constraints:

- Do not default to simulator UI smoke instructions unless they are explicitly needed.
- Do not assume an active maintained Xcode test suite. Shared schemes currently do not provide meaningful test actions for the app.

## Contributor Constraints

- Treat `IOS/PortWorld/` as the source of truth for the active app.
- Preserve the current ownership boundaries between views, view models, assistant runtime, and wearables runtime.
- Keep contributor-facing docs grounded in shipped behavior, not roadmap promises.
- Never commit secrets, private tokens, or environment-specific screenshots/artifacts.

## Related Docs

- `IOS/AGENTS.md` for iOS-specific implementation and verification guidance
- `../backend/README.md` for backend runtime and local backend setup context

<p align="center">
  <img src="Port World logo.png" width="100%" alt="Port:World Logo">
</p>

What if AI could see the world the way we do?

Port:World is an open source framework that turns smart glasses into a live interface between human perception and AI reasoning.

Built during the Mistral Worldwide Hackathon 2026 by Pierre Haas, Vassili de Rosen, Arman Artola.

It combines an iOS glasses client with a FastAPI backend for voice + vision + tool orchestration. You own the domain logic and prompts; Port provides the runtime, transport, and integration surface.

The uses cases are infinite, connect your AI agents, customize them, prompt them, link your MCP, connect your Openclaw...



## Highlights

- Voice in with Voxtral-compatible STT.
- Vision/video understanding with Nemotron-compatible endpoints (NVIDIA GPU BREV Deployments and OpenAI API Compatible.
- Agent presets + runtime overrides
- Live token-to-audio relay (`/v1/pipeline/tts-stream`) with ElevenLabs streaming.
- iOS app (`PortWorld`) with "test backend" flow for end-to-end smoke testing.

## Table Of Contents

- [Architecture](#architecture)
- [Repository Layout](#repository-layout)
- [Quick Start (5 Minutes)](#quick-start-5-minutes)
- [iOS Setup (Simulator + Real iPhone)](#ios-setup-simulator--real-iphone)
- [Run End-to-End Test From The App](#run-end-to-end-test-from-the-app)
- [Backend API Surface](#backend-api-surface)
- [Troubleshooting](#troubleshooting)
- [Security Notes](#security-notes)
- [Additional Docs](#additional-docs)

## Architecture

1. Glasses + iOS app capture audio/video/photo context.
2. Backend resolves runtime profile, selected agent, and provider routing.
3. Main LLM generates response using transcript + visual context + optional tools.
4. TTS endpoint streams assistant audio back to the client.

### MistralAI Worldwide Hackathon Architecture Example

<img width="2600" height="1200" alt="image" src="https://github.com/user-attachments/assets/b025ab6a-47a9-420f-ae9e-288207df02d7" />

## Repository Layout

- `framework/`: backend framework (FastAPI, runtime config, providers, agents, tracing).
- `IOS/`: iOS client app (`PortWorld`) for Meta Wearables DAT integration.

## Quick Start (5 Minutes)

### 1) Clone And Install

```bash
git clone https://github.com/armapidus/PortWorld.git
cd PortWorld

python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r framework/requirements.txt
```

### 2) Configure Backend Environment

```bash
cp framework/.env.example .env
```

Update `.env` with your keys (minimum recommended):

- `MAIN_LLM_API_KEY` (agents other STT)
- `VOXTRAL_API_KEY` (or other STT)
- `NEMOTRON_BASE_URL` (or other VTT)
- `NEMOTRON_API_KEY`
- `ELEVENLABS_API_KEY` 
- optional: `EDGE_API_KEY` (for BREV NVIDIA token deployment - for Mistral Worlwide Hackathon)

### 3) Run Backend

```bash
HOST=0.0.0.0 PORT=8082 python framework/app.py
```

### 4) Smoke Test Backend

```bash
curl -sS http://127.0.0.1:8082/healthz | jq
curl -sS http://127.0.0.1:8082/v1/agents | jq
curl -sS http://127.0.0.1:8082/v1/config/quickstart-template | jq
```

## iOS Setup (Simulator + Real iPhone)

### 1) Open Project

```bash
open IOS/PortWorld.xcodeproj
```

### 2) Configure Backend URL In `Info.plist`

Edit `SON_BACKEND_BASE_URL` in [`IOS/Info.plist`](IOS/Info.plist):

- iOS Simulator: `http://172.16.0.104:8082`
- real iPhone: `http://<YOUR_MAC_LAN_IP>:8082`

Get your Mac LAN IP:

```bash
ipconfig getifaddr en0 || ipconfig getifaddr en1
```

Keep default paths:

- `SON_WS_PATH=/ws/session`
- `SON_VISION_PATH=/vision/frame`
- `SON_QUERY_PATH=/query`

### 3) Signing For Personal Team

If you use a free/personal Apple team:

1. `TARGETS > PortWorld > Signing & Capabilities`
2. Enable `Automatically manage signing`
3. Select your `Personal Team`
4. Use a unique bundle id (for example `com.yourname.PortWorld`)
5. Remove `Associated Domains` capability for local testing
6. In `Build Settings`, clear `Code Signing Entitlements` if still pointing to `PortWorld.entitlements`

### 4) Real iPhone Prerequisites

1. Enable iPhone Developer Mode:
   - `Settings > Privacy & Security > Developer Mode`
2. Keep iPhone and Mac on the same Wi-Fi.
3. Allow local network access for PortWorld in iPhone settings.

## Run End-to-End Test From The App

You can validate backend integration without glasses by using the built-in example media flow.

1. Launch the app from Xcode (`Cmd+R`).
2. Tap `TEST BACKEND (Example Media)`:
   - available on Home screen
   - available on Runtime setup screen
3. App posts to `POST /v1/pipeline/tts-stream` and plays returned audio.

Backend should log a `POST /v1/pipeline/tts-stream` request.

## Backend API Surface

- `GET /healthz`
- `GET /v1/debug/endpoints`
- `GET /v1/agents`
- `GET /v1/config/quickstart-template`
- `GET /v1/config/runtime-template`
- `POST /v1/pipeline`
- `POST /v1/pipeline/tts-stream`
- `POST /v1/elevenlabs/stream`
- `POST /v1/debug/ios/simulate`
- `POST /v1/debug/vision/frame`

## Troubleshooting

### `Connection refused` to `127.0.0.1:8080`

Cause: app points to port `8080` while backend runs on `8082`.  
Fix: set `SON_BACKEND_BASE_URL` to correct host/port.

### `The Internet connection appears to be offline` with `Local network prohibited`

Cause: iOS blocked local network permission.  
Fix:

1. `Settings > PortWorld > Local Network` -> enable.
2. Reinstall app if needed to trigger permission prompt again.

### Can open server in Safari but app still fails

Check:

- `SON_BACKEND_BASE_URL` is exactly correct.
- iPhone and Mac are on same subnet.
- backend running with `HOST=0.0.0.0`.

### App call works but backend audio does not play

Update to latest code on `main`.  
`ExampleMediaPipelineTester` now requests `mp3_44100_128` and includes playback fallbacks.

### `Invalid profile 'uRGB'` warnings

Non-blocking image/profile warning in logs. Usually unrelated to network/audio flow.

## Security Notes

- Never commit real API keys to Git.
- Use `.env` locally and rotate keys if accidentally shared.
- Use `EDGE_API_KEY` when exposing backend beyond local/private network.


## Additional Docs

- Backend details: [`framework/README.md`](framework/README.md)
- iOS details: [`IOS/README.md`](IOS/README.md)

## License

Recommended for open-source release: Apache-2.0 or MIT.

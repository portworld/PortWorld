<h1 align="center">
  Port:World
</h1>

<p align="center">
  <strong>Turn your Ray-Ban Meta glasses into a real-time AI assistant.</strong>
</p>

<p align="center">
  Your AI sees what you see. Talks back instantly. Remembers everything.<br/>
  Open-source iOS app + backend runtime for agents that live in the real world — not just in chat.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License" /></a>
  <a href="https://pypi.org/project/portworld/"><img src="https://img.shields.io/pypi/v/portworld?color=3775A9&label=CLI" alt="PyPI" /></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/iOS-17%2B-black" alt="iOS 17+" />
  <a href="https://github.com/portworld/PortWorld/stargazers"><img src="https://img.shields.io/github/stars/portworld/PortWorld?style=social" alt="GitHub Stars" /></a>
</p>

<p align="center">
  <sub>Top 10 at the Mistral Worldwide Hackathon 2026 · Winner of Giant Ventures' "Future Unicorn" prize</sub>
</p>

<br/>

<p align="center">
  <strong>Look at something → ask a question → get an answer instantly</strong><br/>
  <strong>Walk through the world → your AI remembers what mattered</strong><br/>
  <strong>Talk naturally → no screen, no typing, no app switching</strong>
</p>

<br/>

**Get started in minutes**

```bash
pip install portworld
```

<br/>

## Demo

<!-- Replace with your GIF or video when ready -->
<!-- <p align="center"><img src="assets/demo.gif" width="720" alt="PortWorld demo" /></p> -->
<!-- <p align="center"><a href="https://youtu.be/YOUR_VIDEO_ID"><img src="assets/demo-thumbnail.png" width="720" alt="Watch the demo" /></a></p> -->

Imagine walking through a city with an AI that can:

- **Identify** buildings, objects, and landmarks in real time
- **Answer** follow-up questions by voice
- **Remember** what you saw earlier in the day
- **Act** through tools, APIs, and external agents

> *"What is this building?"*
> *"Where should I eat nearby?"*
> *"What was the name of that church we passed earlier?"*

<br/>

## What Is Port:World?

Port:World is a **runtime for AI agents in the physical world**.

Instead of building chatbots, you build agents that:

- **See** through a camera
- **Hear** through a microphone
- **Speak** back in real time
- **Remember** across sessions
- **Act** through tools

This is not an app — it's a platform. You bring the agent logic. Port:World handles streaming, model routing, memory, and the glasses.

<br/>

## Who Is This For?

- **AI engineers** building agent systems that go beyond text
- **Developers** exploring wearable + AI integration
- **Builders** who want to ship real-world AI — not another chat wrapper

<br/>

## Quickstart

> **Time to first interaction: ~5 minutes**

### Option A: Install the CLI (no clone required)

```bash
pip install portworld
portworld init
portworld doctor --target local
```

### Option B: Clone and run with Docker

```bash
git clone https://github.com/portworld/PortWorld.git && cd PortWorld
cp backend/.env.example backend/.env
# Set at least one provider key — see "Minimum config" below
docker compose up --build
```

Verify:

```bash
curl http://127.0.0.1:8080/livez
# → {"status":"ok","service":"portworld-backend"}
```

### Connect the iOS app

```bash
open IOS/PortWorld.xcodeproj
```

1. Let Xcode resolve Swift Package dependencies
2. Build the **PortWorld** scheme
3. Enter your backend URL in Settings and validate the connection

### Siri shortcuts on iOS

The iOS app exposes a Siri shortcut that opens PortWorld and attempts to start an assistant session.

Available phrases:

- `Start PortWorld session`
- `Start assistant in PortWorld`
- `Launch PortWorld assistant session`

If onboarding is incomplete or backend or glasses readiness is blocked, Siri still opens the app but the session will not start until those requirements are met.

### Minimum config

You only need **one API key** to start:

| Provider | `backend/.env` |
|---|---|
| OpenAI Realtime | `REALTIME_PROVIDER=openai` + `OPENAI_API_KEY=sk-...` |
| Gemini Live | `REALTIME_PROVIDER=gemini_live` + `GEMINI_LIVE_API_KEY=...` |

Vision, search, memory consolidation, and tool integrations are all **off by default** — enable them as you need them.

<br/>

## What You Can Build

| Use case | How it works |
|---|---|
| **Real-time travel guide** | Walk through a city — the agent identifies landmarks, translates signs, suggests restaurants based on what it sees |
| **Hands-free field assistant** | Mechanics, surgeons, or technicians get step-by-step guidance while keeping both hands free |
| **Accessibility companion** | Describe scenes, read text aloud, identify objects and people for visually impaired users |
| **Personal memory engine** | "What was the name of that restaurant we passed?" — the agent remembers what you saw |
| **Live coding pair** | Point your glasses at a whiteboard or screen — the agent reads, reasons, and discusses |
| **Security / inspection** | Walk a site — the agent logs observations, flags anomalies, and generates reports |
| **Your idea here** | Port:World is a runtime, not a single app. Build whatever you want on top of it. |

<br/>

## How It Works

```
┌──────────────┐       WebSocket (audio + control)       ┌──────────────────┐
│              │ ◄──────────────────────────────────────► │                  │
│  Ray-Ban     │                                          │   FastAPI        │
│  Meta        │       HTTP (vision frames)               │   Backend        │
│  Glasses     │ ──────────────────────────────────────►  │                  │
│              │                                          │   ┌────────────┐ │
│  ↕ DAT SDK   │                                          │   │ Realtime   │ │
│              │                                          │   │ Bridge     │─┼──► OpenAI / Gemini
│  iPhone      │                                          │   ├────────────┤ │
│  (bridge)    │                                          │   │ Vision     │─┼──► Mistral / Claude / GPT-4o / ...
│              │                                          │   ├────────────┤ │
│              │                                          │   │ Memory     │ │
│              │                                          │   ├────────────┤ │
│              │                                          │   │ Tools      │─┼──► Web search, MCP, OpenClaw, ...
│              │                                          │   └────────────┘ │
└──────────────┘                                          └──────────────────┘
```

**Glasses** capture audio and camera frames via Meta's DAT SDK.
**iPhone** bridges glasses I/O to the backend over WebSocket (audio) and HTTP (vision).
**Backend** routes audio to a realtime AI provider, processes vision frames through pluggable analyzers, manages persistent memory, and executes tools during the conversation.

### Component map

| Surface | What it does |
|---|---|
| [`backend/`](backend/) | FastAPI server — realtime voice relay, vision pipeline, memory, tools, auth |
| [`IOS/`](IOS/) | SwiftUI app — glasses integration (DAT), audio capture, wake word, WebSocket transport |
| [`portworld_cli/`](portworld_cli/) | Developer CLI — init, doctor, deploy, status, logs, providers, extensions |
| [`portworld_shared/`](portworld_shared/) | Shared Python contracts between CLI and backend |

<br/>

## Supported Providers

### Realtime (voice)

| Provider | ID | Key |
|---|---|---|
| OpenAI Realtime | `openai` | `OPENAI_API_KEY` |
| Gemini Live | `gemini_live` | `GEMINI_LIVE_API_KEY` |

### Vision (opt-in)

| Provider | ID | Key(s) |
|---|---|---|
| Mistral | `mistral` | `VISION_MISTRAL_API_KEY` |
| OpenAI | `openai` | `VISION_OPENAI_API_KEY` |
| Gemini | `gemini` | `VISION_GEMINI_API_KEY` |
| Claude | `claude` | `VISION_CLAUDE_API_KEY` |
| Groq | `groq` | `VISION_GROQ_API_KEY` |
| NVIDIA | `nvidia_integrate` | `VISION_NVIDIA_API_KEY` |
| Azure OpenAI | `azure_openai` | `VISION_AZURE_OPENAI_API_KEY` + endpoint |
| AWS Bedrock | `bedrock` | `VISION_BEDROCK_REGION` (+ IAM) |

### Search & tools (opt-in)

| Provider | ID | Key |
|---|---|---|
| Tavily | `tavily` | `TAVILY_API_KEY` |

```bash
portworld providers list          # see all available providers
portworld providers show <id>     # inspect a specific provider
```

<br/>

## Extending Port:World

Port:World is designed to be extended. Here's how developers plug into the system:

### Add a tool

Tools are async functions the AI can call mid-conversation. Register a definition + executor in the tool catalog:

```python
registry.register(
    ToolDefinition(
        name="my_tool",
        description="Does something useful",
        parameters={"type": "object", "properties": { ... }},
    ),
    executor=my_tool_executor,
)
```

### Add a vision provider

Implement a vision analyzer and register it in the vision factory. Your analyzer receives camera frames and returns semantic descriptions that feed into the agent's memory.

### Add a realtime provider

Implement the realtime bridge interface and register it in the provider registry. The bridge handles upstream audio streaming and tool dispatch for any new model API.

### Connect MCP servers

The backend supports Model Context Protocol (MCP) extensions. Drop a server config into the extensions system and expose new capabilities to the agent without touching core code.

### Delegate to external agents

Use the OpenClaw delegation layer to offload long-running or tool-heavy tasks to external agent runtimes, while Port:World stays the live conversational orchestrator.

See [backend/README.md](backend/README.md) for the full API and extension reference.

<br/>

## Deploy

### Local (Docker Compose)

```bash
docker compose up --build
```

### Cloud (one command)

```bash
portworld deploy gcp-cloud-run       --project <id> --region <region>
portworld deploy aws-ecs-fargate     --region <region>
portworld deploy azure-container-apps --subscription <sub> --resource-group <rg> --region <region>
```

See [portworld_cli/README.md](portworld_cli/README.md) for readiness checks, log streaming, and redeployment.

<br/>

## Roadmap

Port:World is evolving from a hackathon winner into a full wearable AI platform:

- **Agentic delegation** — OpenClaw integration for heavy multi-step tasks
- **Richer memory** — identity, routines, social graph, preferences, with confidence tracking
- **Passive context** — ambient scene understanding even outside active conversations
- **Proactive assistance** — timely suggestions earned through context quality and user trust
- **Siri / App Shortcuts** — launch a session with your voice, no app interaction needed
- **Android support** — bring the same glasses-first experience to Android
- **Multi-agent coordination** — orchestrate multiple specialized agents in parallel

Full roadmap: [docs/roadmap/AGENTIC_PERSONAL_ASSISTANT_ROADMAP.md](docs/roadmap/AGENTIC_PERSONAL_ASSISTANT_ROADMAP.md)

<br/>

## Project Status

Port:World is in its first stable release phase. Core surfaces are release-ready.

- **Stable:** backend self-hosting, CLI bootstrap/deploy, iOS app with Meta glasses
- **Shipping:** first public PyPI + GHCR releases with `v0.2.x`
- **Hardening:** managed cloud defaults, operator docs, production security posture

### Known limitations

- Provider API keys required — no keyless demo mode
- AWS/Azure one-click deploys use public DB access by default (tighten before production)
- Full glasses features require Meta hardware + Meta AI app
- Xcode test schemes are not yet maintained

<br/>

## Documentation

| Doc | What's inside |
|---|---|
| [backend/README.md](backend/README.md) | Backend API, config reference, storage, auth |
| [portworld_cli/README.md](portworld_cli/README.md) | CLI install, commands, deploy workflows |
| [IOS/README.md](IOS/README.md) | iOS setup, Meta DAT, permissions, architecture |
| [GETTING_STARTED.md](GETTING_STARTED.md) | Extended onboarding for all setup paths |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

<br/>

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a PR.

- **Bug reports & features:** [open an issue](https://github.com/portworld/PortWorld/issues)
- **Security:** [SECURITY.md](SECURITY.md)
- **Code of conduct:** [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

<br/>

## Origin

Built during the **Mistral Worldwide Hackathon 2026** by [Pierre Haas](https://github.com/p-haas), Vassili de Rosen, and Arman Artola.

<br/>

## License

MIT — see [LICENSE](LICENSE).

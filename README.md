# ACP → AG-UI

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![ACP SDK](https://img.shields.io/badge/agent--client--protocol-0.10+-blue.svg)](https://pypi.org/project/agent-client-protocol/)
[![AG-UI](https://img.shields.io/badge/AG--UI-protocol-green.svg)](https://docs.ag-ui.com)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-teal.svg)](https://fastapi.tiangolo.com)

> Coding agents live in terminals. This adapter gives them rich web UIs.

<p align="center">
  <img src="docs/assets/httpagent-demo-chat.png" alt="AG-UI HttpAgent consuming ACP agent events" width="700"/>
</p>
<p align="center">
  <em>An ACP coding agent (kiro-cli) powering a web chat UI via AG-UI events. Zero custom protocol code.</em>
</p>

<details>
<summary>See the raw AG-UI events flowing over SSE</summary>
<p align="center">
  <img src="docs/assets/sse-events-terminal.png" alt="AG-UI SSE events" width="700"/>
</p>
</details>

---

## The Problem

There are now **33+ coding agents** that support the [Agent Client Protocol (ACP)](https://agentclientprotocol.com): Kiro, Claude Code, Codex CLI, Cursor, Gemini CLI, GitHub Copilot, OpenCode, Cline, and many more. They all speak JSON-RPC 2.0 over stdio. You can use them in terminals. You can use them in editors.

But what if you want a **custom web workspace**? A task board for your team? A domain-specific IDE? A deployment dashboard powered by an AI agent? You'd need to implement the protocol bridge yourself: parsing JSON-RPC streams, managing subprocesses, translating events into something a web frontend can render.

## The Solution

This project is a **protocol bridge** that sits between any ACP agent and any web frontend:

```mermaid
graph TB
    subgraph agents["ACP Agents (33+)"]
        A1["kiro-cli"] ~~~ A2["claude-agent-acp"] ~~~ A3["codex"] ~~~ A4["gemini cli"] ~~~ A5["cursor"] ~~~ A6["ANY ACP binary"]
    end

    agents <-->|"JSON-RPC 2.0 over stdio"| bridge

    subgraph bridge["This Bridge (Python / FastAPI)"]
        B1["AgentRunner"] ~~~ B2["AcpToAguiBridge"] ~~~ B3["SessionManager"]
    end

    bridge -->|"AG-UI Events over SSE"| frontends

    subgraph frontends["Your Frontend"]
        F1["Reference UI"] ~~~ F2["CopilotKit"] ~~~ F3["AG-UI HttpAgent"] ~~~ F4["Anything"]
    end

    style agents fill:#1e3a5f,stroke:#60a5fa,color:#fff
    style bridge fill:#3b1f6e,stroke:#a78bfa,color:#fff
    style frontends fill:#1a4731,stroke:#6ee7b7,color:#fff
```

Clone this repo, change one line in `bridge.config.json` to point at your agent, and you have a working web UI with streaming chat, tool visualization, and human-in-the-loop approvals.

## Why AG-UI?

[AG-UI](https://docs.ag-ui.com) (Agent-User Interaction Protocol) is the open standard for connecting AI agents to frontends. Instead of rolling your own SSE/WebSocket protocol, you get:

- **~16 standard event types**: streaming chat, tool calls, state sync, generative UI, interrupts
- **Transport agnostic**: works over SSE, WebSockets, or webhooks
- **Rich ecosystem**: supported by CopilotKit, LangGraph, Google ADK, AWS Strands, Pydantic AI, and 20+ frameworks
- **Frontend SDKs**: TypeScript, Python, Kotlin, Go, Rust, and more
- **Human-in-the-loop built in**: pause, approve, reject, or redirect agent execution mid-flow

By emitting AG-UI events, your frontend becomes portable across the entire agent ecosystem. See [`docs/why-agui.md`](docs/why-agui.md) for a deep dive on what this unlocks: CopilotKit integration, shared state, generative UI, and more.

## Quick Start

```bash
git clone https://github.com/namanrajpal/acp-to-agui.git
cd acp-to-agui
pnpm install
# Edit bridge.config.json → set "agentCommand" to your agent
pnpm dev
```

Open **http://localhost:5173**. The bridge spawns your agent, translates its output to AG-UI events, and streams them to the React frontend.

## Configuration

All configuration lives in `bridge.config.json`:

```json
{
  "projectName": "acp-to-agui",
  "displayTitle": "ACP → AG-UI Bridge",
  "agentCommand": ["kiro-cli", "acp"],
  "backendPort": 8000,
  "corsOrigins": ["http://localhost:5173"]
}
```

Just change `agentCommand` to point at your agent:

| Agent | Command |
|-------|---------|
| Kiro CLI | `["kiro-cli", "acp"]` |
| Claude Agent | `["claude-agent-acp"]` |
| Codex CLI | `["codex", "--acp"]` |
| Gemini CLI | `["gemini", "cli", "acp"]` |
| Cursor | `["cursor", "--acp"]` |
| OpenCode | `["opencode", "acp"]` |
| GitHub Copilot | `["github-copilot-cli", "--acp"]` |
| Any ACP binary | `["your-agent", "acp"]` |

## Architecture

```mermaid
graph TB
    subgraph frontend["React Frontend (Vite)"]
        direction LR
        CP["ChatPanel"]
        AD["ApprovalDialog"]
        TC["ToolCard"]
        SS["SessionSidebar"]
    end

    frontend -->|"SSE stream + REST"| backend

    subgraph backend["Python Backend (FastAPI)"]
        SM["SessionManager"]
        BR["AcpToAguiBridge"]
        ST["SessionStore"]
        AR["AgentRunner"]
        PE["PolicyEngine"]
        API["REST APIs"]
        SM --> AR
        SM --> BR
        SM --> ST
        BR --> PE
    end

    AR <-->|"JSON-RPC 2.0 / stdio"| agent

    subgraph agent["ACP Agent (subprocess)"]
        AG["kiro-cli / claude-agent-acp / any"]
    end

    style frontend fill:#1a4731,stroke:#6ee7b7,color:#fff
    style backend fill:#3b1f6e,stroke:#a78bfa,color:#fff
    style agent fill:#1e3a5f,stroke:#60a5fa,color:#fff
```

## Protocol Translation

The core contribution: how ACP maps to AG-UI.

```mermaid
graph TB
    subgraph acp["ACP Notifications (input)"]
        direction TB
        N1["agent_message_chunk"]
        N2["tool_call"]
        N3["tool_call_update"]
        N4["turn_end"]
        N5["request_permission"]
        N6["_vendor.dev/*"]
    end

    N1 --> E1
    N2 --> E2
    N3 --> E3
    N4 --> E4
    N5 --> E5
    N6 --> E6

    subgraph agui["AG-UI Events (output)"]
        direction TB
        E1["TEXT_MESSAGE_START + CONTENT"]
        E2["TOOL_CALL_START + ARGS"]
        E3["TOOL_CALL_ARGS or TOOL_CALL_END"]
        E4["TEXT_MESSAGE_END + RUN_FINISHED"]
        E5["STATE_UPDATE (approval)"]
        E6["CUSTOM (agent:* namespace)"]
    end

    style acp fill:#1e3a5f,stroke:#60a5fa,color:#fff
    style agui fill:#1a4731,stroke:#6ee7b7,color:#fff
```

| ACP Event | AG-UI Event(s) | Notes |
|-----------|---------------|-------|
| `agent_message_chunk` | `TEXT_MESSAGE_START` + `TEXT_MESSAGE_CONTENT` | Opens message on first chunk |
| `tool_call` | `TOOL_CALL_START` + `TOOL_CALL_ARGS` | Closes open text message first |
| `tool_call_update` | `TOOL_CALL_ARGS` or `TOOL_CALL_END` | Based on status field |
| `turn_end` | `TEXT_MESSAGE_END` + `TOOL_CALL_END`(s) + `RUN_FINISHED` | Closes everything |
| `session/request_permission` | `STATE_UPDATE` (approval pending) | Uses asyncio.Future for async bridge |
| Vendor extensions (`_*.dev/*`) | `CUSTOM` events | Normalized to `agent:*` namespace |

## The Tricky Parts

ACP and AG-UI do not map one-to-one. These required a normalization layer:

**Tool Approvals:** ACP's SDK calls `request_permission()` and blocks waiting for a return value. But our approval comes asynchronously from a REST endpoint. We bridge this with `asyncio.Future`: the SDK callback awaits the future, the REST endpoint resolves it.

**Message Boundaries:** ACP streams `agent_message_chunk` continuously. AG-UI needs explicit `TEXT_MESSAGE_START` and `TEXT_MESSAGE_END` events. The bridge tracks open message state and auto-closes before tool calls or turn end.

**Vendor Extensions:** ACP agents send custom notifications (e.g., `_kiro.dev/mcp_servers_ready`). The SDK routes these to `ext_notification()`. We normalize them into `CUSTOM` AG-UI events with a clean `agent:*` namespace.

## Three Ways to Build Your Frontend

```mermaid
graph TB
    bridge["POST /ag-ui endpoint"]

    bridge --> path1
    bridge --> path2
    bridge --> path3

    path1["Reference UI\nFull React app, ~2000 lines, full control"]
    path2["CopilotKit\nCopilotChat component, ~20 lines, ship fast"]
    path3["AG-UI HttpAgent\n@ag-ui/client, ~50 lines, build anything"]

    style bridge fill:#3b1f6e,stroke:#a78bfa,color:#fff
    style path1 fill:#1e3a5f,stroke:#60a5fa,color:#fff
    style path2 fill:#1a4731,stroke:#6ee7b7,color:#fff
    style path3 fill:#4a2c17,stroke:#fbbf24,color:#fff
```

| Approach | Lines of Code | Use When |
|----------|--------------|----------|
| **Reference UI** (`reference-ui/`) | ~2000 | You want full control over every pixel |
| **CopilotKit** (`examples/copilotkit-demo/`) | ~20 | You want to ship fast with production features |
| **AG-UI HttpAgent** (`examples/httpagent-demo/`) | ~50 | You want the raw protocol with your own UI framework |

## How It Works

1. **Configure an agent** in `bridge.config.json`
2. **Create a session**: `POST /v2/tasks` spawns the agent subprocess, initializes ACP
3. **Start a run**: `POST /v2/tasks/{id}/run` sends your prompt via JSON-RPC
4. **Stream events**: `GET /v2/tasks/{id}/events?runId=...` returns AG-UI SSE stream
5. **Or use the standard endpoint**: `POST /ag-ui` (what CopilotKit and AG-UI HttpAgent use)
6. **Handle approvals**: `POST /v2/tasks/{id}/approval` resolves pending tool permissions

## Project Structure

```
├── backend/                    # Python FastAPI (the bridge)
│   ├── agent/                  # ACP SDK integration (spawn + protocol)
│   ├── bridge/                 # ACP → AG-UI event translation
│   ├── agui/                   # AG-UI event types + SSE encoding
│   ├── sessions/               # Session lifecycle, store, routes
│   ├── policy/                 # Tool approval engine
│   ├── api/                    # Side-channel REST (files, git)
│   └── agui_endpoint.py        # POST /ag-ui (AG-UI standard endpoint)
├── reference-ui/               # Full React frontend (Vite + Tailwind)
├── examples/
│   ├── copilotkit-demo/        # CopilotKit in 20 lines
│   ├── httpagent-demo/         # Raw @ag-ui/client AG-UI HttpAgent
│   └── agents.md              # Agent configuration guide
├── docs/
│   ├── architecture.md         # Detailed system design
│   ├── integration-contract.md # REST + SSE API spec
│   ├── protocol-translation.md # Full ACP ↔ AG-UI mapping
│   ├── why-agui.md            # AG-UI ecosystem benefits
│   ├── demo-walkthrough.md    # End-to-end test results
│   └── talk-qanda.md          # Anticipated Q&A
├── bridge.config.json          # Your agent configuration
└── package.json                # Workspace orchestrator
```

## For UI Builders

The backend exposes a **standard AG-UI endpoint** at `POST /ag-ui`. Any AG-UI client can connect:

```typescript
// CopilotKit
<CopilotKit runtimeUrl="http://localhost:8000/ag-ui">
  <CopilotChat />
</CopilotKit>

// AG-UI HttpAgent (@ag-ui/client)
const agent = new HttpAgent({ url: "http://localhost:8000/ag-ui" });
agent.run({ messages, threadId }).subscribe(event => ...);
```

Or use the granular REST API for more control:
- `POST /v2/tasks`: create session (spawn agent)
- `POST /v2/tasks/{id}/run`: start a run
- `GET /v2/tasks/{id}/events?runId=...`: SSE stream
- `POST /v2/tasks/{id}/approval`: resolve tool approval

See [`docs/integration-contract.md`](docs/integration-contract.md) for the full API spec.

## Tested With Real Agents

| Agent | Version | Status | Notes |
|-------|---------|--------|-------|
| **Kiro CLI** | 2.3.0 | ✅ Working | 13 modes, extension notifications, full streaming |
| **Claude Agent** (claude-agent-acp) | 0.36.1 | ✅ Working | 5 modes, prompt queueing, embedded context |

Both tested end-to-end with zero code changes between them. Just swap `agentCommand`. See [`docs/demo-walkthrough.md`](docs/demo-walkthrough.md) for full test results.

## Supported Agents (ACP Ecosystem)

ACP is supported by 33+ agents. Any of them can be used with this bridge:

Augment Code, AutoDev, Blackbox AI, Claude Code, Cline, Codex CLI, Cursor, Docker cagent, fast-agent, Factory Droid, Gemini CLI, GitHub Copilot, Goose, Hermes Agent, Junie (JetBrains), Kimi CLI, Kiro CLI, Mistral Vibe, OpenCode, OpenHands, Poolside, Qwen Code, and more.

Full list: [agentclientprotocol.com/get-started/agents](https://agentclientprotocol.com/get-started/agents)

## The Talk

This repository accompanies the talk:

**"I Built an ACP → AG-UI Adapter So Coding Agents Can Escape the Terminal"**

Presented at [Seattle AI Tinkerers](https://seattle.aitinkerers.org/), May 2025.

## Contributing

Contributions welcome! Areas of interest:

- Additional agent configuration examples
- Frontend components for new AG-UI event types
- Policy engine enhancements (configurable approval rules)
- Session resume/persistence improvements
- More AG-UI event types (STATE_DELTA, activities, reasoning)

## License

MIT

---

<sub>This project is independent and is not affiliated with, endorsed by, or sponsored by Amazon/Kiro, Anthropic/Claude, OpenAI/Codex, Google/Gemini, GitHub/Copilot, Cursor/Anysphere, OpenCode, Cline, or their respective owners. All product names, logos, and trademarks are property of their respective owners.</sub>

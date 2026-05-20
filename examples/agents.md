# Agent Configuration Examples

This guide shows how to use the ACP → AG-UI bridge with real agents. Each example includes installation, configuration, and verification steps.

---

## Kiro CLI

[Kiro CLI](https://kiro.dev) is Amazon's coding agent with full ACP support.

### Install

Download from [kiro.dev/downloads](https://kiro.dev/downloads/). After installation, verify:

```bash
which kiro-cli
# Typically: ~/.local/bin/kiro-cli
```

### Configure

```json
{
  "agentCommand": ["kiro-cli", "acp"]
}
```

Or with a specific agent configuration:

```json
{
  "agentCommand": ["kiro-cli", "acp", "--agent", "my-agent"]
}
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `KIRO_LOG_LEVEL` | Set to `debug` for verbose logging |
| `KIRO_CHAT_LOG_FILE` | Custom log file path |

### Session Data

Sessions are stored at `~/.kiro/sessions/cli/` with `.json` metadata and `.jsonl` event logs.

### Verify It Works

```bash
# Start the bridge
pnpm dev

# In another terminal, check the backend logs for:
# "Spawned agent ['kiro-cli', 'acp'] PID=..."
# "← notification: session/update"
```

### ACP Features Supported

- Modes: `default`, `browser-agent`, `architect`, `ask`, custom agents
- Slash commands: advertised via `_kiro.dev/commands/available`
- MCP servers: configured via `mcpServers` in task creation
- Tool approvals: full `session/request_permission` support
- Session resume: via `session/load`

---

## Claude Agent (via claude-agent-acp)

[claude-agent-acp](https://github.com/agentclientprotocol/claude-agent-acp) is an ACP adapter for the official Claude Agent SDK by Zed Industries. It wraps Anthropic's Claude Agent SDK to speak ACP over stdio.

### Prerequisites

- Node.js 18+
- An Anthropic API key (`ANTHROPIC_API_KEY`)

### Install

```bash
# Option 1: Install globally
npm install -g @agentclientprotocol/claude-agent-acp

# Option 2: Use npx (no install needed)
# The bridge will run: npx @agentclientprotocol/claude-agent-acp

# Option 3: Clone and build from source
git clone https://github.com/agentclientprotocol/claude-agent-acp.git
cd claude-agent-acp
npm install
npm run build
```

### Configure

If installed globally:

```json
{
  "agentCommand": ["claude-agent-acp"]
}
```

If using npx:

```json
{
  "agentCommand": ["npx", "@agentclientprotocol/claude-agent-acp"]
}
```

If built from source:

```json
{
  "agentCommand": ["node", "/path/to/claude-agent-acp/dist/index.js"]
}
```

### Environment Variables

Set your Anthropic API key before starting the bridge:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
pnpm dev
```

Or add it to a `.env` file in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
```

### ACP Features Supported

- Context @-mentions and images
- Tool calls with permission requests
- Edit review
- TODO lists
- Interactive and background terminals
- Custom slash commands
- Client MCP servers

### Verify It Works

```bash
# Set API key
export ANTHROPIC_API_KEY="sk-ant-..."

# Start the bridge
pnpm dev

# Open http://localhost:5173
# Create a task, send a message
# You should see Claude's streaming response in the chat panel
```

---

## Running the Demo (End-to-End)

Here's the complete flow to go from zero to a working web UI on top of an ACP agent:

### Step 1: Clone and install

```bash
git clone https://github.com/your-username/acp-to-agui.git
cd acp-to-agui
pnpm install
```

### Step 2: Choose your agent

Edit `bridge.config.json`:

```json
{
  "agentCommand": ["claude-agent-acp"]
}
```

### Step 3: Set credentials (if needed)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Step 4: Start

```bash
pnpm dev
```

This starts:
- Backend at `http://localhost:8000` (FastAPI, spawns your agent)
- Frontend at `http://localhost:5173` (React, connects via SSE)

### Step 5: Use it

1. Open `http://localhost:5173`
2. Select a project directory
3. The bridge spawns your agent subprocess and initializes ACP
4. Type a message — it flows through JSON-RPC to your agent
5. The agent's response streams back as AG-UI events
6. Tool calls appear with approval dialogs
7. You have a full web workspace powered by a CLI agent

---

## How This Project Helped

Without this bridge, connecting an ACP agent to a web UI would require:

1. **Subprocess management** — spawning, monitoring, killing the agent process
2. **JSON-RPC implementation** — bidirectional message parsing, request correlation, error handling
3. **Protocol translation** — converting ACP's notification model to frontend-friendly events
4. **State tracking** — open messages, open tool calls, pending approvals
5. **SSE streaming** — encoding events, keepalives, connection lifecycle
6. **Task persistence** — SQLite store for session metadata
7. **Approval flow** — holding RPC IDs, emitting state updates, responding to agent

This project handles all of that. You just:
- Set `agentCommand` to your agent binary
- Build your frontend consuming AG-UI events from the SSE stream
- Or use the reference UI as-is

**Time to first working UI: ~2 minutes** (clone, configure, `pnpm dev`).

---

## Other Agents

Any ACP-compatible agent works. Here are more examples:

| Agent | Install | Command |
|-------|---------|---------|
| Codex CLI | `npm i -g @openai/codex` | `["codex", "--acp"]` |
| Gemini CLI | [gemini.google.com/cli](https://gemini.google.com/cli) | `["gemini", "cli", "acp"]` |
| Cursor | Built into Cursor IDE | `["cursor", "--acp"]` |
| OpenCode | `go install github.com/sst/opencode@latest` | `["opencode", "acp"]` |
| Cline | [cline.bot](https://cline.bot) | `["cline", "--acp"]` |
| GitHub Copilot | Part of GitHub CLI | `["github-copilot-cli", "--acp"]` |
| Goose | [github.com/block/goose](https://github.com/block/goose) | `["goose", "--acp"]` |

See the full list of 33+ ACP agents at [agentclientprotocol.com/get-started/agents](https://agentclientprotocol.com/get-started/agents).

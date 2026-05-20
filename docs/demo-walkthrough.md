# Demo Walkthrough: Two Agents, One Bridge, Zero Code Changes

This document walks through the end-to-end test that proves the core thesis of this project: **any ACP-compatible agent can be given a rich web UI by changing a single config line**.

---

## What We Tested

We ran two completely different AI coding agents through the same bridge, with the same frontend, and observed identical AG-UI event streams — proving the protocol translation layer works generically.

| Agent | Creator | Binary | ACP Protocol |
|-------|---------|--------|--------------|
| **Kiro CLI** | Amazon | `kiro-cli acp` | Native ACP support |
| **Claude Agent** | Anthropic (via Zed adapter) | `claude-agent-acp` | Claude Agent SDK wrapped in ACP |

---

## The Setup

### Prerequisites

```bash
# Kiro CLI (already installed)
which kiro-cli  # → ~/.toolbox/bin/kiro-cli (v2.3.0)

# Claude Agent ACP (installed from npm)
npm install -g @agentclientprotocol/claude-agent-acp --registry https://registry.npmjs.org

# Anthropic API key (for claude-agent-acp)
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

### The Only Difference Between Agents

```json
// bridge.config.json for Kiro:
{ "agentCommand": ["kiro-cli", "acp"] }

// bridge.config.json for Claude:
{ "agentCommand": ["claude-agent-acp"] }
```

That's it. No code changes. No different endpoints. No different frontend. One line.

---

## Test 1: Kiro CLI

### Step 1: Start the bridge

```bash
PYTHONPATH=. python -m uvicorn backend.main:app --port 8111
```

Output:
```
Starting ACP → AG-UI Bridge v0.1.0
TaskStore initialized at /Users/namanraj/.acp-to-agui/tasks.db
Uvicorn running on http://127.0.0.1:8111
```

### Step 2: Create a task (spawns kiro-cli)

```bash
curl -X POST http://localhost:8111/v2/tasks \
  -H "Content-Type: application/json" \
  -d '{"cwd": "/path/to/project", "title": "Test Task"}'
```

Response:
```json
{
  "taskId": "8ef0c6aa-5c73-4a24-a1ac-47c39762cbc8",
  "agentSessionId": "40e11e97-4b24-4d39-b548-3957a4febe4f",
  "modes": [
    { "id": "kiro_default", "name": "kiro_default" },
    { "id": "kiro_planner", "name": "kiro_planner" },
    { "id": "kiro_guide", "name": "kiro_guide" }
  ],
  "currentModeId": "kiro_default"
}
```

**What happened under the hood:**
1. `TaskManager` called `AgentRunner.spawn()` → executed `kiro-cli acp` as a subprocess
2. `AcpProtocol.initialize()` sent the JSON-RPC `initialize` request over stdin
3. `AcpProtocol.session_new()` created a new ACP session
4. Kiro returned 13 available modes and the session ID
5. Bridge stored all this in `ActiveTask` and persisted metadata to SQLite

### Step 3: Send a prompt and stream events

```bash
# Start a run
curl -X POST http://localhost:8111/v2/tasks/{taskId}/run \
  -H "Content-Type: application/json" \
  -d '{"input": {"messages": [{"role": "user", "content": "Say hello in one sentence."}]}}'

# Stream AG-UI events
curl -N http://localhost:8111/v2/tasks/{taskId}/events?runId={runId}
```

AG-UI event stream received:
```
event: RUN_STARTED
data: {"type":"RUN_STARTED","runId":"8e5c9e05-...","taskId":"8ef0c6aa-..."}

event: CUSTOM
data: {"type":"CUSTOM","name":"agent:commands_available","value":{"commands":[
  {"name":"/agent","description":"Select or list available agents"},
  {"name":"/clear","description":"Clear conversation history"},
  {"name":"/compact","description":"Compact conversation history"},
  {"name":"/help","description":"Show available commands"},
  ...
]}}

event: TEXT_MESSAGE_START
data: {"type":"TEXT_MESSAGE_START","messageId":"...","role":"assistant"}

event: TEXT_MESSAGE_CONTENT
data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"...","delta":"Hello! I'm..."}

event: TEXT_MESSAGE_CONTENT
data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"...","delta":"here to help..."}

event: TEXT_MESSAGE_END
data: {"type":"TEXT_MESSAGE_END","messageId":"..."}

event: RUN_FINISHED
data: {"type":"RUN_FINISHED","runId":"8e5c9e05-..."}
```

**What happened under the hood:**
1. `TaskManager.start_run()` created an `asyncio.Queue` and called `AcpProtocol.session_prompt()`
2. Kiro processed the prompt and sent back `session/update` JSON-RPC notifications via stdout
3. `AcpToAguiBridge.handle_notification()` translated each ACP notification:
   - `_kiro.dev/commands/available` → `CUSTOM` event (agent:commands_available)
   - `agent_message_chunk` (first) → `TEXT_MESSAGE_START` + `TEXT_MESSAGE_CONTENT`
   - `agent_message_chunk` (subsequent) → `TEXT_MESSAGE_CONTENT`
   - `turn_end` → `TEXT_MESSAGE_END` + `RUN_FINISHED`
4. Events were put into the `asyncio.Queue`
5. The SSE endpoint pulled from the queue and streamed to the HTTP client

---

## Test 2: Claude Agent ACP

### Step 1: Change config (the only change)

```json
{ "agentCommand": ["claude-agent-acp"] }
```

### Step 2: Create a task (spawns claude-agent-acp)

```bash
curl -X POST http://localhost:8112/v2/tasks \
  -H "Content-Type: application/json" \
  -d '{"cwd": "/path/to/project", "title": "Claude Agent Test"}'
```

Response:
```json
{
  "taskId": "a305d00b-3ddb-4c86-935b-20e57b677029",
  "agentSessionId": "c99ff41c-772f-408a-b41e-7df66399fc65",
  "modes": [
    { "id": "default", "name": "Default" },
    { "id": "acceptEdits", "name": "Accept Edits" },
    { "id": "plan", "name": "Plan Mode" },
    { "id": "dontAsk", "name": "Don't Ask" },
    { "id": "bypassPermissions", "name": "Bypass Permissions" }
  ],
  "currentModeId": "default"
}
```

**Different agent, different modes, same API shape.**

### Step 3: Send prompt and stream events

```
event: RUN_STARTED
data: {"type":"RUN_STARTED","runId":"7abd434f-...","taskId":"a305d00b-..."}

event: TEXT_MESSAGE_START
data: {"type":"TEXT_MESSAGE_START","messageId":"b0366817-...","role":"assistant"}

event: TEXT_MESSAGE_CONTENT
data: {"type":"TEXT_MESSAGE_CONTENT","delta":"Hello! I'm ready"}

event: TEXT_MESSAGE_CONTENT
data: {"type":"TEXT_MESSAGE_CONTENT","delta":" to help you with your project"}

event: TEXT_MESSAGE_CONTENT
data: {"type":"TEXT_MESSAGE_CONTENT","delta":" —"}

event: TEXT_MESSAGE_CONTENT
data: {"type":"TEXT_MESSAGE_CONTENT","delta":" let"}

event: TEXT_MESSAGE_CONTENT
data: {"type":"TEXT_MESSAGE_CONTENT","delta":" me know what you'd like to work"}

event: TEXT_MESSAGE_CONTENT
data: {"type":"TEXT_MESSAGE_CONTENT","delta":" on."}

event: TEXT_MESSAGE_END
data: {"type":"TEXT_MESSAGE_END","messageId":"b0366817-..."}

event: RUN_FINISHED
data: {"type":"RUN_FINISHED","runId":"7abd434f-...","taskId":"a305d00b-..."}
```

---

## Side-by-Side Comparison

| Aspect | Kiro CLI | Claude Agent ACP |
|--------|----------|-----------------|
| **Binary** | `kiro-cli acp` | `claude-agent-acp` |
| **Underlying LLM** | Claude (via Amazon) | Claude (direct Anthropic SDK) |
| **Modes** | 13 (kiro_default, planner, guide, ...) | 5 (Default, Accept Edits, Plan, ...) |
| **Custom events** | Yes (`agent:commands_available`) | None in this test |
| **Event sequence** | RUN_STARTED → CUSTOM → TEXT_* → RUN_FINISHED | RUN_STARTED → TEXT_* → RUN_FINISHED |
| **Config change** | — | One line in `bridge.config.json` |
| **Code changes** | — | **Zero** |
| **Frontend changes** | — | **Zero** |

---

## Why This Matters

### 1. Protocol Abstraction Works

The bridge successfully hides the differences between agents. A frontend consuming this SSE stream doesn't need to know whether Kiro or Claude is behind it — the AG-UI events are structurally identical.

### 2. The "Escape the Terminal" Promise Delivers

Both agents normally run as terminal CLIs. Through this bridge, they produce clean SSE event streams that any web frontend can render as rich UI — chat bubbles, tool cards, approval dialogs, streaming text.

### 3. Agent Swapping is Trivial

Want to try a different agent? Change one JSON field. No rebuild, no code changes, no frontend modifications. The bridge handles the protocol translation for any ACP-compatible binary.

### 4. The AG-UI Contract Holds

Both agents produced events that match the AG-UI spec:
- `RUN_STARTED` (exactly once, at the beginning)
- `TEXT_MESSAGE_START` (exactly once per message)
- `TEXT_MESSAGE_CONTENT` (streaming deltas)
- `TEXT_MESSAGE_END` (exactly once, closing the message)
- `RUN_FINISHED` (exactly once, at the end)

A CopilotKit frontend, or any AG-UI-compatible client, could consume these events without modification.

### 5. Extension Events are Normalized

Kiro sends vendor-specific `_kiro.dev/*` notifications. The bridge translates these to generic `CUSTOM` events (namespaced as `agent:*`). The frontend can optionally render them (e.g., showing available slash commands) or ignore them. Claude doesn't send these — and that's fine. The bridge handles both cases.

---

## The Full Data Path (Traced)

```
User types "Say hello"
    │
    ▼
POST /v2/tasks/{id}/run
    │
    ▼
TaskManager.start_run()
    │ Creates asyncio.Queue
    │ Calls bridge.start_run(run_id, queue)
    │   → Emits RUN_STARTED into queue
    │ Spawns background task: _run_prompt()
    │
    ▼
AcpProtocol.session_prompt()
    │ Sends JSON-RPC request over stdin:
    │   {"jsonrpc":"2.0","id":2,"method":"session/prompt",
    │    "params":{"sessionId":"...","prompt":[{"type":"text","text":"Say hello"}]}}
    │
    ▼
Agent subprocess processes prompt (calls LLM, etc.)
    │ Sends notifications over stdout:
    │   {"jsonrpc":"2.0","method":"session/update",
    │    "params":{"update":{"sessionUpdate":"agent_message_chunk",
    │                        "content":{"type":"text","text":"Hello!"}}}}
    │
    ▼
AgentRunner._read_stdout() → _handle_line()
    │ Parses JSON, identifies as notification
    │ Calls: runner.on_notification("session/update", params)
    │
    ▼
AcpToAguiBridge.handle_notification("session/update", params)
    │ Dispatches to _handle_agent_message_chunk()
    │ First chunk? → emit TEXT_MESSAGE_START
    │ Always → emit TEXT_MESSAGE_CONTENT(delta="Hello!")
    │
    ▼
bridge._emit(event)
    │ queue.put_nowait(event)
    │
    ▼
GET /v2/tasks/{id}/events?runId=... (SSE endpoint)
    │ event_stream(queue) async generator
    │ Awaits queue.get()
    │ Encodes as: "event: TEXT_MESSAGE_CONTENT\ndata: {...}\n\n"
    │
    ▼
Browser receives SSE event
    │ useAgUiStream hook parses it
    │ Dispatches to Zustand store
    │ ChatPanel re-renders with new text
```

---

## Reproducing This Test

```bash
cd open-source

# Test with Kiro CLI (default config)
PYTHONPATH=. uvicorn backend.main:app --port 8000 &

curl -X POST http://localhost:8000/v2/tasks \
  -H "Content-Type: application/json" \
  -d '{"cwd": "'$(pwd)'", "title": "Kiro Test"}'
# Copy taskId from response

curl -X POST http://localhost:8000/v2/tasks/{TASK_ID}/run \
  -H "Content-Type: application/json" \
  -d '{"input":{"messages":[{"role":"user","content":"Say hello"}]}}'
# Copy runId from response

curl -N http://localhost:8000/v2/tasks/{TASK_ID}/events?runId={RUN_ID}
# Watch AG-UI events stream in

# Test with Claude Agent (change config, restart)
# Edit bridge.config.json: "agentCommand": ["claude-agent-acp"]
# Repeat the same curl commands — same API, same events, different agent
```

---

## Talk Significance

For the Seattle AI Tinkerers talk, this demo proves:

1. **The protocol bridge concept works in practice** — not just theory
2. **Multiple real agents** work through it — not just one hardcoded integration
3. **The AG-UI output is clean and standard** — any frontend framework could consume it
4. **Switching agents is a config change** — the dream of agent-agnostic UIs is achievable
5. **33+ agents** could plug into this same bridge today — the ACP ecosystem is real

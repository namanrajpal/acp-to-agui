# Talk Q&A: Anticipated Questions

Questions you might get asked during or after the talk, with prepared answers.

---

## "Why can't we just use the ACP SDK directly? Why do we need this bridge?"

The ACP SDK (`agent-client-protocol` on PyPI / `@agentclientprotocol/sdk` on npm) gives you **protocol primitives** — spawn a process, send JSON-RPC messages, receive notifications. It's like having a TCP library. You still need to build everything on top:

**What the ACP SDK gives you:**
- Spawn subprocess, establish JSON-RPC connection
- Send requests (initialize, session/new, prompt)
- Receive callbacks (session_update, request_permission)

**What the ACP SDK does NOT give you:**
- An HTTP API for frontends to connect to
- SSE streaming for real-time UI updates
- Event normalization (ACP chunks → proper start/content/end boundaries)
- Task/session lifecycle management
- Approval flow orchestration (holding state between agent request and user response)
- Persistence (which sessions exist, their status)
- A standard event protocol that frontend tools understand

**What this bridge adds on top of the SDK:**
- Translates ACP callbacks → AG-UI events (a standard that CopilotKit, 9+ SDKs, and 20+ frameworks understand)
- Serves those events over SSE (any HTTP client can consume)
- Manages sessions, queues, approvals, persistence
- Provides REST endpoints for frontends

**The punchline:** The ACP SDK lets you *talk to* an agent. This bridge lets you *build a web app* on top of one — and because it emits AG-UI events, you get access to the entire AG-UI frontend ecosystem for free.

---

## "Why AG-UI instead of just custom SSE events?"

Three reasons:

**1. Ecosystem access.** AG-UI is supported by CopilotKit (React components), LangGraph, CrewAI, Google ADK, AWS Strands, Pydantic AI, and 20+ more frameworks. Custom SSE events are understood by nobody else.

**2. Middleware.** AG-UI has a built-in middleware layer (`agent.use(...)`) that lets you add logging, filtering, authentication, rate limiting, and event transformation without modifying core logic. You'd have to build all that yourself with custom events.

**3. Frontend portability.** If you emit AG-UI events, any AG-UI-compatible client can consume them — CopilotKit for React, native mobile via community SDKs, terminal clients, Slack bots. Switch frontends without changing your backend.

Custom SSE is fine for a prototype. AG-UI is for when you want to participate in an ecosystem.

---

## "Can I use CopilotKit with this?"

Yes. CopilotKit's `HttpAgent` can point at our bridge's SSE endpoint. The events we emit (`TEXT_MESSAGE_START/CONTENT/END`, `TOOL_CALL_*`, `STATE_UPDATE`, etc.) are standard AG-UI events that CopilotKit already knows how to render.

```tsx
import { CopilotKit } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";

// Point at the bridge
<CopilotKit runtimeUrl="http://localhost:8000">
  <CopilotChat />
</CopilotKit>
```

You get streaming chat, tool visualization, shared state, and approval dialogs — without writing any event handling code. Your ACP agent just works through CopilotKit's UI.

---

## "What happens with agent-specific features? Like Kiro's modes or slash commands?"

ACP has a standard extension mechanism. Agents can send vendor-specific notifications (e.g., `_kiro.dev/commands/available`). The official SDK routes these to the `Client.ext_notification()` callback.

Our bridge translates them to AG-UI `CUSTOM` events with a normalized namespace:

| Agent sends | Bridge emits |
|-------------|-------------|
| `_kiro.dev/commands/available` | `CUSTOM { name: "agent:commands_available", value: {...} }` |
| `_kiro.dev/metadata` | `CUSTOM { name: "agent:kiro.dev_metadata", value: {...} }` |

Frontends can optionally render these (show a command palette, display token usage) or ignore them. The base protocol (messages, tool calls, approvals) works identically across all agents — extensions are additive.

---

## "How is this different from just building a wrapper around `subprocess.Popen`?"

Scale of concern:

| Layer | `subprocess.Popen` | This bridge |
|-------|-------------------|-------------|
| Process management | You handle it | AgentRunner (via SDK) with process tree cleanup |
| Protocol | Raw bytes on stdio | JSON-RPC 2.0 with correlation, timeouts, error handling |
| Session lifecycle | Nothing | Create, resume, cancel, stop, destroy, revive |
| Event normalization | Nothing | ACP chunks → AG-UI start/content/end boundaries |
| Streaming to frontend | Nothing | SSE with keepalives, backpressure, clean termination |
| Approval flow | Nothing | Async future bridging SDK callback ↔ REST endpoint |
| State tracking | Nothing | Open messages, open tool calls, pending permissions |
| Persistence | Nothing | SQLite session store |
| Frontend protocol | Nothing | AG-UI standard (ecosystem-compatible) |

You *could* build all of this from a subprocess. That's what this project is — so you don't have to.

---

## "Does this work with agents that aren't coding agents?"

Yes. ACP is used primarily by coding agents today (Kiro, Claude Code, Codex, Cursor, etc.), but the protocol itself is general-purpose. Any agent that speaks JSON-RPC 2.0 over stdio with the ACP message set works.

If you have a custom agent (say, a research assistant or a data analysis agent) and you implement the ACP `initialize` → `session/new` → `session/prompt` flow, this bridge will give it a web UI.

---

## "What about authentication and multi-user?"

The current implementation is single-user (one bridge instance per user). For production multi-user deployment:

- **Agent credentials:** Set per-agent API keys in `.env` (passed to subprocess via environment)
- **User auth:** Add an auth middleware to FastAPI (JWT, OAuth, etc.)
- **Session isolation:** Each task already gets its own subprocess — multi-tenant isolation is per-session
- **Scaling:** Run multiple bridge instances behind a load balancer, sticky sessions by task ID

This is a reference implementation / prototype. Production hardening is left as an exercise — but the architecture supports it.

---

## "Why Python for the backend? Why not Node.js?"

Three reasons:

1. **The ACP Python SDK exists.** We use the official `agent-client-protocol` package directly.
2. **FastAPI's async model.** `asyncio` makes it natural to manage subprocess I/O, SSE streaming, and concurrent sessions.
3. **Separation of concerns.** The frontend is React/TypeScript. Having the backend in a different language forces a clean API boundary — no temptation to share code or blur the layers.

That said, there's also an ACP TypeScript SDK. A Node.js version of this bridge would be equally valid.

---

## "How many concurrent agents can this handle?"

Each agent is a subprocess. Practical limits:

- **Memory:** Each kiro-cli or claude-agent-acp process uses ~100-200MB
- **CPU:** Mostly idle (waiting for LLM responses), spikes during tool execution
- **File descriptors:** 3 per subprocess (stdin, stdout, stderr)

On a typical dev machine, 10-20 concurrent sessions is comfortable. For production with many users, you'd want process pooling and idle timeout (stop agents that haven't been used in N minutes, revive on next request).

---

## "What's the latency overhead of the bridge?"

Minimal. The bridge adds:
- ~1ms for ACP notification → AG-UI event translation (Python dict operations)
- ~0ms for queue put (asyncio.Queue, in-process)
- SSE encoding: string formatting, negligible

The dominant latency is the agent itself (LLM inference, tool execution). The bridge is not on the critical path — it's just reformatting events as they flow through.

---

## "Can the frontend send messages back to the agent mid-stream?"

Not mid-stream (ACP's `session/prompt` blocks until the turn completes). But you can:

1. **Cancel:** `POST /v2/tasks/{id}/cancel` sends `session/cancel` to interrupt
2. **Approve/Reject:** `POST /v2/tasks/{id}/approval` resolves tool permissions
3. **New prompt:** After the run finishes, send another `POST /run`

AG-UI supports "interrupts" (agent pauses for input), which maps cleanly to ACP's `session/request_permission`. The bridge handles this: agent requests permission → SSE event to frontend → user responds → bridge resolves the SDK callback.

---

## "Is this production-ready?"

It's a **working prototype / reference implementation**. What's solid:

- Protocol translation (tested with 2 real agents)
- Session lifecycle (create, run, stream, approve, cancel, stop)
- Extension handling (vendor notifications don't break anything)
- Error handling (process crashes → clean RUN_ERROR events)

What you'd add for production:
- Rate limiting, auth middleware
- Health monitoring, metrics
- Process pool with idle timeout
- Session resume after server restart
- Log aggregation
- Load testing under concurrent sessions

---

## "What was the hardest part to build?"

The **approval flow bridge**. ACP uses a synchronous request/response pattern — the SDK calls `request_permission()` and blocks waiting for a return value. But our approval comes asynchronously from a REST endpoint (user clicks a button). We bridge this with `asyncio.Future`:

```python
# SDK calls this (blocks):
async def request_permission(self, options, session_id, tool_call):
    future = asyncio.get_event_loop().create_future()
    self._pending_approvals[call_id] = future
    # Emit STATE_UPDATE to frontend...
    return await future  # Blocks until REST endpoint resolves it

# REST endpoint resolves it:
async def approve(call_id, approved):
    future = self._pending_approvals.pop(call_id)
    future.set_result(response)  # Unblocks the SDK callback
```

This pattern — bridging a blocking protocol callback with an async HTTP endpoint — is the core engineering challenge of this project.

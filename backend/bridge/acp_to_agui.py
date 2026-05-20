"""AcpToAguiBridge — implements the acp.Client protocol to translate SDK callbacks into AG-UI events.

This is the core of the bridge architecture. It maintains per-run state
(open message, open tool calls) and emits properly sequenced AG-UI events
into an asyncio.Queue that the SSE endpoint drains.

The bridge satisfies the acp.Client Protocol structurally:
    - session_update(session_id, update) — handles streaming updates
    - request_permission(options, session_id, tool_call) — handles tool approval
    - ext_notification(method, params) — handles _kiro.dev/* extensions
    - ext_method(method, params) — handles vendor extension methods
    - read_text_file, write_text_file — file operations for the agent
    - create_terminal, terminal_output, etc. — terminal operations
    - on_connect(conn) — called when the connection is established
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any

import acp
import acp.schema

from backend.agui.events import (
    AguiEventType,
    BaseAguiEvent,
    CustomEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateUpdateEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from backend.policy.tool_policy import ToolPolicyEngine

logger = logging.getLogger(__name__)


class AcpToAguiBridge:
    """Stateful translator from ACP SDK callbacks to AG-UI events.

    Satisfies the acp.Client Protocol so the SDK routes session_update,
    request_permission, ext_notification, and file/terminal callbacks here.

    One bridge instance is created per task and reused across runs.
    Call ``start_run()`` to reset per-run state and connect to a new queue.
    """

    def __init__(
        self,
        task_id: str,
        policy_engine: ToolPolicyEngine,
    ) -> None:
        self.task_id = task_id
        self._policy = policy_engine
        self._log = logging.LoggerAdapter(logger, {"task_id": task_id})

        # Per-run state — reset on start_run()
        self._run_id: str | None = None
        self._queue: asyncio.Queue | None = None
        self._current_message_id: str | None = None
        self._has_open_message: bool = False
        self._open_tool_calls: set[str] = set()

        # Session-level notification buffer — holds _kiro.dev/* notifications
        # that arrive before any run starts (e.g. during session init).
        # Flushed as CUSTOM events when the first run begins.
        self._pending_notifications: list[tuple[str, dict[str, Any]]] = []

        # Permission futures — maps call_id to asyncio.Future that
        # request_permission awaits. Resolved by the REST approval endpoint.
        self._permission_futures: dict[str, asyncio.Future[acp.RequestPermissionResponse]] = {}

        # Working directory for file operations (set by session manager)
        self._cwd: str = ""

    # ── Run lifecycle ────────────────────────────────────────────────────────

    def start_run(self, run_id: str, queue: asyncio.Queue) -> None:
        """Begin a new run — reset state and emit RUN_STARTED."""
        self._run_id = run_id
        self._queue = queue
        self._current_message_id = None
        self._has_open_message = False
        self._open_tool_calls.clear()

        self._emit(
            RunStartedEvent(
                runId=run_id,
                taskId=self.task_id,
                threadId=self.task_id,
            )
        )

        # Flush any buffered session-level notifications as CUSTOM events
        if self._pending_notifications:
            self._log.debug(
                "Flushing %d buffered notifications into run %s",
                len(self._pending_notifications),
                run_id,
            )
            for method, params in self._pending_notifications:
                self._handle_agent_extension(method, params)
            self._pending_notifications.clear()

    def finish_run(self) -> None:
        """Explicitly finish the current run (e.g. on turn_end)."""
        self._close_open_message()
        self._close_all_tool_calls()
        if self._run_id:
            self._emit(RunFinishedEvent(runId=self._run_id, taskId=self.task_id))
        self._run_id = None

    def error_run(self, message: str, code: str | None = None) -> None:
        """Emit RUN_ERROR and close the run."""
        self._close_open_message()
        self._close_all_tool_calls()
        if self._run_id:
            self._emit(
                RunErrorEvent(
                    runId=self._run_id,
                    taskId=self.task_id,
                    message=message,
                    code=code,
                )
            )
        self._run_id = None

    # ── acp.Client Protocol — Core callbacks ─────────────────────────────────

    def on_connect(self, conn: Any) -> None:
        """Called when the connection is established."""
        self._log.info("ACP connection established")

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        """Handle streaming updates from the SDK.

        The `update` is a typed object (AgentMessageChunk, ToolCallStart,
        ToolCallProgress, AvailableCommandsUpdate, CurrentModeUpdate, etc.)
        """
        if self._queue is None:
            self._log.warning("session_update received but no active run (session=%s)", session_id)
            return

        # If the update is a dict (fallback), handle it the old way
        if isinstance(update, dict):
            self._handle_session_update_dict(update)
            return

        # Handle typed SDK objects
        if isinstance(update, acp.schema.AgentMessageChunk):
            self._handle_agent_message_chunk_typed(update)
        elif isinstance(update, acp.schema.ToolCallStart):
            self._handle_tool_call_typed(update)
        elif isinstance(update, acp.schema.ToolCallProgress):
            self._handle_tool_call_update_typed(update)
        elif isinstance(update, acp.schema.CurrentModeUpdate):
            mode_id = getattr(update, "mode_id", "") or getattr(update, "modeId", "")
            self._emit(
                CustomEvent(
                    name="agent:mode_update",
                    value={"modeId": mode_id},
                )
            )
        elif isinstance(update, acp.schema.AvailableCommandsUpdate):
            commands = getattr(update, "commands", [])
            self._emit(
                CustomEvent(
                    name="agent:commands_available",
                    value={"commands": commands},
                )
            )
        else:
            # Fallback: try to extract as dict
            if hasattr(update, "model_dump"):
                self._handle_session_update_dict(update.model_dump(by_alias=True))
            elif hasattr(update, "__dict__"):
                self._handle_session_update_dict(vars(update))
            else:
                self._log.debug("Unhandled session_update type: %s", type(update).__name__)

    async def request_permission(
        self, options: Any, session_id: str, tool_call: Any, **kwargs: Any
    ) -> acp.RequestPermissionResponse:
        """Handle tool approval requests from the SDK.

        This is called by the SDK and expects a return value. We create an
        asyncio.Future that the REST approval endpoint will resolve, then
        await it.
        """
        # Extract info from tool_call
        tool_call_id = str(
            getattr(tool_call, "tool_call_id", None)
            or getattr(tool_call, "toolCallId", None)
            or (tool_call.get("toolCallId") if isinstance(tool_call, dict) else None)
            or str(uuid.uuid4())
        )
        tool_name = (
            getattr(tool_call, "title", None)
            or getattr(tool_call, "tool_name", None)
            or getattr(tool_call, "toolName", None)
            or (tool_call.get("title", tool_call.get("toolName", "unknown")) if isinstance(tool_call, dict) else "unknown")
        )

        # Extract options list
        if isinstance(options, list):
            options_list = options
        elif hasattr(options, "__iter__"):
            options_list = list(options)
        else:
            options_list = []

        # Serialize options for the frontend
        serialized_options = []
        for opt in options_list:
            if isinstance(opt, dict):
                serialized_options.append(opt)
            elif hasattr(opt, "model_dump"):
                serialized_options.append(opt.model_dump(by_alias=True))
            elif hasattr(opt, "__dict__"):
                serialized_options.append(vars(opt))
            else:
                serialized_options.append(str(opt))

        # Emit STATE_UPDATE with pending approval info
        self._emit(
            StateUpdateEvent(
                state={
                    "approval": {
                        "pending": True,
                        "callId": tool_call_id,
                        "toolName": tool_name,
                        "summary": f"Permission required: {tool_name}",
                        "options": serialized_options,
                    }
                }
            )
        )

        # Create a future that the REST endpoint will resolve
        loop = asyncio.get_event_loop()
        future: asyncio.Future[acp.RequestPermissionResponse] = loop.create_future()
        self._permission_futures[tool_call_id] = future

        # Await the future — this blocks until the REST endpoint resolves it
        response = await future
        return response

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        """Handle vendor extension notifications like _kiro.dev/*.

        These become CUSTOM AG-UI events.
        """
        if self._queue is None:
            # No active run — buffer for later
            if method.startswith("_kiro.dev/") or method == "_session/terminate":
                self._log.debug("Buffering ext_notification (no active run): %s", method)
                self._pending_notifications.append((method, params))
            return

        self._handle_agent_extension(method, params)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Handle vendor extension method calls.

        Return empty dict for unhandled methods.
        """
        self._log.debug("ext_method called: %s", method)
        return {}

    # ── acp.Client Protocol — File operations ────────────────────────────────

    async def read_text_file(
        self, path: str, session_id: str, limit: int | None = None, line: int | None = None, **kwargs: Any
    ) -> acp.ReadTextFileResponse:
        """Read a text file on behalf of the agent."""
        self._log.debug("read_text_file: %s", path)
        try:
            full_path = os.path.join(self._cwd, path) if not os.path.isabs(path) else path
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                if line is not None:
                    lines = f.readlines()
                    start = max(0, line - 1)
                    end = start + (limit or len(lines))
                    content = "".join(lines[start:end])
                elif limit is not None:
                    content = f.read(limit)
                else:
                    content = f.read()
            return acp.ReadTextFileResponse(content=content)
        except Exception as exc:
            self._log.error("read_text_file failed: %s", exc)
            return acp.ReadTextFileResponse(content=f"Error reading file: {exc}")

    async def write_text_file(
        self, content: str, path: str, session_id: str, **kwargs: Any
    ) -> acp.WriteTextFileResponse | None:
        """Write a text file on behalf of the agent."""
        self._log.debug("write_text_file: %s", path)
        try:
            full_path = os.path.join(self._cwd, path) if not os.path.isabs(path) else path
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return acp.WriteTextFileResponse()
        except Exception as exc:
            self._log.error("write_text_file failed: %s", exc)
            return None

    # ── acp.Client Protocol — Terminal operations ────────────────────────────

    async def create_terminal(
        self,
        command: str,
        session_id: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: Any = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> acp.CreateTerminalResponse:
        """Create a terminal process for the agent.

        For now, we generate a terminal ID. Full terminal management is
        handled by the agent itself in most cases.
        """
        terminal_id = str(uuid.uuid4())
        self._log.info("create_terminal: %s %s (id=%s)", command, args or [], terminal_id)
        return acp.CreateTerminalResponse(terminalId=terminal_id)

    async def terminal_output(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> acp.TerminalOutputResponse:
        """Get terminal output."""
        return acp.TerminalOutputResponse(output="", truncated=False)

    async def release_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> acp.ReleaseTerminalResponse | None:
        """Release a terminal."""
        return acp.ReleaseTerminalResponse()

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> acp.WaitForTerminalExitResponse:
        """Wait for a terminal to exit."""
        return acp.WaitForTerminalExitResponse(exitCode=0)

    async def kill_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> acp.KillTerminalResponse | None:
        """Kill a terminal."""
        return acp.KillTerminalResponse()

    # ── Permission resolution (called by SessionManager via REST) ────────────

    def resolve_permission(self, call_id: str, approved: bool, option_id: str | None = None) -> None:
        """Resolve a pending permission future.

        Called by the SessionManager when the REST approval endpoint is hit.
        """
        future = self._permission_futures.pop(call_id, None)
        if future is None:
            self._log.warning("No pending permission future for call_id=%s", call_id)
            return

        if approved:
            outcome = {"outcome": "selected", "optionId": option_id or "allow_once"}
        else:
            outcome = {"outcome": "cancelled"}

        response = acp.RequestPermissionResponse(outcome=outcome)

        if not future.done():
            future.set_result(response)

        # Emit STATE_UPDATE clearing the pending approval
        self._emit(
            StateUpdateEvent(
                state={
                    "approval": {
                        "pending": False,
                        "callId": call_id,
                        "approved": approved,
                    }
                }
            )
        )

    # ── Fallback dict-based session/update handling ──────────────────────────

    def _handle_session_update_dict(self, update: dict[str, Any]) -> None:
        """Handle session/update when received as a raw dict (fallback)."""
        kind = update.get("sessionUpdate") or update.get("session_update")
        if kind == "agent_message_chunk":
            self._handle_agent_message_chunk_dict(update)
        elif kind == "tool_call":
            self._handle_tool_call_dict(update)
        elif kind == "tool_call_update":
            self._handle_tool_call_update_dict(update)
        elif kind == "turn_end":
            self._handle_turn_end()
        elif kind == "current_mode_update":
            self._emit(
                CustomEvent(
                    name="agent:mode_update",
                    value={"modeId": update.get("modeId", update.get("mode_id", ""))},
                )
            )
        else:
            self._log.debug("Unhandled session/update kind: %s", kind)

    # ── Typed SDK update handlers ────────────────────────────────────────────

    def _handle_agent_message_chunk_typed(self, update: acp.schema.AgentMessageChunk) -> None:
        """Handle AgentMessageChunk from the SDK."""
        content = getattr(update, "content", None)
        text = ""
        if content:
            text = getattr(content, "text", "") or ""
        if not text:
            return

        if not self._has_open_message:
            msg_id = str(uuid.uuid4())
            self._current_message_id = msg_id
            self._has_open_message = True
            self._emit(TextMessageStartEvent(messageId=msg_id))

        self._emit(
            TextMessageContentEvent(
                messageId=self._current_message_id,  # type: ignore[arg-type]
                delta=text,
            )
        )

    def _handle_tool_call_typed(self, update: acp.schema.ToolCallStart) -> None:
        """Handle ToolCallStart from the SDK."""
        self._close_open_message()

        tool_call_id = str(getattr(update, "tool_call_id", None) or getattr(update, "toolCallId", str(uuid.uuid4())))
        tool_name = getattr(update, "title", None) or getattr(update, "tool_name", "unknown")
        raw_input = getattr(update, "raw_input", None) or getattr(update, "rawInput", {})
        requires_approval = getattr(update, "requires_approval", False) or getattr(update, "requiresApproval", False)

        if isinstance(raw_input, dict):
            raw_input.pop("__tool_use_purpose", None)

        self._emit(
            ToolCallStartEvent(
                toolCallId=tool_call_id,
                toolCallName=tool_name,
                parentMessageId=self._current_message_id,
            )
        )
        self._open_tool_calls.add(tool_call_id)

        args_json = json.dumps(raw_input) if isinstance(raw_input, dict) else str(raw_input)
        self._emit(
            ToolCallArgsEvent(
                toolCallId=tool_call_id,
                delta=args_json,
            )
        )

        # Check policy
        input_dict = raw_input if isinstance(raw_input, dict) else {}
        decision = self._policy.evaluate(
            tool_name, input_dict, kiro_requires=bool(requires_approval)
        )

        if decision.requires_approval:
            permission_options = getattr(update, "permission_options", []) or getattr(update, "permissionOptions", [])
            self._emit(
                StateUpdateEvent(
                    state={
                        "approval": {
                            "pending": True,
                            "callId": tool_call_id,
                            "toolName": tool_name,
                            "summary": f"Tool call: {tool_name}",
                            "options": permission_options,
                            "category": decision.category,
                        }
                    }
                )
            )

    def _handle_tool_call_update_typed(self, update: acp.schema.ToolCallProgress) -> None:
        """Handle ToolCallProgress from the SDK."""
        tool_call_id = str(getattr(update, "tool_call_id", None) or getattr(update, "toolCallId", ""))
        status = getattr(update, "status", "")
        result = getattr(update, "result", None)

        if status in ("completed", "failed"):
            if tool_call_id in self._open_tool_calls:
                self._emit(
                    ToolCallEndEvent(
                        toolCallId=tool_call_id,
                        result=str(result) if result is not None else None,
                    )
                )
                self._open_tool_calls.discard(tool_call_id)
        elif result is not None:
            self._emit(
                ToolCallArgsEvent(
                    toolCallId=tool_call_id,
                    delta=json.dumps({"_progress": result}),
                )
            )

    # ── Dict-based handlers (fallback for raw dict updates) ──────────────────

    def _handle_agent_message_chunk_dict(self, update: dict[str, Any]) -> None:
        """Translate agent_message_chunk dict to TEXT_MESSAGE_START/CONTENT."""
        content = update.get("content", {})
        text = content.get("text", "")
        if not text:
            return

        if not self._has_open_message:
            msg_id = str(uuid.uuid4())
            self._current_message_id = msg_id
            self._has_open_message = True
            self._emit(TextMessageStartEvent(messageId=msg_id))

        self._emit(
            TextMessageContentEvent(
                messageId=self._current_message_id,  # type: ignore[arg-type]
                delta=text,
            )
        )

    def _handle_tool_call_dict(self, update: dict[str, Any]) -> None:
        """Translate tool_call dict to TOOL_CALL_START + TOOL_CALL_ARGS."""
        self._close_open_message()

        tool_call_id = update.get("toolCallId", str(uuid.uuid4()))
        tool_name = update.get("title", update.get("toolName", "unknown"))
        raw_input = update.get("rawInput", {})
        requires_approval = update.get("requiresApproval") or update.get("requires_approval", False)

        raw_input.pop("__tool_use_purpose", None)

        self._emit(
            ToolCallStartEvent(
                toolCallId=tool_call_id,
                toolCallName=tool_name,
                parentMessageId=self._current_message_id,
            )
        )
        self._open_tool_calls.add(tool_call_id)

        args_json = json.dumps(raw_input)
        self._emit(
            ToolCallArgsEvent(
                toolCallId=tool_call_id,
                delta=args_json,
            )
        )

        decision = self._policy.evaluate(
            tool_name, raw_input, kiro_requires=bool(requires_approval)
        )

        if decision.requires_approval:
            permission_options = update.get("permissionOptions", [])
            self._emit(
                StateUpdateEvent(
                    state={
                        "approval": {
                            "pending": True,
                            "callId": tool_call_id,
                            "toolName": tool_name,
                            "summary": f"Tool call: {tool_name}",
                            "options": permission_options,
                            "category": decision.category,
                        }
                    }
                )
            )

    def _handle_tool_call_update_dict(self, update: dict[str, Any]) -> None:
        """Translate tool_call_update dict to TOOL_CALL_ARGS or TOOL_CALL_END."""
        tool_call_id = update.get("toolCallId", "")
        status = update.get("status", "")
        result = update.get("result")

        if status in ("completed", "failed"):
            if tool_call_id in self._open_tool_calls:
                self._emit(
                    ToolCallEndEvent(
                        toolCallId=tool_call_id,
                        result=str(result) if result is not None else None,
                    )
                )
                self._open_tool_calls.discard(tool_call_id)
        elif result is not None:
            self._emit(
                ToolCallArgsEvent(
                    toolCallId=tool_call_id,
                    delta=json.dumps({"_progress": result}),
                )
            )

    # ── Turn end ─────────────────────────────────────────────────────────────

    def _handle_turn_end(self) -> None:
        """Translate turn_end to close open message/tools + RUN_FINISHED."""
        self.finish_run()

    # ── Agent extension notifications to CUSTOM ──────────────────────────────

    def _handle_agent_extension(self, method: str, params: dict[str, Any]) -> None:
        """Map _kiro.dev/* and _session/* notifications to CUSTOM events."""
        name_map = {
            "_kiro.dev/metadata": "agent:metadata",
            "_kiro.dev/mcp/server_initialized": "agent:mcp_initialized",
            "_kiro.dev/mcp/oauth_request": "agent:mcp_oauth",
            "_kiro.dev/compaction/status": "agent:compaction",
            "_kiro.dev/clear/status": "agent:clear",
            "_kiro.dev/commands/available": "agent:commands_available",
            "_session/terminate": "agent:subagent_terminated",
        }
        event_name = name_map.get(method, f"agent:{method.replace('_kiro.dev/', '').replace('/', '_')}")

        self._emit(CustomEvent(name=event_name, value=params))

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _close_open_message(self) -> None:
        """Close the current text message if one is open."""
        if self._has_open_message and self._current_message_id:
            self._emit(TextMessageEndEvent(messageId=self._current_message_id))
            self._has_open_message = False

    def _close_all_tool_calls(self) -> None:
        """Close all open tool calls."""
        for tc_id in list(self._open_tool_calls):
            self._emit(ToolCallEndEvent(toolCallId=tc_id))
        self._open_tool_calls.clear()

    def _emit(self, event: BaseAguiEvent) -> None:
        """Put an event into the asyncio queue (non-blocking)."""
        if self._queue is None:
            self._log.warning("Cannot emit — no queue: %s", event.type)
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._log.error("Event queue full, dropping: %s", event.type)

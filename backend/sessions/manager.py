"""SessionManager — orchestrates session lifecycle using the official ACP SDK.

Manages active sessions: spawns agent via acp.spawn_agent_process, initialises
the connection, holds per-session state, and coordinates run/approval flows.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import acp

from backend.bridge.acp_to_agui import AcpToAguiBridge
from backend.agent.acp_protocol import AcpProtocol
from backend.agent.runner import AgentRunner
from backend.policy.tool_policy import ToolPolicyEngine
from backend.sessions.store import SessionStore

logger = logging.getLogger(__name__)


@dataclass
class ActiveSession:
    task_id: str
    agent_session_id: str
    cwd: str
    runner: AgentRunner
    protocol: AcpProtocol
    bridge: AcpToAguiBridge
    event_queues: dict[str, asyncio.Queue] = field(default_factory=dict)
    current_run_id: str | None = None
    modes: list[dict[str, str]] | None = None
    models: list[dict[str, str]] | None = None
    current_mode_id: str | None = None


class SessionManager:
    def __init__(self, store: SessionStore, agent_command: list[str] | None = None) -> None:
        self._store = store
        self._sessions: dict[str, ActiveSession] = {}
        self._agent_command = agent_command or ["kiro-cli", "acp"]

    async def create_task(
        self,
        task_id: str,
        cwd: str,
        title: str = "New Task",
        resume_session_id: str | None = None,
        mode: str | None = None,
        model: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
    ) -> ActiveSession:
        # Create the bridge (satisfies acp.Client Protocol) before spawning
        policy = ToolPolicyEngine(workspace_cwd=cwd)
        bridge = AcpToAguiBridge(task_id, policy)
        bridge._cwd = cwd

        # Spawn the agent using the SDK via our runner
        runner = AgentRunner(task_id, command=self._agent_command)
        conn = await runner.spawn(client=bridge)

        # Wrap connection in our protocol layer for logging
        protocol = AcpProtocol(task_id)
        protocol.conn = conn

        # Initialize the ACP connection
        await protocol.initialize()

        # Create or load session
        mcp_list = list(mcp_servers.values()) if mcp_servers else []
        modes: list[dict[str, str]] | None = None
        models: list[dict[str, str]] | None = None
        current_mode_id: str | None = None

        if resume_session_id:
            result = await protocol.load_session(resume_session_id, cwd, mcp_list)
            if isinstance(result, dict):
                agent_session_id = result.get("sessionId", resume_session_id)
                if "modes" in result and result["modes"]:
                    modes = [{"id": m["id"], "name": m["name"]} for m in result["modes"].get("availableModes", [])]
                    current_mode_id = result["modes"].get("currentModeId")
                if "models" in result and result["models"]:
                    models = [{"id": m.get("modelId", ""), "name": m.get("name", "")} for m in result["models"].get("availableModels", [])]
            else:
                agent_session_id = getattr(result, "session_id", None) or getattr(result, "sessionId", resume_session_id)
        else:
            result = await protocol.new_session(cwd, mcp_list)
            if isinstance(result, dict):
                agent_session_id = result.get("sessionId", str(uuid.uuid4()))
                if "modes" in result and result["modes"]:
                    modes = [{"id": m["id"], "name": m["name"]} for m in result["modes"].get("availableModes", [])]
                    current_mode_id = result["modes"].get("currentModeId")
            else:
                agent_session_id = getattr(result, "session_id", None) or getattr(result, "sessionId", str(uuid.uuid4()))
                result_modes = getattr(result, "modes", None)
                if result_modes:
                    available = getattr(result_modes, "available_modes", None) or getattr(result_modes, "availableModes", [])
                    modes = [{"id": getattr(m, "id", ""), "name": getattr(m, "name", "")} for m in available]
                    current_mode_id = getattr(result_modes, "current_mode_id", None) or getattr(result_modes, "currentModeId", None)

        # Set mode/model if requested
        if mode and agent_session_id:
            try:
                await protocol.set_mode(agent_session_id, mode)
                current_mode_id = mode
            except Exception as exc:
                logger.warning("Failed to set mode %s: %s", mode, exc)
        if model and agent_session_id:
            try:
                await protocol.set_model(agent_session_id, model)
            except Exception as exc:
                logger.warning("Failed to set model %s: %s", model, exc)

        active = ActiveSession(
            task_id=task_id,
            agent_session_id=agent_session_id,
            cwd=cwd,
            runner=runner,
            protocol=protocol,
            bridge=bridge,
            modes=modes,
            models=models,
            current_mode_id=current_mode_id,
        )

        self._sessions[task_id] = active

        await self._store.create(
            task_id=task_id, agent_session_id=agent_session_id, cwd=cwd, title=title
        )
        logger.info("Session %s created (agent_session=%s, cwd=%s)", task_id, agent_session_id, cwd)
        return active

    async def start_run(self, task_id: str, input_data: dict[str, Any], config: dict[str, Any] | None = None) -> str:
        active = self._get_active(task_id)
        run_id = str(uuid.uuid4())

        queue: asyncio.Queue = asyncio.Queue()
        active.event_queues[run_id] = queue
        active.current_run_id = run_id

        messages = input_data.get("messages", [])
        text = ""
        attachments: list[dict[str, Any]] = []
        if messages:
            last = messages[-1]
            text = last.get("content", "")
            attachments = last.get("attachments", [])

        prompt: list[dict[str, Any]] = []
        if text:
            prompt.append({"type": "text", "text": text})

        for att in attachments:
            att_type = att.get("type", "file")
            att_name = att.get("name", "unnamed")
            att_mime = att.get("mimeType", "application/octet-stream")
            att_data = att.get("data", "")
            if att_type == "image":
                prompt.append({"type": "image", "data": att_data, "mimeType": att_mime})
            else:
                try:
                    import base64
                    decoded = base64.b64decode(att_data).decode("utf-8", errors="replace")
                    prompt.append({"type": "text", "text": f"[File: {att_name}]\n```\n{decoded}\n```"})
                except Exception:
                    prompt.append({"type": "text", "text": f"[File: {att_name} — could not decode]"})

        if not prompt:
            prompt.append({"type": "text", "text": ""})

        active.bridge.start_run(run_id, queue)

        await self._store.update(task_id, status="running")
        asyncio.create_task(self._run_prompt(active, run_id, prompt))
        return run_id

    async def _run_prompt(self, active: ActiveSession, run_id: str, prompt: list[dict[str, Any]]) -> None:
        queue = active.event_queues.get(run_id)
        if queue is None:
            return
        try:
            await active.protocol.prompt(active.agent_session_id, prompt)
            if active.bridge._run_id is not None:
                active.bridge.finish_run()
        except Exception as exc:
            logger.error("Run %s failed: %s", run_id, exc)
            active.bridge.error_run(str(exc))
        finally:
            await self._store.update(active.task_id, status="idle")

    def get_event_queue(self, task_id: str, run_id: str) -> asyncio.Queue | None:
        active = self._sessions.get(task_id)
        if active is None:
            return None
        return active.event_queues.get(run_id)

    async def approve(self, task_id: str, call_id: str, approved: bool, option_id: str | None = None) -> None:
        active = self._get_active(task_id)
        # Resolve the permission future in the bridge
        active.bridge.resolve_permission(call_id, approved, option_id)

    async def cancel_run(self, task_id: str) -> None:
        active = self._get_active(task_id)
        active.protocol.cancel(active.agent_session_id)
        await self._store.update(task_id, status="idle")

    async def set_mode(self, task_id: str, mode_id: str) -> Any:
        active = self._get_active(task_id)
        result = await active.protocol.set_mode(active.agent_session_id, mode_id)
        active.current_mode_id = mode_id
        return result

    async def set_model(self, task_id: str, model_id: str) -> None:
        active = self._get_active(task_id)
        await active.protocol.set_model(active.agent_session_id, model_id)

    async def execute_command(self, task_id: str, command: str, args: dict[str, Any] | None = None) -> None:
        active = self._get_active(task_id)
        args_str = args.get("args", "") if args else ""
        await active.protocol.execute_command(active.agent_session_id, command, args_str)

    async def stop(self, task_id: str) -> bool:
        active = self._sessions.pop(task_id, None)
        if active:
            await active.runner.kill()
            return True
        return False

    async def destroy(self, task_id: str) -> None:
        active = self._sessions.pop(task_id, None)
        if active:
            await active.runner.kill()

    async def shutdown(self) -> None:
        await asyncio.gather(
            *(self.destroy(tid) for tid in list(self._sessions.keys())),
            return_exceptions=True,
        )

    def _get_active(self, task_id: str) -> ActiveSession:
        active = self._sessions.get(task_id)
        if active is None:
            raise KeyError(f"No active session: {task_id}")
        return active


# Backward-compatible alias
TaskManager = SessionManager

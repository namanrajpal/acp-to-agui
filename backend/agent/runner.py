"""AgentRunner — thin wrapper around the acp SDK's spawn_agent_process.

Manages a single ACP agent subprocess via the official agent-client-protocol
SDK. Spawns the agent, holds the ClientSideConnection and subprocess reference.
"""

import asyncio
import logging
import os
import signal
import shutil
import subprocess
from typing import Any

import acp

logger = logging.getLogger(__name__)


def _kill_process_tree(root_pid: int) -> None:
    """Kill all descendants of root_pid, then root_pid itself."""
    ps_path = shutil.which("ps") or "/bin/ps"
    try:
        out = subprocess.check_output([ps_path, "-eo", "pid,ppid"], text=True)
    except Exception:
        try:
            os.kill(root_pid, signal.SIGKILL)
        except OSError:
            pass
        return

    children: dict[int, list[int]] = {}
    for line in out.strip().split("\n")[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)

    def _kill_recursive(pid: int) -> None:
        for child in children.get(pid, []):
            _kill_recursive(child)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    _kill_recursive(root_pid)


class AgentRunner:
    """Manages a single ACP agent subprocess via the official SDK.

    Attributes:
        task_id: Identifier of the owning task/session.
        conn: The SDK's ClientSideConnection (available after spawn).
        process: The subprocess reference (available after spawn).
    """

    def __init__(self, task_id: str, command: list[str]) -> None:
        self.task_id = task_id
        self._command = command
        self._log = logging.LoggerAdapter(logger, {"task_id": task_id})

        self.conn: acp.ClientSideConnection | None = None
        self.process: asyncio.subprocess.Process | None = None
        self._context_manager: Any = None

    async def spawn(self, client: acp.Client, env: dict[str, str] | None = None) -> acp.ClientSideConnection:
        """Spawn the ACP agent using the SDK's spawn_agent_process.

        Args:
            client: An acp.Client implementation that handles callbacks.
            env: Optional additional environment variables.

        Returns:
            The ClientSideConnection for making protocol calls.
        """
        # Set up environment
        proc_env = os.environ.copy()
        if env:
            proc_env.update(env)

        binary = self._command[0]
        args = self._command[1:]

        self._context_manager = acp.spawn_agent_process(
            client, binary, *args,
            transport_kwargs={"limit": 16 * 1024 * 1024},  # 16 MB buffer for large responses
        )
        conn, process = await self._context_manager.__aenter__()

        self.conn = conn
        self.process = process
        self._log.info("Spawned agent %s PID=%s via SDK", self._command, process.pid)

        return conn

    async def kill(self) -> None:
        """Kill the subprocess and all its descendants."""
        if self._context_manager:
            try:
                await self._context_manager.__aexit__(None, None, None)
            except Exception:
                pass
            self._context_manager = None

        # Fallback: if the process is still alive, force-kill the tree
        if self.process and self.process.pid and self.process.returncode is None:
            pid = self.process.pid
            await asyncio.to_thread(_kill_process_tree, pid)

        self.conn = None
        self.process = None

    def is_alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

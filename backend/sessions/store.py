"""SessionStore — SQLite-backed session/task metadata."""

import logging
import os
from datetime import datetime, timezone

import aiosqlite

from backend.sessions.types import TaskSummary

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    agent_session_id TEXT NOT NULL,
    cwd TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT 'New Task',
    status TEXT NOT NULL DEFAULT 'idle',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_agent_session ON tasks(agent_session_id);",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_summary(row: aiosqlite.Row) -> TaskSummary:
    return TaskSummary(
        taskId=row[0],
        agentSessionId=row[1],
        cwd=row[2],
        title=row[3],
        status=row[4],
        createdAt=row[5],
        updatedAt=row[6],
    )


class SessionStore:
    def __init__(self, db_path: str = "~/.acp-to-agui/tasks.db") -> None:
        self._db_path = os.path.expanduser(db_path)
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute(_CREATE_TABLE_SQL)
        for idx_sql in _CREATE_INDEXES_SQL:
            await self._db.execute(idx_sql)
        await self._db.commit()
        logger.info("SessionStore initialized at %s", self._db_path)

    def _ensure_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SessionStore not initialized")
        return self._db

    async def create(self, task_id: str, agent_session_id: str, cwd: str, title: str = "New Task") -> TaskSummary:
        db = self._ensure_db()
        now = _now_iso()
        await db.execute(
            "INSERT INTO tasks (task_id, agent_session_id, cwd, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'idle', ?, ?)",
            (task_id, agent_session_id, cwd, title, now, now),
        )
        await db.commit()
        return TaskSummary(taskId=task_id, agentSessionId=agent_session_id, cwd=cwd, title=title, status="idle", createdAt=now, updatedAt=now)

    async def get(self, task_id: str) -> TaskSummary | None:
        db = self._ensure_db()
        cursor = await db.execute(
            "SELECT task_id, agent_session_id, cwd, title, status, created_at, updated_at FROM tasks WHERE task_id = ?",
            (task_id,),
        )
        row = await cursor.fetchone()
        return _row_to_summary(row) if row else None

    async def list_all(self) -> list[TaskSummary]:
        db = self._ensure_db()
        cursor = await db.execute(
            "SELECT task_id, agent_session_id, cwd, title, status, created_at, updated_at FROM tasks ORDER BY updated_at DESC"
        )
        return [_row_to_summary(row) for row in await cursor.fetchall()]

    async def update(self, task_id: str, **kwargs: str) -> TaskSummary:
        db = self._ensure_db()
        allowed = {"title", "status"}
        fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not fields:
            task = await self.get(task_id)
            if task is None:
                raise ValueError(f"Task {task_id} not found")
            return task
        fields["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [task_id]
        await db.execute(f"UPDATE tasks SET {set_clause} WHERE task_id = ?", values)
        await db.commit()
        task = await self.get(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found after update")
        return task

    async def delete(self, task_id: str) -> bool:
        db = self._ensure_db()
        cursor = await db.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        await db.commit()
        return cursor.rowcount > 0

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None


# Backward-compatible alias
TaskStore = SessionStore

"""AG-UI compatible endpoint — makes this bridge a proper AG-UI server.

Any AG-UI client (CopilotKit, HttpAgent, custom clients) can POST to
this endpoint with a RunAgentInput body and receive back an SSE stream
of AG-UI events. This is the standard AG-UI server contract:

  POST /ag-ui
  Content-Type: application/json
  Accept: text/event-stream

  Body: { threadId, runId, messages, tools, state, context, forwardedProps }

  Response: text/event-stream with AG-UI events
"""

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.agui.sse import event_stream

logger = logging.getLogger(__name__)

router = APIRouter()


class AgUiMessage(BaseModel):
    id: str | None = None
    role: str
    content: str


class RunAgentInput(BaseModel):
    """AG-UI standard RunAgentInput schema."""
    threadId: str | None = None
    runId: str | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    messages: list[AgUiMessage] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    context: list[Any] = Field(default_factory=list)
    forwardedProps: dict[str, Any] = Field(default_factory=dict)


@router.post("/ag-ui")
async def ag_ui_run(body: RunAgentInput, request: Request):
    """AG-UI standard run endpoint.

    Accepts RunAgentInput, creates/reuses a session, sends the prompt,
    and streams back AG-UI events over SSE. This makes the bridge
    compatible with any AG-UI client (CopilotKit, HttpAgent, etc.).
    """
    manager = getattr(request.app.state, "session_manager", None)
    if manager is None:
        return StreamingResponse(
            _error_stream("Session manager not initialized"),
            media_type="text/event-stream",
        )

    config = request.app.state.config
    thread_id = body.threadId or str(uuid.uuid4())
    run_id = body.runId or str(uuid.uuid4())

    # Get or create a session for this thread
    active = manager._sessions.get(thread_id)
    if active is None:
        cwd = body.forwardedProps.get("cwd", ".")
        try:
            active = await manager.create_task(
                task_id=thread_id,
                cwd=cwd,
                title="AG-UI Session",
            )
        except Exception as exc:
            logger.error("Failed to create session for AG-UI: %s", exc)
            return StreamingResponse(
                _error_stream(str(exc)),
                media_type="text/event-stream",
            )

    # Extract the last user message
    user_message = ""
    if body.messages:
        for msg in reversed(body.messages):
            if msg.role == "user":
                user_message = msg.content
                break

    if not user_message:
        return StreamingResponse(
            _error_stream("No user message provided"),
            media_type="text/event-stream",
        )

    # Start a run
    try:
        actual_run_id = await manager.start_run(
            thread_id,
            {"messages": [{"role": "user", "content": user_message}]},
        )
    except Exception as exc:
        logger.error("Failed to start run: %s", exc)
        return StreamingResponse(
            _error_stream(str(exc)),
            media_type="text/event-stream",
        )

    # Get the event queue and stream it
    queue = manager.get_event_queue(thread_id, actual_run_id)
    if queue is None:
        return StreamingResponse(
            _error_stream("No event queue for run"),
            media_type="text/event-stream",
        )

    return StreamingResponse(
        event_stream(queue, thread_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _error_stream(message: str):
    """Yield a single RUN_ERROR event."""
    import json
    import time
    error_event = {
        "type": "RUN_ERROR",
        "timestamp": time.time(),
        "message": message,
        "runId": str(uuid.uuid4()),
        "taskId": "error",
    }
    yield f"event: RUN_ERROR\ndata: {json.dumps(error_event)}\n\n"

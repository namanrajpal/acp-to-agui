"""ACP type definitions — minimal subset for extension handling.

Most ACP types are now provided by the `agent-client-protocol` SDK.
This module retains only types needed for internal use that the SDK
does not directly expose, plus re-exports for backward compatibility.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Types still needed for prompt content construction ───────────────────────


class PromptContent(BaseModel):
    """Content item for a session prompt (text or image)."""
    type: Literal["text", "image"]
    text: str | None = None
    data: str | None = None
    mimeType: str | None = None


# ── Mode/Model info (used by session responses) ─────────────────────────────


class ModeInfo(BaseModel):
    id: str
    name: str
    description: str | None = None


class ModesInfo(BaseModel):
    currentModeId: str
    availableModes: list[ModeInfo] = Field(default_factory=list)

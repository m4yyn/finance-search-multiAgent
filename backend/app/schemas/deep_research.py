from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class DeepResearchStreamRequest(BaseModel):
    """Start or resume a Deep Research multi-agent stream."""

    session_id: UUID
    content: str = Field(min_length=1)
    search_web: bool = True
    search_local: bool = False
    resume: bool = False

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Research content cannot be blank.")
        return normalized


class DeepResearchSSEChunk(BaseModel):
    """Documented SSE payload shape for Deep Research events."""

    type: str
    session_id: UUID | None = None
    agent: str | None = None
    phase: str | None = None
    timestamp: str | None = None
    content: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    done: bool = False
    error: str | None = None
    checkpoint_id: str | None = None
    status: Literal["running", "completed", "failed"] | None = None

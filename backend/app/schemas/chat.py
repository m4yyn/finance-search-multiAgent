from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatSessionCreate(BaseModel):
    """Create a new chat session. Title is optional and can be auto-generated."""

    title: str | None = Field(default=None, max_length=200)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None


class ChatSessionResponse(BaseModel):
    """Public chat session response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    is_active: bool


class SendMessageRequest(BaseModel):
    """Send one user message to an existing chat session."""

    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Message content cannot be blank.")
        return normalized


class ChatSessionCreatedResponse(BaseModel):
    """Minimal response for chat session creation."""

    session_id: UUID
    title: str


class ChatStreamRequest(SendMessageRequest):
    """Stream one user message to an existing chat session."""

    session_id: UUID
    kb_ids: list[UUID] | None = None

    @field_validator("kb_ids")
    @classmethod
    def normalize_kb_ids(cls, value: list[UUID] | None) -> list[UUID] | None:
        if not value:
            return None
        return list(dict.fromkeys(value))


class ChatMessageResponse(BaseModel):
    """Public chat message response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    role: Literal["user", "assistant", "system"]
    content: str
    tokens: int | None
    created_at: datetime


class ChatReference(BaseModel):
    """A retrieved knowledge chunk returned to the frontend for citation display."""

    index: int
    content: str
    filename: str
    score: float
    kb_id: UUID
    document_id: UUID
    chunk_id: str
    chunk_index: int | None = None
    page_number: int | None = None
    row_number: int | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatSSEChunk(BaseModel):
    """Structured SSE data payload for streaming chat responses."""

    type: Literal["delta", "done", "error"]
    session_id: UUID
    message_id: UUID | None = None
    delta: str | None = None
    done: bool = False
    error: str | None = None
    references: list[ChatReference] | None = None

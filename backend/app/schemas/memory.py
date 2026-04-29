from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemoryCreateRequest(BaseModel):
    """Create a long-term memory from a chat session."""

    session_id: UUID


class MemoryResponse(BaseModel):
    """Public long-term memory response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    session_id: UUID | None
    summary: str
    key_insights: dict[str, Any] | list[Any] | None
    milvus_ids: list[str]
    token_count: int | None
    created_at: datetime


class MemorySearchRequest(BaseModel):
    """Search user-owned long-term memories."""

    query: str = Field(min_length=1)
    top_k: int = Field(default=3, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Memory search query cannot be blank.")
        return normalized


class MemorySearchResult(MemoryResponse):
    """A recalled memory with vector similarity score."""

    score: float


class MemoryContextResponse(BaseModel):
    """Prompt-ready long-term memory context."""

    query: str
    top_k: int
    context: str
    memories: list[MemorySearchResult]

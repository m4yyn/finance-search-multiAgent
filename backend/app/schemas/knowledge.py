from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DocumentStatus = Literal["pending", "processing", "success", "failed"]


class KnowledgeBaseCreate(BaseModel):
    """Create a user-owned knowledge base."""

    name: str = Field(min_length=1, max_length=100)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Knowledge base name cannot be blank.")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class KnowledgeBaseResponse(BaseModel):
    """Public knowledge base response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    name: str
    description: str | None
    collection_name: str
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseStats(BaseModel):
    """Storage stats used by acceptance checks and admin diagnostics."""

    kb_id: UUID
    collection_name: str
    pg_chunk_count: int
    milvus_chunk_count: int


class DocumentResponse(BaseModel):
    """Public document response; local file_path is intentionally hidden."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kb_id: UUID
    filename: str
    file_size: int
    mime_type: str
    status: DocumentStatus
    chunk_count: int | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class RetrievalRequest(BaseModel):
    """Request relevant chunks from one or more knowledge bases."""

    kb_id: UUID | None = None
    kb_ids: list[UUID] | None = None
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Retrieval query cannot be blank.")
        return normalized

    @model_validator(mode="after")
    def validate_kb_selector(self) -> "RetrievalRequest":
        if self.kb_id is None and not self.kb_ids:
            raise ValueError("Either kb_id or kb_ids is required.")
        if self.kb_id is not None and self.kb_ids:
            raise ValueError("Use either kb_id or kb_ids, not both.")
        if self.kb_ids:
            self.kb_ids = list(dict.fromkeys(self.kb_ids))
        return self


class RetrivalChunk(BaseModel):
    """A retrieved chunk with optional source coordinates."""

    kb_id: UUID
    document_id: UUID
    filename: str
    content: str
    score: float
    chunk_id: str
    chunk_index: int | None = None
    page_number: int | None = None
    row_number: int | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


RetrievalChunk = RetrivalChunk

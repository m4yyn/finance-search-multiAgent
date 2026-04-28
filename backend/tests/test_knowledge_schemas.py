from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.knowledge import (
    DocumentResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    RetrievalChunk,
    RetrievalRequest,
    RetrivalChunk,
)


def test_knowledge_schemas_validate_expected_fields() -> None:
    user_id = uuid4()
    kb_id = uuid4()
    document_id = uuid4()
    now = datetime.now(UTC)

    create_payload = KnowledgeBaseCreate(
        name="  产业  知识库  ",
        description="  用于研报解析  ",
    )
    kb_response = KnowledgeBaseResponse(
        id=kb_id,
        user_id=user_id,
        name="产业知识库",
        description="用于研报解析",
        collection_name="kb_001",
        created_at=now,
        updated_at=now,
    )
    document_response = DocumentResponse(
        id=document_id,
        kb_id=kb_id,
        filename="report.pdf",
        file_size=1024,
        mime_type="application/pdf",
        status="pending",
        chunk_count=None,
        error_message=None,
        created_at=now,
        updated_at=now,
    )
    request = RetrievalRequest(kb_id=kb_id, query="  贵州茅台营收  ")
    multi_request = RetrievalRequest(kb_ids=[kb_id, kb_id], query="  白酒行业  ")
    chunk = RetrivalChunk(
        kb_id=kb_id,
        document_id=document_id,
        filename="report.pdf",
        content="贵州茅台主营白酒业务。",
        score=0.92,
        chunk_id="chunk-1",
        chunk_index=1,
        page_number=3,
        sheet_name="Sheet1",
        row_start=2,
        row_end=10,
        metadata={"source": "pdf"},
    )

    assert create_payload.name == "产业  知识库"
    assert create_payload.description == "用于研报解析"
    assert kb_response.collection_name == "kb_001"
    assert "file_path" not in document_response.model_dump()
    assert request.query == "贵州茅台营收"
    assert multi_request.kb_ids == [kb_id]
    assert request.top_k == 5
    assert chunk.chunk_index == 1
    assert chunk.page_number == 3
    assert chunk.sheet_name == "Sheet1"
    assert RetrievalChunk is RetrivalChunk


def test_knowledge_schemas_reject_invalid_payloads() -> None:
    kb_id = uuid4()

    with pytest.raises(ValidationError):
        KnowledgeBaseCreate(name="   ")

    with pytest.raises(ValidationError):
        RetrievalRequest(kb_id=kb_id, query="   ")

    with pytest.raises(ValidationError):
        RetrievalRequest(kb_id=kb_id, query="query", top_k=0)

    with pytest.raises(ValidationError):
        RetrievalRequest(kb_id=kb_id, query="query", top_k=21)

    with pytest.raises(ValidationError):
        RetrievalRequest(query="query")

    with pytest.raises(ValidationError):
        RetrievalRequest(kb_id=kb_id, kb_ids=[kb_id], query="query")

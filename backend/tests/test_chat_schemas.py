from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.chat import (
    ChatMessageResponse,
    ChatReference,
    ChatSSEChunk,
    ChatSessionCreate,
    ChatSessionCreatedResponse,
    ChatSessionResponse,
    ChatStreamRequest,
    SendMessageRequest,
)


def test_chat_schemas_validate_expected_fields() -> None:
    session_id = uuid4()
    user_id = uuid4()
    message_id = uuid4()
    kb_id = uuid4()
    document_id = uuid4()

    create_payload = ChatSessionCreate(title="  投资  研究  ")
    send_payload = SendMessageRequest(content="  分析银行板块  ")
    stream_payload = ChatStreamRequest(
        session_id=session_id,
        content="  分析贵州茅台  ",
        search_mode="local",
    )
    created_response = ChatSessionCreatedResponse(session_id=session_id, title="投资研究")
    session_response = ChatSessionResponse(
        id=session_id,
        user_id=user_id,
        title="投资研究",
        created_at="2026-04-27T00:00:00Z",
        updated_at="2026-04-27T00:00:00Z",
        is_active=True,
    )
    message_response = ChatMessageResponse(
        id=message_id,
        session_id=session_id,
        role="assistant",
        content="回答",
        tokens=10,
        created_at="2026-04-27T00:00:00Z",
    )
    reference = ChatReference(
        index=1,
        content="贵州茅台净利润片段",
        filename="annual.pdf",
        score=0.92,
        kb_id=kb_id,
        document_id=document_id,
        chunk_id="chunk-1",
        chunk_index=3,
    )
    web_reference = ChatReference(
        index=2,
        source_type="web",
        content="A股市场新闻摘要",
        filename="A股市场新闻",
        url="https://example.com/a-share",
        site_name="Example Finance",
        date_published="2026-04-28",
    )
    chunk = ChatSSEChunk(
        type="delta",
        session_id=session_id,
        message_id=message_id,
        delta="回答",
        references=[reference, web_reference],
    )

    assert create_payload.title == "投资 研究"
    assert send_payload.content == "分析银行板块"
    assert stream_payload.content == "分析贵州茅台"
    assert stream_payload.search_mode == "local"
    assert created_response.session_id == session_id
    assert session_response.user_id == user_id
    assert message_response.role == "assistant"
    assert chunk.done is False
    assert chunk.references[0].filename == "annual.pdf"
    assert chunk.references[1].source_type == "web"
    assert chunk.references[1].url == "https://example.com/a-share"


def test_send_message_rejects_blank_content() -> None:
    with pytest.raises(ValidationError):
        SendMessageRequest(content="   ")


def test_chat_stream_rejects_unknown_search_mode() -> None:
    with pytest.raises(ValidationError):
        ChatStreamRequest(session_id=uuid4(), content="hello", search_mode="hybrid")

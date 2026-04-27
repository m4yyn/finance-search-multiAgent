from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.chat import (
    ChatMessageResponse,
    ChatSSEChunk,
    ChatSessionCreate,
    ChatSessionResponse,
    SendMessageRequest,
)


def test_chat_schemas_validate_expected_fields() -> None:
    session_id = uuid4()
    user_id = uuid4()
    message_id = uuid4()

    create_payload = ChatSessionCreate(title="  投资  研究  ")
    send_payload = SendMessageRequest(content="  分析银行板块  ")
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
    chunk = ChatSSEChunk(
        type="delta",
        session_id=session_id,
        message_id=message_id,
        delta="回答",
    )

    assert create_payload.title == "投资 研究"
    assert send_payload.content == "分析银行板块"
    assert session_response.user_id == user_id
    assert message_response.role == "assistant"
    assert chunk.done is False


def test_send_message_rejects_blank_content() -> None:
    with pytest.raises(ValidationError):
        SendMessageRequest(content="   ")

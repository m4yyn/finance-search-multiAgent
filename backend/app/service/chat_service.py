import json
from collections.abc import AsyncGenerator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.redis_client import RedisCache
from app.models import ChatMessage, ChatSession
from app.schemas.chat import ChatSSEChunk
from app.service import llm_service
from app.service.session_service import (
    add_message,
    create_chat_session,
    get_chat_session,
    get_formatted_history_messages,
    get_session_messages,
    list_chat_sessions,
)


def format_sse_chunk(chunk: ChatSSEChunk) -> str:
    payload = chunk.model_dump(mode="json", exclude_none=True)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def create_user_chat_session(
    db: AsyncSession,
    user_id: UUID,
    title: str | None = None,
) -> ChatSession:
    return await create_chat_session(db, user_id, title)


async def get_user_chat_session(
    db: AsyncSession,
    user_id: UUID,
    session_id: UUID,
) -> ChatSession | None:
    return await get_chat_session(db, user_id, session_id)


async def list_user_chat_sessions(
    db: AsyncSession,
    user_id: UUID,
) -> list[ChatSession]:
    return await list_chat_sessions(db, user_id)


async def get_user_chat_messages(
    db: AsyncSession,
    user_id: UUID,
    session_id: UUID,
) -> list[ChatMessage] | None:
    chat_session = await get_user_chat_session(db, user_id, session_id)
    if chat_session is None:
        return None
    return await get_session_messages(db, session_id)


async def stream_chat_response(
    sessionmaker: async_sessionmaker[AsyncSession],
    redis_cache: RedisCache,
    session_id: UUID,
    user_content: str,
) -> AsyncGenerator[str, None]:
    """Persist a user message, stream the LLM reply, then persist assistant output."""
    async with sessionmaker() as db:
        await add_message(db, redis_cache, session_id, "user", user_content)
        history_messages = await get_formatted_history_messages(
            db,
            redis_cache,
            session_id,
        )

        assistant_content_parts: list[str] = []
        try:
            async for delta in llm_service.stream_chat_completion(history_messages):
                assistant_content_parts.append(delta)
                yield format_sse_chunk(
                    ChatSSEChunk(type="delta", session_id=session_id, delta=delta)
                )

            assistant_message = await add_message(
                db,
                redis_cache,
                session_id,
                "assistant",
                "".join(assistant_content_parts),
            )
            yield format_sse_chunk(
                ChatSSEChunk(
                    type="done",
                    session_id=session_id,
                    message_id=assistant_message.id,
                    done=True,
                )
            )
        except Exception as exc:
            yield format_sse_chunk(
                ChatSSEChunk(
                    type="error",
                    session_id=session_id,
                    done=True,
                    error=str(exc) or "LLM stream failed.",
                )
            )

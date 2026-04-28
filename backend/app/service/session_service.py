from typing import Any, Literal
from uuid import UUID

import tiktoken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.core.redis_client import RedisCache
from app.models import ChatMessage, ChatSession
from app.models.chat import utc_now


MAX_MESSAGES = 20
TOKEN_LIMIT = 10000
DEFAULT_SESSION_TITLE = "新会话"
MessageRole = Literal["user", "assistant", "system"]


def get_chat_redis_key(session_id: UUID | str) -> str:
    return f"chat:session:{session_id}:messages"


def generate_session_title(content: str) -> str:
    normalized = " ".join(content.split())
    return normalized[:20] or DEFAULT_SESSION_TITLE


def count_message_tokens(content: str) -> int:
    """Count text tokens with tiktoken, falling back to cl100k_base when needed."""
    settings = get_settings()
    try:
        encoding = tiktoken.encoding_for_model(settings.llm_model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(content))


def build_message_data(message: ChatMessage) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "session_id": str(message.session_id),
        "role": message.role,
        "content": message.content,
        "tokens": message.tokens,
        "created_at": message.created_at.isoformat(),
    }


def prune_short_term_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pruned = list(messages)
    while len(pruned) > MAX_MESSAGES:
        pruned.pop(0)

    def total_tokens() -> int:
        return sum(int(message.get("tokens") or 0) for message in pruned)

    # Keep at least the newest message even if a single message exceeds the limit.
    while len(pruned) > 1 and total_tokens() > TOKEN_LIMIT:
        pruned.pop(0)
    return pruned


async def replace_redis_messages(
    redis_cache: RedisCache,
    session_id: UUID | str,
    messages: list[dict[str, Any]],
) -> None:
    key = get_chat_redis_key(session_id)
    await redis_cache.delete(key)
    for message in prune_short_term_messages(messages):
        await redis_cache.add_to_list(key, message)


async def append_redis_message(
    redis_cache: RedisCache,
    session_id: UUID | str,
    message: dict[str, Any],
) -> None:
    key = get_chat_redis_key(session_id)
    messages = await redis_cache.get_list(key)
    messages.append(message)
    await replace_redis_messages(redis_cache, session_id, messages)


async def create_chat_session(
    db: AsyncSession,
    user_id: UUID,
    title: str | None = None,
) -> ChatSession:
    session = ChatSession(user_id=user_id, title=title or DEFAULT_SESSION_TITLE)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def get_chat_session(
    db: AsyncSession,
    user_id: UUID,
    session_id: UUID,
) -> ChatSession | None:
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id,
            ChatSession.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def list_chat_sessions(
    db: AsyncSession,
    user_id: UUID,
) -> list[ChatSession]:
    result = await db.execute(
        select(ChatSession)
        .where(
            ChatSession.user_id == user_id,
            ChatSession.is_active.is_(True),
        )
        .order_by(ChatSession.updated_at.desc(), ChatSession.created_at.desc())
    )
    return list(result.scalars().all())


async def delete_chat_session(
    db: AsyncSession,
    redis_cache: RedisCache,
    user_id: UUID,
    session_id: UUID,
) -> bool:
    """Soft-delete a user-owned chat session and clear its short-term memory."""
    chat_session = await get_chat_session(db, user_id, session_id)
    if chat_session is None:
        return False

    chat_session.is_active = False
    chat_session.updated_at = utc_now()
    await db.commit()
    await redis_cache.delete(get_chat_redis_key(session_id))
    return True


async def add_message(
    db: AsyncSession,
    redis_cache: RedisCache,
    session_id: UUID,
    role: MessageRole,
    content: str,
) -> ChatMessage:
    message = ChatMessage(
        session_id=session_id,
        role=role,
        content=content,
        tokens=count_message_tokens(content),
    )
    chat_session = await db.get(ChatSession, session_id)
    if chat_session is not None:
        if role == "user" and chat_session.title == DEFAULT_SESSION_TITLE:
            chat_session.title = generate_session_title(content)
        chat_session.updated_at = utc_now()
    db.add(message)
    await db.commit()
    await db.refresh(message)
    await append_redis_message(redis_cache, session_id, build_message_data(message))
    return message


async def get_session_messages(
    db: AsyncSession,
    session_id: UUID,
) -> list[ChatMessage]:
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
    )
    return list(result.scalars().all())


async def get_short_term_messages(
    db: AsyncSession,
    redis_cache: RedisCache,
    session_id: UUID,
) -> list[dict[str, Any]]:
    cached_messages = await redis_cache.get_list(get_chat_redis_key(session_id))
    if cached_messages:
        pruned_messages = prune_short_term_messages(cached_messages)
        if pruned_messages != cached_messages:
            await replace_redis_messages(redis_cache, session_id, pruned_messages)
        return pruned_messages

    pg_messages = await get_session_messages(db, session_id)
    message_data = [build_message_data(message) for message in pg_messages]
    pruned_messages = prune_short_term_messages(message_data)
    await replace_redis_messages(redis_cache, session_id, pruned_messages)
    return pruned_messages


async def get_formatted_history_messages(
    db: AsyncSession,
    redis_cache: RedisCache,
    session_id: UUID,
) -> list[dict[str, str]]:
    messages = await get_short_term_messages(db, redis_cache, session_id)
    return [
        {"role": str(message["role"]), "content": str(message["content"])}
        for message in messages
    ]

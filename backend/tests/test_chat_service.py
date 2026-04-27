import asyncio
import json
from collections.abc import AsyncGenerator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.redis_client import RedisCache
from app.models import ChatMessage, User
from app.service import chat_service, session_service
from app.service.chat_service import (
    create_user_chat_session,
    stream_chat_response,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        existed = key in self.values or key in self.lists
        self.values.pop(key, None)
        self.lists.pop(key, None)
        return int(existed)

    async def exists(self, key: str) -> int:
        return int(key in self.values or key in self.lists)

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        values = self.lists.get(key, [])
        if end == -1:
            return values[start:]
        return values[start : end + 1]


async def build_context() -> tuple[
    AsyncSession,
    async_sessionmaker[AsyncSession],
    RedisCache,
    User,
]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    redis_cache = RedisCache(FakeRedis())

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session = sessionmaker()
    user = User(
        username="chat-service-user",
        email="chat-service-user@example.com",
        hashed_password="hashed",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return session, sessionmaker, redis_cache, user


async def close_context(session: AsyncSession) -> None:
    engine = session.bind
    await session.close()
    if engine is not None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def parse_sse_events(chunks: list[str]) -> list[dict]:
    events = []
    for chunk in chunks:
        assert chunk.startswith("data: ")
        events.append(json.loads(chunk.removeprefix("data: ").strip()))
    return events


def test_chat_service_stream_success_persists_user_and_assistant(monkeypatch) -> None:
    async def run_check() -> None:
        session, sessionmaker, redis_cache, user = await build_context()
        try:
            monkeypatch.setattr(session_service, "count_message_tokens", lambda _: 1)

            async def fake_stream(
                messages: list[dict[str, str]],
            ) -> AsyncGenerator[str, None]:
                assert messages[-1] == {"role": "user", "content": "hello"}
                yield "你"
                yield "好"

            monkeypatch.setattr(chat_service.llm_service, "stream_chat_completion", fake_stream)

            chat_session = await create_user_chat_session(session, user.id)
            chunks = [
                chunk
                async for chunk in stream_chat_response(
                    sessionmaker,
                    redis_cache,
                    chat_session.id,
                    "hello",
                )
            ]
            events = parse_sse_events(chunks)
            messages = list(
                (
                    await session.execute(
                        select(ChatMessage)
                        .where(ChatMessage.session_id == chat_session.id)
                        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
                    )
                ).scalars()
            )
            redis_messages = await redis_cache.get_list(
                session_service.get_chat_redis_key(chat_session.id)
            )

            assert [event["type"] for event in events] == ["delta", "delta", "done"]
            assert events[0]["delta"] == "你"
            assert events[2]["done"] is True
            assert [message.role for message in messages] == ["user", "assistant"]
            assert messages[0].content == "hello"
            assert messages[1].content == "你好"
            assert [message["role"] for message in redis_messages] == [
                "user",
                "assistant",
            ]
        finally:
            await close_context(session)

    asyncio.run(run_check())


def test_chat_service_stream_error_keeps_user_without_assistant(monkeypatch) -> None:
    async def run_check() -> None:
        session, sessionmaker, redis_cache, user = await build_context()
        try:
            monkeypatch.setattr(session_service, "count_message_tokens", lambda _: 1)

            async def fake_stream(
                messages: list[dict[str, str]],
            ) -> AsyncGenerator[str, None]:
                assert messages[-1] == {"role": "user", "content": "hello"}
                raise RuntimeError("upstream failed")
                yield "unreachable"

            monkeypatch.setattr(chat_service.llm_service, "stream_chat_completion", fake_stream)

            chat_session = await create_user_chat_session(session, user.id)
            chunks = [
                chunk
                async for chunk in stream_chat_response(
                    sessionmaker,
                    redis_cache,
                    chat_session.id,
                    "hello",
                )
            ]
            events = parse_sse_events(chunks)
            messages = list(
                (
                    await session.execute(
                        select(ChatMessage)
                        .where(ChatMessage.session_id == chat_session.id)
                        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
                    )
                ).scalars()
            )
            redis_messages = await redis_cache.get_list(
                session_service.get_chat_redis_key(chat_session.id)
            )

            assert events == [
                {
                    "type": "error",
                    "session_id": str(chat_session.id),
                    "done": True,
                    "error": "upstream failed",
                }
            ]
            assert [message.role for message in messages] == ["user"]
            assert [message["role"] for message in redis_messages] == ["user"]
        finally:
            await close_context(session)

    asyncio.run(run_check())

import asyncio
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.redis_client import RedisCache
from app.models import User
from app.service import session_service
from app.service.session_service import (
    add_message,
    create_chat_session,
    delete_chat_session,
    get_formatted_history_messages,
    get_chat_session,
    get_session_messages,
    get_short_term_messages,
    list_chat_sessions,
    prune_short_term_messages,
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


async def build_session() -> AsyncGenerator[
    tuple[AsyncSession, async_sessionmaker[AsyncSession], RedisCache, User],
    None,
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

    async with sessionmaker() as session:
        user = User(
            username="session-user",
            email="session-user@example.com",
            hashed_password="hashed",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        yield session, sessionmaker, redis_cache, user

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def test_session_service_persists_pg_and_prunes_redis(monkeypatch) -> None:
    async def run_check() -> None:
        async for db, _, redis_cache, user in build_session():
            monkeypatch.setattr(session_service, "count_message_tokens", lambda _: 1)
            chat_session = await create_chat_session(db, user.id)

            for index in range(25):
                await add_message(
                    db,
                    redis_cache,
                    chat_session.id,
                    "user",
                    f"message {index}",
                )

            pg_messages = await get_session_messages(db, chat_session.id)
            short_messages = await get_short_term_messages(db, redis_cache, chat_session.id)
            await db.refresh(chat_session)

            assert chat_session.title == "message 0"
            assert len(pg_messages) == 25
            assert len(short_messages) == 20
            assert short_messages[0]["content"] == "message 5"
            assert short_messages[-1]["content"] == "message 24"

    asyncio.run(run_check())


def test_session_service_rebuilds_redis_from_pg_and_formats_history(monkeypatch) -> None:
    async def run_check() -> None:
        async for db, _, redis_cache, user in build_session():
            monkeypatch.setattr(session_service, "count_message_tokens", lambda _: 1)
            chat_session = await create_chat_session(db, user.id, title="研究")
            await add_message(db, redis_cache, chat_session.id, "user", "hello")
            await add_message(db, redis_cache, chat_session.id, "assistant", "hi")

            redis_cache.client.lists.clear()
            formatted_messages = await get_formatted_history_messages(
                db,
                redis_cache,
                chat_session.id,
            )

            assert formatted_messages == [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ]
            assert redis_cache.client.lists

    asyncio.run(run_check())


def test_session_service_soft_deletes_session_and_clears_redis(monkeypatch) -> None:
    async def run_check() -> None:
        async for db, _, redis_cache, user in build_session():
            monkeypatch.setattr(session_service, "count_message_tokens", lambda _: 1)
            chat_session = await create_chat_session(db, user.id, title="研究记录")
            await add_message(db, redis_cache, chat_session.id, "user", "hello")

            redis_key = session_service.get_chat_redis_key(chat_session.id)
            assert await redis_cache.exists(redis_key)

            deleted = await delete_chat_session(db, redis_cache, user.id, chat_session.id)
            active_session = await get_chat_session(db, user.id, chat_session.id)
            sessions = await list_chat_sessions(db, user.id)
            pg_messages = await get_session_messages(db, chat_session.id)
            await db.refresh(chat_session)

            assert deleted is True
            assert chat_session.is_active is False
            assert active_session is None
            assert sessions == []
            assert [message.content for message in pg_messages] == ["hello"]
            assert not await redis_cache.exists(redis_key)
            assert (
                await delete_chat_session(db, redis_cache, user.id, chat_session.id)
                is False
            )

    asyncio.run(run_check())


def test_session_service_prunes_by_token_limit() -> None:
    messages = [
        {"content": "old", "tokens": 6000},
        {"content": "newer", "tokens": 5000},
        {"content": "newest", "tokens": 1},
    ]

    pruned_messages = prune_short_term_messages(messages)

    assert [message["content"] for message in pruned_messages] == ["newer", "newest"]

import asyncio
import json
from collections.abc import AsyncGenerator
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.redis_client import RedisCache
from app.models import ChatMessage, User
from app.schemas.knowledge import RetrivalChunk
from app.service import chat_service, session_service
from app.service.local_file_router_service import LocalFileRoute
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

            async def fail_retrieve(*args, **kwargs):  # noqa: ANN002, ANN003
                raise AssertionError("Pure LLM chat must not call retrieval.")

            monkeypatch.setattr(chat_service.llm_service, "stream_chat_completion", fake_stream)
            monkeypatch.setattr(chat_service, "retrieve_from_documents", fail_retrieve)
            monkeypatch.setattr(chat_service, "retrieve_from_kbs", fail_retrieve)
            monkeypatch.setattr(chat_service, "retrieve_from_all_user_kbs", fail_retrieve)

            chat_session = await create_user_chat_session(session, user.id)
            chunks = [
                chunk
                async for chunk in stream_chat_response(
                    sessionmaker,
                    redis_cache,
                    chat_session.id,
                    user.id,
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


def test_chat_service_stream_with_local_document_route_uses_references_without_storing_prompt(
    monkeypatch,
) -> None:
    async def run_check() -> None:
        session, sessionmaker, redis_cache, user = await build_context()
        kb_id = uuid4()
        document_id = uuid4()
        try:
            monkeypatch.setattr(session_service, "count_message_tokens", lambda _: 1)

            async def fake_candidates(db, user_id):  # noqa: ANN001
                assert user_id == user.id
                return ["candidate"]

            async def fake_route(query, candidates):  # noqa: ANN001
                assert query == "公司净利润是多少"
                assert candidates == ["candidate"]
                return LocalFileRoute(route="documents", document_ids=[document_id])

            async def fake_retrieve(db, user_id, document_ids, query, top_k=5):  # noqa: ANN001
                assert user_id == user.id
                assert document_ids == [document_id]
                assert query == "公司净利润是多少"
                assert top_k == 5
                return [
                    RetrivalChunk(
                        kb_id=kb_id,
                        document_id=document_id,
                        filename="annual.pdf",
                        content="公司2023年净利润为100亿元。",
                        score=0.93,
                        chunk_id="chunk-1",
                        chunk_index=7,
                    )
                ]

            async def fake_stream(
                messages: list[dict[str, str]],
            ) -> AsyncGenerator[str, None]:
                prompt = messages[-1]["content"]
                assert messages[-1]["role"] == "user"
                assert "请严格基于以下参考资料回答用户问题" in prompt
                assert "[1] annual.pdf | score=0.9300 | chunk=7" in prompt
                assert "公司2023年净利润为100亿元。" in prompt
                assert "用户问题：\n公司净利润是多少" in prompt
                yield "净利润为"
                yield "100亿元[1]"

            monkeypatch.setattr(chat_service, "list_local_document_candidates", fake_candidates)
            monkeypatch.setattr(chat_service, "route_query_to_local_files", fake_route)
            monkeypatch.setattr(chat_service, "retrieve_from_documents", fake_retrieve)
            monkeypatch.setattr(chat_service.llm_service, "stream_chat_completion", fake_stream)

            chat_session = await create_user_chat_session(session, user.id)
            chunks = [
                chunk
                async for chunk in stream_chat_response(
                    sessionmaker,
                    redis_cache,
                    chat_session.id,
                    user.id,
                    "公司净利润是多少",
                    search_mode="local",
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
            assert events[2]["references"][0]["index"] == 1
            assert events[2]["references"][0]["filename"] == "annual.pdf"
            assert events[2]["references"][0]["content"] == "公司2023年净利润为100亿元。"
            assert [message.role for message in messages] == ["user", "assistant"]
            assert messages[0].content == "公司净利润是多少"
            assert "参考资料" not in messages[0].content
            assert messages[1].content == "净利润为100亿元[1]"
            assert [message["content"] for message in redis_messages] == [
                "公司净利润是多少",
                "净利润为100亿元[1]",
            ]
        finally:
            await close_context(session)

    asyncio.run(run_check())


def test_chat_service_stream_with_local_all_route_uses_all_user_kbs(
    monkeypatch,
) -> None:
    async def run_check() -> None:
        session, sessionmaker, redis_cache, user = await build_context()
        kb_id = uuid4()
        document_id = uuid4()
        try:
            monkeypatch.setattr(session_service, "count_message_tokens", lambda _: 1)

            async def fake_candidates(db, user_id):  # noqa: ANN001
                assert user_id == user.id
                return ["candidate"]

            async def fake_route(query, candidates):  # noqa: ANN001
                assert query == "全局搜索公司净利润"
                assert candidates == ["candidate"]
                return LocalFileRoute(route="all")

            async def fake_retrieve_all(db, user_id, query, top_k=5):  # noqa: ANN001
                assert user_id == user.id
                assert query == "全局搜索公司净利润"
                assert top_k == 5
                return [
                    RetrivalChunk(
                        kb_id=kb_id,
                        document_id=document_id,
                        filename="global.pdf",
                        content="全局知识库片段：净利润为200亿元。",
                        score=0.88,
                        chunk_id="global-chunk",
                        chunk_index=2,
                    )
                ]

            async def fail_retrieve_subset(*args, **kwargs):  # noqa: ANN002, ANN003
                raise AssertionError("All route should not require document_ids or kb_ids.")

            async def fake_stream(
                messages: list[dict[str, str]],
            ) -> AsyncGenerator[str, None]:
                prompt = messages[-1]["content"]
                assert "global.pdf" in prompt
                assert "全局知识库片段：净利润为200亿元。" in prompt
                yield "净利润为200亿元[1]"

            monkeypatch.setattr(chat_service, "list_local_document_candidates", fake_candidates)
            monkeypatch.setattr(chat_service, "route_query_to_local_files", fake_route)
            monkeypatch.setattr(chat_service, "retrieve_from_all_user_kbs", fake_retrieve_all)
            monkeypatch.setattr(chat_service, "retrieve_from_kbs", fail_retrieve_subset)
            monkeypatch.setattr(chat_service, "retrieve_from_documents", fail_retrieve_subset)
            monkeypatch.setattr(chat_service.llm_service, "stream_chat_completion", fake_stream)

            chat_session = await create_user_chat_session(session, user.id)
            chunks = [
                chunk
                async for chunk in stream_chat_response(
                    sessionmaker,
                    redis_cache,
                    chat_session.id,
                    user.id,
                    "全局搜索公司净利润",
                    search_mode="local",
                )
            ]
            events = parse_sse_events(chunks)

            assert [event["type"] for event in events] == ["delta", "done"]
            assert events[1]["references"][0]["filename"] == "global.pdf"
        finally:
            await close_context(session)

    asyncio.run(run_check())


def test_chat_service_web_mode_returns_error_without_persisting(monkeypatch) -> None:
    async def run_check() -> None:
        session, sessionmaker, redis_cache, user = await build_context()
        try:
            async def fail_stream(*args, **kwargs):  # noqa: ANN002, ANN003
                raise AssertionError("Web placeholder must not call LLM stream.")
                yield "unreachable"

            monkeypatch.setattr(chat_service.llm_service, "stream_chat_completion", fail_stream)

            chat_session = await create_user_chat_session(session, user.id)
            chunks = [
                chunk
                async for chunk in stream_chat_response(
                    sessionmaker,
                    redis_cache,
                    chat_session.id,
                    user.id,
                    "查一下最新新闻",
                    search_mode="web",
                )
            ]
            events = parse_sse_events(chunks)
            messages = list(
                (
                    await session.execute(
                        select(ChatMessage).where(ChatMessage.session_id == chat_session.id)
                    )
                ).scalars()
            )

            assert events == [
                {
                    "type": "error",
                    "session_id": str(chat_session.id),
                    "done": True,
                    "error": "网络搜索尚未接入",
                }
            ]
            assert messages == []
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
                    user.id,
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

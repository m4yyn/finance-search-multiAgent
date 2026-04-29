import asyncio
import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import ChatMessage, ChatSession, LongTermMemory, User
from app.service import memory_service


async def build_context() -> tuple[AsyncSession, async_sessionmaker[AsyncSession], User]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session = sessionmaker()
    user = User(
        username="memory-service-user",
        email="memory-service-user@example.com",
        hashed_password="hashed",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return session, sessionmaker, user


async def close_context(session: AsyncSession) -> None:
    engine = session.bind
    await session.close()
    if engine is not None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def add_chat_session_with_messages(
    session: AsyncSession,
    user: User,
    count: int = 2,
) -> ChatSession:
    chat_session = ChatSession(user_id=user.id, title="记忆测试")
    session.add(chat_session)
    await session.commit()
    await session.refresh(chat_session)
    for index in range(count):
        session.add(
            ChatMessage(
                session_id=chat_session.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"message {index}",
                tokens=1,
            )
        )
    await session.commit()
    return chat_session


def test_summarize_conversation_parses_strict_json(monkeypatch) -> None:
    async def run_check() -> None:
        session, _, user = await build_context()
        try:
            chat_session = await add_chat_session_with_messages(session, user)
            messages = list(
                (
                    await session.execute(
                        select(ChatMessage).where(
                            ChatMessage.session_id == chat_session.id
                        )
                    )
                ).scalars()
            )

            async def fake_complete(messages_arg):  # noqa: ANN001
                assert messages_arg[0]["role"] == "system"
                assert "长期记忆压缩器" in messages_arg[0]["content"]
                assert "message 0" in messages_arg[1]["content"]
                return json.dumps(
                    {
                        "summary": "用户关注金融研究报告结构。",
                        "key_insights": ["偏好结论先行"],
                    },
                    ensure_ascii=False,
                )

            monkeypatch.setattr(memory_service.llm_service, "complete_chat_json", fake_complete)

            summary, key_insights = await memory_service.summarize_conversation(messages)

            assert summary == "用户关注金融研究报告结构。"
            assert key_insights == ["偏好结论先行"]
        finally:
            await close_context(session)

    asyncio.run(run_check())


def test_create_memory_writes_pg_and_milvus_payload(monkeypatch) -> None:
    async def run_check() -> None:
        session, _, user = await build_context()
        inserted_payloads = []
        try:
            chat_session = await add_chat_session_with_messages(session, user)

            async def fake_summarize(messages):  # noqa: ANN001
                assert len(messages) == 2
                return "用户关注贵州茅台净利润。", ["关注净利润"]

            async def fake_embedding(text):  # noqa: ANN001
                assert "用户关注贵州茅台净利润" in text
                return [1.0, 0.0, 0.0]

            def fake_insert(payloads, **kwargs):  # noqa: ANN001, ANN003
                inserted_payloads.extend(payloads)
                return {"insert_count": len(payloads), "ids": ["memory-vector"]}

            monkeypatch.setattr(memory_service, "summarize_conversation", fake_summarize)
            monkeypatch.setattr(memory_service, "generate_embedding", fake_embedding)
            monkeypatch.setattr(memory_service, "insert_memory_vectors", fake_insert)

            memory = await memory_service.create_memory(session, user.id, chat_session.id)

            assert memory is not None
            assert memory.summary == "用户关注贵州茅台净利润。"
            assert memory.key_insights == ["关注净利润"]
            assert memory.milvus_ids == [f"memory_{memory.id.hex}"]
            assert memory.token_count == 2
            assert inserted_payloads[0]["memory_id"] == str(memory.id)
            assert inserted_payloads[0]["user_id"] == str(user.id)
            assert inserted_payloads[0]["session_id"] == str(chat_session.id)
        finally:
            await close_context(session)

    asyncio.run(run_check())


def test_retrieve_memories_filters_pg_rows_and_builds_context(monkeypatch) -> None:
    async def run_check() -> None:
        session, _, user = await build_context()
        try:
            chat_session = await add_chat_session_with_messages(session, user)
            memory = LongTermMemory(
                user_id=user.id,
                session_id=chat_session.id,
                summary="用户之前关注白酒行业盈利能力。",
                key_insights=["关注毛利率"],
                milvus_ids=["memory-vector-1"],
                token_count=10,
            )
            session.add(memory)
            await session.commit()
            await session.refresh(memory)

            async def fake_embedding(query):  # noqa: ANN001
                assert query == "继续分析盈利能力"
                return [1.0, 0.0, 0.0]

            def fake_search(vector, user_id, limit=3, **kwargs):  # noqa: ANN001, ANN003
                assert vector == [1.0, 0.0, 0.0]
                assert user_id == str(user.id)
                assert limit == 3
                return [
                    {"distance": 0.92, "entity": {"memory_id": str(memory.id)}},
                    {"distance": 0.1, "entity": {"memory_id": str(uuid4())}},
                ]

            monkeypatch.setattr(memory_service, "generate_embedding", fake_embedding)
            monkeypatch.setattr(memory_service, "search_memory_vectors", fake_search)

            results = await memory_service.retrieve_memories(
                session,
                user.id,
                "继续分析盈利能力",
            )
            context = memory_service.build_memory_context(results)

            assert len(results) == 1
            assert results[0].id == memory.id
            assert results[0].score == 0.92
            assert "[相关历史记忆]" in context
            assert "用户之前关注白酒行业盈利能力" in context
            assert "关注毛利率" in context
        finally:
            await close_context(session)

    asyncio.run(run_check())


def test_maybe_create_memory_uses_new_message_threshold(monkeypatch) -> None:
    async def run_check() -> None:
        session, _, user = await build_context()
        inserted_payloads = []
        try:
            chat_session = await add_chat_session_with_messages(session, user, count=19)

            async def fake_summarize(messages):  # noqa: ANN001
                return f"总结 {len(messages)} 条消息", ["达到阈值"]

            async def fake_embedding(text):  # noqa: ANN001
                return [1.0, 0.0, 0.0]

            def fake_insert(payloads, **kwargs):  # noqa: ANN001, ANN003
                inserted_payloads.extend(payloads)
                return {"insert_count": len(payloads), "ids": ["memory-vector"]}

            monkeypatch.setattr(memory_service, "summarize_conversation", fake_summarize)
            monkeypatch.setattr(memory_service, "generate_embedding", fake_embedding)
            monkeypatch.setattr(memory_service, "insert_memory_vectors", fake_insert)

            assert (
                await memory_service.maybe_create_memory_from_session(
                    session,
                    user.id,
                    chat_session.id,
                )
                is None
            )

            session.add(
                ChatMessage(
                    session_id=chat_session.id,
                    role="assistant",
                    content="message 19",
                    tokens=1,
                )
            )
            await session.commit()

            memory = await memory_service.maybe_create_memory_from_session(
                session,
                user.id,
                chat_session.id,
            )

            assert memory is not None
            assert memory.summary == "总结 20 条消息"
            assert len(inserted_payloads) == 1
            assert (
                await memory_service.maybe_create_memory_from_session(
                    session,
                    user.id,
                    chat_session.id,
                )
                is None
            )
        finally:
            await close_context(session)

    asyncio.run(run_check())

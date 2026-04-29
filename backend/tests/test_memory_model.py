import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import ChatSession, LongTermMemory, User


def test_long_term_memory_model_sqlite_compatible_json_and_array_variants() -> None:
    async def run_check() -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with sessionmaker() as session:
            user = User(
                username="memory-user",
                email="memory-user@example.com",
                hashed_password="hashed",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            chat_session = ChatSession(user_id=user.id, title="长期记忆测试")
            session.add(chat_session)
            await session.commit()
            await session.refresh(chat_session)

            memory = LongTermMemory(
                user_id=user.id,
                session_id=chat_session.id,
                summary="用户关注贵州茅台年报分析。",
                key_insights=["关注净利润", "偏好中文结论先行"],
                milvus_ids=["memory-vector-1"],
                token_count=128,
            )
            session.add(memory)
            await session.commit()
            await session.refresh(memory)

            rows = await session.execute(select(LongTermMemory))
            saved = rows.scalar_one()

            assert saved.id is not None
            assert saved.user_id == user.id
            assert saved.session_id == chat_session.id
            assert saved.key_insights == ["关注净利润", "偏好中文结论先行"]
            assert saved.milvus_ids == ["memory-vector-1"]
            assert saved.token_count == 128
            assert saved.created_at is not None

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    asyncio.run(run_check())

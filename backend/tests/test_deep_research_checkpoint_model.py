import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import ChatSession, DeepResearchCheckpoint, User


def test_deep_research_checkpoint_model_persists_latest_state() -> None:
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
                username="checkpoint-model-user",
                email="checkpoint-model-user@example.com",
                hashed_password="hashed",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            chat_session = ChatSession(user_id=user.id, title="深度研究")
            session.add(chat_session)
            await session.commit()
            await session.refresh(chat_session)
            user_id = user.id
            chat_session_id = chat_session.id

            checkpoint = DeepResearchCheckpoint(
                user_id=user_id,
                session_id=chat_session_id,
                query="分析新能源车行业",
                phase="planning",
                iteration=0,
                max_iterations=3,
                state_json={
                    "query": "分析新能源车行业",
                    "phase_outputs": [{"phase": "planning"}],
                    "agent_outputs": [{"agent": "architect"}],
                },
                ui_state_json={"steps": ["planning"]},
                status="running",
            )
            session.add(checkpoint)
            await session.commit()
            await session.refresh(checkpoint)

            saved = (await session.execute(select(DeepResearchCheckpoint))).scalar_one()

            assert saved.user_id == user_id
            assert saved.session_id == chat_session_id
            assert saved.state_json["phase_outputs"][0]["phase"] == "planning"
            assert saved.state_json["agent_outputs"][0]["agent"] == "architect"
            assert saved.ui_state_json == {"steps": ["planning"]}
            assert saved.status == "running"
            assert saved.created_at is not None
            assert saved.updated_at is not None

            duplicate = DeepResearchCheckpoint(
                user_id=user_id,
                session_id=chat_session_id,
                query="重复 checkpoint",
                phase="researching",
                iteration=1,
                max_iterations=3,
                state_json={},
                status="running",
            )
            session.add(duplicate)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

            invalid_chat_session = ChatSession(user_id=user_id, title="非法状态测试")
            session.add(invalid_chat_session)
            await session.commit()
            await session.refresh(invalid_chat_session)

            invalid_status = DeepResearchCheckpoint(
                user_id=user_id,
                session_id=invalid_chat_session.id,
                query="错误状态",
                phase="planning",
                iteration=0,
                max_iterations=3,
                state_json={},
                status="invalid",
            )
            session.add(invalid_status)
            with pytest.raises(IntegrityError):
                await session.commit()

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    asyncio.run(run_check())

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import ChatSession, User
from app.service import checkpoint_service
from app.service.deep_research.state import ResearchPhase, create_initial_state


async def build_context() -> tuple[AsyncSession, User, User, ChatSession, ChatSession]:
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
        username="checkpoint-user",
        email="checkpoint-user@example.com",
        hashed_password="hashed",
    )
    other_user = User(
        username="checkpoint-other-user",
        email="checkpoint-other-user@example.com",
        hashed_password="hashed",
    )
    session.add_all([user, other_user])
    await session.commit()
    await session.refresh(user)
    await session.refresh(other_user)

    chat_session = ChatSession(user_id=user.id, title="深度研究")
    other_session = ChatSession(user_id=other_user.id, title="其他用户研究")
    session.add_all([chat_session, other_session])
    await session.commit()
    await session.refresh(chat_session)
    await session.refresh(other_session)
    return session, user, other_user, chat_session, other_session


async def close_context(session: AsyncSession) -> None:
    engine = session.bind
    await session.close()
    if engine is not None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def test_checkpoint_service_upserts_deep_research_state_only() -> None:
    async def run_check() -> None:
        session, user, _, chat_session, _ = await build_context()
        try:
            state = create_initial_state(
                "分析券商行业估值",
                user_id=user.id,
                session_id=chat_session.id,
            )
            state["phase"] = ResearchPhase.PLANNING.value
            state["phase_outputs"].append({"phase": "planning", "sections": 4})
            state["agent_outputs"].append({"agent": "architect", "summary": "完成规划"})
            state["agent_events"].append({"type": "phase_done", "agent": "architect"})
            state["_message_queue"] = object()  # type: ignore[typeddict-unknown-key]

            checkpoint_id = await checkpoint_service.save_checkpoint(
                session,
                user.id,
                chat_session.id,
                state,
                ui_state={"research_steps": [{"type": "planning"}]},
            )

            assert checkpoint_id is not None
            loaded = await checkpoint_service.load_checkpoint(
                session,
                user.id,
                chat_session.id,
            )
            assert loaded is not None
            assert loaded["query"] == "分析券商行业估值"
            assert loaded["phase_outputs"] == [{"phase": "planning", "sections": 4}]
            assert loaded["agent_outputs"] == [{"agent": "architect", "summary": "完成规划"}]
            assert loaded["agent_events"] == [{"type": "phase_done", "agent": "architect"}]
            assert "_message_queue" not in loaded
            assert "messages" not in loaded

            state["phase"] = ResearchPhase.RESEARCHING.value
            state["iteration"] = 1
            state["phase_outputs"].append({"phase": "researching", "facts": 8})
            second_id = await checkpoint_service.save_checkpoint(
                session,
                user.id,
                chat_session.id,
                state,
                final_report="阶段报告",
            )
            assert second_id == checkpoint_id

            full = await checkpoint_service.load_full_checkpoint(
                session,
                user.id,
                chat_session.id,
            )
            assert full is not None
            assert full["phase"] == "researching"
            assert full["iteration"] == 1
            assert full["final_report"] == "阶段报告"
            assert len(full["state_json"]["phase_outputs"]) == 2
            assert full["ui_state_json"] == {"research_steps": [{"type": "planning"}]}

            infos = await checkpoint_service.list_checkpoints(session, user.id)
            assert len(infos) == 1
            assert infos[0]["session_id"] == str(chat_session.id)
            assert "state_json" not in infos[0]
        finally:
            await close_context(session)

    asyncio.run(run_check())


def test_checkpoint_service_filters_by_user_and_updates_status() -> None:
    async def run_check() -> None:
        session, user, other_user, chat_session, other_session = await build_context()
        try:
            state = create_initial_state(
                "分析银行资产质量",
                session_id=chat_session.id,
                user_id=user.id,
            )
            other_state = create_initial_state(
                "分析保险行业",
                session_id=other_session.id,
                user_id=other_user.id,
            )

            assert await checkpoint_service.save_checkpoint(
                session,
                user.id,
                chat_session.id,
                state,
            )
            assert await checkpoint_service.save_checkpoint(
                session,
                other_user.id,
                other_session.id,
                other_state,
            )

            assert (
                await checkpoint_service.load_checkpoint(
                    session,
                    other_user.id,
                    chat_session.id,
                )
                is None
            )

            updated = await checkpoint_service.update_status(
                session,
                user.id,
                chat_session.id,
                "failed",
                error_message="agent failed",
            )
            assert updated is True

            info = await checkpoint_service.get_checkpoint_info(
                session,
                user.id,
                chat_session.id,
            )
            assert info is not None
            assert info["status"] == "failed"
            assert info["error_message"] == "agent failed"

            failed = await checkpoint_service.list_checkpoints(
                session,
                user.id,
                status="failed",
            )
            assert len(failed) == 1

            assert await checkpoint_service.delete_checkpoint(
                session,
                user.id,
                chat_session.id,
            )
            assert (
                await checkpoint_service.load_checkpoint(
                    session,
                    user.id,
                    chat_session.id,
                )
                is None
            )
        finally:
            await close_context(session)

    asyncio.run(run_check())

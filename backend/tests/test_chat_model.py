import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models import ChatMessage, ChatSession, User


def test_chat_models_can_persist_sessions_and_messages() -> None:
    async def run_check() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with sessionmaker() as session:
            user = User(
                username="chat-user",
                email="chat-user@example.com",
                hashed_password="hashed",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            chat_session = ChatSession(user_id=user.id)
            session.add(chat_session)
            await session.commit()
            await session.refresh(chat_session)

            message = ChatMessage(
                session_id=chat_session.id,
                role="user",
                content="hello",
                tokens=1,
            )
            session.add(message)
            await session.commit()
            await session.refresh(message)

            saved_message = (
                await session.execute(
                    select(ChatMessage).where(ChatMessage.session_id == chat_session.id)
                )
            ).scalar_one()

            assert isinstance(chat_session.id, UUID)
            assert chat_session.user_id == user.id
            assert chat_session.title == "新会话"
            assert chat_session.is_active is True
            assert chat_session.created_at is not None
            assert chat_session.updated_at is not None
            assert saved_message.role == "user"
            assert saved_message.tokens == 1
            assert saved_message.created_at is not None

        await engine.dispose()

    asyncio.run(run_check())

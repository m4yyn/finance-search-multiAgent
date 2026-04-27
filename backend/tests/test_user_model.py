import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models import User


def test_user_model_can_be_created_with_uuid_primary_key() -> None:
    async def run_check() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with sessionmaker() as session:
            user = User(
                username="alice",
                email="alice@example.com",
                hashed_password="hashed",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            saved_user = (
                await session.execute(select(User).where(User.username == "alice"))
            ).scalar_one()

            assert isinstance(saved_user.id, UUID)
            assert saved_user.email == "alice@example.com"
            assert saved_user.is_active is True
            assert saved_user.is_superuser is False
            assert saved_user.created_at is not None
            assert saved_user.updated_at is not None

        await engine.dispose()

    asyncio.run(run_check())


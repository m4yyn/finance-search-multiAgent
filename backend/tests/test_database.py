import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import database


def test_create_database_engine_and_sessionmaker_support_async_sqlite() -> None:
    async def run_check() -> None:
        engine = database.create_database_engine("sqlite+aiosqlite:///:memory:")
        sessionmaker = database.create_sessionmaker(engine)

        assert isinstance(sessionmaker, async_sessionmaker)

        async with sessionmaker() as session:
            result = await session.execute(text("select 1"))
            assert result.scalar_one() == 1

        await engine.dispose()

    asyncio.run(run_check())


def test_postgres_dsn_is_converted_to_asyncpg_url() -> None:
    assert database.normalize_database_url(
        "postgresql://user:pass@localhost:5432/finance_assistant"
    ) == "postgresql+asyncpg://user:pass@localhost:5432/finance_assistant"


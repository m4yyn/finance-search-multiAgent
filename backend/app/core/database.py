from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config.settings import get_settings


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models and Alembic metadata."""


def normalize_database_url(database_url: str) -> str:
    """Convert common PostgreSQL URLs to SQLAlchemy's asyncpg dialect URL."""
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url.removeprefix("postgresql://")
    return database_url


def create_database_engine(database_url: str | None = None) -> AsyncEngine:
    """Create an async SQLAlchemy engine without opening a connection eagerly."""
    settings = get_settings()
    url = normalize_database_url(database_url or settings.postgres_dsn)
    return create_async_engine(url, pool_pre_ping=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build the async session factory used by request dependencies and tests."""
    return async_sessionmaker(engine, expire_on_commit=False)


@lru_cache
def get_database_engine() -> AsyncEngine:
    return create_database_engine()


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return create_sessionmaker(get_database_engine())


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields one database session per request."""
    async with get_sessionmaker()() as session:
        yield session


# Backward-compatible alias for the initial scaffold.
get_db_session = get_db

import asyncio
from collections.abc import AsyncGenerator, Generator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.redis_client import get_redis_cache
from app.core.security import create_access_token
from app.main import create_app
from app.models import User
from app.schemas.user import Token, UserResponse


class FakeRedisCache:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, str]] = {}

    async def set_session(
        self,
        session_id: str,
        data: dict[str, str],
        expire_seconds: int | None = None,
    ) -> bool:
        self.sessions[session_id] = data
        return True

    async def get_session(self, session_id: str) -> dict[str, str] | None:
        return self.sessions.get(session_id)

    async def delete(self, key: str) -> bool:
        self.sessions.pop(key, None)
        return True


@pytest.fixture()
def auth_client() -> Generator[
    tuple[TestClient, FakeRedisCache, async_sessionmaker[AsyncSession]],
    None,
    None,
]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    redis_cache = FakeRedisCache()

    async def create_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def drop_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessionmaker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis_cache] = lambda: redis_cache

    asyncio.run(create_tables())
    with TestClient(app) as client:
        yield client, redis_cache, sessionmaker
    app.dependency_overrides.clear()
    asyncio.run(drop_tables())


def test_public_user_schemas_do_not_expose_hashed_password() -> None:
    assert "hashed_password" not in UserResponse.model_fields
    assert "hashed_password" not in Token.model_fields
    assert set(Token.model_fields) == {"access_token", "token_type"}


def test_register_login_and_me_flow(
    auth_client: tuple[TestClient, FakeRedisCache, async_sessionmaker[AsyncSession]],
) -> None:
    client, redis_cache, _ = auth_client

    register_response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "correct-password",
        },
    )
    assert register_response.status_code == 201
    assert register_response.json()["username"] == "alice"
    assert "hashed_password" not in register_response.json()

    duplicate_response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice-copy@example.com",
            "password": "correct-password",
        },
    )
    assert duplicate_response.status_code == 409

    login_response = client.post(
        "/api/v1/auth/login",
        json={"username_or_email": "alice", "password": "correct-password"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    assert login_response.json()["token_type"] == "bearer"
    assert redis_cache.sessions

    me_response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "alice@example.com"

    logout_response = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert logout_response.status_code == 204
    assert redis_cache.sessions == {}


def test_login_rejects_bad_password(
    auth_client: tuple[TestClient, FakeRedisCache, async_sessionmaker[AsyncSession]],
) -> None:
    client, _, _ = auth_client
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "bob",
            "email": "bob@example.com",
            "password": "correct-password",
        },
    )

    response = client.post(
        "/api/v1/auth/login",
        json={"username_or_email": "bob@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401


def test_me_rejects_invalid_token(
    auth_client: tuple[TestClient, FakeRedisCache, async_sessionmaker[AsyncSession]],
) -> None:
    client, _, _ = auth_client

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not-a-valid-token"},
    )

    assert response.status_code == 401


def test_me_rejects_missing_user(
    auth_client: tuple[TestClient, FakeRedisCache, async_sessionmaker[AsyncSession]],
) -> None:
    client, _, _ = auth_client
    token = create_access_token(str(uuid4()))

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_me_rejects_inactive_user(
    auth_client: tuple[TestClient, FakeRedisCache, async_sessionmaker[AsyncSession]],
) -> None:
    client, _, sessionmaker = auth_client
    register_response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "carol",
            "email": "carol@example.com",
            "password": "correct-password",
        },
    )
    assert register_response.status_code == 201
    user_id = register_response.json()["id"]

    async def deactivate_user() -> None:
        async with sessionmaker() as session:
            await session.execute(
                update(User)
                .where(User.id == UUID(user_id))
                .values(is_active=False)
            )
            await session.commit()

    asyncio.run(deactivate_user())
    token = create_access_token(user_id)

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403

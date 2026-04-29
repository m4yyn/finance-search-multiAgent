import asyncio
from collections.abc import AsyncGenerator, Generator
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import create_app
from app.models import LongTermMemory, User
from app.router import memory_router
from app.schemas.memory import MemorySearchResult


@pytest.fixture()
def memory_client(monkeypatch) -> Generator[tuple[TestClient, str, str], None, None]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def create_tables_and_users() -> tuple[str, str]:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with sessionmaker() as session:
            owner = User(
                username="memory-owner",
                email="memory-owner@example.com",
                hashed_password="hashed",
            )
            other = User(
                username="memory-other",
                email="memory-other@example.com",
                hashed_password="hashed",
            )
            session.add_all([owner, other])
            await session.commit()
            await session.refresh(owner)
            await session.refresh(other)
            return str(owner.id), str(other.id)

    async def drop_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessionmaker() as session:
            yield session

    owner_id, other_id = asyncio.run(create_tables_and_users())
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client, create_access_token(owner_id), create_access_token(other_id)
    app.dependency_overrides.clear()
    asyncio.run(drop_tables())


def test_memory_router_requires_authentication(memory_client) -> None:
    client, _, _ = memory_client

    response = client.post("/api/v1/memories/search", json={"query": "净利润"})

    assert response.status_code == 401


def test_memory_router_create_search_and_context(memory_client, monkeypatch) -> None:
    client, owner_token, _ = memory_client
    headers = {"Authorization": f"Bearer {owner_token}"}
    session_id = uuid4()
    memory_id = uuid4()
    user_id_holder = {}

    async def fake_create_memory(db, user_id, session_id_arg):  # noqa: ANN001
        user_id_holder["id"] = user_id
        assert session_id_arg == session_id
        return LongTermMemory(
            id=memory_id,
            user_id=user_id,
            session_id=session_id,
            summary="用户关注新能源行业盈利能力。",
            key_insights=["关注净利润"],
            milvus_ids=["memory-vector-1"],
            token_count=128,
            created_at=datetime.now(timezone.utc),
        )

    async def fake_retrieve(db, user_id, query, top_k=3):  # noqa: ANN001
        assert user_id == user_id_holder["id"]
        assert query in {"新能源盈利能力", "新能源"}
        assert top_k in {3, 5}
        return [
            MemorySearchResult(
                id=memory_id,
                user_id=user_id,
                session_id=session_id,
                summary="用户关注新能源行业盈利能力。",
                key_insights=["关注净利润"],
                milvus_ids=["memory-vector-1"],
                token_count=128,
                created_at="2026-04-29T00:00:00Z",
                score=0.91,
            )
        ]

    monkeypatch.setattr(memory_router, "create_memory", fake_create_memory)
    monkeypatch.setattr(memory_router, "retrieve_memories", fake_retrieve)

    create_response = client.post(
        "/api/v1/memories/create",
        json={"session_id": str(session_id)},
        headers=headers,
    )
    assert create_response.status_code == 201
    assert create_response.json()["summary"] == "用户关注新能源行业盈利能力。"
    assert create_response.json()["milvus_ids"] == ["memory-vector-1"]

    search_response = client.post(
        "/api/v1/memories/search",
        json={"query": "新能源盈利能力", "top_k": 5},
        headers=headers,
    )
    assert search_response.status_code == 200
    assert search_response.json()[0]["score"] == 0.91

    context_response = client.get(
        "/api/v1/memories/context/新能源",
        headers=headers,
    )
    assert context_response.status_code == 200
    assert "[相关历史记忆]" in context_response.json()["context"]
    assert context_response.json()["memories"][0]["id"] == str(memory_id)


def test_memory_router_create_missing_session_returns_404(memory_client, monkeypatch) -> None:
    client, owner_token, _ = memory_client
    headers = {"Authorization": f"Bearer {owner_token}"}

    async def fake_create_memory(db, user_id, session_id):  # noqa: ANN001
        return None

    monkeypatch.setattr(memory_router, "create_memory", fake_create_memory)

    response = client.post(
        "/api/v1/memories/create",
        json={"session_id": str(uuid4())},
        headers=headers,
    )

    assert response.status_code == 404


def test_memory_openapi_exposes_memory_paths(memory_client) -> None:
    client, _, _ = memory_client

    response = client.get("/openapi.json")
    paths = response.json()["paths"]

    assert "/api/v1/memories/create" in paths
    assert "/api/v1/memories/search" in paths
    assert "/api/v1/memories/context/{query}" in paths

import asyncio
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config.settings import get_settings
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import create_app
from app.models import Document, KnowledgeBase, User
from app.router import knowledge_router


@pytest.fixture()
def knowledge_client(monkeypatch, tmp_path) -> Generator:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    deleted_collections: list[str] = []

    async def create_tables_and_users() -> tuple[str, str]:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessionmaker() as session:
            owner = User(
                username="owner",
                email="owner@example.com",
                hashed_password="hashed",
            )
            other = User(
                username="other",
                email="other@example.com",
                hashed_password="hashed",
            )
            session.add_all([owner, other])
            await session.commit()
            await session.refresh(owner)
            await session.refresh(other)
            return str(owner.id), str(other.id)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessionmaker() as session:
            yield session

    async def drop_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    monkeypatch.setattr(
        knowledge_router,
        "delete_collection",
        lambda collection_name: deleted_collections.append(collection_name) or True,
    )
    monkeypatch.setattr(knowledge_router, "count_collection_rows", lambda _: 2)
    owner_id, other_id = asyncio.run(create_tables_and_users())
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, sessionmaker, deleted_collections, owner_id, other_id, tmp_path
    app.dependency_overrides.clear()
    asyncio.run(drop_tables())
    get_settings.cache_clear()


def auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_knowledge_router_creates_lists_and_deletes_kb(knowledge_client) -> None:
    client, sessionmaker, deleted_collections, owner_id, _, tmp_path = knowledge_client
    headers = auth_headers(owner_id)

    create_response = client.post(
        "/api/v1/knowledge/bases",
        json={"name": "  研报库  ", "description": "  描述  "},
        headers=headers,
    )
    assert create_response.status_code == 201
    kb = create_response.json()
    assert kb["name"] == "研报库"
    assert kb["collection_name"] == f"kb_{UUID(kb['id']).hex}"

    list_response = client.get("/api/v1/knowledge/bases", headers=headers)
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [kb["id"]]

    async def add_document_file() -> Path:
        async with sessionmaker() as session:
            file_path = tmp_path / "owned.pdf"
            file_path.write_text("content", encoding="utf-8")
            document = Document(
                kb_id=UUID(kb["id"]),
                filename="owned.pdf",
                file_path=str(file_path),
                file_size=7,
                mime_type="application/pdf",
                status="success",
                chunk_count=2,
            )
            session.add(document)
            await session.commit()
            return file_path

    file_path = asyncio.run(add_document_file())
    stats_response = client.get(
        f"/api/v1/knowledge/bases/{kb['id']}/stats",
        headers=headers,
    )
    assert stats_response.status_code == 200
    assert stats_response.json()["pg_chunk_count"] == 2
    assert stats_response.json()["milvus_chunk_count"] == 2

    delete_response = client.delete(
        f"/api/v1/knowledge/bases/{kb['id']}",
        headers=headers,
    )

    assert delete_response.status_code == 204
    assert deleted_collections == [kb["collection_name"]]
    assert not file_path.exists()

    async def count_kbs() -> int:
        async with sessionmaker() as session:
            return len((await session.execute(KnowledgeBase.__table__.select())).all())

    assert asyncio.run(count_kbs()) == 0


def test_knowledge_router_hides_other_users_kb(knowledge_client) -> None:
    client, _, _, owner_id, other_id, _ = knowledge_client
    create_response = client.post(
        "/api/v1/knowledge/bases",
        json={"name": "owner kb"},
        headers=auth_headers(owner_id),
    )
    kb_id = create_response.json()["id"]

    response = client.delete(
        f"/api/v1/knowledge/bases/{kb_id}",
        headers=auth_headers(other_id),
    )

    assert response.status_code == 404

import asyncio
from collections.abc import AsyncGenerator, Generator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config.settings import get_settings
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import create_app
from app.models import Document, KnowledgeBase, User
from app.router import document_router


@pytest.fixture()
def document_client(monkeypatch, tmp_path) -> Generator:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    ingested: list[str] = []
    deleted_chunks: list[tuple[str, str]] = []

    async def create_tables_and_data() -> tuple[str, str, str, str]:
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
            kb = KnowledgeBase(
                user_id=owner.id,
                name="KB",
                collection_name=f"kb_{owner.id.hex}",
            )
            session.add(kb)
            await session.commit()
            await session.refresh(kb)
            return str(owner.id), str(other.id), str(kb.id), kb.collection_name

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessionmaker() as session:
            yield session

    async def fake_ingest(document_id):
        ingested.append(str(document_id))

    def fake_delete_chunks(collection_name, document_id):
        deleted_chunks.append((collection_name, document_id))
        return {"delete_count": 1}

    async def drop_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    monkeypatch.setattr(document_router, "ingest_document", fake_ingest)
    monkeypatch.setattr(document_router, "delete_document_chunks", fake_delete_chunks)
    owner_id, other_id, kb_id, collection_name = asyncio.run(create_tables_and_data())
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield (
            client,
            sessionmaker,
            ingested,
            deleted_chunks,
            owner_id,
            other_id,
            kb_id,
            collection_name,
        )
    app.dependency_overrides.clear()
    asyncio.run(drop_tables())
    get_settings.cache_clear()


def auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_document_router_uploads_lists_and_deletes_document(document_client) -> None:
    (
        client,
        sessionmaker,
        ingested,
        deleted_chunks,
        owner_id,
        _,
        kb_id,
        collection_name,
    ) = document_client
    headers = auth_headers(owner_id)

    upload_response = client.post(
        f"/api/v1/knowledge/bases/{kb_id}/documents",
        files={"file": ("report.pdf", b"%PDF-1.4", "application/pdf")},
        headers=headers,
    )
    assert upload_response.status_code == 201
    document = upload_response.json()
    assert document["status"] == "pending"
    assert document["filename"] == "report.pdf"
    assert "file_path" not in document
    assert ingested == [document["id"]]

    list_response = client.get(
        f"/api/v1/knowledge/bases/{kb_id}/documents",
        headers=headers,
    )
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [document["id"]]

    async def saved_file_exists() -> bool:
        async with sessionmaker() as session:
            saved = await session.get(Document, UUID(document["id"]))
            return saved is not None and __import__("pathlib").Path(saved.file_path).exists()

    assert asyncio.run(saved_file_exists())

    delete_response = client.delete(
        f"/api/v1/knowledge/bases/{kb_id}/documents/{document['id']}",
        headers=headers,
    )
    assert delete_response.status_code == 204
    assert deleted_chunks == [(collection_name, document["id"])]

    async def document_exists() -> bool:
        async with sessionmaker() as session:
            return (
                await session.scalar(select(Document).where(Document.id == UUID(document["id"])))
            ) is not None

    assert not asyncio.run(document_exists())


def test_document_router_rejects_invalid_extension(document_client) -> None:
    client, _, _, _, owner_id, _, kb_id, _ = document_client

    response = client.post(
        f"/api/v1/knowledge/bases/{kb_id}/documents",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        headers=auth_headers(owner_id),
    )

    assert response.status_code == 400


def test_document_router_hides_other_users_kb(document_client) -> None:
    client, _, _, _, _, other_id, kb_id, _ = document_client

    response = client.get(
        f"/api/v1/knowledge/bases/{kb_id}/documents",
        headers=auth_headers(other_id),
    )

    assert response.status_code == 404

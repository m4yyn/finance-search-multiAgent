import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models import Document, KnowledgeBase, User
from app.service import document_service


async def setup_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with sessionmaker() as session:
        user = User(
            username="document-user",
            email="document-user@example.com",
            hashed_password="hashed",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        kb = KnowledgeBase(
            user_id=user.id,
            name="知识库",
            collection_name=f"kb_{user.id.hex}",
        )
        session.add(kb)
        await session.commit()
        await session.refresh(kb)

        document = Document(
            kb_id=kb.id,
            filename="demo.pdf",
            file_path="./data/uploads/demo.pdf",
            file_size=10,
            mime_type="application/pdf",
        )
        session.add(document)
        await session.commit()
        await session.refresh(document)
        return engine, sessionmaker, document.id, kb.collection_name


def test_process_document_success(monkeypatch) -> None:
    async def run_check() -> None:
        engine, sessionmaker, document_id, collection_name = await setup_db()
        inserted: dict = {}
        deleted: list[tuple[str, str]] = []
        try:
            monkeypatch.setattr(
                document_service,
                "parse_document_to_chunks",
                lambda *_: [
                    {"content": "第一段", "source_type": "pdf", "chunk_index": 0},
                    {"content": "第二段", "source_type": "pdf", "chunk_index": 1},
                ],
            )

            async def fake_embedding(texts):
                return [[1.0, 0.0, 0.0] for _ in texts]

            monkeypatch.setattr(document_service, "generate_embedding", fake_embedding)
            monkeypatch.setattr(document_service, "create_collection", lambda name: True)
            monkeypatch.setattr(
                document_service,
                "delete_document_chunks",
                lambda name, doc_id: deleted.append((name, doc_id)) or {"delete_count": 0},
            )
            monkeypatch.setattr(
                document_service,
                "batch_insert",
                lambda name, chunks: inserted.update({"name": name, "chunks": chunks})
                or {"insert_count": len(chunks)},
            )

            result = await document_service.process_document(
                document_id,
                sessionmaker=sessionmaker,
            )

            async with sessionmaker() as session:
                saved = await session.get(Document, document_id)
                assert saved.status == "success"
                assert saved.chunk_count == 2
                assert saved.error_message is None
            assert result.status == "success"
            assert inserted["name"] == collection_name
            assert len(inserted["chunks"]) == 2
            assert inserted["chunks"][0]["document_id"] == str(document_id)
            assert inserted["chunks"][0]["content"] == "第一段"
            assert deleted == [(collection_name, str(document_id))]
        finally:
            await engine.dispose()

    asyncio.run(run_check())


def test_process_document_marks_failed_on_error(monkeypatch) -> None:
    async def run_check() -> None:
        engine, sessionmaker, document_id, collection_name = await setup_db()
        deleted: list[tuple[str, str]] = []
        try:
            monkeypatch.setattr(
                document_service,
                "parse_document_to_chunks",
                lambda *_: (_ for _ in ()).throw(RuntimeError("parse failed")),
            )
            monkeypatch.setattr(
                document_service,
                "delete_document_chunks",
                lambda name, doc_id: deleted.append((name, doc_id)) or {"delete_count": 0},
            )

            result = await document_service.process_document(
                document_id,
                sessionmaker=sessionmaker,
            )

            async with sessionmaker() as session:
                saved = await session.scalar(select(Document).where(Document.id == document_id))
                assert saved.status == "failed"
                assert "parse failed" in saved.error_message
                assert saved.chunk_count is None
            assert result.status == "failed"
            assert deleted == [(collection_name, str(document_id))]
        finally:
            await engine.dispose()

    asyncio.run(run_check())


def test_process_document_missing_document_raises(monkeypatch) -> None:
    async def run_check() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        try:
            missing_id = UUID("00000000-0000-0000-0000-000000000001")
            try:
                await document_service.process_document(
                    missing_id,
                    sessionmaker=sessionmaker,
                )
            except RuntimeError as exc:
                assert "Document not found" in str(exc)
            else:
                raise AssertionError("Expected RuntimeError")
        finally:
            await engine.dispose()

    asyncio.run(run_check())

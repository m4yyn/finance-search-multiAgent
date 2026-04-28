import asyncio
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models import Document, KnowledgeBase, User
from app.service import retrieval_service


async def setup_retrieval_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
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

        kb1 = KnowledgeBase(
            user_id=owner.id,
            name="KB1",
            collection_name="kb_one",
        )
        kb2 = KnowledgeBase(
            user_id=owner.id,
            name="KB2",
            collection_name="kb_two",
        )
        other_kb = KnowledgeBase(
            user_id=other.id,
            name="Other",
            collection_name="kb_other",
        )
        session.add_all([kb1, kb2, other_kb])
        await session.commit()
        await session.refresh(kb1)
        await session.refresh(kb2)
        await session.refresh(other_kb)
        return engine, sessionmaker, owner.id, kb1.id, kb2.id, other_kb.id


def test_retrieve_from_single_kb_maps_milvus_results(monkeypatch) -> None:
    async def run_check() -> None:
        engine, sessionmaker, user_id, kb_id, _, _ = await setup_retrieval_db()
        try:
            async def fake_embedding(query):
                assert query == "贵州茅台"
                return [1.0, 0.0, 0.0]

            def fake_search(collection_name, query_vector, **kwargs):
                assert collection_name == "kb_one"
                assert query_vector == [1.0, 0.0, 0.0]
                assert kwargs["limit"] == 2
                return [
                    {
                        "distance": 0.91,
                        "entity": {
                            "chunk_id": "chunk-1",
                            "document_id": str(uuid4()),
                            "kb_id": str(kb_id),
                            "filename": "report.pdf",
                            "content": "贵州茅台主营白酒。",
                            "chunk_index": 3,
                            "page_number": 5,
                            "source_type": "pdf",
                        },
                    }
                ]

            monkeypatch.setattr(retrieval_service, "generate_embedding", fake_embedding)
            monkeypatch.setattr(retrieval_service, "vector_search", fake_search)

            async with sessionmaker() as session:
                chunks = await retrieval_service.retrieve_from_kb(
                    session,
                    user_id,
                    kb_id,
                    "贵州茅台",
                    top_k=2,
                )

            assert len(chunks) == 1
            assert chunks[0].filename == "report.pdf"
            assert chunks[0].score == 0.91
            assert chunks[0].chunk_index == 3
            assert chunks[0].page_number == 5
            assert chunks[0].metadata["source_type"] == "pdf"
        finally:
            await engine.dispose()

    asyncio.run(run_check())


def test_retrieve_from_kbs_merges_and_sorts(monkeypatch) -> None:
    async def run_check() -> None:
        engine, sessionmaker, user_id, kb1_id, kb2_id, _ = await setup_retrieval_db()
        document_id = uuid4()
        try:
            async def fake_embedding(query):
                return [1.0, 0.0, 0.0]

            def fake_search(collection_name, query_vector, **kwargs):
                score = 0.7 if collection_name == "kb_one" else 0.95
                kb_id = kb1_id if collection_name == "kb_one" else kb2_id
                return [
                    {
                        "distance": score,
                        "entity": {
                            "chunk_id": f"{collection_name}-chunk",
                            "document_id": str(document_id),
                            "kb_id": str(kb_id),
                            "filename": f"{collection_name}.pdf",
                            "content": collection_name,
                        },
                    }
                ]

            monkeypatch.setattr(retrieval_service, "generate_embedding", fake_embedding)
            monkeypatch.setattr(retrieval_service, "vector_search", fake_search)

            async with sessionmaker() as session:
                chunks = await retrieval_service.retrieve_from_kbs(
                    session,
                    user_id,
                    [kb1_id, kb2_id],
                    "query",
                    top_k=1,
                )

            assert len(chunks) == 1
            assert chunks[0].kb_id == kb2_id
            assert chunks[0].score == 0.95
        finally:
            await engine.dispose()

    asyncio.run(run_check())


def test_retrieve_from_all_user_kbs_searches_owned_kbs_only(monkeypatch) -> None:
    async def run_check() -> None:
        engine, sessionmaker, user_id, kb1_id, kb2_id, other_kb_id = (
            await setup_retrieval_db()
        )
        document_id = uuid4()
        searched_collections: list[str] = []
        embedding_calls = 0
        try:
            async def fake_embedding(query):
                nonlocal embedding_calls
                embedding_calls += 1
                assert query == "global query"
                return [1.0, 0.0, 0.0]

            def fake_search(collection_name, query_vector, **kwargs):
                searched_collections.append(collection_name)
                kb_id = kb1_id if collection_name == "kb_one" else kb2_id
                score = 0.8 if collection_name == "kb_one" else 0.9
                return [
                    {
                        "distance": score,
                        "entity": {
                            "chunk_id": f"{collection_name}-chunk",
                            "document_id": str(document_id),
                            "kb_id": str(kb_id),
                            "filename": f"{collection_name}.pdf",
                            "content": collection_name,
                        },
                    }
                ]

            monkeypatch.setattr(retrieval_service, "generate_embedding", fake_embedding)
            monkeypatch.setattr(retrieval_service, "vector_search", fake_search)

            async with sessionmaker() as session:
                chunks = await retrieval_service.retrieve_from_all_user_kbs(
                    session,
                    user_id,
                    "global query",
                    top_k=5,
                )

            assert embedding_calls == 1
            assert set(searched_collections) == {"kb_one", "kb_two"}
            assert "kb_other" not in searched_collections
            assert [chunk.kb_id for chunk in chunks] == [kb2_id, kb1_id]
            assert other_kb_id not in {chunk.kb_id for chunk in chunks}
        finally:
            await engine.dispose()

    asyncio.run(run_check())


def test_retrieve_from_documents_filters_owned_success_documents(monkeypatch) -> None:
    async def run_check() -> None:
        engine, sessionmaker, user_id, kb1_id, kb2_id, other_kb_id = (
            await setup_retrieval_db()
        )
        document_id = uuid4()
        second_document_id = uuid4()
        failed_document_id = uuid4()
        other_document_id = uuid4()
        searched: list[tuple[str, str]] = []
        try:
            async with sessionmaker() as session:
                session.add_all(
                    [
                        Document(
                            id=document_id,
                            kb_id=kb1_id,
                            filename="one.pdf",
                            file_path="/tmp/one.pdf",
                            file_size=1,
                            mime_type="application/pdf",
                            status="success",
                        ),
                        Document(
                            id=second_document_id,
                            kb_id=kb2_id,
                            filename="two.pdf",
                            file_path="/tmp/two.pdf",
                            file_size=1,
                            mime_type="application/pdf",
                            status="success",
                        ),
                        Document(
                            id=failed_document_id,
                            kb_id=kb1_id,
                            filename="failed.pdf",
                            file_path="/tmp/failed.pdf",
                            file_size=1,
                            mime_type="application/pdf",
                            status="failed",
                        ),
                        Document(
                            id=other_document_id,
                            kb_id=other_kb_id,
                            filename="other.pdf",
                            file_path="/tmp/other.pdf",
                            file_size=1,
                            mime_type="application/pdf",
                            status="success",
                        ),
                    ]
                )
                await session.commit()

            async def fake_embedding(query):
                assert query == "doc query"
                return [1.0, 0.0, 0.0]

            def fake_search(collection_name, query_vector, **kwargs):
                searched.append((collection_name, kwargs["filter_expr"]))
                kb_id = kb1_id if collection_name == "kb_one" else kb2_id
                doc_id = document_id if collection_name == "kb_one" else second_document_id
                return [
                    {
                        "distance": 0.9,
                        "entity": {
                            "chunk_id": f"{collection_name}-chunk",
                            "document_id": str(doc_id),
                            "kb_id": str(kb_id),
                            "filename": f"{collection_name}.pdf",
                            "content": collection_name,
                        },
                    }
                ]

            monkeypatch.setattr(retrieval_service, "generate_embedding", fake_embedding)
            monkeypatch.setattr(retrieval_service, "vector_search", fake_search)

            async with sessionmaker() as session:
                chunks = await retrieval_service.retrieve_from_documents(
                    session,
                    user_id,
                    [document_id, second_document_id, failed_document_id, other_document_id],
                    "doc query",
                    top_k=5,
                )

            assert len(chunks) == 2
            assert {collection_name for collection_name, _ in searched} == {
                "kb_one",
                "kb_two",
            }
            assert all(str(failed_document_id) not in expr for _, expr in searched)
            assert all(str(other_document_id) not in expr for _, expr in searched)
            assert any(str(document_id) in expr for _, expr in searched)
            assert any(str(second_document_id) in expr for _, expr in searched)
        finally:
            await engine.dispose()

    asyncio.run(run_check())


def test_retrieve_from_other_users_kb_returns_empty(monkeypatch) -> None:
    async def run_check() -> None:
        engine, sessionmaker, user_id, _, _, other_kb_id = await setup_retrieval_db()
        called = False
        try:
            async def fake_embedding(query):
                nonlocal called
                called = True
                return [1.0, 0.0, 0.0]

            monkeypatch.setattr(retrieval_service, "generate_embedding", fake_embedding)

            async with sessionmaker() as session:
                chunks = await retrieval_service.retrieve_from_kb(
                    session,
                    user_id,
                    other_kb_id,
                    "query",
                )

            assert chunks == []
            assert called is False
        finally:
            await engine.dispose()

    asyncio.run(run_check())

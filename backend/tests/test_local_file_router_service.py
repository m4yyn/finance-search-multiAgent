import asyncio
import json
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models import Document, KnowledgeBase, User
from app.service import local_file_router_service
from app.service.local_file_router_service import (
    LocalDocumentCandidate,
    LocalFileRoute,
    list_local_document_candidates,
    parse_and_validate_route,
    route_query_to_local_files,
)


async def setup_router_db():
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

        kb = KnowledgeBase(user_id=owner.id, name="年报库", collection_name="kb_owner")
        other_kb = KnowledgeBase(
            user_id=other.id,
            name="其他库",
            collection_name="kb_other",
        )
        session.add_all([kb, other_kb])
        await session.commit()
        await session.refresh(kb)
        await session.refresh(other_kb)

        success_doc = Document(
            kb_id=kb.id,
            filename="贵州茅台2023年报.pdf",
            file_path="/tmp/maotai.pdf",
            file_size=1,
            mime_type="application/pdf",
            status="success",
            chunk_count=3,
        )
        failed_doc = Document(
            kb_id=kb.id,
            filename="失败.pdf",
            file_path="/tmp/failed.pdf",
            file_size=1,
            mime_type="application/pdf",
            status="failed",
        )
        other_doc = Document(
            kb_id=other_kb.id,
            filename="其他.pdf",
            file_path="/tmp/other.pdf",
            file_size=1,
            mime_type="application/pdf",
            status="success",
        )
        session.add_all([success_doc, failed_doc, other_doc])
        await session.commit()
        await session.refresh(success_doc)
        return engine, sessionmaker, owner.id, kb.id, success_doc.id


def make_candidate(
    document_id=None,
    kb_id=None,
    filename: str = "贵州茅台2023年报.pdf",
    kb_name: str = "年报库",
) -> LocalDocumentCandidate:
    return LocalDocumentCandidate(
        document_id=document_id or uuid4(),
        kb_id=kb_id or uuid4(),
        kb_name=kb_name,
        filename=filename,
        mime_type="application/pdf",
        chunk_count=3,
        created_at="2026-04-28T00:00:00Z",
    )


def test_list_local_document_candidates_returns_owned_success_documents() -> None:
    async def run_check() -> None:
        engine, sessionmaker, user_id, kb_id, document_id = await setup_router_db()
        try:
            async with sessionmaker() as session:
                candidates = await list_local_document_candidates(session, user_id)

            assert len(candidates) == 1
            assert candidates[0].document_id == document_id
            assert candidates[0].kb_id == kb_id
            assert candidates[0].kb_name == "年报库"
            assert candidates[0].filename == "贵州茅台2023年报.pdf"
        finally:
            await engine.dispose()

    asyncio.run(run_check())


def test_parse_route_accepts_existing_document_ids() -> None:
    candidate = make_candidate()
    route = parse_and_validate_route(
        json.dumps(
            {
                "route": "documents",
                "document_ids": [str(candidate.document_id)],
                "kb_ids": [],
            }
        ),
        [candidate],
    )

    assert route == LocalFileRoute(route="documents", document_ids=[candidate.document_id])


def test_parse_route_accepts_existing_kb_ids() -> None:
    candidate = make_candidate()
    route = parse_and_validate_route(
        json.dumps(
            {
                "route": "knowledge_bases",
                "document_ids": [],
                "kb_ids": [str(candidate.kb_id)],
            }
        ),
        [candidate],
    )

    assert route == LocalFileRoute(route="knowledge_bases", kb_ids=[candidate.kb_id])


def test_parse_route_falls_back_to_all_for_ambiguous_or_invalid_output() -> None:
    candidate = make_candidate()

    assert parse_and_validate_route('{"route":"all"}', [candidate]).route == "all"
    assert parse_and_validate_route("not json", [candidate]).route == "all"
    assert (
        parse_and_validate_route(
            json.dumps(
                {
                    "route": "documents",
                    "document_ids": [str(uuid4())],
                    "kb_ids": [],
                }
            ),
            [candidate],
        ).route
        == "all"
    )


def test_parse_route_returns_none_when_no_candidates() -> None:
    route = parse_and_validate_route('{"route":"all"}', [])

    assert route == LocalFileRoute(route="none")


def test_route_query_to_local_files_uses_llm_json(monkeypatch) -> None:
    async def run_check() -> None:
        candidate = make_candidate()

        async def fake_complete(messages):
            assert "候选文件列表" in messages[1]["content"]
            assert "贵州茅台" in messages[1]["content"]
            return json.dumps(
                {
                    "route": "documents",
                    "document_ids": [str(candidate.document_id)],
                    "kb_ids": [],
                }
            )

        monkeypatch.setattr(
            local_file_router_service.llm_service,
            "complete_chat_json",
            fake_complete,
        )

        route = await route_query_to_local_files("贵州茅台净利润", [candidate])

        assert route.route == "documents"
        assert route.document_ids == [candidate.document_id]

    asyncio.run(run_check())

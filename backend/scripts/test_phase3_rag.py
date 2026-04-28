import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from redis.asyncio import Redis
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config.settings import get_settings  # noqa: E402
from app.core.database import create_database_engine  # noqa: E402
from app.models import ChatMessage, ChatSession, Document, KnowledgeBase, User  # noqa: E402
from app.service.milvus_service import delete_collection  # noqa: E402
from app.service.session_service import get_chat_redis_key  # noqa: E402


BASE_URL = os.getenv("BASE_URL", "http://localhost:8000/api/v1").rstrip("/")
PDF_PATH = PROJECT_DIR / "data" / "2026032702988_c.pdf"
XLSX_PATH = PROJECT_DIR / "data" / "china_annual.xlsx"
RAG_QUERY = "公司2023年净利润是多少"
RETRIEVAL_QUERY = "公司2025年净利润"
POLL_TIMEOUT_SECONDS = int(os.getenv("RAG_POLL_TIMEOUT_SECONDS", "900"))


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def parse_sse_line(line: str) -> dict | None:
    if not line.startswith("data: "):
        return None
    return json.loads(line.removeprefix("data: "))


def extract_numbers(text: str) -> set[str]:
    numbers: set[str] = set()
    for match in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", text):
        normalized = match.replace(",", "")
        if len(normalized) >= 2:
            numbers.add(match)
            numbers.add(normalized)
    return numbers


def register_and_login(client: httpx.Client, username: str, email: str) -> str:
    password = "correct-password"
    register_response = client.post(
        f"{BASE_URL}/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    assert register_response.status_code == 201, register_response.text

    login_response = client.post(
        f"{BASE_URL}/auth/login",
        json={"username_or_email": username, "password": password},
    )
    assert login_response.status_code == 200, login_response.text
    return login_response.json()["access_token"]


def assert_openapi_paths(client: httpx.Client) -> None:
    response = client.get(f"{BASE_URL.removesuffix('/api/v1')}/openapi.json")
    assert response.status_code == 200, response.text
    paths = set(response.json()["paths"])
    expected_paths = {
        "/api/v1/knowledge/bases",
        "/api/v1/knowledge/bases/{kb_id}",
        "/api/v1/knowledge/bases/{kb_id}/stats",
        "/api/v1/knowledge/bases/{kb_id}/documents",
        "/api/v1/knowledge/bases/{kb_id}/documents/{doc_id}",
        "/api/v1/knowledge/retrieve",
        "/api/v1/chat/stream",
    }
    missing = expected_paths - paths
    assert not missing, f"Missing OpenAPI paths: {sorted(missing)}"
    print("OpenAPI exposes KB CRUD, document upload/list/delete, retrieve, and chat stream.")


async def db_check_tables_and_version() -> None:
    engine = create_database_engine()
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("select version_num from alembic_version"))
            knowledge_bases = await connection.scalar(
                text("select to_regclass('public.knowledge_bases')")
            )
            documents = await connection.scalar(text("select to_regclass('public.documents')"))
            chat_messages = await connection.scalar(
                text("select to_regclass('public.chat_messages')")
            )
    finally:
        await engine.dispose()

    print(f"Alembic current: {version}")
    print(f"PG table knowledge_bases: {knowledge_bases}")
    print(f"PG table documents: {documents}")
    assert version == "202604280003"
    assert knowledge_bases == "knowledge_bases"
    assert documents == "documents"
    assert chat_messages == "chat_messages"


async def db_fetch_chat_messages(session_id: str) -> list[ChatMessage]:
    engine = create_database_engine()
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as db:
            result = await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == UUID(session_id))
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            )
            return list(result.scalars().all())
    finally:
        await engine.dispose()


async def db_fetch_document_chunk_total(kb_id: str) -> int:
    engine = create_database_engine()
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as db:
            result = await db.execute(
                select(func.coalesce(func.sum(Document.chunk_count), 0)).where(
                    Document.kb_id == UUID(kb_id),
                    Document.status == "success",
                )
            )
            return int(result.scalar_one())
    finally:
        await engine.dispose()


async def redis_fetch_chat_messages(session_id: str) -> list[dict]:
    client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        raw_messages = await client.lrange(get_chat_redis_key(session_id), 0, -1)
        return [json.loads(message) for message in raw_messages]
    finally:
        await client.aclose()


async def cleanup(
    usernames: list[str],
    kb_id: str | None,
    collection_name: str | None,
    session_ids: list[str],
) -> None:
    engine = create_database_engine()
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    redis_client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        if collection_name:
            try:
                delete_collection(collection_name)
            except Exception as exc:
                print(f"Best-effort Milvus cleanup failed: {exc}")

        async with sessionmaker() as db:
            if kb_id:
                result = await db.execute(
                    select(Document.file_path).where(Document.kb_id == UUID(kb_id))
                )
                for file_path in result.scalars().all():
                    Path(file_path).unlink(missing_ok=True)
                await db.execute(delete(Document).where(Document.kb_id == UUID(kb_id)))
                await db.execute(delete(KnowledgeBase).where(KnowledgeBase.id == UUID(kb_id)))

            for session_id in session_ids:
                await db.execute(
                    delete(ChatMessage).where(ChatMessage.session_id == UUID(session_id))
                )
                await db.execute(delete(ChatSession).where(ChatSession.id == UUID(session_id)))
                await redis_client.delete(get_chat_redis_key(session_id))

            for username in usernames:
                user_id = await db.scalar(select(User.id).where(User.username == username))
                if user_id is not None:
                    await db.execute(
                        delete(ChatMessage).where(
                            ChatMessage.session_id.in_(
                                select(ChatSession.id).where(ChatSession.user_id == user_id)
                            )
                        )
                    )
                    await db.execute(delete(ChatSession).where(ChatSession.user_id == user_id))
                    await db.execute(delete(User).where(User.id == user_id))
            await db.commit()
    finally:
        await redis_client.aclose()
        await engine.dispose()


def create_knowledge_base(client: httpx.Client, token: str) -> tuple[str, str]:
    response = client.post(
        f"{BASE_URL}/knowledge/bases",
        json={"name": f"Phase3 RAG {uuid4().hex[:8]}", "description": "Phase 3 RAG E2E"},
        headers=auth_headers(token),
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    print(f"Created KB: {payload['id']} collection={payload['collection_name']}")
    return payload["id"], payload["collection_name"]


def upload_document(client: httpx.Client, token: str, kb_id: str, path: Path) -> str:
    assert path.exists(), f"Missing test file: {path}"
    with path.open("rb") as file_handle:
        response = client.post(
            f"{BASE_URL}/knowledge/bases/{kb_id}/documents",
            files={"file": (path.name, file_handle, "application/octet-stream")},
            headers=auth_headers(token),
            timeout=120,
        )
    assert response.status_code == 201, response.text
    payload = response.json()
    print(f"Uploaded {path.name}: document={payload['id']} status={payload['status']}")
    assert payload["status"] == "pending"
    return payload["id"]


def poll_documents_until_success(
    client: httpx.Client,
    token: str,
    kb_id: str,
    expected_count: int,
) -> list[dict]:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    last_statuses: list[str] = []
    while time.monotonic() < deadline:
        response = client.get(
            f"{BASE_URL}/knowledge/bases/{kb_id}/documents",
            headers=auth_headers(token),
            timeout=30,
        )
        assert response.status_code == 200, response.text
        documents = response.json()
        statuses = [document["status"] for document in documents]
        if statuses != last_statuses:
            print(f"Document statuses: {statuses}")
            last_statuses = statuses
        if len(documents) >= expected_count and all(
            document["status"] == "success" for document in documents
        ):
            return documents
        failed = [document for document in documents if document["status"] == "failed"]
        if failed:
            raise AssertionError(f"Document ingestion failed: {failed}")
        time.sleep(5)
    raise TimeoutError(f"Timed out waiting for {expected_count} document(s) to succeed.")


def retrieve_chunks(client: httpx.Client, token: str, kb_id: str) -> list[dict]:
    response = client.post(
        f"{BASE_URL}/knowledge/retrieve",
        json={"kb_id": kb_id, "query": RETRIEVAL_QUERY, "top_k": 5},
        headers=auth_headers(token),
        timeout=120,
    )
    assert response.status_code == 200, response.text
    chunks = response.json()
    assert chunks, "Retrieval returned no chunks."
    print(f"Retrieved {len(chunks)} chunk(s).")
    for index, chunk in enumerate(chunks, start=1):
        print(
            f"- [{index}] score={chunk['score']:.4f} file={chunk['filename']} "
            f"chunk={chunk.get('chunk_index')}"
        )
        assert 0 <= chunk["score"] <= 1
    return chunks


def get_knowledge_stats(client: httpx.Client, token: str, kb_id: str) -> dict:
    response = client.get(
        f"{BASE_URL}/knowledge/bases/{kb_id}/stats",
        headers=auth_headers(token),
        timeout=60,
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_chat_session(client: httpx.Client, token: str) -> str:
    response = client.post(f"{BASE_URL}/chat/session", json={}, headers=auth_headers(token))
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def stream_chat(
    client: httpx.Client,
    token: str,
    session_id: str,
    content: str,
    search_mode: str = "none",
) -> tuple[str, list[dict]]:
    print(f"\nStreaming prompt: {content} search_mode={search_mode}")
    answer_parts: list[str] = []
    events: list[dict] = []
    payload: dict[str, object] = {"session_id": session_id, "content": content}
    if search_mode != "none":
        payload["search_mode"] = search_mode

    with client.stream(
        "POST",
        f"{BASE_URL}/chat/stream",
        json=payload,
        headers=auth_headers(token),
        timeout=180,
    ) as response:
        assert response.status_code == 200, response.text
        for line in response.iter_lines():
            event = parse_sse_line(line)
            if event is None:
                continue
            events.append(event)
            if event["type"] == "delta":
                delta = event.get("delta", "")
                answer_parts.append(delta)
                print(delta, end="", flush=True)
            elif event["type"] == "done":
                print("\n[SSE done]")
            elif event["type"] == "error":
                raise AssertionError(f"SSE error: {event.get('error')}")

    assert events, "No SSE events received."
    assert any(event["type"] == "delta" for event in events)
    assert events[-1]["type"] == "done"
    answer = "".join(answer_parts)
    print(f"Received {len(answer_parts)} SSE delta chunk(s).")
    return answer, events


def main() -> None:
    suffix = uuid4().hex
    username = f"phase3_rag_{suffix}"
    email = f"phase3_rag_{suffix}@example.com"
    other_username = f"phase3_other_{suffix}"
    other_email = f"phase3_other_{suffix}@example.com"
    kb_id: str | None = None
    collection_name: str | None = None
    session_id: str | None = None

    with httpx.Client(timeout=180) as client:
        try:
            health = httpx.get(BASE_URL.removesuffix("/api/v1") + "/health", timeout=10)
            assert health.status_code == 200, health.text
            assert_openapi_paths(client)
            asyncio.run(db_check_tables_and_version())
            milvus_path = BACKEND_DIR / get_settings().milvus_uri
            print(f"Milvus Lite path: {milvus_path}")

            token = register_and_login(client, username, email)
            other_token = register_and_login(client, other_username, other_email)
            print("Registered and logged in temporary users.")

            kb_id, collection_name = create_knowledge_base(client, token)
            upload_document(client, token, kb_id, PDF_PATH)
            upload_document(client, token, kb_id, XLSX_PATH)

            documents = poll_documents_until_success(client, token, kb_id, expected_count=2)
            chunk_total = sum(document["chunk_count"] or 0 for document in documents)
            assert chunk_total > 0
            pg_chunk_total = asyncio.run(db_fetch_document_chunk_total(kb_id))
            stats = get_knowledge_stats(client, token, kb_id)
            print(f"PG chunk_count sum: {pg_chunk_total}")
            print(f"Milvus row_count: {stats['milvus_chunk_count']}")
            assert pg_chunk_total == chunk_total
            assert stats["pg_chunk_count"] == chunk_total
            assert stats["milvus_chunk_count"] == chunk_total
            assert milvus_path.exists()

            chunks = retrieve_chunks(client, token, kb_id)
            reference_text = "\n".join(chunk["content"] for chunk in chunks)
            numeric_candidates = extract_numbers(reference_text)
            print(f"Numeric candidates from retrieval: {sorted(numeric_candidates)[:12]}")

            session_id = create_chat_session(client, token)
            rag_answer, rag_events = stream_chat(
                client,
                token,
                session_id,
                RAG_QUERY,
                search_mode="local",
            )
            done_event = rag_events[-1]
            references = done_event.get("references") or []
            assert references, "RAG done event did not include references."
            print(f"References returned: {len(references)}")
            for reference in references:
                assert reference["index"] >= 1
                assert 0 <= reference["score"] <= 1
                print(
                    f"- ref[{reference['index']}] {reference['filename']} "
                    f"score={reference['score']:.4f}"
                )

            if numeric_candidates:
                normalized_answer = rag_answer.replace(",", "")
                assert any(candidate.replace(",", "") in normalized_answer for candidate in numeric_candidates), (
                    "RAG answer did not include any numeric value visible in retrieved references."
                )
            else:
                assert "[1]" in rag_answer or "本地知识库" in rag_answer

            pure_answer, pure_events = stream_chat(client, token, session_id, RAG_QUERY)
            assert not (pure_events[-1].get("references") or [])
            print("\nRAG answer preview:", rag_answer[:180])
            print("Pure answer preview:", pure_answer[:180])
            assert rag_answer != pure_answer

            chat_messages = asyncio.run(db_fetch_chat_messages(session_id))
            redis_messages = asyncio.run(redis_fetch_chat_messages(session_id))
            print(f"PG chat_messages count: {len(chat_messages)}")
            print(f"Redis chat history count: {len(redis_messages)}")
            assert len(chat_messages) == 4
            assert len(redis_messages) == 4

            other_docs_response = client.get(
                f"{BASE_URL}/knowledge/bases/{kb_id}/documents",
                headers=auth_headers(other_token),
            )
            print(f"Cross-user document list status: {other_docs_response.status_code}")
            assert other_docs_response.status_code in {403, 404}

            delete_response = client.delete(
                f"{BASE_URL}/knowledge/bases/{kb_id}",
                headers=auth_headers(token),
            )
            assert delete_response.status_code == 204, delete_response.text
            deleted_docs_response = client.get(
                f"{BASE_URL}/knowledge/bases/{kb_id}/documents",
                headers=auth_headers(token),
            )
            assert deleted_docs_response.status_code == 404
            print("Deleted KB via API; PG rows, Milvus collection, and local files cleaned.")
            kb_id = None
            collection_name = None
            print("\nPhase 3 RAG E2E acceptance passed.")
        finally:
            asyncio.run(
                cleanup(
                    [username, other_username],
                    kb_id,
                    collection_name,
                    [session_id] if session_id else [],
                )
            )
            print("Cleaned temporary Phase 3 E2E data.")


if __name__ == "__main__":
    main()

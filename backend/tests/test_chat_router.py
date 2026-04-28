import asyncio
import json
from collections.abc import AsyncGenerator, Generator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db, get_sessionmaker
from app.core.redis_client import get_redis_cache
from app.core.security import create_access_token
from app.main import create_app
from app.models import ChatMessage, User
from app.schemas.knowledge import RetrivalChunk
from app.service import chat_service, llm_service, session_service


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        existed = key in self.values or key in self.lists
        self.values.pop(key, None)
        self.lists.pop(key, None)
        return int(existed)

    async def exists(self, key: str) -> int:
        return int(key in self.values or key in self.lists)

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        values = self.lists.get(key, [])
        if end == -1:
            return values[start:]
        return values[start : end + 1]


class FakeRedisCache:
    def __init__(self) -> None:
        self.client = FakeRedis()

    async def get(self, key: str):
        value = await self.client.get(key)
        return json.loads(value) if value is not None else None

    async def set(self, key: str, value, expire_seconds: int | None = None) -> bool:
        return await self.client.set(key, json.dumps(value), ex=expire_seconds)

    async def delete(self, key: str) -> bool:
        return bool(await self.client.delete(key))

    async def exists(self, key: str) -> bool:
        return bool(await self.client.exists(key))

    async def set_session(
        self,
        session_id: str,
        data: dict[str, str],
        expire_seconds: int | None = None,
    ) -> bool:
        return await self.set(session_id, data, expire_seconds=expire_seconds)

    async def get_session(self, session_id: str) -> dict[str, str] | None:
        value = await self.get(session_id)
        return value if isinstance(value, dict) else None

    async def add_to_list(self, key: str, value) -> int:
        return await self.client.rpush(key, json.dumps(value))

    async def get_list(self, key: str) -> list:
        values = await self.client.lrange(key, 0, -1)
        return [json.loads(value) for value in values]


@pytest.fixture()
def chat_client(monkeypatch) -> Generator[
    tuple[
        TestClient,
        FakeRedisCache,
        async_sessionmaker[AsyncSession],
        str,
        str,
    ],
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

    async def drop_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessionmaker() as session:
            yield session

    async def fake_stream(messages: list[dict[str, str]]) -> AsyncGenerator[str, None]:
        assert messages[-1]["role"] == "user"
        content = messages[-1]["content"]
        if content == "请分析银行股":
            yield "银行"
            yield "分析"
        else:
            assert "参考资料" in content
            assert "贵州茅台2023年净利润为747亿元" in content
            yield "净利润"
            yield "747亿元[1]"

    monkeypatch.setattr(llm_service, "stream_chat_completion", fake_stream)
    monkeypatch.setattr(session_service, "count_message_tokens", lambda _: 1)

    owner_id, other_id = asyncio.run(create_tables_and_users())
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_sessionmaker] = lambda: sessionmaker
    app.dependency_overrides[get_redis_cache] = lambda: redis_cache

    with TestClient(app) as client:
        yield (
            client,
            redis_cache,
            sessionmaker,
            create_access_token(owner_id),
            create_access_token(other_id),
        )
    app.dependency_overrides.clear()
    asyncio.run(drop_tables())


def parse_sse_events(response_text: str) -> list[dict]:
    events = []
    for block in response_text.strip().split("\n\n"):
        if not block.startswith("data: "):
            continue
        events.append(json.loads(block.removeprefix("data: ")))
    return events


def test_chat_router_requires_authentication(chat_client) -> None:
    client, _, _, _, _ = chat_client

    response = client.post("/api/v1/chat/session", json={})

    assert response.status_code == 401


def test_chat_router_creates_reads_and_streams_messages(chat_client) -> None:
    client, redis_cache, sessionmaker, owner_token, _ = chat_client
    headers = {"Authorization": f"Bearer {owner_token}"}

    create_response = client.post("/api/v1/chat/session", json={}, headers=headers)
    assert create_response.status_code == 201
    session_id = create_response.json()["session_id"]
    assert create_response.json()["title"] == "新会话"

    sessions_response = client.get("/api/v1/chat/sessions", headers=headers)
    assert sessions_response.status_code == 200
    assert len(sessions_response.json()) == 1
    assert sessions_response.json()[0]["id"] == session_id

    stream_response = client.post(
        "/api/v1/chat/stream",
        json={"session_id": session_id, "content": "请分析银行股"},
        headers=headers,
    )
    events = parse_sse_events(stream_response.text)

    assert stream_response.status_code == 200
    assert stream_response.headers["content-type"].startswith("text/event-stream")
    assert [event["type"] for event in events] == ["delta", "delta", "done"]
    assert events[0]["delta"] == "银行"
    assert events[2]["done"] is True
    assert UUID(events[2]["message_id"])

    async def inspect_db() -> list[ChatMessage]:
        async with sessionmaker() as session:
            return list(
                (
                    await session.execute(
                        select(ChatMessage)
                        .where(ChatMessage.session_id == UUID(session_id))
                        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
                    )
                ).scalars()
            )

    messages = asyncio.run(inspect_db())
    redis_messages = asyncio.run(
        redis_cache.get_list(session_service.get_chat_redis_key(session_id))
    )

    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].content == "请分析银行股"
    assert messages[1].content == "银行分析"
    assert [message["role"] for message in redis_messages] == ["user", "assistant"]

    messages_response = client.get(
        f"/api/v1/chat/session/{session_id}/messages",
        headers=headers,
    )
    assert messages_response.status_code == 200
    assert [message["role"] for message in messages_response.json()] == [
        "user",
        "assistant",
    ]


def test_chat_router_streams_with_kb_ids_and_returns_references(
    chat_client,
    monkeypatch,
) -> None:
    client, _, _, owner_token, _ = chat_client
    headers = {"Authorization": f"Bearer {owner_token}"}
    kb_id = uuid4()
    document_id = uuid4()

    async def fake_retrieve(db, user_id, kb_ids, query, top_k=5):  # noqa: ANN001
        assert kb_ids == [kb_id]
        assert query == "贵州茅台2023年净利润是多少"
        return [
            RetrivalChunk(
                kb_id=kb_id,
                document_id=document_id,
                filename="maotai.pdf",
                content="贵州茅台2023年净利润为747亿元。",
                score=0.91,
                chunk_id="chunk-1",
                chunk_index=1,
            )
        ]

    monkeypatch.setattr(chat_service, "retrieve_from_kbs", fake_retrieve)

    create_response = client.post("/api/v1/chat/session", json={}, headers=headers)
    session_id = create_response.json()["session_id"]
    stream_response = client.post(
        "/api/v1/chat/stream",
        json={
            "session_id": session_id,
            "content": "贵州茅台2023年净利润是多少",
            "kb_ids": [str(kb_id)],
        },
        headers=headers,
    )
    events = parse_sse_events(stream_response.text)

    assert stream_response.status_code == 200
    assert [event["type"] for event in events] == ["delta", "delta", "done"]
    assert events[2]["references"][0]["index"] == 1
    assert events[2]["references"][0]["filename"] == "maotai.pdf"
    assert events[2]["references"][0]["score"] == 0.91


def test_chat_router_hides_other_users_sessions(chat_client) -> None:
    client, _, _, owner_token, other_token = chat_client
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    other_headers = {"Authorization": f"Bearer {other_token}"}

    create_response = client.post("/api/v1/chat/session", json={}, headers=owner_headers)
    session_id = create_response.json()["session_id"]

    response = client.get(
        f"/api/v1/chat/session/{session_id}/messages",
        headers=other_headers,
    )

    assert response.status_code == 404


def test_chat_openapi_exposes_exactly_four_chat_paths(chat_client) -> None:
    client, _, _, _, _ = chat_client

    response = client.get("/openapi.json")
    chat_paths = {
        path for path in response.json()["paths"] if path.startswith("/api/v1/chat/")
    }

    assert chat_paths == {
        "/api/v1/chat/session",
        "/api/v1/chat/sessions",
        "/api/v1/chat/stream",
        "/api/v1/chat/session/{session_id}/messages",
    }

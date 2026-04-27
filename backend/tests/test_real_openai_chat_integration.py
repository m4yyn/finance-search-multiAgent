import asyncio
import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config.settings import get_settings
from app.core.database import create_database_engine, get_database_engine, get_sessionmaker
from app.core.redis_client import get_redis_cache, get_redis_pool
from app.core.security import create_access_token, hash_password
from app.main import create_app
from app.models import ChatMessage, ChatSession, User
from app.service.session_service import get_chat_redis_key


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_OPENAI_TEST") != "1",
    reason="Set RUN_REAL_OPENAI_TEST=1 to run the real OpenAI chat integration test.",
)


async def redis_accepts_no_password() -> bool:
    settings = get_settings()
    client = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        decode_responses=True,
    )
    try:
        return bool(await client.ping())
    except Exception:
        return False
    finally:
        await client.aclose()


async def create_temp_user() -> User:
    engine = create_database_engine()
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as db:
            session_id = uuid4().hex
            user = User(
                username=f"real_chat_{session_id}",
                email=f"real_chat_{session_id}@example.com",
                hashed_password=hash_password("correct-password"),
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            return user
    finally:
        await engine.dispose()


async def inspect_and_cleanup(user_id, session_id) -> tuple[list[ChatMessage], list[dict]]:
    engine = create_database_engine()
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    redis_client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        async with sessionmaker() as db:
            messages = list(
                (
                    await db.execute(
                        select(ChatMessage)
                        .where(ChatMessage.session_id == session_id)
                        .order_by(ChatMessage.created_at.asc())
                    )
                ).scalars()
            )
            raw_messages = await redis_client.lrange(get_chat_redis_key(session_id), 0, -1)
            redis_messages = [json.loads(message) for message in raw_messages]

            await redis_client.delete(get_chat_redis_key(session_id))
            await db.execute(
                delete(ChatMessage).where(ChatMessage.session_id == session_id)
            )
            await db.execute(
                delete(ChatSession).where(ChatSession.id == session_id)
            )
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()

        return messages, redis_messages
    finally:
        await redis_client.aclose()
        await engine.dispose()


def parse_sse_text(text: str) -> list[dict]:
    events = []
    for block in text.strip().split("\n\n"):
        if block.startswith("data: "):
            events.append(json.loads(block.removeprefix("data: ")))
    return events


def test_real_openai_chat_stream_persists_pg_and_redis(monkeypatch) -> None:
    settings = get_settings()
    if not settings.openai_api_key or not settings.openai_api_key.get_secret_value():
        pytest.skip("OPENAI_API_KEY is not configured.")

    if asyncio.run(redis_accepts_no_password()):
        monkeypatch.setenv("REDIS_PASSWORD", "")

    get_settings.cache_clear()
    get_database_engine.cache_clear()
    get_sessionmaker.cache_clear()
    get_redis_cache.cache_clear()
    get_redis_pool.cache_clear()

    user = asyncio.run(create_temp_user())
    token = create_access_token(str(user.id))
    session_id = None
    cleaned = False

    try:
        with TestClient(create_app()) as client:
            headers = {"Authorization": f"Bearer {token}"}
            create_response = client.post(
                "/api/v1/chat/sessions",
                json={},
                headers=headers,
            )
            assert create_response.status_code == 201
            session_id = create_response.json()["id"]

            with client.stream(
                "POST",
                f"/api/v1/chat/sessions/{session_id}/messages",
                json={"content": "请用一句中文回复：连通性测试"},
                headers=headers,
            ) as response:
                assert response.status_code == 200
                response_text = "".join(response.iter_text())

        events = parse_sse_text(response_text)
        messages, redis_messages = asyncio.run(
            inspect_and_cleanup(user.id, session_id)
        )
        cleaned = True

        assert any(event["type"] == "delta" for event in events)
        assert events[-1]["type"] == "done"
        assert [message.role for message in messages] == ["user", "assistant"]
        assert [message["role"] for message in redis_messages] == ["user", "assistant"]
    finally:
        if session_id is not None and not cleaned:
            asyncio.run(inspect_and_cleanup(user.id, session_id))
        elif session_id is None and not cleaned:
            engine_cleanup = create_database_engine()

            async def cleanup_user_only() -> None:
                try:
                    async with engine_cleanup.begin() as connection:
                        await connection.execute(delete(User).where(User.id == user.id))
                finally:
                    await engine_cleanup.dispose()

            asyncio.run(cleanup_user_only())
        get_database_engine.cache_clear()
        get_sessionmaker.cache_clear()
        get_redis_cache.cache_clear()
        get_redis_pool.cache_clear()

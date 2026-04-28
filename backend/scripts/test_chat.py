import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from redis.asyncio import Redis
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config.settings import get_settings  # noqa: E402
from app.core.database import create_database_engine  # noqa: E402
from app.models import ChatMessage, ChatSession, User  # noqa: E402
from app.service.session_service import get_chat_redis_key  # noqa: E402


BASE_URL = os.getenv("BASE_URL", "http://localhost:8000/api/v1").rstrip("/")
FIRST_PROMPT = "你好，介绍一下贵州茅台"
SECOND_PROMPT = "请引用上一轮提到的公司，用一句话说明它属于哪个行业。"


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def parse_sse_line(line: str) -> dict | None:
    if not line.startswith("data: "):
        return None
    return json.loads(line.removeprefix("data: "))


async def db_fetch_messages(session_id: str) -> list[ChatMessage]:
    engine = create_database_engine()
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    session_uuid = UUID(session_id)
    try:
        async with sessionmaker() as db:
            result = await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_uuid)
                .order_by(ChatMessage.created_at.asc())
            )
            return list(result.scalars().all())
    finally:
        await engine.dispose()


async def db_check_tables_and_version() -> None:
    engine = create_database_engine()
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("select version_num from alembic_version"))
            chat_sessions = await connection.scalar(text("select to_regclass('public.chat_sessions')"))
            chat_messages = await connection.scalar(text("select to_regclass('public.chat_messages')"))
            knowledge_bases = await connection.scalar(
                text("select to_regclass('public.knowledge_bases')")
            )
            documents = await connection.scalar(text("select to_regclass('public.documents')"))
    finally:
        await engine.dispose()

    print(f"Alembic current: {version}")
    print(f"PG table chat_sessions: {chat_sessions}")
    print(f"PG table chat_messages: {chat_messages}")
    print(f"PG table knowledge_bases: {knowledge_bases}")
    print(f"PG table documents: {documents}")
    assert version == "202604280003"
    assert chat_sessions == "chat_sessions"
    assert chat_messages == "chat_messages"
    assert knowledge_bases == "knowledge_bases"
    assert documents == "documents"


async def redis_fetch_messages(session_id: str) -> list[dict]:
    client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        raw_messages = await client.lrange(get_chat_redis_key(session_id), 0, -1)
        return [json.loads(message) for message in raw_messages]
    finally:
        await client.aclose()


async def cleanup(username: str, session_id: str | None) -> None:
    engine = create_database_engine()
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    redis_client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        async with sessionmaker() as db:
            user_id = await db.scalar(
                select(User.id).where(User.username == username)
            )
            if session_id:
                session_uuid = UUID(session_id)
                await db.execute(
                    delete(ChatMessage).where(ChatMessage.session_id == session_uuid)
                )
                await db.execute(
                    delete(ChatSession).where(ChatSession.id == session_uuid)
                )
                await redis_client.delete(get_chat_redis_key(session_id))
            if user_id:
                await db.execute(delete(User).where(User.id == user_id))
            await db.commit()
    finally:
        await redis_client.aclose()
        await engine.dispose()


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


def stream_chat(client: httpx.Client, token: str, session_id: str, content: str) -> str:
    print(f"\nStreaming prompt: {content}")
    answer_parts: list[str] = []
    event_types: list[str] = []
    with client.stream(
        "POST",
        f"{BASE_URL}/chat/stream",
        json={"session_id": session_id, "content": content},
        headers=auth_headers(token),
        timeout=120,
    ) as response:
        assert response.status_code == 200, response.text
        for line in response.iter_lines():
            event = parse_sse_line(line)
            if event is None:
                continue
            event_types.append(event["type"])
            if event["type"] == "delta":
                delta = event.get("delta", "")
                answer_parts.append(delta)
                print(delta, end="", flush=True)
            elif event["type"] == "done":
                print("\n[SSE done]")
            elif event["type"] == "error":
                raise AssertionError(f"SSE error: {event.get('error')}")

    answer = "".join(answer_parts)
    assert "delta" in event_types
    assert event_types[-1] == "done"
    assert answer
    print(f"Received {len(answer_parts)} SSE delta chunks.")
    return answer


def main() -> None:
    suffix = uuid4().hex
    username = f"e2e_chat_{suffix}"
    email = f"e2e_chat_{suffix}@example.com"
    other_username = f"e2e_other_{suffix}"
    other_email = f"e2e_other_{suffix}@example.com"
    session_id: str | None = None

    with httpx.Client(timeout=120) as client:
        try:
            health = httpx.get(BASE_URL.removesuffix("/api/v1") + "/health", timeout=10)
            assert health.status_code == 200, health.text

            asyncio.run(db_check_tables_and_version())

            token = register_and_login(client, username, email)
            other_token = register_and_login(client, other_username, other_email)
            print("Registered and logged in two temporary users.")

            create_response = client.post(
                f"{BASE_URL}/chat/session",
                json={},
                headers=auth_headers(token),
            )
            assert create_response.status_code == 201, create_response.text
            session_id = create_response.json()["session_id"]
            print(f"Created chat session: {session_id}")

            sessions_response = client.get(
                f"{BASE_URL}/chat/sessions",
                headers=auth_headers(token),
            )
            assert sessions_response.status_code == 200, sessions_response.text
            sessions = sessions_response.json()
            print(f"GET /chat/sessions -> {len(sessions)} session(s)")
            assert len(sessions) == 1
            assert sessions[0]["id"] == session_id

            first_answer = stream_chat(client, token, session_id, FIRST_PROMPT)
            messages_response = client.get(
                f"{BASE_URL}/chat/session/{session_id}/messages",
                headers=auth_headers(token),
            )
            assert messages_response.status_code == 200, messages_response.text
            messages = messages_response.json()
            print(f"Messages after first round: {[message['role'] for message in messages]}")
            assert [message["role"] for message in messages] == ["user", "assistant"]

            second_answer = stream_chat(client, token, session_id, SECOND_PROMPT)
            print(f"\nSecond answer includes 贵州茅台: {'贵州茅台' in second_answer}")
            assert "贵州茅台" in second_answer

            final_messages_response = client.get(
                f"{BASE_URL}/chat/session/{session_id}/messages",
                headers=auth_headers(token),
            )
            final_messages = final_messages_response.json()
            print(f"Messages after second round: {[message['role'] for message in final_messages]}")
            assert len(final_messages) == 4

            pg_messages = asyncio.run(db_fetch_messages(session_id))
            redis_messages = asyncio.run(redis_fetch_messages(session_id))
            print("PG chat_messages rows:")
            for message in pg_messages:
                print(f"- {message.role}: {message.content[:80]}")
            print("Redis LRANGE rows:")
            for message in redis_messages:
                print(f"- {message['role']}: {message['content'][:80]}")
            assert len(pg_messages) == 4
            assert len(redis_messages) == 4

            cross_user_response = client.get(
                f"{BASE_URL}/chat/session/{session_id}/messages",
                headers=auth_headers(other_token),
            )
            print(f"Cross-user access status: {cross_user_response.status_code}")
            assert cross_user_response.status_code in {403, 404}

            print("\nE2E chat acceptance passed.")
            print(f"First answer preview: {first_answer[:120]}")
        finally:
            asyncio.run(cleanup(username, session_id))
            asyncio.run(cleanup(other_username, None))
            print("Cleaned temporary PG and Redis data.")


if __name__ == "__main__":
    main()

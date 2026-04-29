import asyncio
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import delete, select, text

from app.config.settings import get_settings
from app.core.database import create_database_engine
from app.core.redis_client import (
    RedisCache,
    create_redis_client,
    get_redis_cache,
    get_redis_pool,
)
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.main import app
from app.models import User
from app.schemas.user import Token, UserCreate, UserLogin, UserResponse


BACKEND_DIR = Path(__file__).resolve().parents[1]


def run_async(coro):
    return asyncio.run(coro)


async def db_scalar(sql: str):
    engine = create_database_engine()
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text(sql))
    finally:
        await engine.dispose()


async def cleanup_user(username: str) -> None:
    engine = create_database_engine()
    try:
        async with engine.begin() as connection:
            await connection.execute(delete(User).where(User.username == username))
    finally:
        await engine.dispose()


async def read_hashed_password(username: str) -> str | None:
    engine = create_database_engine()
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                select(User.hashed_password).where(User.username == username)
            )
            return result.scalar_one_or_none()
    finally:
        await engine.dispose()


async def redis_round_trip() -> str | None:
    cache = RedisCache(create_redis_client())
    key = f"acceptance:redis:{uuid4().hex}"
    try:
        await cache.set(key, {"status": "OK"}, expire_seconds=30)
        value = await cache.get(key)
        await cache.delete(key)
        return value["status"] if isinstance(value, dict) else None
    finally:
        await cache.client.aclose()
        await get_redis_pool().disconnect()
        get_redis_cache.cache_clear()
        get_redis_pool.cache_clear()


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


async def delete_redis_session(token: str) -> None:
    # The app dependency uses a cached Redis pool inside TestClient's event loop.
    # Cleanup uses a fresh client so it never reuses a pool from a closed loop.
    get_redis_cache.cache_clear()
    get_redis_pool.cache_clear()
    client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        token_id = decode_access_token(token).get("jti")
        if token_id:
            await client.delete(token_id)
    finally:
        await client.aclose()


@pytest.fixture(autouse=True)
def align_redis_auth_to_local_server(monkeypatch: pytest.MonkeyPatch):
    # Local Redis may be installed without requirepass. In that case, use the
    # actual server mode for smoke tests without editing the developer's .env.
    if run_async(redis_accepts_no_password()):
        monkeypatch.setenv("REDIS_PASSWORD", "")

    get_settings.cache_clear()
    get_redis_cache.cache_clear()
    get_redis_pool.cache_clear()
    yield
    get_settings.cache_clear()
    get_redis_cache.cache_clear()
    get_redis_pool.cache_clear()


def test_task_1_db_connection_demo_outputs_one() -> None:
    assert run_async(db_scalar("select 1")) == 1


def test_task_2_redis_read_write_ok() -> None:
    assert run_async(redis_round_trip()) == "OK"


def test_task_3_hash_and_jwt_five_asserts_pass() -> None:
    password = "correct-password"
    hashed_password = hash_password(password)
    token = create_access_token("user-123")
    payload = decode_access_token(token)

    assert hashed_password != password
    assert verify_password(password, hashed_password)
    assert not verify_password("wrong-password", hashed_password)
    assert payload["sub"] == "user-123"
    assert payload["jti"] and payload["exp"] and payload["iat"]


def test_task_4_import_user_model_success() -> None:
    assert User.__tablename__ == "users"


def test_task_5_alembic_current_and_pg_users_table_exist() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=BACKEND_DIR,
        capture_output=True,
        check=False,
        text=True,
    )
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0
    assert "202604290004" in output
    assert run_async(db_scalar("select to_regclass('public.users')")) == "users"
    assert (
        run_async(db_scalar("select to_regclass('public.chat_sessions')"))
        == "chat_sessions"
    )
    assert (
        run_async(db_scalar("select to_regclass('public.chat_messages')"))
        == "chat_messages"
    )
    assert (
        run_async(db_scalar("select to_regclass('public.knowledge_bases')"))
        == "knowledge_bases"
    )
    assert run_async(db_scalar("select to_regclass('public.documents')")) == "documents"
    assert (
        run_async(db_scalar("select to_regclass('public.long_term_memories')"))
        == "long_term_memories"
    )


def test_task_6_four_user_schemas_validate() -> None:
    create_payload = UserCreate(
        username="schema_demo",
        email="schema_demo@example.com",
        password="correct-password",
    )
    login_payload = UserLogin(
        username_or_email="schema_demo",
        password="correct-password",
    )
    token_payload = Token(access_token="token-value")
    response_payload = UserResponse(
        id=uuid4(),
        username="schema_demo",
        email="schema_demo@example.com",
        is_active=True,
        is_superuser=False,
        created_at="2026-04-27T00:00:00Z",
        updated_at="2026-04-27T00:00:00Z",
    )

    assert create_payload.username == "schema_demo"
    assert login_payload.username_or_email == "schema_demo"
    assert token_payload.token_type == "bearer"
    assert "hashed_password" not in response_payload.model_dump()

    with pytest.raises(ValidationError):
        UserCreate(username="ab", email="x@example.com", password="short")


def test_tasks_7_and_8_docs_show_auth_paths() -> None:
    with TestClient(app) as client:
        docs_response = client.get("/docs")
        openapi_response = client.get("/openapi.json")
    auth_paths = {
        path
        for path in openapi_response.json()["paths"]
        if path.startswith("/api/v1/auth/")
    }

    assert docs_response.status_code == 200
    assert "Swagger UI" in docs_response.text
    assert auth_paths == {
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/me",
        "/api/v1/auth/logout",
    }


def test_cors_preflight_allows_local_vite_frontend() -> None:
    with TestClient(app) as client:
        response = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_origins_env_accepts_comma_separated_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    get_settings.cache_clear()

    assert get_settings().cors_origin_list == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def test_task_9_auth_flow_and_db_password_is_hashed() -> None:
    suffix = uuid4().hex
    username = f"demo_{suffix}"
    email = f"demo_{suffix}@example.com"
    duplicate_email = f"demo_duplicate_{suffix}@example.com"
    password = "correct-password"
    token = ""

    try:
        with TestClient(app) as client:
            register_response = client.post(
                "/api/v1/auth/register",
                json={"username": username, "email": email, "password": password},
            )
            duplicate_response = client.post(
                "/api/v1/auth/register",
                json={
                    "username": username,
                    "email": duplicate_email,
                    "password": password,
                },
            )
            wrong_password_response = client.post(
                "/api/v1/auth/login",
                json={"username_or_email": username, "password": "wrong-password"},
            )
            login_response = client.post(
                "/api/v1/auth/login",
                json={"username_or_email": username, "password": password},
            )
            token = login_response.json()["access_token"]
            me_without_token_response = client.get("/api/v1/auth/me")
            me_with_token_response = client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
        stored_password = run_async(read_hashed_password(username))

        assert register_response.status_code == 201
        assert register_response.json()["username"] == username
        assert "hashed_password" not in register_response.json()
        assert duplicate_response.status_code == 409
        assert wrong_password_response.status_code == 401
        assert login_response.status_code == 200
        assert login_response.json()["token_type"] == "bearer"
        assert me_without_token_response.status_code == 401
        assert me_with_token_response.status_code == 200
        assert me_with_token_response.json()["username"] == username
        assert stored_password is not None
        assert stored_password != password
        assert verify_password(password, stored_password)
    finally:
        if token:
            run_async(delete_redis_session(token))
        run_async(cleanup_user(username))

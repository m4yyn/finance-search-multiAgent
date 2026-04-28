from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.redis_client import get_redis_cache
from app.main import create_app
from app.models import User
from app.router import search_router
from app.router.auth_router import get_current_user_required
from app.schemas.search import WebSearchResponse, WebSearchResult


class FakeRedisCache:
    async def get(self, key: str):  # noqa: ANN001
        return None

    async def set(self, key: str, value, expire_seconds: int | None = None) -> bool:  # noqa: ANN001
        return True


def test_search_router_requires_authentication() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/search/web",
            json={"query": "2026年4月A股市场"},
        )

    assert response.status_code == 401


def test_search_router_returns_cached_and_uncached_responses(monkeypatch) -> None:
    calls = 0

    async def fake_current_user() -> User:
        return User(
            id=uuid4(),
            username="search-user",
            email="search-user@example.com",
            hashed_password="hashed",
        )

    async def fake_search_web(redis_cache, query, count=5, freshness="noLimit", summary=True):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return WebSearchResponse(
            query=query,
            count=count,
            freshness=freshness,
            summary=summary,
            cached=calls > 1,
            results=[
                WebSearchResult(
                    index=1,
                    title="2026年4月A股市场",
                    url="https://example.com/a-share",
                    snippet="A股摘要",
                    summary="A股结构化摘要",
                    site_name="Example Finance",
                )
            ],
        )

    monkeypatch.setattr(search_router, "search_web", fake_search_web)

    app = create_app()
    app.dependency_overrides[get_current_user_required] = fake_current_user
    app.dependency_overrides[get_redis_cache] = lambda: FakeRedisCache()
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/search/web",
            json={"query": "2026年4月A股市场", "count": 5},
            headers={"Authorization": "Bearer test"},
        )
        second = client.post(
            "/api/v1/search/web",
            json={"query": "2026年4月A股市场", "count": 5},
            headers={"Authorization": "Bearer test"},
        )
        openapi = client.get("/openapi.json")
    app.dependency_overrides.clear()

    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert first.json()["results"][0]["title"] == "2026年4月A股市场"
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert "/api/v1/search/web" in openapi.json()["paths"]

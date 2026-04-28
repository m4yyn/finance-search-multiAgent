import asyncio
import json

import pytest

from app.config.settings import get_settings
from app.core.redis_client import RedisCache
from app.service.search_service import (
    BOCHA_WEB_SEARCH_URL,
    BochaSearchError,
    SearchService,
    build_web_search_cache_key,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)

    async def exists(self, key: str) -> int:
        return int(key in self.values)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class FakeHttpClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def post(self, url: str, **kwargs) -> FakeResponse:  # noqa: ANN003
        self.calls.append({"url": url, **kwargs})
        return self.response


def bocha_payload(count: int = 5) -> dict:
    return {
        "code": 200,
        "msg": "success",
        "data": {
            "webPages": {
                "value": [
                    {
                        "name": f"2026年4月A股市场结果{i}",
                        "url": f"https://example.com/a-share-{i}",
                        "displayUrl": f"example.com/a-share-{i}",
                        "snippet": f"A股市场摘要{i}",
                        "summary": f"2026年4月A股市场结构化摘要{i}",
                        "siteName": "Example Finance",
                        "siteIcon": "https://example.com/favicon.ico",
                        "datePublished": "2026-04-28",
                    }
                    for i in range(1, count + 1)
                ]
            }
        },
    }


@pytest.fixture(autouse=True)
def configure_bocha_key(monkeypatch):
    monkeypatch.setenv("BOCHA_API_KEY", "test-bocha-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_search_web_returns_five_structured_results_and_caches_same_query() -> None:
    async def run_check() -> None:
        redis_cache = RedisCache(FakeRedis())
        http_client = FakeHttpClient(FakeResponse(bocha_payload()))
        service = SearchService(redis_cache, http_client=http_client)

        first = await service.search_web("2026年4月A股市场")
        second = await service.search_web("2026年4月A股市场")
        cache_key = build_web_search_cache_key("2026年4月A股市场", 5, "noLimit", True)

        assert len(first.results) == 5
        assert first.cached is False
        assert second.cached is True
        assert len(second.results) == 5
        assert len(http_client.calls) == 1
        assert http_client.calls[0]["url"] == BOCHA_WEB_SEARCH_URL
        assert http_client.calls[0]["json"] == {
            "query": "2026年4月A股市场",
            "summary": True,
            "freshness": "noLimit",
            "count": 5,
        }
        assert http_client.calls[0]["headers"]["Authorization"] == "Bearer test-bocha-key"
        assert cache_key.startswith("web_search:bocha:")
        cached_payload = json.loads(redis_cache.client.values[cache_key])
        assert cached_payload["results"][0]["title"] == "2026年4月A股市场结果1"
        assert first.results[0].url == "https://example.com/a-share-1"
        assert first.results[0].summary == "2026年4月A股市场结构化摘要1"

    asyncio.run(run_check())


def test_search_web_raises_on_bocha_error_status() -> None:
    async def run_check() -> None:
        redis_cache = RedisCache(FakeRedis())
        service = SearchService(
            redis_cache,
            http_client=FakeHttpClient(FakeResponse({"code": 500, "msg": "bad"})),
        )

        with pytest.raises(BochaSearchError, match="bad"):
            await service.search_web("2026年4月A股市场")

    asyncio.run(run_check())

import asyncio
import os

import pytest

from app.core.redis_client import RedisCache
from app.service.search_service import SearchService


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


@pytest.mark.skipif(
    os.getenv("RUN_REAL_BOCHA_TEST") != "1",
    reason="Set RUN_REAL_BOCHA_TEST=1 to call the real Bocha API.",
)
def test_real_bocha_web_search_returns_structured_results() -> None:
    async def run_check() -> None:
        service = SearchService(RedisCache(FakeRedis()))
        response = await service.search_web("2026年4月A股市场", count=5)

        assert response.cached is False
        assert len(response.results) <= 5
        assert response.results
        assert response.results[0].title
        assert response.results[0].url.startswith("http")

    asyncio.run(run_check())

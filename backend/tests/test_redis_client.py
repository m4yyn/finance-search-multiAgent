import asyncio

from app.core.redis_client import RedisCache


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
        existed = key in self.values
        self.values.pop(key, None)
        return int(existed)

    async def exists(self, key: str) -> int:
        return int(key in self.values)

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        values = self.lists.get(key, [])
        if end == -1:
            return values[start:]
        return values[start : end + 1]


def test_redis_cache_supports_values_sessions_and_lists() -> None:
    async def run_check() -> None:
        cache = RedisCache(FakeRedis())

        assert await cache.set("key", {"value": 1}, expire_seconds=60) is True
        assert await cache.get("key") == {"value": 1}
        assert await cache.exists("key") is True

        await cache.set_session("token-id", {"user_id": "user-1"}, expire_seconds=60)
        assert await cache.get_session("token-id") == {"user_id": "user-1"}

        await cache.add_to_list("events", {"name": "login"})
        await cache.add_to_list("events", {"name": "logout"})
        assert await cache.get_list("events") == [
            {"name": "login"},
            {"name": "logout"},
        ]

        assert await cache.delete("key") is True
        assert await cache.exists("key") is False

    asyncio.run(run_check())


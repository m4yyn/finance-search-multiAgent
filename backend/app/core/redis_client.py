import json
from functools import lru_cache
from typing import Any

from redis.asyncio import ConnectionPool, Redis

from app.config.settings import get_settings


@lru_cache
def get_redis_pool() -> ConnectionPool:
    """Create a shared Redis connection pool from local environment settings."""
    settings = get_settings()
    return ConnectionPool.from_url(settings.redis_url, decode_responses=True)


def create_redis_client() -> Redis:
    """Create a Redis client backed by the shared connection pool."""
    return Redis(connection_pool=get_redis_pool())


class RedisCache:
    """Small JSON-aware Redis helper used by auth sessions and later caching."""

    def __init__(self, client: Redis) -> None:
        self.client = client

    async def get(self, key: str) -> Any | None:
        value = await self.client.get(key)
        if value is None:
            return None
        return json.loads(value)

    async def set(self, key: str, value: Any, expire_seconds: int | None = None) -> bool:
        return bool(await self.client.set(key, json.dumps(value), ex=expire_seconds))

    async def delete(self, key: str) -> bool:
        return bool(await self.client.delete(key))

    async def exists(self, key: str) -> bool:
        return bool(await self.client.exists(key))

    async def set_session(
        self,
        session_id: str,
        data: dict[str, Any],
        expire_seconds: int | None = None,
    ) -> bool:
        return await self.set(session_id, data, expire_seconds=expire_seconds)

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        value = await self.get(session_id)
        return value if isinstance(value, dict) else None

    async def add_to_list(self, key: str, value: Any) -> int:
        return int(await self.client.rpush(key, json.dumps(value)))

    async def get_list(self, key: str) -> list[Any]:
        values = await self.client.lrange(key, 0, -1)
        return [json.loads(value) for value in values]


@lru_cache
def get_redis_cache() -> RedisCache:
    return RedisCache(create_redis_client())

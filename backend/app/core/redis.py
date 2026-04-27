from redis.asyncio import Redis

from app.core.redis_client import RedisCache, create_redis_client, get_redis_cache
from app.config.settings import get_settings


def create_legacy_redis_client() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_url, decode_responses=True)


__all__ = ["RedisCache", "create_redis_client", "get_redis_cache"]

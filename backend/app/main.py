from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import get_settings
from app.core.database import get_database_engine, get_sessionmaker
from app.core.redis_client import get_redis_cache, get_redis_pool
from app.router.api import api_router
from app.router.health import router as health_router


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    del application
    try:
        yield
    finally:
        if get_database_engine.cache_info().currsize:
            await get_database_engine().dispose()
        get_database_engine.cache_clear()
        get_sessionmaker.cache_clear()

        if get_redis_cache.cache_info().currsize:
            await get_redis_cache().client.aclose()
        if get_redis_pool.cache_info().currsize:
            await get_redis_pool().disconnect()
        get_redis_cache.cache_clear()
        get_redis_pool.cache_clear()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(health_router)
    application.include_router(api_router, prefix=settings.api_v1_prefix)
    return application


app = create_app()

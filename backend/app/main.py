from fastapi import FastAPI

from app.config.settings import get_settings
from app.router.api import api_router


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
    )
    application.include_router(api_router)
    return application


app = create_app()

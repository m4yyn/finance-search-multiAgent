from openai import AsyncOpenAI

from app.config.settings import get_settings


def create_openai_client() -> AsyncOpenAI:
    settings = get_settings()
    api_key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    return AsyncOpenAI(api_key=api_key, base_url=settings.openai_base_url)

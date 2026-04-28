import asyncio
import os

import pytest

from app.config.settings import get_settings
from app.service.embedding_service import generate_embedding


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_OPENAI_TEST") != "1",
    reason="Set RUN_REAL_OPENAI_TEST=1 to run the real OpenAI embedding test.",
)


def test_real_openai_embedding_returns_configured_dimension() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    if not settings.openai_api_key or not settings.openai_api_key.get_secret_value():
        pytest.skip("OPENAI_API_KEY is not configured.")

    vector = asyncio.run(generate_embedding("embedding smoke"))

    assert len(vector) == settings.embedding_dim

from collections.abc import AsyncGenerator

from app.config.settings import get_settings
from app.core.openai_client import create_openai_client


async def stream_chat_completion(
    messages: list[dict[str, str]],
) -> AsyncGenerator[str, None]:
    """Stream text deltas from OpenAI. Temporary wrapper for the future Agent layer."""
    settings = get_settings()
    client = create_openai_client()
    stream = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        stream=True,
    )

    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        content = getattr(delta, "content", None)
        if content:
            yield content

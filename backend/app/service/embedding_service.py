from typing import overload

from app.config.settings import get_settings
from app.core.openai_client import create_openai_client


DEFAULT_BATCH_SIZE = 100
Vector = list[float]


def _normalize_texts(texts: str | list[str], batch_size: int) -> tuple[list[str], bool]:
    if batch_size < 1:
        raise ValueError("batch_size must be greater than 0.")

    is_single_input = isinstance(texts, str)
    text_list = [texts] if is_single_input else texts
    normalized_texts = [text.strip() for text in text_list]

    if any(not text for text in normalized_texts):
        raise ValueError("Embedding input text cannot be blank.")
    return normalized_texts, is_single_input


def _is_retryable_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code == 429 or (
        isinstance(status_code, int) and status_code >= 500
    ):
        return True

    retryable_names = {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
    }
    if exc.__class__.__name__ in retryable_names:
        return True
    return isinstance(exc, TimeoutError | ConnectionError)


async def _create_embedding_batch(
    client,
    input_batch: list[str],
    model: str,
    dimensions: int,
):
    for attempt in range(2):
        try:
            return await client.embeddings.create(
                model=model,
                input=input_batch,
                dimensions=dimensions,
            )
        except Exception as exc:
            if attempt == 0 and _is_retryable_error(exc):
                continue
            raise


def _extract_vectors(response, dimensions: int) -> list[Vector]:
    data = list(response.data)
    if all(hasattr(item, "index") for item in data):
        data.sort(key=lambda item: item.index)

    vectors = [list(item.embedding) for item in data]
    for vector in vectors:
        if len(vector) != dimensions:
            raise RuntimeError(
                f"Embedding dimension mismatch: expected {dimensions}, got {len(vector)}."
            )
    return vectors


@overload
async def generate_embedding(
    texts: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> Vector: ...


@overload
async def generate_embedding(
    texts: list[str],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[Vector]: ...


async def generate_embedding(
    texts: str | list[str],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> Vector | list[Vector]:
    """Generate OpenAI embeddings for one text or a batch of texts."""
    normalized_texts, is_single_input = _normalize_texts(texts, batch_size)
    if not normalized_texts:
        return []

    settings = get_settings()
    client = create_openai_client()
    vectors: list[Vector] = []
    for start in range(0, len(normalized_texts), batch_size):
        batch = normalized_texts[start : start + batch_size]
        response = await _create_embedding_batch(
            client,
            batch,
            settings.embedding_model,
            settings.embedding_dim,
        )
        vectors.extend(_extract_vectors(response, settings.embedding_dim))

    if is_single_input:
        return vectors[0]
    return vectors

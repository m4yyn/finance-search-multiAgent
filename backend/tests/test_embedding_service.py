import asyncio
from dataclasses import dataclass

import pytest

from app.service import embedding_service


@dataclass
class FakeSettings:
    embedding_model: str = "fake-embedding-model"
    embedding_dim: int = 3


class FakeEmbeddingItem:
    def __init__(self, embedding: list[float], index: int) -> None:
        self.embedding = embedding
        self.index = index


class FakeEmbeddingResponse:
    def __init__(self, embeddings: list[list[float]]) -> None:
        self.data = [
            FakeEmbeddingItem(embedding=embedding, index=index)
            for index, embedding in enumerate(embeddings)
        ]


class RetryableError(Exception):
    status_code = 500


class NonRetryableError(Exception):
    status_code = 400


class FakeEmbeddings:
    def __init__(
        self,
        calls: list[dict],
        *,
        fail_once: bool = False,
        bad_dimension: bool = False,
        non_retryable: bool = False,
    ) -> None:
        self.calls = calls
        self.fail_once = fail_once
        self.bad_dimension = bad_dimension
        self.non_retryable = non_retryable
        self.call_count = 0

    async def create(
        self,
        model: str,
        input: list[str],
        dimensions: int,
    ) -> FakeEmbeddingResponse:
        self.call_count += 1
        self.calls.append(
            {"model": model, "input": input, "dimensions": dimensions}
        )
        if self.non_retryable:
            raise NonRetryableError("bad request")
        if self.fail_once and self.call_count == 1:
            raise RetryableError("temporary failure")

        embedding_length = dimensions - 1 if self.bad_dimension else dimensions
        embeddings = [
            [float(index + offset) for offset in range(embedding_length)]
            for index, _ in enumerate(input)
        ]
        return FakeEmbeddingResponse(embeddings)


class FakeClient:
    def __init__(self, embeddings: FakeEmbeddings) -> None:
        self.embeddings = embeddings


def patch_embedding_client(monkeypatch, embeddings: FakeEmbeddings) -> None:
    monkeypatch.setattr(embedding_service, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        embedding_service,
        "create_openai_client",
        lambda: FakeClient(embeddings),
    )


def test_generate_embedding_returns_single_vector(monkeypatch) -> None:
    async def run_check() -> None:
        calls: list[dict] = []
        patch_embedding_client(monkeypatch, FakeEmbeddings(calls))

        vector = await embedding_service.generate_embedding(" hello ")

        assert vector == [0.0, 1.0, 2.0]
        assert calls == [
            {
                "model": "fake-embedding-model",
                "input": ["hello"],
                "dimensions": 3,
            }
        ]

    asyncio.run(run_check())


def test_generate_embedding_batches_texts_and_preserves_order(monkeypatch) -> None:
    async def run_check() -> None:
        calls: list[dict] = []
        patch_embedding_client(monkeypatch, FakeEmbeddings(calls))
        texts = [f"text-{index}" for index in range(205)]

        vectors = await embedding_service.generate_embedding(texts)

        assert len(vectors) == 205
        assert vectors[0] == [0.0, 1.0, 2.0]
        assert vectors[100] == [0.0, 1.0, 2.0]
        assert vectors[204] == [4.0, 5.0, 6.0]
        assert [len(call["input"]) for call in calls] == [100, 100, 5]
        assert all(call["dimensions"] == 3 for call in calls)

    asyncio.run(run_check())


def test_generate_embedding_validates_inputs(monkeypatch) -> None:
    async def run_check() -> None:
        patch_embedding_client(monkeypatch, FakeEmbeddings([]))

        assert await embedding_service.generate_embedding([]) == []
        with pytest.raises(ValueError):
            await embedding_service.generate_embedding("   ")
        with pytest.raises(ValueError):
            await embedding_service.generate_embedding(["valid", "   "])
        with pytest.raises(ValueError):
            await embedding_service.generate_embedding("valid", batch_size=0)

    asyncio.run(run_check())


def test_generate_embedding_retries_once_for_transient_error(monkeypatch) -> None:
    async def run_check() -> None:
        calls: list[dict] = []
        embeddings = FakeEmbeddings(calls, fail_once=True)
        patch_embedding_client(monkeypatch, embeddings)

        vector = await embedding_service.generate_embedding("retry")

        assert vector == [0.0, 1.0, 2.0]
        assert embeddings.call_count == 2

    asyncio.run(run_check())


def test_generate_embedding_does_not_retry_non_retryable_error(monkeypatch) -> None:
    async def run_check() -> None:
        calls: list[dict] = []
        embeddings = FakeEmbeddings(calls, non_retryable=True)
        patch_embedding_client(monkeypatch, embeddings)

        with pytest.raises(NonRetryableError):
            await embedding_service.generate_embedding("bad")
        assert embeddings.call_count == 1

    asyncio.run(run_check())


def test_generate_embedding_rejects_dimension_mismatch(monkeypatch) -> None:
    async def run_check() -> None:
        patch_embedding_client(monkeypatch, FakeEmbeddings([], bad_dimension=True))

        with pytest.raises(RuntimeError, match="Embedding dimension mismatch"):
            await embedding_service.generate_embedding("bad dimension")

    asyncio.run(run_check())

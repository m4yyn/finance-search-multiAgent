import asyncio
from collections.abc import AsyncGenerator

from app.service import llm_service


class FakeDelta:
    def __init__(self, content: str | None) -> None:
        self.content = content


class FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.delta = FakeDelta(content)


class FakeChunk:
    def __init__(self, content: str | None) -> None:
        self.choices = [FakeChoice(content)]


class FakeCompletions:
    async def create(
        self,
        model: str,
        messages: list[dict[str, str]],
        stream: bool,
    ) -> AsyncGenerator[FakeChunk, None]:
        assert model
        assert messages == [{"role": "user", "content": "hello"}]
        assert stream is True

        async def stream_chunks() -> AsyncGenerator[FakeChunk, None]:
            yield FakeChunk("你")
            yield FakeChunk(None)
            yield FakeChunk("好")

        return stream_chunks()


class FakeChat:
    def __init__(self) -> None:
        self.completions = FakeCompletions()


class FakeClient:
    def __init__(self) -> None:
        self.chat = FakeChat()


def test_llm_service_streams_text_deltas(monkeypatch) -> None:
    async def run_check() -> None:
        monkeypatch.setattr(llm_service, "create_openai_client", lambda: FakeClient())

        deltas = [
            delta
            async for delta in llm_service.stream_chat_completion(
                [{"role": "user", "content": "hello"}]
            )
        ]

        assert deltas == ["你", "好"]

    asyncio.run(run_check())

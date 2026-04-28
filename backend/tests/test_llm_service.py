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


class FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class FakeJsonChoice:
    def __init__(self, content: str | None) -> None:
        self.message = FakeMessage(content)


class FakeJsonResponse:
    def __init__(self, content: str | None) -> None:
        self.choices = [FakeJsonChoice(content)]


class FakeCompletions:
    async def create(
        self,
        model: str,
        messages: list[dict[str, str]],
        stream: bool = False,
        temperature: int | None = None,
        response_format: dict[str, str] | None = None,
    ):
        assert model
        if stream:
            assert messages == [{"role": "user", "content": "hello"}]

            async def stream_chunks() -> AsyncGenerator[FakeChunk, None]:
                yield FakeChunk("你")
                yield FakeChunk(None)
                yield FakeChunk("好")

            return stream_chunks()

        assert temperature == 0
        assert response_format == {"type": "json_object"}
        return FakeJsonResponse('{"route":"all"}')


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


def test_llm_service_returns_json_completion(monkeypatch) -> None:
    async def run_check() -> None:
        monkeypatch.setattr(llm_service, "create_openai_client", lambda: FakeClient())

        content = await llm_service.complete_chat_json(
            [{"role": "user", "content": "route"}]
        )

        assert content == '{"route":"all"}'

    asyncio.run(run_check())

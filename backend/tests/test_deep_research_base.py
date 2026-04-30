import asyncio
from abc import ABC
from typing import Any
from uuid import uuid4

import pytest

from app.service.deep_research import base
from app.service.deep_research.base import AgentRegistry, BaseAgent
from app.service.deep_research.state import ResearchState, create_initial_state


class FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.choices = [FakeChoice(content)]


class FakeCompletions:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> FakeResponse:
        self.kwargs = kwargs
        return FakeResponse('{"answer":"ok"}')


class FakeChat:
    def __init__(self) -> None:
        self.completions = FakeCompletions()


class FakeClient:
    def __init__(self) -> None:
        self.chat = FakeChat()


class DummyAgent(BaseAgent):
    async def process(self, state: ResearchState) -> ResearchState:
        return state


def test_base_agent_is_abstract() -> None:
    assert issubclass(BaseAgent, ABC)
    with pytest.raises(TypeError):
        BaseAgent("base", "abstract", client=FakeClient())  # type: ignore[abstract]


def test_call_llm_uses_to_thread_and_json_mode(monkeypatch) -> None:
    async def run_check() -> None:
        fake_client = FakeClient()
        calls: list[tuple[Any, dict[str, Any]]] = []

        async def fake_to_thread(func, **kwargs):  # noqa: ANN001
            calls.append((func, kwargs))
            return func(**kwargs)

        monkeypatch.setattr(base.asyncio, "to_thread", fake_to_thread)
        agent = DummyAgent(
            "architect",
            "规划专家",
            model="test-model",
            client=fake_client,
        )

        content = await agent.call_llm(
            "system prompt",
            "user prompt",
            json_mode=True,
            temperature=0.2,
            max_tokens=100,
        )

        assert content == '{"answer":"ok"}'
        assert len(calls) == 1
        kwargs = fake_client.chat.completions.kwargs
        assert kwargs is not None
        assert kwargs["model"] == "test-model"
        assert kwargs["messages"] == [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "user prompt"},
        ]
        assert kwargs["temperature"] == 0.2
        assert kwargs["max_tokens"] == 100
        assert kwargs["response_format"] == {"type": "json_object"}

    asyncio.run(run_check())


def test_call_llm_can_disable_json_mode(monkeypatch) -> None:
    async def run_check() -> None:
        fake_client = FakeClient()

        async def fake_to_thread(func, **kwargs):  # noqa: ANN001
            return func(**kwargs)

        monkeypatch.setattr(base.asyncio, "to_thread", fake_to_thread)
        agent = DummyAgent("writer", "写作专家", model="test-model", client=fake_client)

        await agent.call_llm("system", "user", json_mode=False)

        kwargs = fake_client.chat.completions.kwargs
        assert kwargs is not None
        assert "response_format" not in kwargs

    asyncio.run(run_check())


def test_parse_json_response_handles_common_llm_shapes() -> None:
    agent = DummyAgent("parser", "解析测试", model="test-model", client=FakeClient())

    assert agent.parse_json_response('{"a": 1}') == {"a": 1}
    assert agent.parse_json_response('```json\n{"route": "all"}\n```') == {
        "route": "all"
    }
    assert agent.parse_json_response('说明文字 {"answer": "ok"} 结束') == {
        "answer": "ok"
    }
    assert agent.parse_json_response('{route: "all", // comment\n value: "x\\#y",}') == {
        "route": "all",
        "value": "x#y",
    }
    assert agent.parse_json_response("{'flag': true, 'items': [1, 2]}") == {
        "flag": True,
        "items": [1, 2],
    }


def test_parse_json_response_fixes_escaped_text_but_preserves_code_fields() -> None:
    agent = DummyAgent("parser", "解析测试", model="test-model", client=FakeClient())

    parsed = agent.parse_json_response(
        r'{"summary": "第一行\\n第二行", "code": "print(1)\\nprint(2)"}'
    )

    assert parsed["summary"] == "第一行\n第二行"
    assert parsed["code"] == r"print(1)\nprint(2)"


def test_add_message_records_agent_event_and_pushes_queue() -> None:
    async def run_check() -> None:
        agent = DummyAgent("scout", "搜索专家", model="test-model", client=FakeClient())
        state = create_initial_state(
            "分析光伏行业",
            session_id=uuid4(),
            user_id=uuid4(),
        )
        state["phase"] = "researching"
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        state["_message_queue"] = queue  # type: ignore[typeddict-unknown-key]

        event = agent.add_message(
            state,
            "search_result",
            {"title": "行业数据"},
            metadata={"source": "web"},
        )
        queued_event = queue.get_nowait()

        assert state["agent_events"] == [event]
        assert queued_event == event
        assert event["type"] == "search_result"
        assert event["agent"] == "scout"
        assert event["phase"] == "researching"
        assert event["content"] == {"title": "行业数据"}
        assert event["metadata"] == {"source": "web"}
        assert "messages" not in state

    asyncio.run(run_check())


def test_add_log_and_agent_registry() -> None:
    AgentRegistry.clear()
    agent = DummyAgent("critic", "审核专家", model="test-model", client=FakeClient())
    state = create_initial_state("分析银行业", session_id=uuid4())

    log = agent.add_log(
        state,
        action="review",
        input_summary="报告草稿",
        output_summary="发现 1 个问题",
        duration_ms=12,
        tokens_used=34,
    )

    assert state["logs"] == [log]
    assert log["agent"] == "critic"
    assert log["action"] == "review"

    AgentRegistry.register(agent)
    assert AgentRegistry.get("critic") is agent
    assert AgentRegistry.all() == {"critic": agent}
    AgentRegistry.clear()
    assert AgentRegistry.all() == {}

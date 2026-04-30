import asyncio
import json
from typing import Any
from uuid import uuid4

from app.service.deep_research.agents.architect import Architect
from app.service.deep_research.state import ResearchPhase, ResearchState, create_initial_state


def valid_flat_payload() -> dict[str, str]:
    return {
        "hypothesis_1": "银行业净息差可能继续承压，需要验证贷款收益率变化。",
        "hypothesis_2": "大型银行资产质量更稳健，需要验证不良率和拨备覆盖率。",
        "hypothesis_3": "稳增长政策可能改善信贷需求，需要验证政策与新增贷款数据。",
        "sec_1_title": "市场概况",
        "sec_1_desc": "分析银行业规模、资产增速和信贷需求。",
        "sec_1_query": "银行业 总资产 增速 信贷需求 2025",
        "sec_2_title": "竞争格局",
        "sec_2_desc": "分析国有行、股份行、城商行竞争差异。",
        "sec_2_query": "国有银行 股份制银行 城商行 竞争格局",
        "sec_3_title": "技术趋势",
        "sec_3_desc": "分析金融科技、AI风控和数字化运营。",
        "sec_3_query": "银行业 金融科技 AI风控 数字化运营",
        "sec_4_title": "政策环境",
        "sec_4_desc": "分析货币政策、资本监管和房地产政策影响。",
        "sec_4_query": "银行业 货币政策 资本监管 房地产政策",
        "sec_5_title": "挑战机遇",
        "sec_5_desc": "分析净息差、不良资产、财富管理机会。",
        "sec_5_query": "银行业 净息差 不良资产 财富管理 机遇",
        "sec_6_title": "未来展望",
        "sec_6_desc": "分析盈利修复、估值和长期趋势。",
        "sec_6_query": "银行业 盈利修复 估值 展望",
        "questions": "净息差压力何时缓解;资产质量是否稳定;政策如何影响估值",
    }


class StubArchitect(Architect):
    def __init__(self, responses: list[str]) -> None:
        super().__init__(client=object(), model="test-model")
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = True,
        temperature: float = 0.3,
        max_tokens: int = 16000,
    ) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "json_mode": json_mode,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if not self.responses:
            raise AssertionError("Unexpected LLM call.")
        return self.responses.pop(0)


def run(coro):  # noqa: ANN001
    return asyncio.run(coro)


def test_architect_generates_plan_from_flat_json() -> None:
    async def run_check() -> None:
        state = create_initial_state(
            "分析中国银行业2025年投资机会",
            session_id=uuid4(),
            user_id=uuid4(),
        )
        agent = StubArchitect([json.dumps(valid_flat_payload(), ensure_ascii=False)])

        result = await agent.process(state)

        assert result["phase"] == ResearchPhase.PLANNING.value
        assert len(result["outline"]) == 6
        assert result["outline"][0]["id"] == "sec_1"
        assert result["outline"][0]["title"] == "市场概况"
        assert result["outline"][0]["status"] == "pending"
        assert result["outline"][0]["search_queries"] == ["银行业 总资产 增速 信贷需求 2025"]
        assert len(result["hypotheses"]) == 3
        assert result["hypotheses"][0]["status"] == "unverified"
        assert len(result["research_questions"]) == 3
        assert result["knowledge_graph"]["nodes"][0]["type"] == "topic"
        assert len(result["knowledge_graph"]["nodes"]) == 10
        assert result["agent_outputs"][0]["agent"] == "Architect"
        assert result["phase_outputs"][0]["phase"] == ResearchPhase.PLANNING.value
        assert [event["type"] for event in result["agent_events"]].count("research_step") == 2
        assert "outline" in [event["type"] for event in result["agent_events"]]
        assert "messages" not in result
        assert len(agent.calls) == 1
        assert agent.calls[0]["json_mode"] is True

    run(run_check())


def test_architect_skips_non_init_phase_without_llm_call() -> None:
    async def run_check() -> None:
        state = create_initial_state("分析银行业", session_id=uuid4())
        state["phase"] = ResearchPhase.PLANNING.value
        agent = StubArchitect([json.dumps(valid_flat_payload(), ensure_ascii=False)])

        result = await agent.process(state)

        assert result is state
        assert len(agent.calls) == 0
        assert result["outline"] == []

    run(run_check())


def test_architect_retries_with_fallback_prompt_after_invalid_plan() -> None:
    async def run_check() -> None:
        state = create_initial_state("分析券商行业", session_id=uuid4())
        agent = StubArchitect(
            [
                json.dumps({"sec_1_title": "市场概况"}, ensure_ascii=False),
                json.dumps(valid_flat_payload(), ensure_ascii=False),
            ]
        )

        result = await agent.process(state)

        assert result["phase"] == ResearchPhase.PLANNING.value
        assert len(result["outline"]) == 6
        assert len(agent.calls) == 2
        assert "必须输出这些字符串字段" in agent.calls[1]["user_prompt"]
        retry_events = [
            event for event in result["agent_events"] if event["type"] == "planning_retry"
        ]
        assert len(retry_events) == 1
        assert "章节不足" in retry_events[0]["content"]["reason"]

    run(run_check())


def test_architect_soft_fails_after_retries() -> None:
    async def run_check() -> None:
        state = create_initial_state("分析保险行业", session_id=uuid4())
        agent = StubArchitect([json.dumps({}, ensure_ascii=False), "not json"])

        result = await agent.process(state)

        assert result["phase"] == ResearchPhase.INIT.value
        assert result["outline"] == []
        assert result["errors"]
        assert "Architect planning failed" in result["errors"][0]
        assert len(agent.calls) == 2
        failed_events = [
            event
            for event in result["agent_events"]
            if event["type"] == "research_step"
            and event["content"].get("status") == "failed"
        ]
        assert len(failed_events) == 1
        assert result["agent_outputs"][0]["status"] == "failed"
        assert "messages" not in result

    run(run_check())


def test_architect_pushes_events_to_message_queue() -> None:
    async def run_check() -> None:
        state = create_initial_state("分析新能源行业", session_id=uuid4())
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        state["_message_queue"] = queue  # type: ignore[typeddict-unknown-key]
        agent = StubArchitect([json.dumps(valid_flat_payload(), ensure_ascii=False)])

        await agent.process(state)

        queued_events = []
        while not queue.empty():
            queued_events.append(queue.get_nowait())

        assert queued_events == state["agent_events"]
        assert queued_events[0]["type"] == "research_step"
        assert queued_events[-1]["content"]["status"] == "completed"

    run(run_check())

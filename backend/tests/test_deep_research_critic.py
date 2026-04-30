import asyncio
import json
from typing import Any
from uuid import uuid4

from app.service.deep_research.agents.critic import Critic
from app.service.deep_research.state import ResearchPhase, ResearchState, create_initial_state


def review_state() -> ResearchState:
    state = create_initial_state(
        "分析中国银行业2025年经营趋势",
        session_id=uuid4(),
        user_id=uuid4(),
    )
    state["phase"] = ResearchPhase.REVIEWING.value
    state["outline"] = [
        {
            "id": "sec_1",
            "title": "市场概况",
            "description": "分析银行业规模和盈利压力。",
            "status": "drafted",
        }
    ]
    state["draft_sections"] = {
        "sec_1": "银行业资产规模增长，但净息差仍需观察。"
    }
    state["final_report"] = "## 执行摘要\n\n银行业资产规模增长，但净息差仍需观察。"
    state["facts"] = [
        {
            "id": "fact_1",
            "content": "银行业资产规模保持增长。",
            "source_name": "监管数据",
            "source_url": "https://example.com/regulator",
            "credibility_score": 0.9,
            "related_sections": ["sec_1"],
        }
    ]
    state["data_points"] = [
        {
            "id": "dp_1",
            "name": "银行业总资产增速",
            "value": "8.5",
            "unit": "%",
            "year": 2025,
            "source": "监管数据",
            "confidence": 0.9,
        }
    ]
    return state


class StubCritic(Critic):
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        super().__init__(client=object(), model="test-model")
        self.responses = responses
        self.llm_calls: list[dict[str, Any]] = []

    async def call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = True,
        temperature: float = 0.3,
        max_tokens: int = 16000,
    ) -> str:
        self.llm_calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "json_mode": json_mode,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if not self.responses:
            raise AssertionError("Unexpected Critic LLM call.")
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


def pass_review() -> dict[str, Any]:
    return {
        "overall_assessment": {
            "quality_score": 8.4,
            "verdict": "pass",
            "summary": "报告事实和引用基本充分。",
        },
        "issues": [],
        "fact_check_results": [{"fact_id": "fact_1", "status": "verified", "reason": "有来源"}],
        "missing_aspects": [],
        "strength_points": ["风险提示较完整"],
    }


def issue_review(
    *,
    issue_type: str = "logic_error",
    severity: str = "major",
    requires_new_search: bool = False,
    search_query: str = "",
) -> dict[str, Any]:
    return {
        "overall_assessment": {
            "quality_score": 5.5,
            "verdict": "needs_revision",
            "summary": "报告仍需修订。",
        },
        "issues": [
            {
                "id": "issue_1",
                "target_section": "sec_1",
                "issue_type": issue_type,
                "severity": severity,
                "location": "市场概况",
                "description": "缺少原始来源支撑。",
                "evidence": "引用不是监管或公司公告。",
                "suggestion": "补充官方来源后修订。",
                "requires_new_search": requires_new_search,
                "search_query": search_query,
            }
        ],
        "fact_check_results": [],
        "missing_aspects": [],
        "strength_points": [],
    }


def run(coro):  # noqa: ANN001
    return asyncio.run(coro)


def test_critic_skips_non_reviewing_phase() -> None:
    async def run_check() -> None:
        state = review_state()
        state["phase"] = ResearchPhase.WRITING.value
        critic = StubCritic([pass_review()])

        result = await critic.process(state)

        assert result is state
        assert result["phase"] == ResearchPhase.WRITING.value
        assert critic.llm_calls == []

    run(run_check())


def test_critic_pass_verdict_completes_state() -> None:
    async def run_check() -> None:
        state = review_state()
        critic = StubCritic([pass_review()])

        result = await critic.process(state)

        assert result["phase"] == ResearchPhase.COMPLETED.value
        assert result["quality_score"] == 8.4
        assert result["review_verdict"] == "pass"
        assert result["unresolved_issues"] == 0
        assert any(event["type"] == "review" for event in result["agent_events"])
        assert result["agent_outputs"][-1]["agent"] == "Critic"
        assert "股票推荐" in critic.llm_calls[0]["user_prompt"]
        assert "messages" not in result

    run(run_check())


def test_critic_routes_revision_without_new_search() -> None:
    async def run_check() -> None:
        state = review_state()
        critic = StubCritic([issue_review(issue_type="logic_error", severity="minor")])

        result = await critic.process(state)

        assert result["phase"] == ResearchPhase.REVISING.value
        assert result["iteration"] == 1
        assert result["critic_feedback"][0]["resolved"] is False
        assert result["pending_search_queries"] == []
        assert result["unresolved_issues"] == 0

    run(run_check())


def test_critic_routes_to_supplementary_research() -> None:
    async def run_check() -> None:
        state = review_state()
        critic = StubCritic(
            [
                issue_review(
                    issue_type="missing_source",
                    severity="critical",
                    requires_new_search=True,
                    search_query="中国银行业 2025 监管数据 官方",
                )
            ]
        )

        result = await critic.process(state)

        assert result["phase"] == ResearchPhase.RE_RESEARCHING.value
        assert result["iteration"] == 1
        assert result["pending_search_queries"] == ["中国银行业 2025 监管数据 官方"]
        assert any(event["type"] == "critic_feedback" for event in result["agent_events"])

    run(run_check())


def test_critic_forces_completed_at_max_iterations() -> None:
    async def run_check() -> None:
        state = review_state()
        state["iteration"] = 3
        state["max_iterations"] = 3
        critic = StubCritic([issue_review(issue_type="incomplete", severity="major")])

        result = await critic.process(state)

        assert result["phase"] == ResearchPhase.COMPLETED.value
        assert result["forced_completed"] is True
        assert any(event["type"] == "warning" for event in result["agent_events"])

    run(run_check())


def test_critic_records_forbidden_investment_advice_issue() -> None:
    async def run_check() -> None:
        state = review_state()
        state["final_report"] += "\n\n建议买入，目标价 10 元。"
        critic = StubCritic(
            [
                issue_review(
                    issue_type="bias",
                    severity="critical",
                    requires_new_search=False,
                )
            ]
        )

        result = await critic.process(state)

        assert result["critic_feedback"][0]["issue_type"] == "bias"
        assert result["critic_feedback"][0]["severity"] == "critical"
        assert result["phase"] == ResearchPhase.RE_RESEARCHING.value

    run(run_check())

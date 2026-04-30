import asyncio
import json
from typing import Any
from uuid import uuid4

from app.service.deep_research.agents.writer import Writer
from app.service.deep_research.state import ResearchPhase, ResearchState, create_initial_state


FORBIDDEN_TERMS = ["买入", "卖出", "持有", "目标价", "收益保证"]


def writer_state() -> ResearchState:
    state = create_initial_state(
        "分析中国银行业2025年经营趋势",
        session_id=uuid4(),
        user_id=uuid4(),
    )
    state["phase"] = ResearchPhase.WRITING.value
    state["outline"] = [
        {
            "id": "sec_1",
            "title": "市场概况",
            "description": "分析银行业资产规模与盈利趋势。",
            "section_type": "mixed",
            "status": "researching",
            "requires_chart": True,
        },
        {
            "id": "sec_2",
            "title": "风险因素",
            "description": "分析净息差和资产质量压力。",
            "section_type": "mixed",
            "status": "researching",
        },
    ]
    state["facts"] = [
        {
            "id": "fact_1",
            "content": "2024年银行业总资产保持增长，但净息差继续承压。",
            "source_name": "监管年报",
            "source_url": "https://example.com/report",
            "source_type": "official",
            "credibility_score": 0.9,
            "related_sections": ["sec_1"],
        },
        {
            "id": "fact_2",
            "content": "部分银行不良贷款率小幅波动，拨备覆盖率仍是风险观察重点。",
            "source_name": "银行年报",
            "source_url": "local://kb/report",
            "source_type": "local",
            "credibility_score": 0.8,
            "related_sections": ["sec_2"],
        },
    ]
    state["data_points"] = [
        {
            "id": "dp_1",
            "name": "总资产",
            "value": "420",
            "unit": "万亿元",
            "year": 2024,
            "source": "监管年报",
            "confidence": 0.9,
        }
    ]
    state["insights"] = ["规模增长与息差压力并存。"]
    state["charts"] = [
        {
            "id": "chart_1",
            "title": "总资产趋势",
            "chart_type": "line",
            "section_id": "sec_1",
            "metadata": {"insight": "规模增长"},
        }
    ]
    return state


class StubWriter(Writer):
    def __init__(self, responses: list[dict[str, Any] | str | Exception]) -> None:
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
            raise AssertionError("Unexpected Writer LLM call.")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, str):
            return response
        return json.dumps(response, ensure_ascii=False)


def section_payload(section_name: str) -> dict[str, Any]:
    return {
        "content": f"{section_name}显示，银行业经营需要同时观察规模、息差和资产质量。[监管年报](https://example.com/report)",
        "key_points": ["规模增长", "风险需跟踪"],
        "citations": [
            {
                "source": "监管年报",
                "url": "https://example.com/report",
                "title": "监管年报",
            }
        ],
    }


def synthesis_payload() -> dict[str, Any]:
    return {
        "executive_summary": "银行业研究摘要。",
        "full_report": (
            "## 执行摘要\n\n银行业规模增长但盈利与资产质量仍需观察。\n\n"
            "## 1 市场概况\n\n总资产保持增长。\n\n"
            "## 风险与限制\n\n数据存在时点限制。\n\n"
            "## 结论与展望\n\n持续跟踪公告和监管数据。\n\n"
            "## 参考文献\n\n1. [监管年报](https://example.com/report)"
        ),
        "conclusions": ["规模增长", "风险需跟踪"],
        "outlook": "持续跟踪。",
        "references": [
            {
                "id": 1,
                "source": "监管年报",
                "title": "监管年报",
                "url": "https://example.com/report",
            }
        ],
    }


def run(coro):  # noqa: ANN001
    return asyncio.run(coro)


def test_writer_ignores_non_writing_or_revising_phase() -> None:
    async def run_check() -> None:
        state = writer_state()
        state["phase"] = ResearchPhase.ANALYZING.value
        agent = StubWriter([])

        result = await agent.process(state)

        assert result is state
        assert agent.llm_calls == []
        assert state["draft_sections"] == {}

    run(run_check())


def test_writer_creates_sections_report_and_references() -> None:
    async def run_check() -> None:
        state = writer_state()
        agent = StubWriter(
            [
                section_payload("市场概况"),
                section_payload("风险因素"),
                synthesis_payload(),
            ]
        )

        result = await agent.process(state)

        assert result["phase"] == ResearchPhase.REVIEWING.value
        assert set(result["draft_sections"]) == {"sec_1", "sec_2"}
        assert result["outline"][0]["status"] == "drafted"
        assert result["final_report"].startswith("## 执行摘要")
        assert result["references"][0]["url"] == "https://example.com/report"
        assert any(event["type"] == "section_content" for event in result["agent_events"])
        assert any(event["type"] == "report_draft" for event in result["agent_events"])
        assert result["agent_outputs"][-1]["agent"] == "Writer"
        assert "禁止输出股票推荐" in agent.llm_calls[0]["user_prompt"]

    run(run_check())


def test_writer_synthesis_fallback_uses_draft_sections() -> None:
    async def run_check() -> None:
        state = writer_state()
        agent = StubWriter(
            [
                section_payload("市场概况"),
                section_payload("风险因素"),
                {"not_full_report": "bad"},
            ]
        )

        result = await agent.process(state)

        assert "## 执行摘要" in result["final_report"]
        assert "市场概况" in result["final_report"]
        assert "风险与限制" in result["final_report"]

    run(run_check())


def test_writer_sanitizes_stringified_dict_report_into_markdown_sections() -> None:
    async def run_check() -> None:
        state = writer_state()
        agent = StubWriter(
            [
                section_payload("市场概况"),
                section_payload("风险因素"),
                {
                    "executive_summary": "摘要",
                    "full_report": (
                        "{'1 市场概况': {'内容': '银行业规模保持增长。'}, "
                        "'2 风险因素': {'内容': '净息差和资产质量仍需跟踪。'}}"
                    ),
                    "references": [],
                },
            ]
        )

        result = await agent.process(state)

        assert result["final_report"].startswith("## 执行摘要")
        assert "## 1 市场概况" in result["final_report"]
        assert "银行业规模保持增长。" in result["final_report"]
        assert "## 风险与限制" in result["final_report"]
        assert "{" not in result["final_report"]
        assert "}" not in result["final_report"]
        assert "'内容'" not in result["final_report"]

    run(run_check())


def test_writer_rebuilds_report_when_synthesis_drops_sections_and_uses_machine_keys() -> None:
    async def run_check() -> None:
        state = writer_state()
        state["outline"].extend(
            [
                {
                    "id": "sec_3",
                    "title": "技术趋势",
                    "description": "分析金融科技与数字化趋势。",
                    "section_type": "mixed",
                    "status": "researching",
                },
                {
                    "id": "sec_4",
                    "title": "政策环境",
                    "description": "分析监管政策影响。",
                    "section_type": "mixed",
                    "status": "researching",
                },
                {
                    "id": "sec_5",
                    "title": "挑战机遇",
                    "description": "分析风险和机会。",
                    "section_type": "mixed",
                    "status": "researching",
                },
                {
                    "id": "sec_6",
                    "title": "未来展望",
                    "description": "分析未来经营趋势。",
                    "section_type": "mixed",
                    "status": "researching",
                },
            ]
        )
        bad_synthesis = {
            "executive_summary": "摘要",
            "full_report": (
                "## 执行摘要\n\n摘要。\n\n"
                "## executive_summary\n\n机器字段不应进入正文。\n\n"
                "## 1_market_overview\n\n只保留了第一节。\n\n"
                "## 6_future_outlook\n\n只保留了第六节。\n\n"
                "## 风险与限制\n\n风险说明。\n\n"
                "## 参考文献\n\n1. 来源。\n\n"
                "## 风险与限制\n\n重复风险说明。"
            ),
            "references": [],
        }
        agent = StubWriter(
            [
                section_payload("市场概况"),
                section_payload("风险因素"),
                section_payload("技术趋势"),
                section_payload("政策环境"),
                section_payload("挑战机遇"),
                section_payload("未来展望"),
                bad_synthesis,
            ]
        )

        result = await agent.process(state)

        for title in ["市场概况", "风险因素", "技术趋势", "政策环境", "挑战机遇", "未来展望"]:
            assert title in result["final_report"]
        assert "executive_summary" not in result["final_report"]
        assert "1_market_overview" not in result["final_report"]
        assert "6_future_outlook" not in result["final_report"]
        assert result["final_report"].count("## 风险与限制") == 1
        assert result["final_report"].count("## 参考文献") == 1
        assert any("missing_sections" in error for error in result["errors"])

    run(run_check())


def test_writer_revision_marks_feedback_resolved() -> None:
    async def run_check() -> None:
        state = writer_state()
        state["phase"] = ResearchPhase.REVISING.value
        state["final_report"] = "## 执行摘要\n\n原报告。"
        state["critic_feedback"] = [
            {
                "id": "fb_1",
                "severity": "major",
                "description": "缺少风险说明",
                "suggestion": "补充数据局限",
                "resolved": False,
            }
        ]
        agent = StubWriter(
            [
                {
                    "revised_content": "## 执行摘要\n\n补充风险与限制说明。",
                    "changes_made": ["补充风险"],
                    "addressed_issues": ["fb_1"],
                    "unable_to_address": [],
                }
            ]
        )

        result = await agent.process(state)

        assert result["phase"] == ResearchPhase.REVIEWING.value
        assert result["critic_feedback"][0]["resolved"] is True
        assert "补充风险" in result["final_report"]
        assert any(event["type"] == "revision_complete" for event in result["agent_events"])

    run(run_check())


def test_writer_removes_forbidden_investment_advice_terms() -> None:
    async def run_check() -> None:
        state = writer_state()
        agent = StubWriter(
            [
                {
                    "content": "建议买入该股票，目标价10元，收益保证。",
                    "key_points": ["建议买入"],
                    "citations": [],
                },
                section_payload("风险因素"),
                {
                    **synthesis_payload(),
                    "full_report": "## 执行摘要\n\n建议买入，目标价10元，收益保证。",
                },
            ]
        )

        result = await agent.process(state)

        combined = result["final_report"] + "\n".join(result["draft_sections"].values())
        for term in FORBIDDEN_TERMS:
            assert term not in combined

    run(run_check())

import asyncio
import base64
import json
from typing import Any
from uuid import uuid4

from app.service.deep_research.agents.data_analyst import DataAnalyst
from app.service.deep_research.agents.wizard import Wizard
from app.service.deep_research.state import ResearchPhase, ResearchState, create_initial_state


def wizard_state(data_points_count: int = 4) -> ResearchState:
    state = create_initial_state(
        "分析中国银行业2025年投资机会",
        session_id=uuid4(),
        user_id=uuid4(),
    )
    state["phase"] = ResearchPhase.ANALYZING.value
    state["outline"] = [
        {
            "id": "sec_1",
            "title": "市场概况",
            "description": "分析行业资产规模和增速。",
            "section_type": "quantitative",
            "requires_chart": True,
            "status": "researching",
        }
    ]
    state["facts"] = [
        {
            "id": "fact_1",
            "content": "银行业资产规模增长。",
            "related_sections": ["sec_1"],
            "data_points": [
                {"name": "资产规模", "value": 120, "unit": "万亿元", "year": 2022},
                {"name": "资产规模", "value": 135, "unit": "万亿元", "year": 2023},
            ],
        }
    ]
    state["data_points"] = [
        {
            "id": f"dp_{index}",
            "name": "银行业总资产",
            "value": 100 + index * 15,
            "unit": "万亿元",
            "year": 2020 + index,
            "source": "测试来源",
            "confidence": 0.9,
        }
        for index in range(data_points_count)
    ]
    return state


def analysis_code() -> str:
    return "\n".join(
        [
            "sns.set_theme(style='whitegrid')",
            "data = {'Year': [2020, 2021, 2022, 2023], 'Value': [100, 115, 130, 150]}",
            "df = pd.DataFrame(data)",
            "df['Value'] = pd.to_numeric(df['Value'], errors='coerce')",
            "df = df.dropna()",
            "plt.figure(figsize=(12, 7), dpi=200)",
            "plt.plot(df['Year'], df['Value'], linewidth=2.5, marker='o', color='#2f6f8f')",
            "plt.title('银行业总资产趋势', fontsize=18, fontweight='bold', pad=20)",
            "plt.xlabel('年份', fontsize=14)",
            "plt.ylabel('总资产（万亿元）', fontsize=14)",
            "sns.despine()",
            "plt.savefig('chart.png', dpi=200, bbox_inches='tight', facecolor='white')",
        ]
    )


def bad_then_fixed_payloads() -> list[dict[str, Any]]:
    return [
        {
            "analysis_plan": "先生成错误代码以触发自愈。",
            "code": "data = {'Year': [2020], 'Value': [100]}\ndf = pd.DataFrame(data)\nplt.plot(df['Missing'], df['Value'])",
        },
        {
            "error_analysis": "列名 Missing 不存在。",
            "fix_description": "改用 Year 列。",
            "fixed_code": analysis_code(),
        },
    ]


class StubWizard(Wizard):
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
            raise AssertionError("Unexpected Wizard LLM call.")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, str):
            return response
        return json.dumps(response, ensure_ascii=False)


class NoSectionChartWizard(StubWizard):
    async def generate_charts(self, state: ResearchState) -> None:
        del state


def run(coro):  # noqa: ANN001
    return asyncio.run(coro)


def test_wizard_skips_when_not_analyzing_and_data_points_insufficient() -> None:
    async def run_check() -> None:
        state = wizard_state(data_points_count=2)
        state["phase"] = ResearchPhase.RESEARCHING.value
        agent = StubWizard([])

        result = await agent.process(state)

        assert result is state
        assert result["phase"] == ResearchPhase.RESEARCHING.value
        assert result["charts"] == []
        assert agent.llm_calls == []
        assert result["agent_events"][-1]["type"] == "observation"

    run(run_check())


def test_wizard_switches_to_analyzing_when_data_points_are_available() -> None:
    async def run_check() -> None:
        state = wizard_state()
        state["phase"] = ResearchPhase.RESEARCHING.value
        agent = NoSectionChartWizard(
            [{"analysis_plan": "生成整体趋势图", "code": analysis_code()}]
        )

        result = await agent.process(state)

        assert result["phase"] == ResearchPhase.ANALYZING.value
        assert len(result["charts"]) == 1
        assert result["charts"][0]["artifact_type"] == "report_image"
        assert result["code_executions"][0]["success"] is True
        assert result["agent_outputs"][-1]["agent"] == "Wizard"
        assert any(event["type"] == "chart" for event in result["agent_events"])

    run(run_check())


def test_clean_code_handles_dirty_llm_output() -> None:
    agent = StubWizard([])
    dirty = (
        "```python\\n"
        "# 数据准备 data = {'Year': [2020, 2021], 'Value': [100, 120]}\\\\[10pt]"
        "df = pd.DataFrame(data)\\n"
        "plt.figure(figsize=(12, 7)) \\\\\\n"
        "plt.plot(df['Year'], df['Value'])\\n"
        "```"
    )

    cleaned = agent.clean_code(dirty)

    assert "```" not in cleaned
    assert "\\[10pt]" not in cleaned
    assert "data =" in cleaned
    assert "\ndf = pd.DataFrame" in cleaned
    assert not cleaned.splitlines()[2].endswith("\\")
    compile(cleaned, "<test>", "exec")


def test_execute_code_generates_png_base64_and_rejects_unsafe_code() -> None:
    async def run_check() -> None:
        agent = StubWizard([])

        result = await agent.execute_code(analysis_code())
        unsafe = await agent.execute_code("open('x.txt', 'w')")
        unsafe_pandas = await agent.execute_code("df.to_csv('chart.csv')")

        assert result["success"] is True
        assert len(result["charts"]) == 1
        assert base64.b64decode(result["charts"][0]).startswith(b"\x89PNG")
        assert unsafe["success"] is False
        assert "forbidden" in unsafe["error"]
        assert unsafe_pandas["success"] is False
        assert "forbidden" in unsafe_pandas["error"]

    run(run_check())


def test_execute_with_self_correction_fixes_failed_code() -> None:
    async def run_check() -> None:
        state = wizard_state()
        payloads = bad_then_fixed_payloads()
        agent = StubWizard(payloads[1:])

        result = await agent.execute_with_self_correction(payloads[0]["code"], state, max_retries=2)

        assert result["success"] is True
        assert result["retries"] == 1
        assert len(result["charts"]) == 1
        assert any(event["type"] == "code_fix" for event in state["agent_events"])

    run(run_check())


def test_generate_charts_creates_section_report_image() -> None:
    async def run_check() -> None:
        state = wizard_state()
        agent = StubWizard([{"code": analysis_code(), "chart_description": "市场概况图"}])

        await agent.generate_charts(state)

        assert len(state["charts"]) == 1
        assert state["charts"][0]["section_id"] == "sec_1"
        assert state["charts"][0]["artifact_type"] == "report_image"
        assert base64.b64decode(state["charts"][0]["image_base64"]).startswith(b"\x89PNG")
        assert state["code_executions"][0]["success"] is True
        assert any(event["type"] == "chart" for event in state["agent_events"])

    run(run_check())


def test_architect_scout_data_analyst_wizard_flow_has_interactive_and_report_charts() -> None:
    async def run_check() -> None:
        from tests.test_deep_research_architect import StubArchitect, valid_flat_payload
        from tests.test_deep_research_data_analyst import (
            StubDataAnalyst,
            chart_payload,
            extraction_payload,
            graph_payload,
        )
        from tests.test_deep_research_scout import StubScout, analysis_payload, fake_web_search

        state = create_initial_state(
            "分析中国银行业2025年投资机会",
            session_id=uuid4(),
            user_id=uuid4(),
        )
        architect = StubArchitect([json.dumps(valid_flat_payload(), ensure_ascii=False)])
        scout = StubScout([analysis_payload()], web_search_func=fake_web_search)
        analyst = StubDataAnalyst([extraction_payload(), graph_payload(), chart_payload()])
        wizard = NoSectionChartWizard(
            [{"analysis_plan": "生成报告图", "code": analysis_code()}]
        )

        await architect.process(state)
        await scout.process(state)
        state["phase"] = ResearchPhase.ANALYZING.value
        await analyst.process(state)
        await wizard.process(state)

        assert any(chart.get("echarts_option") for chart in state["charts"])
        assert any(chart.get("image_base64") for chart in state["charts"])
        assert state["code_executions"]
        assert any(event["type"] == "chart" for event in state["agent_events"])

    run(run_check())

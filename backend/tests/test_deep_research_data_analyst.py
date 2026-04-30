import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

from app.service.deep_research.agents.architect import Architect
from app.service.deep_research.agents.data_analyst import DataAnalyst
from app.service.deep_research.agents.scout import Scout
from app.service.deep_research.state import ResearchPhase, ResearchState, create_initial_state


def analysis_state() -> ResearchState:
    state = create_initial_state(
        "分析中国银行业2025年投资机会",
        session_id=uuid4(),
        user_id=uuid4(),
    )
    state["phase"] = ResearchPhase.ANALYZING.value
    state["facts"] = [
        {
            "id": "fact_1",
            "content": "2025年中国银行业总资产同比增长8.5%，但净息差继续承压。",
            "source_name": "权威财经研究",
            "source_url": "https://example.com/bank",
            "source_type": "report",
            "credibility_score": 0.86,
        }
    ]
    state["data_points"] = [
        {
            "id": "dp_existing",
            "name": "银行业总资产增速",
            "value": "8.5",
            "unit": "%",
            "year": 2025,
            "source": "权威财经研究",
            "confidence": 0.86,
        }
    ]
    return state


def extraction_payload() -> dict[str, Any]:
    return {
        "data_points": [
            {
                "name": "银行业总资产增速",
                "value": "8.5",
                "unit": "%",
                "year": 2025,
                "source": "权威财经研究",
                "confidence": 0.9,
                "context": "资产规模保持增长。",
            },
            {
                "name": "净息差趋势",
                "value": "继续承压",
                "unit": "",
                "year": 2025,
                "source": "权威财经研究",
                "confidence": 0.8,
                "context": "盈利能力仍受利率环境影响。",
            },
        ],
        "time_series": [
            {
                "name": "银行业总资产增速",
                "unit": "%",
                "points": [{"year": 2025, "value": "8.5", "source": "权威财经研究"}],
            }
        ],
        "distributions": [
            {
                "name": "盈利压力来源",
                "unit": "",
                "items": [{"label": "净息差", "value": "主要压力", "source": "权威财经研究"}],
            }
        ],
        "insights": ["银行资产扩张和息差压力同时存在。"],
    }


def graph_payload() -> dict[str, Any]:
    return {
        "nodes": [
            {
                "id": "topic",
                "label": "中国银行业投资机会",
                "type": "topic",
                "importance": 10,
            },
            {
                "id": "indicator_nim",
                "label": "净息差",
                "type": "indicator",
                "importance": 8,
            },
        ],
        "edges": [
            {
                "source": "topic",
                "target": "indicator_nim",
                "type": "constrains",
                "weight": 7,
                "description": "净息差影响盈利修复。",
            }
        ],
    }


def chart_payload() -> dict[str, Any]:
    return {
        "charts": [
            {
                "title": "银行业总资产增速",
                "description": "展示资产规模增长。",
                "chart_type": "bar",
                "section_id": "sec_1",
                "data": {"labels": ["2025"], "values": [8.5]},
                "echarts_option": {
                    "title": {"text": "银行业总资产增速"},
                    "xAxis": {"type": "category", "data": ["2025"]},
                    "yAxis": {"type": "value"},
                    "series": [{"type": "bar", "data": [8.5]}],
                },
                "insight": "资产扩张仍在延续。",
            }
        ]
    }


class StubDataAnalyst(DataAnalyst):
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
            raise AssertionError("Unexpected DataAnalyst LLM call.")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, str):
            return response
        return json.dumps(response, ensure_ascii=False)


class FailingDataAnalyst(DataAnalyst):
    def __init__(self) -> None:
        super().__init__(client=object(), model="test-model")

    async def analyze_data(self, state: ResearchState) -> ResearchState:
        del state
        raise RuntimeError("boom")


def run(coro):  # noqa: ANN001
    return asyncio.run(coro)


def test_data_analyst_skips_non_analyzing_phase() -> None:
    async def run_check() -> None:
        state = analysis_state()
        state["phase"] = ResearchPhase.RESEARCHING.value
        agent = StubDataAnalyst([extraction_payload()])

        result = await agent.process(state)

        assert result is state
        assert agent.llm_calls == []
        assert result["charts"] == []

    run(run_check())


def test_data_analyst_extracts_data_graph_and_charts() -> None:
    async def run_check() -> None:
        state = analysis_state()
        agent = StubDataAnalyst([extraction_payload(), graph_payload(), chart_payload()])

        result = await agent.process(state)

        assert result["phase"] == ResearchPhase.ANALYZING.value
        assert len(result["data_points"]) == 2
        assert result["data_points"][1]["name"] == "净息差趋势"
        assert result["insights"] == ["银行资产扩张和息差压力同时存在。"]
        graph = result["knowledge_graph"]
        assert graph["nodes"][1]["label"] == "净息差"
        assert graph["nodes"][1]["size"] == 44
        assert graph["edges"][0]["source"] == "topic"
        assert len(result["charts"]) == 1
        assert UUID(result["charts"][0]["id"])
        assert result["charts"][0]["echarts_option"]["series"][0]["type"] == "bar"
        assert result["agent_outputs"][-1]["agent"] == "DataAnalyst"
        assert result["phase_outputs"][-1]["phase"] == ResearchPhase.ANALYZING.value
        assert [event["type"] for event in result["agent_events"]].count("research_step") == 2
        assert any(event["type"] == "knowledge_graph" for event in result["agent_events"])
        assert any(event["type"] == "charts" for event in result["agent_events"])
        assert "messages" not in result
        assert len(agent.llm_calls) == 3
        assert "金融行业信息报告" in agent.llm_calls[0]["user_prompt"]

    run(run_check())


def test_data_analyst_handles_empty_facts_without_llm_calls() -> None:
    async def run_check() -> None:
        state = analysis_state()
        state["facts"] = []
        state["data_points"] = []
        agent = StubDataAnalyst([])

        result = await agent.process(state)

        assert result["data_points"] == []
        assert result["charts"] == []
        assert result["knowledge_graph"]["nodes"][0]["id"] == "topic"
        assert agent.llm_calls == []
        assert any(event["type"] == "charts" for event in result["agent_events"])

    run(run_check())


def test_data_analyst_soft_fails_inside_extraction() -> None:
    async def run_check() -> None:
        state = analysis_state()
        agent = StubDataAnalyst(
            [
                RuntimeError("extract failed"),
                graph_payload(),
            ]
        )

        result = await agent.process(state)

        assert result["errors"]
        assert "data extraction failed" in result["errors"][0]
        assert result["charts"] == []
        assert result["knowledge_graph"]["nodes"][1]["label"] == "净息差"
        assert result["agent_outputs"][-1]["status"] == "completed"

    run(run_check())


def test_data_analyst_process_soft_fails_on_unexpected_error() -> None:
    async def run_check() -> None:
        state = analysis_state()
        agent = FailingDataAnalyst()

        result = await agent.process(state)

        assert result["errors"]
        assert "DataAnalyst analysis failed" in result["errors"][0]
        failed_events = [
            event
            for event in result["agent_events"]
            if event["type"] == "research_step" and event["content"].get("status") == "failed"
        ]
        assert len(failed_events) == 1
        assert result["agent_outputs"][-1]["status"] == "failed"

    run(run_check())


def test_data_analyst_repairs_or_skips_incomplete_chart_options() -> None:
    agent = StubDataAnalyst([])

    charts = agent._normalize_charts(  # noqa: SLF001
        {
            "charts": [
                {
                    "title": "指标对比",
                    "chart_type": "bar",
                    "data": {"categories": ["营收", "利润"], "values": [100, 20]},
                    "echarts_option": {
                        "xAxis": {"type": "category", "data": ["营收", "利润"]},
                        "yAxis": {"type": "value"},
                        "series": [
                            {"type": "bar", "name": "营收", "data": [100]},
                            {"type": "bar", "name": "利润", "data": [20]},
                        ],
                    },
                },
                {
                    "title": "单点趋势",
                    "chart_type": "line",
                    "echarts_option": {
                        "xAxis": {"type": "category", "data": ["2024"]},
                        "series": [{"type": "line", "data": [7.0]}],
                    },
                },
                {
                    "title": "单值占比",
                    "chart_type": "pie",
                    "echarts_option": {
                        "series": [{"type": "pie", "data": [{"name": "市场", "value": 10}]}],
                    },
                },
            ]
        }
    )

    assert len(charts) == 1
    option = charts[0]["echarts_option"]
    assert len(option["series"]) == 1
    assert option["series"][0]["data"] == [100, 20]
    assert option["grid"]["containLabel"] is True


def test_data_analyst_normalizes_graph_for_ui() -> None:
    state = analysis_state()
    agent = StubDataAnalyst([])

    graph = agent._normalize_knowledge_graph(  # noqa: SLF001
        {
            "nodes": [
                {
                    "id": "company",
                    "label": "一个非常非常长的公司或行业节点名称",
                    "type": "company",
                    "importance": 8,
                },
                {"id": "company", "label": "重复节点", "type": "company"},
            ],
            "edges": [
                {"source": "topic", "target": "company", "type": "supports"},
                {"source": "topic", "target": "company", "type": "supports"},
                {"source": "company", "target": "company", "type": "self"},
                {"source": "missing", "target": "company", "type": "bad"},
            ],
        },
        state,
    )

    assert [node["id"] for node in graph["nodes"]] == ["topic", "company"]
    assert graph["nodes"][1]["display_label"].endswith("…")
    assert graph["edges"] == [
        {
            "source": "topic",
            "target": "company",
            "type": "supports",
            "relation": "supports",
            "weight": 3.0,
            "description": "",
        }
    ]


def test_data_analyst_pushes_events_to_queue() -> None:
    async def run_check() -> None:
        state = analysis_state()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        state["_message_queue"] = queue  # type: ignore[typeddict-unknown-key]
        agent = StubDataAnalyst([extraction_payload(), graph_payload(), chart_payload()])

        await agent.process(state)

        queued_events = []
        while not queue.empty():
            queued_events.append(queue.get_nowait())

        assert queued_events == state["agent_events"]
        assert queued_events[-1]["content"]["status"] == "completed"

    run(run_check())


def test_architect_scout_data_analyst_flow_with_real_query() -> None:
    async def run_check() -> None:
        from tests.test_deep_research_architect import StubArchitect, valid_flat_payload
        from tests.test_deep_research_scout import StubScout, analysis_payload, fake_web_search

        state = create_initial_state(
            "分析中国银行业2025年投资机会",
            session_id=uuid4(),
            user_id=uuid4(),
        )
        architect = StubArchitect([json.dumps(valid_flat_payload(), ensure_ascii=False)])
        scout = StubScout([analysis_payload()], web_search_func=fake_web_search)
        analyst = StubDataAnalyst([extraction_payload(), graph_payload(), chart_payload()])

        await architect.process(state)
        assert state["phase"] == ResearchPhase.PLANNING.value
        await scout.process(state)
        assert state["phase"] == ResearchPhase.RESEARCHING.value
        state["phase"] = ResearchPhase.ANALYZING.value
        await analyst.process(state)

        assert state["outline"]
        assert state["facts"]
        assert state["data_points"]
        assert state["charts"]
        assert state["knowledge_graph"]["nodes"]
        assert any(event["type"] == "charts" for event in state["agent_events"])

    run(run_check())

import asyncio
import json
from typing import Any
from uuid import uuid4

from app.schemas.knowledge import RetrivalChunk
from app.schemas.search import WebSearchResponse, WebSearchResult
from app.service.deep_research.agents.architect import Architect
from app.service.deep_research.agents.scout import Scout
from app.service.deep_research.state import ResearchPhase, ResearchState, create_initial_state


def one_section_state() -> ResearchState:
    state = create_initial_state(
        "分析中国银行业2025年投资机会",
        session_id=uuid4(),
        user_id=uuid4(),
        search_web=True,
        search_local=False,
    )
    state["phase"] = ResearchPhase.PLANNING.value
    state["outline"] = [
        {
            "id": "sec_1",
            "title": "市场概况",
            "description": "分析银行业资产规模和信贷需求。",
            "section_type": "mixed",
            "status": "pending",
            "requires_data": True,
            "requires_chart": True,
            "priority": 1,
            "search_queries": ["银行业 总资产 信贷需求 2025"],
        }
    ]
    state["hypotheses"] = [
        {
            "id": "h_1",
            "content": "银行业净息差可能继续承压。",
            "status": "unverified",
            "evidence_for": [],
            "evidence_against": [],
        }
    ]
    state["knowledge_graph"] = {"nodes": [{"id": "topic", "label": state["query"], "type": "topic"}], "edges": []}
    return state


def analysis_payload(
    *,
    content: str = "银行业总资产保持增长，但净息差仍面临压力。",
    source_type: str = "report",
) -> dict[str, Any]:
    return {
        "extracted_facts": [
            {
                "content": content,
                "source_name": "权威财经研究",
                "source_url": "https://example.com/bank-report",
                "source_type": source_type,
                "credibility_score": 0.82,
                "data_points": [
                    {
                        "name": "银行业总资产增速",
                        "value": "8.5",
                        "unit": "%",
                        "year": 2025,
                    }
                ],
                "related_hypothesis": "h_1",
                "hypothesis_support": "supports",
            }
        ],
        "hypothesis_evidence": [
            {
                "hypothesis_id": "h_1",
                "evidence_type": "supports",
                "evidence_summary": "检索结果显示净息差仍承压。",
            }
        ],
        "entities_discovered": [
            {
                "name": "净息差",
                "type": "indicator",
                "relations": ["影响银行盈利能力"],
            }
        ],
        "key_insights": ["资产扩张与息差压力并存。"],
        "follow_up_queries": [],
        "source_tracing_queries": [],
        "missing_info": ["分银行类型的净息差数据"],
        "source_quality_assessment": "来源质量较高，但仍需更多官方数据交叉验证。",
    }


def web_response(query: str) -> WebSearchResponse:
    return WebSearchResponse(
        query=query,
        count=1,
        freshness="noLimit",
        summary=True,
        cached=False,
        results=[
            WebSearchResult(
                index=1,
                title="银行业2025年投资展望",
                url="https://example.com/bank-report",
                snippet="银行业资产规模增长，净息差承压。",
                summary="银行业资产规模增长，净息差仍面临下行压力。",
                site_name="Example Finance",
                date_published="2025-01-01",
            )
        ],
    )


class StubScout(Scout):
    def __init__(
        self,
        responses: list[dict[str, Any] | str],
        **kwargs: Any,
    ) -> None:
        super().__init__(client=object(), model="test-model", **kwargs)
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
            raise AssertionError("Unexpected Scout LLM call.")
        response = self.responses.pop(0)
        if isinstance(response, str):
            return response
        return json.dumps(response, ensure_ascii=False)


class StubArchitect(Architect):
    def __init__(self, response: dict[str, str]) -> None:
        super().__init__(client=object(), model="test-model")
        self.response = response

    async def call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = True,
        temperature: float = 0.3,
        max_tokens: int = 16000,
    ) -> str:
        return json.dumps(self.response, ensure_ascii=False)


async def fake_web_search(redis_cache, query: str, count: int = 5, **kwargs):  # noqa: ANN001, ANN003
    del redis_cache, count, kwargs
    return web_response(query)


async def fake_local_retrieve(db, user_id, query: str, top_k: int = 5):  # noqa: ANN001
    del db, user_id, query, top_k
    return [
        RetrivalChunk(
            kb_id=uuid4(),
            document_id=uuid4(),
            filename="bank-local-report.pdf",
            content="本地研报显示银行净息差承压，但资产质量保持稳定。",
            score=0.93,
            chunk_id="chunk-1",
            chunk_index=1,
        )
    ]


def run(coro):  # noqa: ANN001
    return asyncio.run(coro)


def test_scout_web_search_extracts_facts_and_updates_state() -> None:
    async def run_check() -> None:
        state = one_section_state()
        scout = StubScout(
            [analysis_payload()],
            web_search_func=fake_web_search,
        )

        result = await scout.process(state)

        assert result["phase"] == ResearchPhase.RESEARCHING.value
        assert result["outline"][0]["status"] == "researching"
        assert len(result["facts"]) == 1
        assert result["facts"][0]["content"] == "银行业总资产保持增长，但净息差仍面临压力。"
        assert result["facts"][0]["related_sections"] == ["sec_1"]
        assert result["data_points"][0]["name"] == "银行业总资产增速"
        assert result["insights"] == ["资产扩张与息差压力并存。"]
        assert result["hypotheses"][0]["status"] == "supported"
        assert result["hypotheses"][0]["evidence_for"]
        assert any(event["type"] == "search_results" for event in result["agent_events"])
        assert any(event["type"] == "knowledge_graph" for event in result["agent_events"])
        assert result["agent_outputs"][-1]["agent"] == "Scout"
        assert result["phase_outputs"][-1]["phase"] == ResearchPhase.RESEARCHING.value
        assert "messages" not in result
        assert len(scout.llm_calls) == 1
        assert "金融行业信息报告编写 Agent 助手" in scout.llm_calls[0]["user_prompt"]

    run(run_check())


def test_scout_local_search_converts_retrieval_chunks_to_ui_results() -> None:
    async def run_check() -> None:
        state = one_section_state()
        state["search_web"] = False
        state["search_local"] = True
        scout = StubScout(
            [analysis_payload(source_type="local")],
            local_retrieve_func=fake_local_retrieve,
        )

        result = await scout.process(state)

        assert len(result["facts"]) == 1
        assert result["facts"][0]["source_type"] == "local"
        search_events = [
            event for event in result["agent_events"] if event["type"] == "search_results"
        ]
        assert search_events
        assert search_events[0]["content"]["searchType"] == "local"
        assert search_events[0]["content"]["results"][0]["isLocal"] is True

    run(run_check())


def test_scout_defaults_to_web_when_no_search_mode_selected() -> None:
    async def run_check() -> None:
        state = one_section_state()
        state["search_web"] = False
        state["search_local"] = False
        scout = StubScout(
            [analysis_payload()],
            web_search_func=fake_web_search,
        )

        result = await scout.process(state)

        assert result["search_web"] is True
        assert len(result["facts"]) == 1

    run(run_check())


def test_scout_skips_non_research_phase_without_search() -> None:
    async def run_check() -> None:
        state = one_section_state()
        state["phase"] = ResearchPhase.INIT.value
        scout = StubScout(
            [analysis_payload()],
            web_search_func=fake_web_search,
        )

        result = await scout.process(state)

        assert result is state
        assert result["facts"] == []
        assert scout.llm_calls == []

    run(run_check())


def test_scout_supplementary_branch_adds_facts_and_routes_to_revision() -> None:
    async def run_check() -> None:
        state = one_section_state()
        state["phase"] = ResearchPhase.RE_RESEARCHING.value
        state["pending_search_queries"] = ["银行业 净息差 监管数据 官方"]
        state["critic_feedback"] = [
            {
                "id": "issue_1",
                "issue_type": "missing_source",
                "severity": "critical",
                "description": "缺少官方数据来源。",
                "requires_new_search": True,
                "search_query": "银行业 净息差 监管数据 官方",
                "resolved": False,
            }
        ]
        scout = StubScout(
            [analysis_payload()],
            web_search_func=fake_web_search,
        )

        result = await scout.process(state)

        assert result is state
        assert result["phase"] == ResearchPhase.REVISING.value
        assert result["pending_search_queries"] == []
        assert len(result["facts"]) == 1
        assert result["facts"][0]["metadata"]["triggered_by"] == "Critic"
        assert result["facts"][0]["metadata"]["critic_issue_ids"] == ["issue_1"]
        assert any(event["type"] == "search_results" for event in result["agent_events"])
        assert any(event["type"] == "observation" for event in result["agent_events"])
        assert scout.llm_calls

    run(run_check())


def test_scout_supplementary_missing_search_dependency_soft_fails() -> None:
    async def run_check() -> None:
        state = one_section_state()
        state["phase"] = ResearchPhase.RE_RESEARCHING.value
        state["pending_search_queries"] = ["银行业 净息差 监管数据 官方"]
        scout = StubScout([analysis_payload()])

        result = await scout.process(state)

        assert result["phase"] == ResearchPhase.REVISING.value
        assert result["facts"] == []
        assert result["errors"]
        assert "web search skipped" in result["errors"][0]
        assert scout.llm_calls == []

    run(run_check())


def test_scout_local_dependency_missing_soft_fails() -> None:
    async def run_check() -> None:
        state = one_section_state()
        state["search_web"] = False
        state["search_local"] = True
        scout = StubScout([analysis_payload()])

        result = await scout.process(state)

        assert result["errors"]
        assert "local search skipped" in result["errors"][0]
        assert result["facts"] == []
        assert result["outline"][0]["status"] == "researching"
        assert scout.llm_calls == []

    run(run_check())


def test_scout_deduplicates_repeated_facts() -> None:
    async def run_check() -> None:
        state = one_section_state()
        duplicate_analysis = analysis_payload()
        duplicate_analysis["extracted_facts"].append(dict(duplicate_analysis["extracted_facts"][0]))
        scout = StubScout(
            [duplicate_analysis],
            web_search_func=fake_web_search,
        )

        result = await scout.process(state)

        assert len(result["facts"]) == 1
        observation_events = [
            event for event in result["agent_events"] if event["type"] == "observation"
        ]
        assert observation_events[-1]["content"]["duplicates_removed"] == 1

    run(run_check())


def test_scout_pushes_events_to_sse_queue() -> None:
    async def run_check() -> None:
        state = one_section_state()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        state["_message_queue"] = queue  # type: ignore[typeddict-unknown-key]
        scout = StubScout(
            [analysis_payload()],
            web_search_func=fake_web_search,
        )

        await scout.process(state)

        queued_events = []
        while not queue.empty():
            queued_events.append(queue.get_nowait())
        assert queued_events == state["agent_events"]
        assert queued_events[0]["type"] == "research_step"
        assert any(
            event["type"] == "research_step"
            and event["content"].get("status") == "completed"
            for event in queued_events
        )

    run(run_check())


def test_architect_to_scout_real_query_flow_with_fake_external_dependencies() -> None:
    async def run_check() -> None:
        query = "分析中国银行业2025年投资机会"
        architect_payload = {
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
        state = create_initial_state(
            query,
            session_id=uuid4(),
            user_id=uuid4(),
            search_web=True,
            search_local=False,
        )
        state = await StubArchitect(architect_payload).process(state)
        assert state["phase"] == ResearchPhase.PLANNING.value
        assert len(state["outline"]) == 6

        scout = StubScout(
            [analysis_payload(), analysis_payload(content="大型银行资产质量保持相对稳健。"), analysis_payload(content="金融科技继续改善银行风控效率。")],
            web_search_func=fake_web_search,
        )
        result = await scout.process(state)

        assert result["phase"] == ResearchPhase.RESEARCHING.value
        assert len(result["facts"]) == 3
        assert result["outline"][0]["status"] == "researching"
        assert result["outline"][1]["status"] == "researching"
        assert result["outline"][2]["status"] == "researching"
        assert any(event["type"] == "outline" for event in result["agent_events"])
        assert any(event["type"] == "search_results" for event in result["agent_events"])
        assert any(event["type"] == "observation" for event in result["agent_events"])
        assert result["hypotheses"][0]["status"] == "supported"
        assert "messages" not in result

    run(run_check())

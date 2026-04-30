import hashlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import RedisCache
from app.schemas.knowledge import RetrivalChunk
from app.schemas.search import WebSearchResponse, WebSearchResult
from app.service.deep_research.base import BaseAgent
from app.service.deep_research.state import ResearchPhase, ResearchState
from app.service.retrieval_service import retrieve_from_all_user_kbs
from app.service.search_service import search_web


SearchFunc = Callable[..., Awaitable[Any]]
LocalRetrieveFunc = Callable[..., Awaitable[Any]]


SEARCH_ANALYSIS_PROMPT = """你是“金融行业信息报告编写 Agent 助手”的深度检索分析 Agent。

## 原始研究课题
{query}

## 当前研究章节
标题：{section_title}
描述：{section_description}

## 待验证研究假设
{hypotheses}

## 检索结果
{search_results}

## 任务
请基于检索结果提取可用于金融行业报告写作的结构化证据。必须遵守：
1. 只提取检索结果中明确出现的信息，不要编造数据、年份、公司或政策。
2. 优先提取公司公告、年报/季报/财报、交易所公告、监管机构文件、央行/统计局/行业协会数据、券商/咨询报告中的市场规模、增速、份额、盈利能力、估值、政策、竞争格局、风险等可验证事实。
3. 对公司财报类信息，尽量保留报告期、会计口径、指标单位和披露主体；对政策类信息，保留发布机构和发布时间。
4. 每条事实必须带来源名称、来源 URL、来源类型和可信度评分；官方公告、监管/交易所、央行/统计局、公司年报应给更高可信度。
5. 明确判断证据是支持、反驳还是无法判断某个研究假设。
6. 如果来源只是转述数据，source_tracing_queries 必须优先追溯原始公告、监管文件、统计数据或公司财报。
7. 禁止输出股票推荐、买入/卖出/持有评级、目标价、收益承诺、仓位建议或任何交易指令。

输出 JSON object：
{{
  "extracted_facts": [
    {{
      "content": "具体、可验证的事实陈述",
      "source_name": "来源名称",
      "source_url": "来源URL或local://引用",
      "source_type": "official/academic/news/report/self_media/local",
      "credibility_score": 0.0,
      "data_points": [
        {{"name": "指标名", "value": "数值", "unit": "单位", "year": 2025}}
      ],
      "related_hypothesis": "h_1或h_2或null",
      "hypothesis_support": "supports/refutes/neutral"
    }}
  ],
  "hypothesis_evidence": [
    {{
      "hypothesis_id": "h_1",
      "evidence_type": "supports/refutes/inconclusive",
      "evidence_summary": "证据摘要"
    }}
  ],
  "entities_discovered": [
    {{"name": "实体名", "type": "company/industry/policy/indicator/technology", "relations": ["关系描述"]}}
  ],
  "key_insights": ["关键洞察"],
  "follow_up_queries": ["进一步检索关键词"],
  "source_tracing_queries": ["原始数据源追溯关键词"],
  "missing_info": ["仍缺失的信息"],
  "source_quality_assessment": "整体来源质量评估"
}}"""


SYSTEM_PROMPT = (
    "你是专业的金融行业信息检索与证据分析专家。"
    "你的输出必须是 JSON object，并且只能基于给定检索结果提取事实。"
)


class Scout(BaseAgent):
    """Deep Research search agent that collects facts for planned sections."""

    def __init__(
        self,
        llm_api_key: str | None = None,
        llm_base_url: str | None = None,
        model: str | None = None,
        client: Any | None = None,
        db: AsyncSession | None = None,
        redis_cache: RedisCache | None = None,
        user_id: UUID | str | None = None,
        web_search_func: SearchFunc | None = None,
        local_retrieve_func: LocalRetrieveFunc | None = None,
    ) -> None:
        super().__init__(
            name="Scout",
            role="深度搜索Agent",
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            model=model,
            client=client,
        )
        self.db = db
        self.redis_cache = redis_cache
        self.user_id = UUID(str(user_id)) if user_id is not None else None
        self.web_search_func = web_search_func or search_web
        self.local_retrieve_func = local_retrieve_func or retrieve_from_all_user_kbs
        self._custom_web_search = web_search_func is not None
        self._custom_local_retrieve = local_retrieve_func is not None
        self.fact_fingerprints: dict[str, str] = {}

    async def process(self, state: ResearchState) -> ResearchState:
        """Run normal section research or reserved supplementary research."""

        if state["phase"] == ResearchPhase.RE_RESEARCHING.value:
            return await self._supplementary_research(state)
        if state["phase"] not in {
            ResearchPhase.PLANNING.value,
            ResearchPhase.RESEARCHING.value,
        }:
            return state

        state["phase"] = ResearchPhase.RESEARCHING.value
        search_web_enabled = bool(state.get("search_web", True))
        search_local_enabled = bool(state.get("search_local", False))
        if not search_web_enabled and not search_local_enabled:
            search_web_enabled = True
            state["search_web"] = True

        pending_sections = [
            section
            for section in state.get("outline", [])
            if section.get("status") == "pending"
        ]
        if not pending_sections:
            return state

        started_at = datetime.now(timezone.utc)
        initial_facts_count = len(state.get("facts", []))
        step_id = f"step_searching_{uuid.uuid4().hex[:8]}"
        subtitle = self._search_mode_label(search_web_enabled, search_local_enabled)

        self.add_message(
            state,
            "research_step",
            {
                "step_id": step_id,
                "step_type": "searching",
                "title": "信息检索",
                "subtitle": subtitle,
                "status": "running",
                "started_at": started_at.isoformat(),
                "stats": {
                    "sections_count": min(len(pending_sections), 3),
                    "results_count": 0,
                },
                "search_web": search_web_enabled,
                "search_local": search_local_enabled,
            },
        )
        self.add_message(
            state,
            "thought",
            {
                "content": (
                    f"开始基于 Architect 研究计划执行{subtitle}，"
                    f"本轮处理 {min(len(pending_sections), 3)} 个待研究章节。"
                )
            },
        )

        for section in pending_sections[:3]:
            await self._research_section(
                state,
                section,
                search_web_enabled=search_web_enabled,
                search_local_enabled=search_local_enabled,
            )

        completed_at = datetime.now(timezone.utc)
        new_facts_count = len(state.get("facts", [])) - initial_facts_count
        self._record_outputs(state, new_facts_count, completed_at)
        self.add_log(
            state,
            action="section_research",
            input_summary=f"pending_sections={len(pending_sections)}",
            output_summary=f"新增 {new_facts_count} 条事实",
            duration_ms=int((completed_at - started_at).total_seconds() * 1000),
        )
        self.add_message(
            state,
            "research_step",
            {
                "step_id": step_id,
                "step_type": "searching",
                "title": "信息检索",
                "subtitle": subtitle,
                "status": "completed",
                "completed_at": completed_at.isoformat(),
                "stats": {
                    "results_count": len(state.get("facts", [])),
                    "new_results_count": new_facts_count,
                    "sources_count": len(
                        {
                            fact.get("source_url", "")
                            for fact in state.get("facts", [])
                            if fact.get("source_url")
                        }
                    ),
                },
            },
        )
        self._emit_search_results_event(state)
        return state

    async def _supplementary_research(self, state: ResearchState) -> ResearchState:
        """Run Critic-triggered supplementary search before Writer revision."""

        search_queries, critic_issue_ids = self._collect_supplementary_queries(state)
        started_at = datetime.now(timezone.utc)
        step_id = f"step_researching_{uuid.uuid4().hex[:8]}"
        search_web_enabled = bool(state.get("search_web", True))
        search_local_enabled = bool(state.get("search_local", False))
        if not search_web_enabled and not search_local_enabled:
            search_web_enabled = True
            state["search_web"] = True

        self.add_message(
            state,
            "research_step",
            {
                "step_id": step_id,
                "step_type": "re_researching",
                "title": "补充检索",
                "subtitle": "根据 Critic 审核反馈补充事实和溯源证据",
                "status": "running",
                "started_at": started_at.isoformat(),
                "stats": {"queries_count": len(search_queries)},
                "search_web": search_web_enabled,
                "search_local": search_local_enabled,
            },
        )

        if not search_queries:
            state["phase"] = ResearchPhase.REVISING.value
            self.add_message(
                state,
                "observation",
                {
                    "title": "补充检索跳过",
                    "content": "Critic 未提供可执行的补充检索关键词，直接进入报告修订。",
                    "facts_count": 0,
                },
            )
            return state

        self.add_message(
            state,
            "thought",
            {
                "content": f"开始执行 {len(search_queries)} 个补充检索任务，用于解决审核反馈。",
                "queries": search_queries,
                "critic_issue_ids": critic_issue_ids,
            },
        )

        section = {
            "id": "critic_supplement",
            "title": "审核补充检索",
            "description": "根据 Critic 审核反馈补充缺失来源、过时信息或未覆盖维度。",
            "search_queries": search_queries,
        }
        total_added_facts = 0
        total_duplicate_facts = 0
        total_data_points = 0
        collected_results: list[dict[str, Any]] = []

        for index, query in enumerate(search_queries, start=1):
            query_results: list[dict[str, Any]] = []
            if search_web_enabled:
                web_results = await self._execute_web_search(state, query)
                query_results.extend(web_results)
                self._emit_incremental_results(
                    state,
                    query,
                    "审核补充检索",
                    web_results,
                    index,
                    len(search_queries),
                    "web",
                )
            if search_local_enabled:
                local_results = await self._execute_local_search(state, query)
                query_results.extend(local_results)
                self._emit_incremental_results(
                    state,
                    query,
                    "审核补充检索",
                    local_results,
                    index,
                    len(search_queries),
                    "local",
                )

            collected_results.extend(query_results)
            if not query_results:
                self.add_message(
                    state,
                    "observation",
                    {
                        "title": "补充检索无结果",
                        "query": query,
                        "facts_count": 0,
                    },
                )
                continue

            analysis = await self._analyze_search_results(state, section, query_results)
            if not analysis:
                continue

            before_fact_count = len(state.get("facts", []))
            added_facts, duplicate_facts, data_points_count = self._apply_analysis(
                state,
                "critic_supplement",
                "审核补充检索",
                analysis,
                query_results,
            )
            for fact in state.get("facts", [])[before_fact_count:]:
                metadata = fact.setdefault("metadata", {})
                if isinstance(metadata, dict):
                    metadata["triggered_by"] = "Critic"
                    metadata["critic_issue_ids"] = critic_issue_ids
                    metadata["supplementary_query"] = query

            total_added_facts += added_facts
            total_duplicate_facts += duplicate_facts
            total_data_points += data_points_count
            self.add_message(
                state,
                "observation",
                {
                    "title": "补充检索分析完成",
                    "query": query,
                    "facts_count": added_facts,
                    "duplicates_removed": duplicate_facts,
                    "data_points_count": data_points_count,
                    "insights": analysis.get("key_insights", [])[:3],
                    "source_quality": analysis.get("source_quality_assessment", ""),
                    "critic_issue_ids": critic_issue_ids,
                },
            )

        state["pending_search_queries"] = []
        state["phase"] = ResearchPhase.REVISING.value
        completed_at = datetime.now(timezone.utc)
        output = {
            "queries_count": len(search_queries),
            "new_facts_count": total_added_facts,
            "duplicates_removed": total_duplicate_facts,
            "data_points_count": total_data_points,
            "critic_issue_ids": critic_issue_ids,
        }
        state.setdefault("agent_outputs", []).append(
            {
                "agent": self.name,
                "phase": ResearchPhase.RE_RESEARCHING.value,
                "status": "completed",
                "output": output,
                "completed_at": completed_at.isoformat(),
            }
        )
        state.setdefault("phase_outputs", []).append(
            {
                "phase": ResearchPhase.RE_RESEARCHING.value,
                "status": "completed",
                "agent": self.name,
                "output": output,
                "completed_at": completed_at.isoformat(),
            }
        )
        self.add_log(
            state,
            action="supplementary_research",
            input_summary=f"queries={len(search_queries)}",
            output_summary=f"new_facts={total_added_facts}",
            duration_ms=int((completed_at - started_at).total_seconds() * 1000),
        )
        self.add_message(
            state,
            "research_step",
            {
                "step_id": step_id,
                "step_type": "re_researching",
                "title": "补充检索",
                "subtitle": "根据 Critic 审核反馈补充事实和溯源证据",
                "status": "completed",
                "completed_at": completed_at.isoformat(),
                "stats": output,
            },
        )
        if collected_results:
            self.add_message(
                state,
                "search_results",
                {
                    "results": [
                        self._to_ui_search_result(result)
                        for result in collected_results[:10]
                    ],
                    "isIncremental": False,
                    "searchType": "supplementary",
                },
            )
        return state

    async def _research_section(
        self,
        state: ResearchState,
        section: dict[str, Any],
        search_web_enabled: bool,
        search_local_enabled: bool,
    ) -> None:
        section_id = str(section.get("id") or f"sec_{uuid.uuid4().hex[:6]}")
        section_title = str(section.get("title") or "未命名章节")
        search_queries = self._normalize_search_queries(
            section.get("search_queries"),
            fallback=section_title,
        )

        self.add_message(
            state,
            "action",
            {
                "tool": "section_search",
                "section_id": section_id,
                "section": section_title,
                "queries": search_queries,
                "search_web": search_web_enabled,
                "search_local": search_local_enabled,
            },
        )

        all_results: list[dict[str, Any]] = []
        for index, query in enumerate(search_queries, start=1):
            if search_web_enabled:
                web_results = await self._execute_web_search(state, query)
                all_results.extend(web_results)
                self._emit_incremental_results(
                    state,
                    query,
                    section_title,
                    web_results,
                    index,
                    len(search_queries),
                    "web",
                )

            if search_local_enabled:
                local_results = await self._execute_local_search(state, query)
                all_results.extend(local_results)
                self._emit_incremental_results(
                    state,
                    query,
                    section_title,
                    local_results,
                    index,
                    len(search_queries),
                    "local",
                )

        if not all_results:
            section["status"] = "researching"
            self.add_message(
                state,
                "observation",
                {
                    "section": section_title,
                    "facts_count": 0,
                    "message": "该章节未检索到可用结果。",
                },
            )
            return

        self.add_message(
            state,
            "thought",
            {
                "content": (
                    f"{section_title} 检索完成，获得 {len(all_results)} 条结果，"
                    "正在提取事实并验证研究假设。"
                )
            },
        )
        analysis = await self._analyze_search_results(
            state,
            section,
            all_results,
        )
        if not analysis:
            section["status"] = "researching"
            return

        added_facts, duplicate_facts, data_points_count = self._apply_analysis(
            state,
            section_id,
            section_title,
            analysis,
            all_results,
        )
        section["status"] = "researching"

        source_tracing_queries = self._normalize_query_list(
            analysis.get("source_tracing_queries")
        )
        follow_up_queries = self._normalize_query_list(analysis.get("follow_up_queries"))
        if source_tracing_queries:
            await self._execute_deep_search(
                state,
                section_id,
                source_tracing_queries[:2],
                search_type="source_tracing",
            )
        if follow_up_queries:
            await self._execute_deep_search(
                state,
                section_id,
                follow_up_queries[:2],
                search_type="follow_up",
            )

        self.add_message(
            state,
            "observation",
            {
                "section": section_title,
                "facts_count": added_facts,
                "duplicates_removed": duplicate_facts,
                "data_points_count": data_points_count,
                "insights": analysis.get("key_insights", [])[:3],
                "source_quality": analysis.get("source_quality_assessment", ""),
                "hypothesis_updates": len(analysis.get("hypothesis_evidence", [])),
                "search_results": [
                    self._to_ui_search_result(result)
                    for result in all_results[:10]
                ],
                "extracted_facts": [
                    {
                        "content": fact.get("content", ""),
                        "source_name": fact.get("source_name", ""),
                        "source_url": fact.get("source_url", ""),
                        "credibility": fact.get("credibility_score", 0.5),
                    }
                    for fact in analysis.get("extracted_facts", [])[:8]
                    if isinstance(fact, dict)
                ],
            },
        )

    async def _execute_web_search(
        self,
        state: ResearchState,
        query: str,
        count: int = 8,
    ) -> list[dict[str, Any]]:
        if not self._custom_web_search and self.redis_cache is None:
            state["errors"].append("Scout web search skipped: Redis cache is not configured.")
            return []

        try:
            response = await self.web_search_func(self.redis_cache, query, count=count)
        except Exception as exc:
            state["errors"].append(f"Scout web search failed for '{query}': {exc}")
            return []
        return self._normalize_web_response(response)

    async def _execute_local_search(
        self,
        state: ResearchState,
        query: str,
        top_k: int = 8,
    ) -> list[dict[str, Any]]:
        user_id = self.user_id or self._state_user_id(state)
        if not self._custom_local_retrieve and (self.db is None or user_id is None):
            state["errors"].append(
                "Scout local search skipped: db or user_id is not configured."
            )
            return []

        try:
            chunks = await self.local_retrieve_func(self.db, user_id, query, top_k=top_k)
        except Exception as exc:
            state["errors"].append(f"Scout local search failed for '{query}': {exc}")
            return []
        return [self._normalize_local_chunk(chunk) for chunk in chunks]

    async def _analyze_search_results(
        self,
        state: ResearchState,
        section: dict[str, Any],
        results: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not results:
            return None

        prompt = SEARCH_ANALYSIS_PROMPT.format(
            query=state["query"],
            section_title=section.get("title", ""),
            section_description=section.get("description", ""),
            hypotheses=self._format_hypotheses(state.get("hypotheses", [])),
            search_results=self._format_search_results(results),
        )
        try:
            response = await self.call_llm(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                json_mode=True,
                temperature=0.2,
                max_tokens=16000,
            )
            parsed = self.parse_json_response(response)
            return parsed if isinstance(parsed, dict) else None
        except Exception as exc:
            state["errors"].append(f"Scout analysis failed: {exc}")
            return None

    async def _execute_deep_search(
        self,
        state: ResearchState,
        section_id: str,
        queries: list[str],
        search_type: str,
    ) -> None:
        if state.get("iteration", 0) >= state.get("max_iterations", 3):
            return

        self.add_message(
            state,
            "action",
            {
                "tool": f"deep_search_{search_type}",
                "section_id": section_id,
                "queries": queries,
            },
        )
        for query in queries:
            results: list[dict[str, Any]] = []
            if state.get("search_web", True):
                results.extend(await self._execute_web_search(state, query, count=5))
            if state.get("search_local", False):
                results.extend(await self._execute_local_search(state, query, top_k=5))
            if results:
                self.add_message(
                    state,
                    "search_results",
                    {
                        "results": [
                            self._to_ui_search_result(result)
                            for result in results[:5]
                        ],
                        "isIncremental": True,
                        "searchType": search_type,
                    },
                )

    def _apply_analysis(
        self,
        state: ResearchState,
        section_id: str,
        section_title: str,
        analysis: dict[str, Any],
        results: list[dict[str, Any]],
    ) -> tuple[int, int, int]:
        added_facts = 0
        duplicate_facts = 0
        data_points_count = 0

        for fact in analysis.get("extracted_facts", []):
            if not isinstance(fact, dict):
                continue
            content = str(fact.get("content") or "").strip()
            if not content:
                continue
            source_url = str(fact.get("source_url") or "")
            if self._is_duplicate_fact(content, source_url):
                duplicate_facts += 1
                continue

            fact_entry = {
                "id": f"fact_{uuid.uuid4().hex[:8]}",
                "content": content,
                "source_url": source_url,
                "source_name": str(fact.get("source_name") or ""),
                "source_type": str(fact.get("source_type") or "news"),
                "credibility_score": float(fact.get("credibility_score") or 0.5),
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "related_sections": [section_id],
                "verified": False,
                "related_hypothesis": fact.get("related_hypothesis"),
                "hypothesis_support": fact.get("hypothesis_support"),
                "metadata": {
                    "section_title": section_title,
                    "search_result_count": len(results),
                },
            }
            state["facts"].append(fact_entry)
            added_facts += 1

            data_points_count += self._append_data_points(state, fact, fact_entry)
            self._update_hypothesis_from_fact(state, fact_entry)

        self._update_hypothesis_status(state, analysis.get("hypothesis_evidence", []))
        self._update_knowledge_graph(state, analysis.get("entities_discovered", []))
        for insight in analysis.get("key_insights", []):
            if isinstance(insight, str) and insight.strip() and insight not in state["insights"]:
                state["insights"].append(insight.strip())
        return added_facts, duplicate_facts, data_points_count

    def _append_data_points(
        self,
        state: ResearchState,
        fact: dict[str, Any],
        fact_entry: dict[str, Any],
    ) -> int:
        count = 0
        for data_point in fact.get("data_points", []):
            if not isinstance(data_point, dict):
                continue
            name = str(data_point.get("name") or "").strip()
            if not name:
                continue
            state["data_points"].append(
                {
                    "id": f"dp_{uuid.uuid4().hex[:8]}",
                    "name": name,
                    "value": data_point.get("value", ""),
                    "unit": str(data_point.get("unit") or ""),
                    "year": data_point.get("year"),
                    "source": fact_entry.get("source_name", ""),
                    "confidence": fact_entry.get("credibility_score", 0.5),
                }
            )
            count += 1
        return count

    def _update_hypothesis_from_fact(
        self,
        state: ResearchState,
        fact_entry: dict[str, Any],
    ) -> None:
        hypothesis_id = fact_entry.get("related_hypothesis")
        support = fact_entry.get("hypothesis_support")
        if not hypothesis_id or support not in {"supports", "refutes"}:
            return
        evidence_summary = str(fact_entry.get("content") or "")[:160]
        self._update_hypothesis_status(
            state,
            [
                {
                    "hypothesis_id": hypothesis_id,
                    "evidence_type": support,
                    "evidence_summary": evidence_summary,
                }
            ],
        )

    def _update_hypothesis_status(
        self,
        state: ResearchState,
        evidence: Any,
    ) -> None:
        if not isinstance(evidence, list):
            return
        hypotheses = state.get("hypotheses", [])
        for item in evidence:
            if not isinstance(item, dict):
                continue
            hypothesis_id = item.get("hypothesis_id")
            evidence_type = item.get("evidence_type")
            summary = str(item.get("evidence_summary") or "").strip()
            if not hypothesis_id or not summary:
                continue
            for hypothesis in hypotheses:
                if hypothesis.get("id") != hypothesis_id:
                    continue
                if evidence_type == "supports":
                    hypothesis.setdefault("evidence_for", []).append(summary)
                    hypothesis["status"] = "supported"
                elif evidence_type == "refutes":
                    hypothesis.setdefault("evidence_against", []).append(summary)
                    hypothesis["status"] = "refuted"
                elif hypothesis.get("status") == "unverified":
                    hypothesis["status"] = "partially_supported"
                break
        state["hypotheses"] = hypotheses

    def _update_knowledge_graph(
        self,
        state: ResearchState,
        entities: Any,
    ) -> None:
        if not isinstance(entities, list):
            return
        graph = state.get("knowledge_graph") or {"nodes": [], "edges": []}
        nodes = graph.setdefault("nodes", [])
        edges = graph.setdefault("edges", [])
        existing_labels = {
            str(node.get("label") or node.get("name") or "")
            for node in nodes
            if isinstance(node, dict)
        }

        added = 0
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            name = str(entity.get("name") or "").strip()
            if not name or name in existing_labels:
                continue
            node_id = f"entity_{hashlib.sha256(name.encode()).hexdigest()[:10]}"
            nodes.append(
                {
                    "id": node_id,
                    "label": name,
                    "name": name,
                    "type": entity.get("type") or "entity",
                }
            )
            existing_labels.add(name)
            added += 1
            for relation in entity.get("relations", []):
                edges.append(
                    {
                        "source": "topic",
                        "target": node_id,
                        "type": str(relation),
                    }
                )

        state["knowledge_graph"] = graph
        if added:
            self.add_message(
                state,
                "knowledge_graph",
                {
                    "graph": graph,
                    "stats": {
                        "entitiesCount": len(nodes),
                        "relationsCount": len(edges),
                    },
                    "isIncremental": True,
                },
            )

    def _record_outputs(
        self,
        state: ResearchState,
        new_facts_count: int,
        completed_at: datetime,
    ) -> None:
        output = {
            "facts_count": len(state.get("facts", [])),
            "new_facts_count": new_facts_count,
            "data_points_count": len(state.get("data_points", [])),
            "insights_count": len(state.get("insights", [])),
            "researched_sections": [
                section.get("id")
                for section in state.get("outline", [])
                if section.get("status") == "researching"
            ],
        }
        state["agent_outputs"].append(
            {
                "agent": self.name,
                "phase": ResearchPhase.RESEARCHING.value,
                "status": "completed",
                "output": output,
                "completed_at": completed_at.isoformat(),
            }
        )
        state["phase_outputs"].append(
            {
                "phase": ResearchPhase.RESEARCHING.value,
                "status": "completed",
                "agent": self.name,
                "output": output,
                "completed_at": completed_at.isoformat(),
            }
        )

    def _emit_incremental_results(
        self,
        state: ResearchState,
        query: str,
        section_title: str,
        results: list[dict[str, Any]],
        index: int,
        total: int,
        search_type: str,
    ) -> None:
        if not results:
            return
        self.add_message(
            state,
            "search_progress",
            {
                "query": query,
                "section": section_title,
                "results_count": len(results),
                "progress": f"{index}/{total}",
                "search_type": search_type,
            },
        )
        self.add_message(
            state,
            "search_results",
            {
                "results": [
                    self._to_ui_search_result(result)
                    for result in results[:5]
                ],
                "isIncremental": True,
                "searchType": search_type,
            },
        )

    def _emit_search_results_event(self, state: ResearchState) -> None:
        facts = state.get("facts", [])[-20:]
        if not facts:
            return
        self.add_message(
            state,
            "search_results",
            {
                "results": [
                    {
                        "id": fact.get("id", ""),
                        "title": self._truncate(str(fact.get("content") or ""), 80),
                        "source": fact.get("source_name") or "未知来源",
                        "url": fact.get("source_url") or "",
                        "snippet": self._truncate(str(fact.get("content") or ""), 200),
                        "isLocal": fact.get("source_type") == "local",
                    }
                    for fact in facts
                ],
                "isIncremental": False,
                "searchType": "summary",
            },
        )

    def _normalize_web_response(self, response: Any) -> list[dict[str, Any]]:
        if isinstance(response, WebSearchResponse):
            results = response.results
        elif isinstance(response, list):
            results = response
        else:
            results = getattr(response, "results", [])

        normalized = []
        for item in results:
            if isinstance(item, WebSearchResult):
                payload = item.model_dump()
            elif isinstance(item, dict):
                payload = item
            else:
                payload = {
                    "title": getattr(item, "title", ""),
                    "url": getattr(item, "url", ""),
                    "snippet": getattr(item, "snippet", ""),
                    "summary": getattr(item, "summary", ""),
                    "site_name": getattr(item, "site_name", ""),
                    "date_published": getattr(item, "date_published", ""),
                }
            normalized.append(
                {
                    "url": str(payload.get("url") or ""),
                    "title": str(payload.get("title") or payload.get("name") or "未命名结果"),
                    "summary": str(payload.get("summary") or payload.get("snippet") or ""),
                    "snippet": str(payload.get("snippet") or payload.get("summary") or ""),
                    "site_name": payload.get("site_name") or payload.get("siteName") or "",
                    "date": payload.get("date_published")
                    or payload.get("datePublished")
                    or payload.get("date")
                    or "",
                    "source_type": "web",
                    "is_local": False,
                }
            )
        return normalized

    def _normalize_local_chunk(self, chunk: Any) -> dict[str, Any]:
        if isinstance(chunk, RetrivalChunk):
            payload = chunk.model_dump()
        elif isinstance(chunk, dict):
            payload = chunk
        else:
            payload = {
                "kb_id": getattr(chunk, "kb_id", ""),
                "document_id": getattr(chunk, "document_id", ""),
                "filename": getattr(chunk, "filename", ""),
                "content": getattr(chunk, "content", ""),
                "score": getattr(chunk, "score", 0.0),
                "chunk_id": getattr(chunk, "chunk_id", ""),
            }
        kb_id = payload.get("kb_id") or ""
        document_id = payload.get("document_id") or ""
        chunk_id = payload.get("chunk_id") or ""
        content = str(payload.get("content") or "")
        return {
            "url": f"local://kb/{kb_id}/documents/{document_id}/chunks/{chunk_id}",
            "title": str(payload.get("filename") or "本地知识库片段"),
            "summary": content[:500],
            "snippet": content[:200],
            "site_name": "本地知识库",
            "date": "",
            "source_type": "local",
            "is_local": True,
            "score": float(payload.get("score") or 0.0),
            "metadata": payload.get("metadata") or {},
        }

    def _to_ui_search_result(self, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": f"sr_{uuid.uuid4().hex[:8]}",
            "title": self._truncate(str(result.get("title") or ""), 80),
            "source": result.get("site_name") or "未知来源",
            "url": result.get("url") or "",
            "snippet": self._truncate(
                str(result.get("summary") or result.get("snippet") or ""),
                200,
            ),
            "date": result.get("date") or "",
            "isLocal": bool(result.get("is_local")),
            "score": result.get("score"),
        }

    def _format_search_results(self, results: list[dict[str, Any]]) -> str:
        blocks = []
        for index, result in enumerate(results[:15], start=1):
            blocks.append(
                "\n".join(
                    [
                        f"[{index}] {result.get('title') or '未命名结果'}",
                        f"URL: {result.get('url') or ''}",
                        f"来源: {result.get('site_name') or ''}",
                        f"日期: {result.get('date') or ''}",
                        f"摘要: {self._truncate(str(result.get('summary') or result.get('snippet') or ''), 500)}",
                    ]
                )
            )
        return "\n\n".join(blocks)

    def _format_hypotheses(self, hypotheses: list[dict[str, Any]]) -> str:
        if not hypotheses:
            return "无待验证假设。"
        return "\n".join(
            f"- [{hypothesis.get('id')}] {hypothesis.get('content')} "
            f"(当前状态: {hypothesis.get('status', 'unverified')})"
            for hypothesis in hypotheses
        )

    def _normalize_search_queries(self, value: Any, fallback: str) -> list[str]:
        if isinstance(value, list):
            queries = [str(item).strip() for item in value if str(item).strip()]
        elif isinstance(value, str):
            queries = [item.strip() for item in value.replace("；", ";").split(";") if item.strip()]
        else:
            queries = []
        return queries or [fallback]

    def _normalize_query_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [item.strip() for item in value.replace("；", ";").split(";") if item.strip()]
        return []

    def _collect_supplementary_queries(
        self,
        state: ResearchState,
    ) -> tuple[list[str], list[str]]:
        queries: list[str] = []
        issue_ids: list[str] = []
        queries.extend(self._normalize_query_list(state.get("pending_search_queries", [])))

        for issue in state.get("critic_feedback", []):
            if not isinstance(issue, dict) or issue.get("resolved"):
                continue
            issue_id = str(issue.get("id") or "").strip()
            if issue_id:
                issue_ids.append(issue_id)
            query = str(issue.get("search_query") or "").strip()
            if query:
                queries.append(query)

        return self._dedupe_queries(queries)[:6], self._dedupe_queries(issue_ids)

    def _dedupe_queries(self, queries: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for query in queries:
            normalized = " ".join(str(query).split())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    def _state_user_id(self, state: ResearchState) -> UUID | None:
        raw_user_id = state.get("user_id")
        if raw_user_id is None:
            return None
        try:
            return UUID(str(raw_user_id))
        except ValueError:
            return None

    def _compute_fact_fingerprint(self, content: str) -> str:
        normalized = " ".join(content.split()).lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]

    def _is_duplicate_fact(self, content: str, source_url: str) -> bool:
        fingerprint = self._compute_fact_fingerprint(content)
        if fingerprint in self.fact_fingerprints:
            return True
        self.fact_fingerprints[fingerprint] = source_url
        return False

    def _search_mode_label(self, search_web_enabled: bool, search_local_enabled: bool) -> str:
        modes = []
        if search_web_enabled:
            modes.append("网络搜索")
        if search_local_enabled:
            modes.append("本地知识库检索")
        return " + ".join(modes) if modes else "信息检索"

    def _truncate(self, text: str, max_length: int) -> str:
        if len(text) <= max_length:
            return text
        return text[:max_length] + "..."


DeepScout = Scout

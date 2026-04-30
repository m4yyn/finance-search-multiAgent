import copy
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.service.deep_research.base import BaseAgent
from app.service.deep_research.state import ResearchPhase, ResearchState, to_serializable


DATA_EXTRACTION_PROMPT = """你是“金融行业信息报告编写 Agent 助手”的数据分析 Agent。

## 原始研究课题
{query}

## 已检索事实
{search_result_text}

## 任务
请只基于上面的事实，提取可用于金融行业研究报告、前端图表和后续报告生成的结构化数据。必须遵守：
1. 不编造事实中没有出现的数值、年份、机构、政策或公司名称。
2. 优先提取市场规模、同比/环比增速、行业份额、利润率、估值、风险指标、政策影响等金融报告关键数据。
3. 对时间序列数据保留年份或日期；对分布数据保留类别名称。
4. 每个 data_point 都必须带来源名称和置信度。
5. insights 必须是可以写入行业报告的简短分析判断。

输出严格 JSON object，不要 Markdown：
{{
  "data_points": [
    {{
      "name": "指标名",
      "value": "数值或文本",
      "unit": "单位",
      "year": 2025,
      "source": "来源名称",
      "confidence": 0.0,
      "context": "该数据说明什么"
    }}
  ],
  "time_series": [
    {{
      "name": "时间序列指标名",
      "unit": "单位",
      "points": [
        {{"year": 2023, "value": "数值", "source": "来源名称"}}
      ]
    }}
  ],
  "distributions": [
    {{
      "name": "分布指标名",
      "unit": "单位",
      "items": [
        {{"label": "类别", "value": "数值", "source": "来源名称"}}
      ]
    }}
  ],
  "insights": ["关键洞察"]
}}"""


KNOWLEDGE_GRAPH_PROMPT = """你是金融行业研究知识图谱构建 Agent。

## 原始研究课题
{query}

## 已检索事实
{fact_text}

## 任务
请基于事实重建一张用于前端交互展示的知识图谱。节点应覆盖行业、公司、政策、指标、风险、机会、技术、宏观变量等实体；边表示影响、驱动、约束、竞争、验证等关系。

要求：
1. 只使用事实中出现或由事实直接指向的实体，不要添加无法溯源的实体。
2. importance 使用 1 到 10，越重要的节点越高。
3. 节点 id 必须稳定、短小、只使用英文字母、数字、下划线。
4. 边的 source/target 必须引用节点 id。

输出严格 JSON object，不要 Markdown：
{{
  "nodes": [
    {{
      "id": "topic",
      "label": "实体名称",
      "type": "topic/industry/company/policy/indicator/risk/opportunity/technology/macro",
      "importance": 10,
      "summary": "该节点在研究中的作用"
    }}
  ],
  "edges": [
    {{
      "source": "topic",
      "target": "indicator_1",
      "type": "drives/supports/refutes/affects/competes_with/constrains",
      "weight": 5,
      "description": "关系说明"
    }}
  ]
}}"""


CHART_GENERATION_PROMPT = """你是金融行业研究报告的数据可视化 Agent。

## 原始研究课题
{query}

## 结构化数据
{structured_data}

## 任务
请基于结构化数据生成适合前端 ECharts 渲染的图表配置。必须遵守：
1. 只使用结构化数据中已有的数据，不要补造时间、数值或分类。
2. 图表应服务于金融行业报告表达，例如趋势、结构占比、指标对比、风险分布。
3. echarts_option 必须是合法 ECharts option object，包含 title、tooltip、legend 或坐标轴等必要配置。
4. 趋势图 line 至少需要 2 个时间点；结构图 pie 至少需要 2 个类别；指标对比 bar 的类目数量必须与 series data 数量一致。
5. 标题或副标题必须体现单位、数据口径或来源；不要生成无法解释的单点折线、单值饼图或空白坐标系。
6. 如果数据不足以形成完整图表，只输出空 charts 数组。

输出严格 JSON object，不要 Markdown：
{{
  "charts": [
    {{
      "title": "图表标题",
      "description": "图表说明",
      "chart_type": "line/bar/pie/scatter/table/heatmap",
      "section_id": null,
      "data": {{}},
      "echarts_option": {{}},
      "insight": "该图表支持的核心洞察"
    }}
  ]
}}"""


SYSTEM_PROMPT = (
    "你是专业的金融数据分析、知识图谱和 ECharts 可视化专家。"
    "你的输出必须是 JSON object，并且只能基于用户提供的 Deep Research state 内容。"
)


class DataAnalyst(BaseAgent):
    """Deep Research data analysis agent for structured data and visual outputs."""

    def __init__(
        self,
        llm_api_key: str | None = None,
        llm_base_url: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        super().__init__(
            name="DataAnalyst",
            role="数据分析Agent",
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            model=model,
            client=client,
        )

    async def process(self, state: ResearchState) -> ResearchState:
        """Run data analysis only during the analyzing phase."""

        if state["phase"] != ResearchPhase.ANALYZING.value:
            return state

        try:
            return await self.analyze_data(state)
        except Exception as exc:
            message = f"DataAnalyst analysis failed: {exc}"
            state.setdefault("errors", []).append(message)
            completed_at = datetime.now(timezone.utc)
            self.add_message(
                state,
                "research_step",
                {
                    "step_type": "analyzing",
                    "title": "数据分析",
                    "status": "failed",
                    "error": str(exc),
                    "completed_at": completed_at.isoformat(),
                },
            )
            state.setdefault("agent_outputs", []).append(
                {
                    "agent": self.name,
                    "phase": ResearchPhase.ANALYZING.value,
                    "status": "failed",
                    "output": {"error": str(exc)},
                    "completed_at": completed_at.isoformat(),
                }
            )
            return state

    async def analyze_data(self, state: ResearchState) -> ResearchState:
        """Extract data, rebuild the graph, and generate chart configurations."""

        started_at = datetime.now(timezone.utc)
        step_id = f"step_analyzing_{uuid.uuid4().hex[:8]}"
        initial_data_points = len(state.get("data_points", []))
        initial_insights = len(state.get("insights", []))

        self.add_message(
            state,
            "research_step",
            {
                "step_id": step_id,
                "step_type": "analyzing",
                "title": "数据分析",
                "subtitle": "结构化数据、知识图谱与可视化配置",
                "status": "running",
                "started_at": started_at.isoformat(),
                "stats": {
                    "facts_count": len(state.get("facts", [])),
                    "existing_data_points": initial_data_points,
                },
            },
        )

        self.add_message(
            state,
            "thought",
            {"content": "开始从已检索事实中提取金融指标、时间序列和可视化数据。"},
        )
        extracted_data = await self.extract_data(state)

        self.add_message(
            state,
            "thought",
            {"content": "正在基于事实和结构化数据重建研究知识图谱。"},
        )
        knowledge_graph = await self.build_knowledge_graph(state)
        state["knowledge_graph"] = knowledge_graph
        self.add_message(
            state,
            "knowledge_graph",
            {
                "graph": knowledge_graph,
                "stats": {
                    "entitiesCount": len(knowledge_graph.get("nodes", [])),
                    "relationsCount": len(knowledge_graph.get("edges", [])),
                },
                "isIncremental": False,
            },
        )

        self.add_message(
            state,
            "thought",
            {"content": "正在生成可用于前端交互展示的 ECharts 图表配置。"},
        )
        charts = await self.generate_charts(state, extracted_data)
        state["charts"] = charts
        self.add_message(
            state,
            "charts",
            {
                "charts": charts,
                "stats": {"chartsCount": len(charts)},
                "isIncremental": False,
            },
        )

        completed_at = datetime.now(timezone.utc)
        output = {
            "data_points_count": len(state.get("data_points", [])),
            "new_data_points_count": len(state.get("data_points", [])) - initial_data_points,
            "insights_count": len(state.get("insights", [])),
            "new_insights_count": len(state.get("insights", [])) - initial_insights,
            "charts_count": len(charts),
            "knowledge_graph_nodes": len(knowledge_graph.get("nodes", [])),
            "knowledge_graph_edges": len(knowledge_graph.get("edges", [])),
        }
        state.setdefault("agent_outputs", []).append(
            {
                "agent": self.name,
                "phase": ResearchPhase.ANALYZING.value,
                "status": "completed",
                "output": output,
                "completed_at": completed_at.isoformat(),
            }
        )
        state.setdefault("phase_outputs", []).append(
            {
                "phase": ResearchPhase.ANALYZING.value,
                "status": "completed",
                "agent": self.name,
                "output": output,
                "completed_at": completed_at.isoformat(),
            }
        )
        self.add_log(
            state,
            action="analyze_data",
            input_summary=f"facts={len(state.get('facts', []))}",
            output_summary=f"charts={len(charts)} data_points={output['data_points_count']}",
            duration_ms=int((completed_at - started_at).total_seconds() * 1000),
        )
        self.add_message(
            state,
            "research_step",
            {
                "step_id": step_id,
                "step_type": "analyzing",
                "title": "数据分析",
                "subtitle": "结构化数据、知识图谱与可视化配置",
                "status": "completed",
                "completed_at": completed_at.isoformat(),
                "stats": output,
            },
        )
        return state

    async def extract_data(self, state: ResearchState) -> dict[str, Any]:
        """Extract structured data from facts and merge data points into state."""

        search_result_text = self._format_facts_for_prompt(state.get("facts", []))
        if not search_result_text:
            return {"data_points": [], "time_series": [], "distributions": [], "insights": []}

        try:
            response = await self.call_llm(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=DATA_EXTRACTION_PROMPT.format(
                    query=state["query"],
                    search_result_text=search_result_text,
                ),
                json_mode=True,
                temperature=0.2,
                max_tokens=12000,
            )
            parsed = self.parse_json_response(response)
        except Exception as exc:
            state.setdefault("errors", []).append(f"DataAnalyst data extraction failed: {exc}")
            return {"data_points": [], "time_series": [], "distributions": [], "insights": []}

        extracted = self._normalize_extracted_data(parsed)
        self._merge_data_points(state, extracted["data_points"])
        self._merge_insights(state, extracted["insights"])
        self.add_message(
            state,
            "observation",
            {
                "title": "结构化数据提取",
                "data_points_count": len(extracted["data_points"]),
                "time_series_count": len(extracted["time_series"]),
                "distributions_count": len(extracted["distributions"]),
                "insights": extracted["insights"][:5],
            },
        )
        return extracted

    async def build_knowledge_graph(self, state: ResearchState) -> dict[str, Any]:
        """Rebuild the knowledge graph from extracted facts."""

        fact_text = self._format_facts_for_prompt(state.get("facts", []), max_items=40)
        if not fact_text:
            return self._normalize_knowledge_graph(
                state.get("knowledge_graph") or {"nodes": [], "edges": []},
                state,
            )

        try:
            response = await self.call_llm(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=KNOWLEDGE_GRAPH_PROMPT.format(
                    query=state["query"],
                    fact_text=fact_text,
                ),
                json_mode=True,
                temperature=0.15,
                max_tokens=10000,
            )
            parsed = self.parse_json_response(response)
        except Exception as exc:
            state.setdefault("errors", []).append(f"DataAnalyst knowledge graph failed: {exc}")
            return self._normalize_knowledge_graph(
                state.get("knowledge_graph") or {"nodes": [], "edges": []},
                state,
            )

        return self._normalize_knowledge_graph(parsed, state)

    async def generate_charts(
        self,
        state: ResearchState,
        extracted_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Generate ECharts chart configurations from extracted data."""

        if not self._has_visualizable_data(extracted_data):
            return []

        try:
            response = await self.call_llm(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=CHART_GENERATION_PROMPT.format(
                    query=state["query"],
                    structured_data=json.dumps(
                        to_serializable(extracted_data),
                        ensure_ascii=False,
                    )[:14000],
                ),
                json_mode=True,
                temperature=0.2,
                max_tokens=12000,
            )
            parsed = self.parse_json_response(response)
        except Exception as exc:
            state.setdefault("errors", []).append(f"DataAnalyst chart generation failed: {exc}")
            return []

        return self._normalize_charts(parsed)

    def _normalize_extracted_data(self, payload: dict[str, Any]) -> dict[str, Any]:
        data_points = payload.get("data_points") if isinstance(payload, dict) else []
        time_series = payload.get("time_series") if isinstance(payload, dict) else []
        distributions = (
            payload.get("distributions")
            if isinstance(payload, dict)
            else []
        )
        if not distributions and isinstance(payload, dict):
            distributions = payload.get("distribution")

        return {
            "data_points": [
                self._normalize_data_point(item)
                for item in data_points
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            ],
            "time_series": [
                item
                for item in (time_series if isinstance(time_series, list) else [])
                if isinstance(item, dict)
            ],
            "distributions": [
                item
                for item in (distributions if isinstance(distributions, list) else [])
                if isinstance(item, dict)
            ],
            "insights": [
                str(item).strip()
                for item in (payload.get("insights", []) if isinstance(payload, dict) else [])
                if str(item).strip()
            ],
        }

    def _normalize_data_point(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(item.get("id") or f"dp_{uuid.uuid4().hex[:8]}"),
            "name": str(item.get("name") or "").strip(),
            "value": item.get("value", ""),
            "unit": str(item.get("unit") or ""),
            "year": item.get("year"),
            "source": str(item.get("source") or item.get("source_name") or ""),
            "confidence": self._clamp_float(item.get("confidence"), 0.0, 1.0, 0.6),
            "metadata": {
                "context": item.get("context", ""),
                "generated_by": self.name,
            },
        }

    def _merge_data_points(
        self,
        state: ResearchState,
        data_points: list[dict[str, Any]],
    ) -> None:
        existing = {
            self._data_point_key(point)
            for point in state.setdefault("data_points", [])
            if isinstance(point, dict)
        }
        for point in data_points:
            key = self._data_point_key(point)
            if not key or key in existing:
                continue
            state["data_points"].append(point)
            existing.add(key)

    def _merge_insights(self, state: ResearchState, insights: list[str]) -> None:
        normalized_existing = {item.strip() for item in state.setdefault("insights", [])}
        for insight in insights:
            if insight not in normalized_existing:
                state["insights"].append(insight)
                normalized_existing.add(insight)

    def _normalize_knowledge_graph(
        self,
        payload: dict[str, Any],
        state: ResearchState,
    ) -> dict[str, Any]:
        graph_payload = payload.get("knowledge_graph") if isinstance(payload, dict) else None
        if isinstance(graph_payload, dict):
            payload = graph_payload

        raw_nodes = payload.get("nodes", []) if isinstance(payload, dict) else []
        raw_edges = payload.get("edges", []) if isinstance(payload, dict) else []
        nodes: list[dict[str, Any]] = []
        id_by_label: dict[str, str] = {}

        topic_label = state.get("query") or "研究课题"
        topic_node = {
            "id": "topic",
            "label": topic_label,
            "display_label": self._truncate_label(str(topic_label)),
            "name": topic_label,
            "type": "topic",
            "importance": 10.0,
            "size": 50.0,
            "summary": "原始研究课题",
        }
        nodes.append(topic_node)
        id_by_label[str(topic_label)] = "topic"

        seen_ids = {"topic"}
        for raw_node in raw_nodes if isinstance(raw_nodes, list) else []:
            if not isinstance(raw_node, dict):
                continue
            label = str(
                raw_node.get("label")
                or raw_node.get("name")
                or raw_node.get("title")
                or ""
            ).strip()
            if not label:
                continue
            node_id = self._safe_node_id(raw_node.get("id"), label)
            if node_id == "topic":
                nodes[0] = {
                    **topic_node,
                    "label": label,
                    "name": label,
                    "summary": raw_node.get("summary", topic_node["summary"]),
                }
                id_by_label[label] = "topic"
                continue
            if node_id in seen_ids:
                continue
            importance = self._clamp_float(raw_node.get("importance"), 1.0, 10.0, 5.0)
            node = {
                "id": node_id,
                "label": label,
                "display_label": self._truncate_label(label),
                "name": label,
                "type": str(raw_node.get("type") or "entity"),
                "importance": importance,
                "size": 20 + importance * 3,
                "summary": str(raw_node.get("summary") or ""),
            }
            nodes.append(node)
            seen_ids.add(node_id)
            id_by_label[label] = node_id
            if len(nodes) >= 24:
                break

        edges: list[dict[str, Any]] = []
        seen_edges: set[tuple[str, str, str]] = set()
        for raw_edge in raw_edges if isinstance(raw_edges, list) else []:
            if not isinstance(raw_edge, dict):
                continue
            source = self._resolve_node_ref(raw_edge.get("source"), seen_ids, id_by_label)
            target = self._resolve_node_ref(raw_edge.get("target"), seen_ids, id_by_label)
            if not source or not target or source == target:
                continue
            relation_type = str(raw_edge.get("type") or raw_edge.get("relation") or "related_to")
            edge_key = (source, target, relation_type)
            if edge_key in seen_edges:
                continue
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "type": relation_type,
                    "relation": relation_type,
                    "weight": self._clamp_float(raw_edge.get("weight"), 1.0, 10.0, 3.0),
                    "description": str(raw_edge.get("description") or ""),
                }
            )
            seen_edges.add(edge_key)
            if len(edges) >= 40:
                break

        return {"nodes": nodes, "edges": edges}

    def _normalize_charts(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_charts = payload.get("charts", []) if isinstance(payload, dict) else []
        if not isinstance(raw_charts, list):
            return []

        charts: list[dict[str, Any]] = []
        for raw_chart in raw_charts:
            if not isinstance(raw_chart, dict):
                continue
            title = str(raw_chart.get("title") or "").strip()
            if not title:
                continue
            chart_type = str(raw_chart.get("chart_type") or raw_chart.get("type") or "bar")
            option = raw_chart.get("echarts_option") or raw_chart.get("option") or {}
            if not isinstance(option, dict):
                option = {}
            normalized_option = self._normalize_echarts_option(
                option,
                chart_type=chart_type,
                title=title,
                raw_data=raw_chart.get("data") if isinstance(raw_chart.get("data"), dict) else {},
            )
            if normalized_option is None:
                continue
            charts.append(
                {
                    "id": str(uuid.uuid4()),
                    "title": title,
                    "description": str(raw_chart.get("description") or ""),
                    "chart_type": chart_type,
                    "type": chart_type,
                    "section_id": raw_chart.get("section_id"),
                    "data": raw_chart.get("data") if isinstance(raw_chart.get("data"), dict) else {},
                    "echarts_option": normalized_option,
                    "metadata": {
                        "insight": raw_chart.get("insight", ""),
                        "generated_by": self.name,
                    },
                }
            )
        return charts

    def _normalize_echarts_option(
        self,
        option: dict[str, Any],
        chart_type: str,
        title: str,
        raw_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        normalized = copy.deepcopy(option)
        chart_type = chart_type.lower().strip()
        if chart_type in {"table", "kpi"}:
            return None
        series = normalized.get("series")
        if isinstance(series, dict):
            series = [series]
        if not isinstance(series, list) or not series:
            return None
        series = [item for item in series if isinstance(item, dict)]
        if not series:
            return None

        categories = self._category_axis_data(normalized)
        labels = raw_data.get("labels") or raw_data.get("categories") or raw_data.get("years")
        values = raw_data.get("values") or raw_data.get("value") or raw_data.get("growth_rate")
        if not isinstance(labels, list):
            labels = []
        if not isinstance(values, list):
            values = []

        if chart_type == "line":
            valid_series = [
                item
                for item in series
                if isinstance(item.get("data"), list) and len(item["data"]) >= 2
            ]
            if not valid_series:
                return None
            series = valid_series

        if chart_type == "pie":
            data = series[0].get("data")
            if isinstance(data, list) and len(data) >= 2:
                pass
            elif len(labels) >= 2 and len(values) >= 2:
                series[0]["data"] = [
                    {"name": str(label), "value": value}
                    for label, value in zip(labels, values, strict=False)
                ]
            else:
                return None

        if chart_type == "bar":
            if categories and len(series) > 1 and all(
                isinstance(item.get("data"), list) and len(item["data"]) == 1
                for item in series
            ):
                series = [
                    {
                        "type": "bar",
                        "name": title,
                        "data": [item["data"][0] for item in series],
                    }
                ]
            if categories:
                fixed_series: list[dict[str, Any]] = []
                for item in series:
                    data = item.get("data")
                    if isinstance(data, list) and len(data) == len(categories):
                        fixed_series.append(item)
                    elif len(values) == len(categories):
                        fixed_series.append({**item, "data": values})
                if not fixed_series:
                    return None
                series = fixed_series

        normalized["series"] = series
        normalized.setdefault("title", {"text": title})
        normalized.setdefault("tooltip", {"trigger": "axis" if chart_type != "pie" else "item"})
        if chart_type != "pie":
            normalized.setdefault("grid", {})
            if isinstance(normalized["grid"], dict):
                normalized["grid"] = {
                    "left": normalized["grid"].get("left", 48),
                    "right": normalized["grid"].get("right", 24),
                    "top": normalized["grid"].get("top", 64),
                    "bottom": normalized["grid"].get("bottom", 48),
                    "containLabel": True,
                }
            normalized.setdefault("xAxis", {"type": "category", "data": categories})
            normalized.setdefault("yAxis", {"type": "value"})
        if len(series) > 1 or chart_type == "pie":
            normalized.setdefault("legend", {"bottom": 0})
        elif chart_type != "pie":
            normalized.pop("legend", None)
        return normalized

    def _category_axis_data(self, option: dict[str, Any]) -> list[Any]:
        for axis_name in ("xAxis", "yAxis"):
            axis = option.get(axis_name)
            axis_items = axis if isinstance(axis, list) else [axis]
            for axis_item in axis_items:
                if (
                    isinstance(axis_item, dict)
                    and axis_item.get("type") == "category"
                    and isinstance(axis_item.get("data"), list)
                ):
                    return axis_item["data"]
        return []

    def _format_facts_for_prompt(
        self,
        facts: list[dict[str, Any]],
        max_items: int = 30,
    ) -> str:
        blocks = []
        for index, fact in enumerate(facts[:max_items], start=1):
            if not isinstance(fact, dict):
                continue
            content = str(fact.get("content") or "").strip()
            if not content:
                continue
            source_name = str(fact.get("source_name") or "未知来源")
            source_url = str(fact.get("source_url") or "")
            blocks.append(
                "\n".join(
                    [
                        f"[{index}] {content}",
                        f"来源: {source_name}",
                        f"URL: {source_url}",
                    ]
                )
            )
        return "\n\n".join(blocks)

    def _has_visualizable_data(self, extracted_data: dict[str, Any]) -> bool:
        return any(
            bool(extracted_data.get(key))
            for key in ("data_points", "time_series", "distributions")
        )

    def _data_point_key(self, point: dict[str, Any]) -> str:
        name = str(point.get("name") or "").strip().lower()
        value = str(point.get("value") or "").strip().lower()
        unit = str(point.get("unit") or "").strip().lower()
        year = str(point.get("year") or "").strip()
        source = str(point.get("source") or "").strip().lower()
        if not name:
            return ""
        return "|".join([name, value, unit, year, source])

    def _safe_node_id(self, raw_id: Any, label: str) -> str:
        candidate = str(raw_id or "").strip().lower()
        safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in candidate)
        safe = "_".join(part for part in safe.split("_") if part)
        if safe:
            return safe[:64]
        return f"node_{uuid.uuid5(uuid.NAMESPACE_URL, label).hex[:12]}"

    def _truncate_label(self, label: str, max_length: int = 12) -> str:
        return label if len(label) <= max_length else f"{label[:max_length]}…"

    def _resolve_node_ref(
        self,
        value: Any,
        known_ids: set[str],
        id_by_label: dict[str, str],
    ) -> str | None:
        if value is None:
            return None
        raw = str(value).strip()
        if raw in known_ids:
            return raw
        return id_by_label.get(raw)

    def _clamp_float(
        self,
        value: Any,
        minimum: float,
        maximum: float,
        default: float,
    ) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, numeric))


__all__ = ["DataAnalyst"]

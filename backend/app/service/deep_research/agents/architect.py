import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app.service.deep_research.base import BaseAgent
from app.service.deep_research.state import ResearchPhase, ResearchState


PLANNING_PROMPT = """研究课题：{query}

你是金融行业信息报告编写 Agent 助手的研究规划 Agent。
请先理解课题所属行业、公司、指标、政策与时间范围，再把研究任务拆成可执行的检索计划。

请为该课题生成研究大纲和研究假设，输出JSON格式如下：

{{
  "hypothesis_1": "关于市场/行业趋势的假设（需要验证）",
  "hypothesis_2": "关于竞争格局或技术发展的假设（需要验证）",
  "hypothesis_3": "关于政策或外部因素影响的假设（需要验证）",
  "sec_1_title": "市场概况",
  "sec_1_desc": "描述市场规模、增速",
  "sec_1_query": "搜索关键词",
  "sec_2_title": "竞争格局",
  "sec_2_desc": "描述主要企业",
  "sec_2_query": "搜索关键词",
  "sec_3_title": "技术趋势",
  "sec_3_desc": "描述核心技术",
  "sec_3_query": "搜索关键词",
  "sec_4_title": "政策环境",
  "sec_4_desc": "描述相关政策",
  "sec_4_query": "搜索关键词",
  "sec_5_title": "挑战机遇",
  "sec_5_desc": "描述挑战和机会",
  "sec_5_query": "搜索关键词",
  "sec_6_title": "未来展望",
  "sec_6_desc": "描述发展趋势",
  "sec_6_query": "搜索关键词",
  "questions": "核心问题1;核心问题2;核心问题3"
}}

研究假设示例：
- 假设市场规模将持续增长，需要用数据验证增速
- 假设某类技术会成为主流，需要找证据支持或反驳
- 假设政策变化会影响行业格局，需要分析政策走向

金融研究要求：
1. 章节必须覆盖市场规模/增速、竞争格局、技术或产品趋势、政策环境、挑战机遇、未来展望。
2. 检索关键词应可直接交给下游 Scout 搜索，尽量包含行业/公司/指标/年份/政策/报告等关键词。
3. 不要编造具体数据，只提出需要验证的问题和检索方向。
4. 每个字段都是字符串类型，只输出 JSON object，不要输出 Markdown 或解释。"""


FALLBACK_PLANNING_PROMPT = """请为研究课题生成严格 JSON object，不要 Markdown，不要解释。

研究课题：{query}

必须输出这些字符串字段：
hypothesis_1, hypothesis_2, hypothesis_3,
sec_1_title, sec_1_desc, sec_1_query,
sec_2_title, sec_2_desc, sec_2_query,
sec_3_title, sec_3_desc, sec_3_query,
sec_4_title, sec_4_desc, sec_4_query,
sec_5_title, sec_5_desc, sec_5_query,
sec_6_title, sec_6_desc, sec_6_query,
questions

章节固定为：市场概况、竞争格局、技术趋势、政策环境、挑战机遇、未来展望。
questions 用英文分号 ; 分隔 3 个核心问题。"""


SYSTEM_PROMPT = (
    "你是一位专业的金融行业研究规划师。"
    "你的任务是把用户研究课题拆解为假设驱动、可检索、可执行的研究计划。"
    "必须严格输出 JSON object。"
)


class Architect(BaseAgent):
    """Deep Research planning agent that turns a query into an executable plan."""

    def __init__(
        self,
        llm_api_key: str | None = None,
        llm_base_url: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        super().__init__(
            name="Architect",
            role="研究规划师",
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            model=model,
            client=client,
        )

    async def process(self, state: ResearchState) -> ResearchState:
        """Run initial planning only from the init phase."""

        if state["phase"] != ResearchPhase.INIT.value:
            return state
        return await self._initial_planning(state)

    async def _initial_planning(self, state: ResearchState) -> ResearchState:
        step_id = f"step_planning_{uuid.uuid4().hex[:8]}"
        started_at = datetime.now(timezone.utc)
        query = state["query"]

        self.add_message(
            state,
            "research_step",
            {
                "step_id": step_id,
                "step_type": "planning",
                "title": "研究计划",
                "subtitle": "理解课题并生成检索大纲",
                "status": "running",
                "started_at": started_at.isoformat(),
                "stats": {},
            },
        )
        self.add_message(
            state,
            "thought",
            {
                "content": "正在分析研究问题，生成研究假设、核心问题和下游检索计划。",
            },
        )

        max_retries = 2
        final_plan: dict[str, Any] | None = None
        failure_reason = "未开始生成研究计划。"

        for attempt in range(max_retries):
            prompt = (
                PLANNING_PROMPT.format(query=query)
                if attempt == 0
                else FALLBACK_PLANNING_PROMPT.format(query=query)
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
                candidate = self._normalize_planning_result(parsed)
                valid, failure_reason = self._validate_plan(candidate)
                if valid:
                    final_plan = candidate
                    break
            except Exception as exc:
                failure_reason = f"LLM planning call failed: {exc}"
                self.logger.warning(failure_reason)

            if attempt < max_retries - 1:
                self.add_message(
                    state,
                    "planning_retry",
                    {
                        "attempt": attempt + 1,
                        "max_retries": max_retries,
                        "reason": failure_reason,
                        "next_strategy": "fallback_prompt",
                    },
                )

        if final_plan is None:
            return self._mark_planning_failed(state, step_id, failure_reason)

        self._apply_plan_to_state(state, final_plan)
        state["phase"] = ResearchPhase.PLANNING.value

        completed_at = datetime.now(timezone.utc)
        duration_ms = int((completed_at - started_at).total_seconds() * 1000)
        self.add_log(
            state,
            action="initial_planning",
            input_summary=query[:200],
            output_summary=(
                f"生成 {len(state['outline'])} 个章节、"
                f"{len(state['hypotheses'])} 个研究假设、"
                f"{len(state['research_questions'])} 个核心问题"
            ),
            duration_ms=duration_ms,
        )
        self._record_outputs(state, final_plan, completed_at)

        self.add_message(
            state,
            "outline",
            {
                "outline": state["outline"],
                "hypotheses": state["hypotheses"],
                "research_questions": state["research_questions"],
                "knowledge_graph": state["knowledge_graph"],
            },
        )
        self.add_message(
            state,
            "research_step",
            {
                "step_id": step_id,
                "step_type": "planning",
                "title": "研究计划",
                "subtitle": "研究大纲已生成",
                "status": "completed",
                "completed_at": completed_at.isoformat(),
                "stats": {
                    "sections_count": len(state["outline"]),
                    "hypotheses_count": len(state["hypotheses"]),
                    "questions_count": len(state["research_questions"]),
                },
            },
        )
        return state

    def _normalize_planning_result(self, result: dict[str, Any]) -> dict[str, Any]:
        if not result:
            return {"outline": [], "hypotheses": [], "research_questions": []}

        if result.get("outline"):
            outline = self._normalize_outline(result.get("outline"))
        else:
            outline = self._convert_flat_to_outline(result)

        hypotheses = self._extract_hypotheses(result)
        research_questions = self._extract_research_questions(result)
        return {
            "outline": outline,
            "hypotheses": hypotheses,
            "research_questions": research_questions,
        }

    def _convert_flat_to_outline(self, flat_result: dict[str, Any]) -> list[dict[str, Any]]:
        outline: list[dict[str, Any]] = []
        for index in range(1, 10):
            title = self._clean_text(flat_result.get(f"sec_{index}_title"))
            if not title:
                continue
            description = self._clean_text(flat_result.get(f"sec_{index}_desc"))
            query = self._clean_text(flat_result.get(f"sec_{index}_query")) or title
            outline.append(
                self._build_section(
                    index=index,
                    title=title,
                    description=description,
                    search_queries=[query],
                )
            )
        return outline

    def _normalize_outline(self, outline_value: Any) -> list[dict[str, Any]]:
        if not isinstance(outline_value, list):
            return []

        normalized: list[dict[str, Any]] = []
        for index, raw_section in enumerate(outline_value, start=1):
            if not isinstance(raw_section, dict):
                continue
            title = self._clean_text(raw_section.get("title"))
            if not title:
                continue
            description = self._clean_text(raw_section.get("description"))
            search_queries = raw_section.get("search_queries") or raw_section.get("search_query")
            normalized.append(
                self._build_section(
                    index=index,
                    title=title,
                    description=description,
                    search_queries=self._normalize_search_queries(search_queries, title),
                    section_id=self._clean_text(raw_section.get("id")) or f"sec_{index}",
                    section_type=self._clean_text(raw_section.get("section_type")) or "mixed",
                    requires_data=bool(raw_section.get("requires_data", index <= 2)),
                    requires_chart=bool(raw_section.get("requires_chart", index <= 2)),
                )
            )
        return normalized

    def _build_section(
        self,
        index: int,
        title: str,
        description: str,
        search_queries: list[str],
        section_id: str | None = None,
        section_type: str = "mixed",
        requires_data: bool | None = None,
        requires_chart: bool | None = None,
    ) -> dict[str, Any]:
        return {
            "id": section_id or f"sec_{index}",
            "title": title,
            "description": description,
            "section_type": section_type if section_type in {"qualitative", "quantitative", "mixed"} else "mixed",
            "status": "pending",
            "requires_data": index <= 2 if requires_data is None else requires_data,
            "requires_chart": index <= 2 if requires_chart is None else requires_chart,
            "priority": index,
            "search_queries": self._normalize_search_queries(search_queries, title),
        }

    def _extract_hypotheses(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        if isinstance(result.get("hypotheses"), list):
            hypotheses: list[dict[str, Any]] = []
            for index, hypothesis in enumerate(result["hypotheses"], start=1):
                content = (
                    self._clean_text(hypothesis.get("content"))
                    if isinstance(hypothesis, dict)
                    else self._clean_text(hypothesis)
                )
                if content:
                    hypotheses.append(self._build_hypothesis(index, content))
            return hypotheses

        hypotheses = []
        for index in range(1, 8):
            content = self._clean_text(result.get(f"hypothesis_{index}"))
            if content:
                hypotheses.append(self._build_hypothesis(index, content))
        return hypotheses

    def _build_hypothesis(self, index: int, content: str) -> dict[str, Any]:
        return {
            "id": f"h_{index}",
            "content": content,
            "status": "unverified",
            "evidence_for": [],
            "evidence_against": [],
        }

    def _extract_research_questions(self, result: dict[str, Any]) -> list[str]:
        questions = result.get("research_questions", result.get("questions", ""))
        if isinstance(questions, list):
            return [self._clean_text(question) for question in questions if self._clean_text(question)]
        if isinstance(questions, str):
            return [
                question.strip()
                for question in re.split(r"[;；\n]+", questions)
                if question.strip()
            ]
        return []

    def _normalize_search_queries(self, value: Any, fallback: str) -> list[str]:
        if isinstance(value, list):
            queries = [self._clean_text(item) for item in value]
        elif isinstance(value, str):
            queries = [item.strip() for item in re.split(r"[;；\n]+", value) if item.strip()]
        else:
            queries = []
        queries = [query for query in queries if query]
        return queries or [fallback]

    def _validate_plan(self, plan: dict[str, Any]) -> tuple[bool, str]:
        outline = plan.get("outline", [])
        hypotheses = plan.get("hypotheses", [])
        questions = plan.get("research_questions", [])

        if len(outline) < 5:
            return False, f"研究大纲章节不足：{len(outline)} < 5"
        for section in outline:
            if not section.get("title"):
                return False, "研究大纲存在缺少标题的章节"
            if not section.get("search_queries"):
                return False, f"章节缺少检索关键词：{section.get('title')}"
        if len(hypotheses) < 2:
            return False, f"研究假设不足：{len(hypotheses)} < 2"
        if len(questions) < 1:
            return False, "核心研究问题为空"
        return True, ""

    def _apply_plan_to_state(
        self,
        state: ResearchState,
        plan: dict[str, Any],
    ) -> None:
        state["outline"] = plan["outline"]
        state["hypotheses"] = plan["hypotheses"]
        state["research_questions"] = plan["research_questions"]
        state["key_entities"] = self._infer_key_entities(state)
        state["mind_map"] = self._build_mind_map(state)
        state["knowledge_graph"] = self._build_knowledge_graph(state)

    def _record_outputs(
        self,
        state: ResearchState,
        plan: dict[str, Any],
        completed_at: datetime,
    ) -> None:
        output = {
            "outline": plan["outline"],
            "hypotheses": plan["hypotheses"],
            "research_questions": plan["research_questions"],
            "knowledge_graph": state["knowledge_graph"],
        }
        state["agent_outputs"].append(
            {
                "agent": self.name,
                "phase": ResearchPhase.PLANNING.value,
                "status": "completed",
                "output": output,
                "completed_at": completed_at.isoformat(),
            }
        )
        state["phase_outputs"].append(
            {
                "phase": ResearchPhase.PLANNING.value,
                "status": "completed",
                "agent": self.name,
                "output": {
                    "sections_count": len(plan["outline"]),
                    "hypotheses_count": len(plan["hypotheses"]),
                    "questions_count": len(plan["research_questions"]),
                },
                "completed_at": completed_at.isoformat(),
            }
        )

    def _mark_planning_failed(
        self,
        state: ResearchState,
        step_id: str,
        reason: str,
    ) -> ResearchState:
        error = f"Architect planning failed: {reason}"
        state["errors"].append(error)
        failed_at = datetime.now(timezone.utc)
        state["agent_outputs"].append(
            {
                "agent": self.name,
                "phase": ResearchPhase.INIT.value,
                "status": "failed",
                "output": {"error": error},
                "completed_at": failed_at.isoformat(),
            }
        )
        self.add_message(
            state,
            "research_step",
            {
                "step_id": step_id,
                "step_type": "planning",
                "title": "研究计划",
                "subtitle": "研究大纲生成失败",
                "status": "failed",
                "completed_at": failed_at.isoformat(),
                "error": error,
            },
        )
        return state

    def _build_mind_map(self, state: ResearchState) -> dict[str, Any]:
        return {
            "topic": state["query"],
            "sections": [
                {
                    "id": section["id"],
                    "title": section["title"],
                    "search_queries": section["search_queries"],
                }
                for section in state["outline"]
            ],
            "hypotheses": [
                {"id": hypothesis["id"], "content": hypothesis["content"]}
                for hypothesis in state["hypotheses"]
            ],
            "research_questions": state["research_questions"],
        }

    def _build_knowledge_graph(self, state: ResearchState) -> dict[str, Any]:
        nodes = [{"id": "topic", "label": state["query"], "type": "topic"}]
        edges: list[dict[str, str]] = []

        for section in state["outline"]:
            nodes.append(
                {
                    "id": section["id"],
                    "label": section["title"],
                    "type": "section",
                }
            )
            edges.append({"source": "topic", "target": section["id"], "type": "covers"})

        for hypothesis in state["hypotheses"]:
            nodes.append(
                {
                    "id": hypothesis["id"],
                    "label": hypothesis["content"],
                    "type": "hypothesis",
                }
            )
            edges.append({"source": "topic", "target": hypothesis["id"], "type": "tests"})

        return {"nodes": nodes, "edges": edges}

    def _infer_key_entities(self, state: ResearchState) -> list[str]:
        entities = [state["query"]]
        entities.extend(section["title"] for section in state["outline"])
        return list(dict.fromkeys(entity for entity in entities if entity))

    def _clean_text(self, value: Any) -> str:
        if value is None:
            return ""
        return " ".join(str(value).split()).strip()


ChiefArchitect = Architect

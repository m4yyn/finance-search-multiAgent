import ast
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app.service.deep_research.base import BaseAgent
from app.service.deep_research.state import ResearchPhase, ResearchState, to_serializable


SECTION_WRITING_PROMPT = """你是“金融行业信息报告编写 Agent 助手”的资深金融研究报告撰写 Agent。

## 原始研究课题
{query}

## 当前章节
标题：{section_title}
描述：{section_description}
类型：{section_type}

## 可用材料
### 相关事实
{facts}

### 结构化数据
{data_points}

### 已有洞察
{insights}

### 相关图表
{charts_info}

## 写作任务
请撰写该章节正文，服务于金融行业研究、公司/行业信息解释、财务与政策影响分析。必须遵守：
1. 只使用给定材料，不编造公司、年份、财务指标、监管文件或来源。
2. 用研究报告语气解释“发生了什么、为什么重要、对行业/公司经营与风险有什么影响”。
3. 关键判断必须有事实、数据或来源支撑；来源使用 Markdown 链接：[来源名称](URL)。local:// 来源只写来源名称，不做外链。
4. 如存在图表，用 `![图表标题](chart_id)` 引用；报告图、ECharts 图都只作为论据辅助。
5. 必须包含风险与不确定性表述，避免单边结论。
6. 禁止输出股票推荐、买入/卖出/持有评级、目标价、收益承诺、仓位建议或任何交易指令。
7. 不要重复章节标题；正文 500-1000 字，Markdown 格式。

输出严格 JSON object，不要 Markdown 代码块：
{{
  "content": "章节正文内容",
  "key_points": ["核心要点1", "核心要点2"],
  "citations": [
    {{"source": "来源名称", "url": "https://example.com", "title": "来源标题"}}
  ],
  "suggested_improvements": ["仍需补充的信息"]
}}"""


SYNTHESIS_PROMPT = """你是金融研究报告主编，需要将章节草稿整合成完整报告。

## 原始研究课题
{query}

## 章节草稿
{sections_content}

## 可用来源
{all_sources}

## 任务
生成完整金融研究报告。报告必须用于信息解释、资料整理、行业/公司经营分析和风险识别，而不是股票投资推荐。

## 结构要求
1. 直接从 `## 执行摘要` 开始，不要使用 # 一级标题。
2. 固定包含：执行摘要、分章节正文、风险与限制、结论与展望、参考文献。
3. 分章节正文按 outline 顺序整合，标题使用 `## 1 章节名`、`### 1.1 小节名`。
4. 参考文献去重，正文引用使用 `[来源名称](URL)`；local:// 来源只保留来源名。
5. 明确说明数据局限、来源局限和外部不确定性。
6. 禁止输出股票推荐、买入/卖出/持有评级、目标价、收益承诺、仓位建议或任何交易指令。

输出严格 JSON object，不要 Markdown 代码块：
{{
  "executive_summary": "执行摘要",
  "full_report": "完整 Markdown 报告",
  "conclusions": ["核心结论1", "核心结论2"],
  "outlook": "结论与展望",
  "references": [
    {{"id": 1, "title": "来源标题", "source": "来源机构", "url": "https://example.com", "author": "机构", "date": "日期"}}
  ]
}}"""


REVISION_PROMPT = """你是金融研究报告修订编辑，需要根据审核反馈修订报告。

## 原始报告
{original_content}

## 未解决审核反馈
{feedback}

## 补充信息
{new_info}

## 修订要求
1. 只针对反馈涉及的事实、引用、逻辑和风险表述进行修订。
2. 新增观点必须来自补充信息或原有报告材料。
3. 保持金融研究报告风格，强化来源和风险限制。
4. 禁止新增股票推荐、买入/卖出/持有评级、目标价、收益承诺、仓位建议或任何交易指令。

输出严格 JSON object，不要 Markdown 代码块：
{{
  "revised_content": "修订后的完整报告",
  "changes_made": ["修改说明1"],
  "addressed_issues": ["feedback_id"],
  "unable_to_address": ["无法解决的问题及原因"]
}}"""


SYSTEM_PROMPT = (
    "你是专业的金融研究报告作者和编辑。"
    "你的输出必须是 JSON object，只能基于给定 Deep Research state 材料。"
    "禁止输出股票买卖建议、评级、目标价、收益承诺或交易指令。"
)


FORBIDDEN_ADVICE_PATTERNS = [
    r"买入评级",
    r"卖出评级",
    r"持有评级",
    r"买入",
    r"卖出",
    r"持有",
    r"强烈推荐",
    r"推荐买入",
    r"建议买入",
    r"建议卖出",
    r"建议持有",
    r"目标价",
    r"止损",
    r"止盈",
    r"仓位建议",
    r"收益保证",
    r"保证收益",
    r"稳赚",
]


class Writer(BaseAgent):
    """Write and revise the final Deep Research financial report."""

    def __init__(
        self,
        llm_api_key: str | None = None,
        llm_base_url: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        super().__init__(
            name="Writer",
            role="金融研究报告撰写Agent",
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            model=model,
            client=client,
        )

    async def process(self, state: ResearchState) -> ResearchState:
        if state["phase"] == ResearchPhase.WRITING.value:
            return await self.write_report(state)
        if state["phase"] == ResearchPhase.REVISING.value:
            return await self.revise_report(state)
        return state

    async def write_report(self, state: ResearchState) -> ResearchState:
        started_at = datetime.now(timezone.utc)
        step_id = f"step_writing_{uuid.uuid4().hex[:8]}"
        initial_references = len(state.get("references", []))
        self.add_message(
            state,
            "research_step",
            {
                "step_id": step_id,
                "step_type": "writing",
                "title": "报告撰写",
                "subtitle": "分章节写作与最终报告整合",
                "status": "running",
                "started_at": started_at.isoformat(),
                "stats": {"sections_count": len(state.get("outline", [])), "word_count": 0},
            },
        )
        self.add_message(
            state,
            "thought",
            {"content": "开始基于事实、数据、洞察和图表撰写金融研究报告。"},
        )

        for section in state.get("outline", []):
            section_id = str(section.get("id") or "")
            if not section_id:
                continue
            if section.get("status") in {"final"} and state.get("draft_sections", {}).get(section_id):
                continue
            await self.write_section(state, section)

        await self.synthesize_report(state)
        state["final_report"] = self._validated_report_or_fallback(
            state.get("final_report", ""),
            state,
            "Writer final report failed validation after synthesis",
        )
        state["phase"] = ResearchPhase.REVIEWING.value

        completed_at = datetime.now(timezone.utc)
        output = {
            "draft_sections_count": len(state.get("draft_sections", {})),
            "references_count": len(state.get("references", [])),
            "new_references_count": len(state.get("references", [])) - initial_references,
            "report_word_count": len(state.get("final_report", "")),
        }
        state.setdefault("agent_outputs", []).append(
            {
                "agent": self.name,
                "phase": ResearchPhase.WRITING.value,
                "status": "completed",
                "output": output,
                "completed_at": completed_at.isoformat(),
            }
        )
        state.setdefault("phase_outputs", []).append(
            {
                "phase": ResearchPhase.WRITING.value,
                "status": "completed",
                "agent": self.name,
                "output": output,
                "completed_at": completed_at.isoformat(),
            }
        )
        self.add_log(
            state,
            action="write_report",
            input_summary=f"sections={len(state.get('outline', []))}",
            output_summary=f"report_chars={output['report_word_count']}",
            duration_ms=int((completed_at - started_at).total_seconds() * 1000),
        )
        self.add_message(
            state,
            "research_step",
            {
                "step_id": step_id,
                "step_type": "writing",
                "title": "报告撰写",
                "subtitle": "分章节写作与最终报告整合",
                "status": "completed",
                "completed_at": completed_at.isoformat(),
                "stats": output,
            },
        )
        return state

    async def write_section(self, state: ResearchState, section: dict[str, Any]) -> None:
        section_id = str(section.get("id") or "")
        section_title = str(section.get("title") or section_id or "未命名章节")
        self.add_message(
            state,
            "action",
            {
                "tool": "write_section",
                "section_id": section_id,
                "section": section_title,
            },
        )

        related_facts = self._related_facts(state, section_id)
        related_data_points = self._related_data_points(state, related_facts)
        related_charts = self._related_charts(state, section_id)
        prompt = SECTION_WRITING_PROMPT.format(
            query=state["query"],
            section_title=section_title,
            section_description=section.get("description", ""),
            section_type=section.get("section_type", "mixed"),
            facts=self._format_facts(related_facts),
            data_points=self._format_data_points(related_data_points),
            insights=self._format_insights(state.get("insights", [])),
            charts_info=self._format_charts(related_charts),
        )

        try:
            response = await self.call_llm(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                json_mode=True,
                temperature=0.35,
                max_tokens=16000,
            )
            result = self.parse_json_response(response)
        except Exception as exc:
            state.setdefault("errors", []).append(
                f"Writer section '{section_title}' failed: {exc}"
            )
            result = {}

        section_content = self._extract_text_payload(
            result.get("content") if isinstance(result, dict) else ""
        )
        if not section_content:
            section_content = self._fallback_section_content(section, related_facts, related_data_points)
        section_content = self._sanitize_investment_advice(section_content)
        state.setdefault("draft_sections", {})[section_id] = section_content
        section["status"] = "drafted"

        self._merge_references(state, result.get("citations", []))
        self.add_message(
            state,
            "section_content",
            {
                "section_id": section_id,
                "section_title": section_title,
                "content": section_content,
                "word_count": len(section_content),
                "key_points": result.get("key_points", []) if isinstance(result, dict) else [],
                "citations_count": len(result.get("citations", [])) if isinstance(result, dict) else 0,
            },
        )
        self.add_message(
            state,
            "observation",
            {
                "title": f"章节完成：{section_title}",
                "content": f"已生成章节草稿，长度 {len(section_content)} 字符。",
                "insights": result.get("key_points", [])[:2] if isinstance(result, dict) else [],
            },
        )

    async def synthesize_report(self, state: ResearchState) -> None:
        self.add_message(
            state,
            "thought",
            {"content": "正在整合各章节草稿、来源与图表引用，生成完整报告。"},
        )
        sections_content = self._format_sections_for_synthesis(state)
        all_sources = self._collect_all_sources(state)
        try:
            response = await self.call_llm(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=SYNTHESIS_PROMPT.format(
                    query=state["query"],
                    sections_content=sections_content or "（暂无章节草稿）",
                    all_sources="\n".join(all_sources[:40]) if all_sources else "（暂无来源）",
                ),
                json_mode=True,
                temperature=0.25,
                max_tokens=16000,
            )
            result = self.parse_json_response(response)
        except Exception as exc:
            state.setdefault("errors", []).append(f"Writer synthesis failed: {exc}")
            result = {}

        if isinstance(result, dict) and result.get("full_report"):
            self._merge_references(state, result.get("references", []))
            state["final_report"] = self._validated_report_or_fallback(
                result["full_report"],
                state,
                "Writer synthesis returned an incomplete report",
            )
            executive_summary = str(result.get("executive_summary") or "")
            conclusions = result.get("conclusions", [])
        else:
            state["final_report"] = self._sanitize_report_content(self._fallback_report(state), state)
            executive_summary = ""
            conclusions = []

        self.add_message(
            state,
            "report_draft",
            {
                "content": state["final_report"],
                "executive_summary": executive_summary,
                "conclusions": conclusions if isinstance(conclusions, list) else [],
                "word_count": len(state["final_report"]),
                "references_count": len(state.get("references", [])),
            },
        )

    async def revise_report(self, state: ResearchState) -> ResearchState:
        self.add_message(
            state,
            "research_step",
            {
                "step_type": "revising",
                "title": "报告修订",
                "subtitle": "根据审核反馈修订报告",
                "status": "running",
            },
        )
        unresolved = [
            feedback
            for feedback in state.get("critic_feedback", [])
            if isinstance(feedback, dict) and not feedback.get("resolved")
        ]
        feedback_text = "\n".join(
            f"- [{item.get('severity', 'major')}] {item.get('id', '')}: "
            f"{item.get('description', '')} 建议: {item.get('suggestion', '')}"
            for item in unresolved
        )
        new_info = "\n".join(
            f"- {fact.get('content', '')}（来源: {fact.get('source_name', '')}）"
            for fact in state.get("facts", [])[-8:]
            if isinstance(fact, dict)
        )

        try:
            response = await self.call_llm(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=REVISION_PROMPT.format(
                    original_content=state.get("final_report", "")[:10000],
                    feedback=feedback_text or "无未解决反馈。",
                    new_info=new_info or "无补充信息。",
                ),
                json_mode=True,
                temperature=0.25,
                max_tokens=16000,
            )
            result = self.parse_json_response(response)
        except Exception as exc:
            state.setdefault("errors", []).append(f"Writer revision failed: {exc}")
            result = {}

        if isinstance(result, dict) and result.get("revised_content"):
            if state.get("draft_sections"):
                state["final_report"] = self._validated_report_or_fallback(
                    result["revised_content"],
                    state,
                    "Writer revision returned an incomplete report",
                )
            else:
                state["final_report"] = self._sanitize_report_content(
                    result["revised_content"],
                    state,
                )
            addressed = {str(item) for item in result.get("addressed_issues", [])}
            for feedback in state.get("critic_feedback", []):
                if str(feedback.get("id")) in addressed:
                    feedback["resolved"] = True
            self.add_message(
                state,
                "revision_complete",
                {
                    "changes_count": len(result.get("changes_made", [])),
                    "addressed_issues": list(addressed),
                    "unable_to_address": result.get("unable_to_address", []),
                },
            )

        state["phase"] = ResearchPhase.REVIEWING.value
        self.add_message(
            state,
            "research_step",
            {
                "step_type": "revising",
                "title": "报告修订",
                "subtitle": "根据审核反馈修订报告",
                "status": "completed",
                "stats": {"unresolved_before": len(unresolved)},
            },
        )
        return state

    def _related_facts(self, state: ResearchState, section_id: str) -> list[dict[str, Any]]:
        facts = [fact for fact in state.get("facts", []) if isinstance(fact, dict)]
        related = [
            fact
            for fact in facts
            if section_id and section_id in (fact.get("related_sections") or [])
        ]
        if related:
            return related[:15]
        return sorted(
            facts,
            key=lambda fact: float(fact.get("credibility_score") or 0.0),
            reverse=True,
        )[:12]

    def _related_data_points(
        self,
        state: ResearchState,
        related_facts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        source_names = {
            str(fact.get("source_name") or "")
            for fact in related_facts
            if fact.get("source_name")
        }
        data_points = [
            point
            for point in state.get("data_points", [])
            if isinstance(point, dict)
            and (not source_names or str(point.get("source") or "") in source_names)
        ]
        if len(data_points) < 5:
            for point in state.get("data_points", []):
                if isinstance(point, dict) and point not in data_points:
                    data_points.append(point)
                if len(data_points) >= 12:
                    break
        return data_points[:12]

    def _related_charts(self, state: ResearchState, section_id: str) -> list[dict[str, Any]]:
        charts = [chart for chart in state.get("charts", []) if isinstance(chart, dict)]
        related = [chart for chart in charts if chart.get("section_id") == section_id]
        return (related or charts[:4])[:6]

    def _format_facts(self, facts: list[dict[str, Any]]) -> str:
        if not facts:
            return "（暂无相关事实）"
        lines = []
        for index, fact in enumerate(facts[:15], start=1):
            source = self._format_source_link(
                str(fact.get("source_name") or "未知来源"),
                str(fact.get("source_url") or ""),
            )
            lines.append(
                f"{index}. {fact.get('content', '')} 来源: {source}; "
                f"可信度: {fact.get('credibility_score', 'N/A')}"
            )
        return "\n".join(lines)

    def _format_data_points(self, data_points: list[dict[str, Any]]) -> str:
        if not data_points:
            return "（暂无结构化数据）"
        lines = []
        for index, point in enumerate(data_points[:12], start=1):
            lines.append(
                f"{index}. {point.get('name')}: {point.get('value')} "
                f"{point.get('unit', '')} ({point.get('year', 'N/A')}, "
                f"来源: {point.get('source', '')}, 置信度: {point.get('confidence', 'N/A')})"
            )
        return "\n".join(lines)

    def _format_insights(self, insights: list[str]) -> str:
        if not insights:
            return "（暂无洞察）"
        return "\n".join(f"- {insight}" for insight in insights[:8])

    def _format_charts(self, charts: list[dict[str, Any]]) -> str:
        if not charts:
            return "（暂无图表）"
        lines = []
        for chart in charts:
            metadata = chart.get("metadata") if isinstance(chart.get("metadata"), dict) else {}
            lines.append(
                f"- {chart.get('title', '未命名图表')} "
                f"(ID: {chart.get('id')}, 类型: {chart.get('chart_type') or chart.get('type')}, "
                f"用途: {metadata.get('insight') or metadata.get('for_report') or ''})"
            )
        return "\n".join(lines)

    def _format_sections_for_synthesis(self, state: ResearchState) -> str:
        sections = []
        drafts = state.get("draft_sections", {})
        for index, section in enumerate(state.get("outline", []), start=1):
            section_id = str(section.get("id") or "")
            content = drafts.get(section_id)
            if content:
                sections.append(f"## {index} {section.get('title', section_id)}\n{content}")
        return "\n\n".join(sections)

    def _collect_all_sources(self, state: ResearchState) -> list[str]:
        sources: list[str] = []
        seen: set[tuple[str, str]] = set()
        for ref in state.get("references", []):
            if not isinstance(ref, dict):
                continue
            source = str(ref.get("source") or ref.get("title") or "未知来源")
            url = str(ref.get("url") or "")
            key = (source, url)
            if key in seen:
                continue
            seen.add(key)
            sources.append(f"- {source} ({url or '无链接'})")
        for fact in state.get("facts", []):
            if not isinstance(fact, dict):
                continue
            source = str(fact.get("source_name") or "未知来源")
            url = str(fact.get("source_url") or "")
            key = (source, url)
            if key in seen:
                continue
            seen.add(key)
            sources.append(f"- {source} ({url or '无链接'})")
        return sources

    def _merge_references(self, state: ResearchState, references: Any) -> None:
        if not isinstance(references, list):
            return
        existing = {
            (str(ref.get("source") or ref.get("title") or ""), str(ref.get("url") or ""))
            for ref in state.setdefault("references", [])
            if isinstance(ref, dict)
        }
        for ref in references:
            if not isinstance(ref, dict):
                continue
            source = str(ref.get("source") or ref.get("title") or ref.get("author") or "").strip()
            url = str(ref.get("url") or "").strip()
            if not source and not url:
                continue
            if url.startswith("local://"):
                url = ""
            key = (source, url)
            if key in existing:
                continue
            state["references"].append(
                {
                    "id": len(state["references"]) + 1,
                    "source": source or "未知来源",
                    "title": str(ref.get("title") or source or "未知来源"),
                    "url": url,
                    "author": str(ref.get("author") or ""),
                    "date": str(ref.get("date") or ""),
                }
            )
            existing.add(key)

    def _fallback_section_content(
        self,
        section: dict[str, Any],
        facts: list[dict[str, Any]],
        data_points: list[dict[str, Any]],
    ) -> str:
        title = section.get("title", "本章节")
        fact_lines = "\n".join(
            f"- {fact.get('content', '')}（来源：{fact.get('source_name', '未知来源')}）"
            for fact in facts[:5]
        )
        data_lines = "\n".join(
            f"- {point.get('name')}: {point.get('value')} {point.get('unit', '')}"
            for point in data_points[:5]
        )
        body = f"本节围绕{title}整理已有证据。"
        if fact_lines:
            body += f"\n\n关键事实包括：\n{fact_lines}"
        if data_lines:
            body += f"\n\n可提取的数据点包括：\n{data_lines}"
        body += "\n\n上述信息仅用于研究解释，仍需结合来源时点、统计口径和后续披露进行审慎判断。"
        return self._sanitize_investment_advice(body)

    def _fallback_report(self, state: ResearchState) -> str:
        sections = self._format_sections_for_synthesis(state)
        references = "\n".join(self._collect_all_sources(state)[:30])
        report = (
            "## 执行摘要\n\n"
            f"本报告围绕“{state.get('query', '')}”整理已检索事实、数据和图表，"
            "用于金融信息解释和风险识别。\n\n"
            f"{sections}\n\n"
            "## 风险与限制\n\n"
            "本报告依赖当前检索和本地资料，可能受到来源覆盖、数据时点和统计口径限制。\n\n"
            "## 结论与展望\n\n"
            "后续应持续跟踪公司公告、监管政策、宏观数据和行业经营指标变化。\n\n"
            "## 参考文献\n\n"
            f"{references or '暂无可用参考文献。'}"
        )
        return self._sanitize_investment_advice(report)

    def _format_source_link(self, source: str, url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return f"[{source}]({url})"
        return source

    def _sanitize_investment_advice(self, content: str) -> str:
        sanitized = content
        for pattern in FORBIDDEN_ADVICE_PATTERNS:
            sanitized = re.sub(pattern, "审慎关注", sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r"目标价格\s*[:：]?\s*[\d.]+元?", "估值敏感性需审慎评估", sanitized)
        return sanitized

    def _sanitize_report_content(self, raw_content: Any, state: ResearchState) -> str:
        content = self._extract_text_payload(raw_content)
        if self._looks_like_raw_mapping(content):
            content = self._fallback_report(state)
        content = self._ensure_report_structure(content, state)
        content = self._strip_raw_json_artifacts(content)
        return self._sanitize_investment_advice(content).strip()

    def _validated_report_or_fallback(
        self,
        raw_content: Any,
        state: ResearchState,
        error_context: str,
    ) -> str:
        candidate = self._sanitize_report_content(raw_content, state)
        if self._is_report_complete(candidate, state):
            return candidate

        missing_titles = self._missing_outline_titles(candidate, state)
        reasons: list[str] = []
        if missing_titles:
            reasons.append(f"missing_sections={','.join(missing_titles)}")
        if self._has_machine_generated_headings(candidate):
            reasons.append("machine_generated_headings")
        if self._has_duplicate_required_sections(candidate):
            reasons.append("duplicate_required_sections")
        if not reasons:
            reasons.append("invalid_report_structure")
        state.setdefault("errors", []).append(
            f"{error_context}; {'; '.join(reasons)}; rebuilt from draft sections."
        )
        return self._sanitize_report_content(self._fallback_report(state), state)

    def _extract_text_payload(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            for key in (
                "full_report",
                "revised_content",
                "content",
                "正文",
                "报告",
                "内容",
            ):
                if key in value:
                    extracted = self._extract_text_payload(value[key])
                    if extracted:
                        return extracted
            sections = []
            for key, item in value.items():
                text = self._extract_text_payload(item)
                if text:
                    sections.append(f"## {self._clean_heading(str(key))}\n\n{text}")
            return "\n\n".join(sections)
        if isinstance(value, list):
            return "\n\n".join(
                item for item in (self._extract_text_payload(item) for item in value) if item
            )

        text = str(value).strip()
        if not text:
            return ""
        parsed = self._parse_possible_mapping(text)
        if parsed is not None:
            extracted = self._extract_text_payload(parsed)
            if extracted:
                return extracted
        return text

    def _parse_possible_mapping(self, text: str) -> Any | None:
        stripped = text.strip()
        if not (
            (stripped.startswith("{") and stripped.endswith("}"))
            or (stripped.startswith("[") and stripped.endswith("]"))
        ):
            return None
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
        try:
            return ast.literal_eval(stripped)
        except (SyntaxError, ValueError):
            return None

    def _ensure_report_structure(self, content: str, state: ResearchState) -> str:
        content = self._normalize_markdown_spacing(content)
        if not content:
            return self._fallback_report(state)
        if "## 执行摘要" in content:
            return self._append_missing_required_sections(content, state)

        references = "\n".join(self._collect_all_sources(state)[:30])
        return (
            "## 执行摘要\n\n"
            f"本报告围绕“{state.get('query', '')}”整理已检索事实、结构化数据和图表，"
            "用于金融信息解释、经营分析和风险识别，不构成投资建议。\n\n"
            f"{content}\n\n"
            "## 风险与限制\n\n"
            "本报告依赖当前可用检索结果和本地资料，可能受到来源覆盖、数据时点、统计口径和披露完整性限制；"
            "相关判断应随公司公告、监管政策和行业数据更新而调整。\n\n"
            "## 结论与展望\n\n"
            "后续应持续跟踪公司定期报告、监管政策、行业需求、竞争格局和关键财务指标变化。\n\n"
            "## 参考文献\n\n"
            f"{references or '暂无可用参考文献。'}"
        )

    def _append_missing_required_sections(self, content: str, state: ResearchState) -> str:
        normalized_headings = {
            self._normalize_heading_label(heading)
            for heading in self._extract_markdown_headings(content)
        }
        additions: list[str] = []
        if "风险与限制" not in normalized_headings:
            additions.append(
                "## 风险与限制\n\n"
                "本报告依赖当前可用检索结果和本地资料，可能受到来源覆盖、数据时点、统计口径和披露完整性限制；"
                "相关判断应随公司公告、监管政策和行业数据更新而调整。"
            )
        if "结论与展望" not in normalized_headings:
            additions.append(
                "## 结论与展望\n\n"
                "后续应持续跟踪公司定期报告、监管政策、行业需求、竞争格局和关键财务指标变化。"
            )
        if "参考文献" not in normalized_headings:
            references = "\n".join(self._collect_all_sources(state)[:30])
            additions.append(f"## 参考文献\n\n{references or '暂无可用参考文献。'}")
        if not additions:
            return content
        return f"{content.rstrip()}\n\n" + "\n\n".join(additions)

    def _append_missing_reference_section(self, content: str, state: ResearchState) -> str:
        if "## 参考文献" in content:
            return content
        references = "\n".join(self._collect_all_sources(state)[:30])
        return f"{content.rstrip()}\n\n## 参考文献\n\n{references or '暂无可用参考文献。'}"

    def _normalize_markdown_spacing(self, content: str) -> str:
        content = content.replace("\\n", "\n")
        content = re.sub(r"\r\n?", "\n", content)
        content = re.sub(r"(?<!\n)(##\s+)", r"\n\n\1", content)
        content = re.sub(r"\n{3,}", "\n\n", content)
        return content.strip()

    def _strip_raw_json_artifacts(self, content: str) -> str:
        content = content.strip()
        content = re.sub(r"^[\s{'\"]+|[\s}'\"]+$", "", content)
        content = re.sub(r"['\"]?内容['\"]?\s*[:：]\s*", "", content)
        content = re.sub(
            r"['\"]?(full_report|executive_summary|conclusions|outlook)['\"]?\s*[:：]\s*",
            "",
            content,
        )
        content = content.replace("{", "").replace("}", "")
        return self._normalize_markdown_spacing(content)

    def _looks_like_raw_mapping(self, content: str) -> bool:
        stripped = content.strip()
        if not stripped:
            return False
        if stripped.startswith("{") or stripped.endswith("}"):
            return True
        mapping_tokens = len(
            re.findall(r"['\"]?[\w\u4e00-\u9fff]+['\"]?\s*[:：]\s*[{'\"]", stripped)
        )
        return mapping_tokens >= 3

    def _clean_heading(self, heading: str) -> str:
        heading = heading.strip().strip("'\"{}[]")
        heading = re.sub(r"^#+\s*", "", heading)
        return heading or "未命名章节"

    def _is_report_complete(self, content: str, state: ResearchState) -> bool:
        if not content.strip():
            return False
        normalized_headings = {
            self._normalize_heading_label(heading)
            for heading in self._extract_markdown_headings(content)
        }
        required = {"执行摘要", "风险与限制", "结论与展望", "参考文献"}
        if not required.issubset(normalized_headings):
            return False
        if self._has_machine_generated_headings(content):
            return False
        if self._has_duplicate_required_sections(content):
            return False
        return not self._missing_outline_titles(content, state)

    def _missing_outline_titles(self, content: str, state: ResearchState) -> list[str]:
        headings = {
            self._normalize_heading_label(heading)
            for heading in self._extract_markdown_headings(content)
        }
        expected = self._expected_outline_titles(state)
        return [
            title
            for title in expected
            if self._normalize_heading_label(title) not in headings
        ]

    def _expected_outline_titles(self, state: ResearchState) -> list[str]:
        titles: list[str] = []
        drafts = state.get("draft_sections", {})
        for section in state.get("outline", []):
            if not isinstance(section, dict):
                continue
            section_id = str(section.get("id") or "")
            title = str(section.get("title") or "").strip()
            if not title:
                continue
            if drafts and section_id and section_id not in drafts:
                continue
            titles.append(title)
        return titles

    def _has_machine_generated_headings(self, content: str) -> bool:
        machine_names = {
            "executive_summary",
            "full_report",
            "market_overview",
            "competitive_landscape",
            "technology_trends",
            "policy_environment",
            "challenges_opportunities",
            "future_outlook",
            "risk_limitations",
            "conclusions",
            "references",
        }
        for heading in self._extract_markdown_headings(content):
            label = heading.strip().strip("#").strip().lower()
            if label in machine_names:
                return True
            if re.match(r"^(?:\d+[_-])?[a-z][a-z0-9]+(?:[_-][a-z0-9]+)+$", label):
                return True
        return False

    def _has_duplicate_required_sections(self, content: str) -> bool:
        counts: dict[str, int] = {}
        required = {"执行摘要", "风险与限制", "结论与展望", "参考文献"}
        for heading in self._extract_markdown_headings(content):
            label = self._normalize_heading_label(heading)
            if label in required:
                counts[label] = counts.get(label, 0) + 1
        return any(count > 1 for count in counts.values())

    def _extract_markdown_headings(self, content: str) -> list[str]:
        return [
            match.group(1).strip()
            for match in re.finditer(r"^#{2,3}\s+(.+?)\s*$", content, flags=re.MULTILINE)
        ]

    def _normalize_heading_label(self, heading: str) -> str:
        label = self._clean_heading(heading)
        label = re.sub(r"^[0-9一二三四五六七八九十]+[\s.、_-]+", "", label)
        label = re.sub(r"\s+", "", label)
        return label.lower()


LeadWriter = Writer

__all__ = ["LeadWriter", "Writer"]

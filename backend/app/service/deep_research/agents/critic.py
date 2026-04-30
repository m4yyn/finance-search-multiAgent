import uuid
from datetime import datetime, timezone
from typing import Any

from app.service.deep_research.base import BaseAgent
from app.service.deep_research.state import ResearchPhase, ResearchState


REVIEW_PROMPT = """你是“金融行业信息报告编写 Agent 助手”的最终质量审核 Agent。

## 原始研究课题
{query}

## 研究大纲
{outline_summary}

## 章节草稿与最终报告
{draft_content}

## 已沉淀事实
{facts_summary}

## 已沉淀结构化数据
{data_summary}

## 审核任务
请从金融研究报告质量、事实一致性、引用完整性、逻辑严谨性、风险披露和合规边界进行审核。

必须重点检查：
1. 报告是否只基于给定 facts/data_points，不得编造公司、年份、财务指标、监管文件或来源。
2. 关键结论是否有事实、数据或引用支撑；缺少来源必须标记为 missing_source。
3. 是否存在过时数据、遗漏的重要金融维度、单边叙述、逻辑跳跃或不确定性披露不足。
4. 是否存在股票推荐、买入/卖出/持有评级、目标价、收益承诺、仓位建议、交易指令或诱导交易表达；如发现，必须标记为 critical 或 major。
5. 对公司公告、年报/季报、交易所公告、监管机构、央行/统计局/行业协会等原始来源缺失的问题，应建议补充搜索。

输出严格 JSON object，不要 Markdown 代码块：
{{
  "overall_assessment": {{
    "quality_score": 1,
    "verdict": "pass/needs_revision/major_issues",
    "summary": "整体评估摘要"
  }},
  "issues": [
    {{
      "id": "issue_1",
      "target_section": "章节ID或'全局'",
      "issue_type": "missing_source/logic_error/bias/hallucination/outdated/incomplete",
      "severity": "critical/major/minor",
      "location": "具体位置描述",
      "description": "问题详细描述",
      "evidence": "为什么这是问题的证据",
      "suggestion": "具体的修改建议",
      "requires_new_search": true,
      "search_query": "如果需要补充搜索，建议的关键词"
    }}
  ],
  "fact_check_results": [
    {{
      "fact_id": "事实ID",
      "status": "verified/unverified/suspicious/false",
      "reason": "判断理由"
    }}
  ],
  "missing_aspects": ["报告中遗漏的重要方面"],
  "strength_points": ["报告中做得好的地方"]
}}"""


SYSTEM_PROMPT = (
    "你是专业的金融研究报告审核员。你的输出必须是 JSON object。"
    "你必须严格识别事实缺口、引用缺失、逻辑问题和金融合规风险，"
    "尤其禁止股票买卖建议、评级、目标价、收益承诺和交易指令。"
)


RESEARCH_NEEDED_TYPES = {"missing_source", "incomplete", "outdated"}
VALID_VERDICTS = {"pass", "needs_revision", "major_issues"}
VALID_SEVERITIES = {"critical", "major", "minor"}
VALID_ISSUE_TYPES = {
    "missing_source",
    "logic_error",
    "bias",
    "hallucination",
    "outdated",
    "incomplete",
}


class Critic(BaseAgent):
    """Final quality gate and routing agent for Deep Research reports."""

    def __init__(
        self,
        llm_api_key: str | None = None,
        llm_base_url: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        super().__init__(
            name="Critic",
            role="金融研究报告质量审核Agent",
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            model=model,
            client=client,
        )

    async def process(self, state: ResearchState) -> ResearchState:
        if state["phase"] != ResearchPhase.REVIEWING.value:
            return state

        started_at = datetime.now(timezone.utc)
        step_id = f"step_review_{uuid.uuid4().hex[:8]}"
        self.add_message(
            state,
            "research_step",
            {
                "step_id": step_id,
                "step_type": "reviewing",
                "title": "报告质量审核",
                "subtitle": "核查事实、引用、逻辑和金融合规边界",
                "status": "running",
                "started_at": started_at.isoformat(),
            },
        )
        self.add_message(
            state,
            "thought",
            {"content": "开始审核报告草稿，重点检查来源缺口、事实一致性与投资建议合规风险。"},
        )

        review_result = await self.review_content(state)
        self._apply_review_result(state, review_result)

        assessment = self._assessment(review_result)
        verdict = self._normalize_verdict(assessment.get("verdict"))
        quality_score = self._normalize_score(assessment.get("quality_score"))
        issues = self._normalize_issues(review_result.get("issues", []))
        counts = self._issue_counts(issues)
        state["quality_score"] = quality_score
        state["review_verdict"] = verdict
        state["unresolved_issues"] = sum(
            1
            for issue in state.get("critic_feedback", [])
            if isinstance(issue, dict)
            and not issue.get("resolved")
            and issue.get("severity") in {"critical", "major"}
        )

        self.add_message(
            state,
            "review",
            {
                "quality_score": quality_score,
                "verdict": verdict,
                "summary": str(assessment.get("summary") or ""),
                "issues_count": len(issues),
                "critical_count": counts["critical"],
                "major_count": counts["major"],
                "minor_count": counts["minor"],
                "fact_check_results": review_result.get("fact_check_results", []),
                "missing_aspects": review_result.get("missing_aspects", []),
                "strength_points": review_result.get("strength_points", []),
            },
        )
        for issue in self._top_critical_issues(issues):
            self.add_message(state, "critic_feedback", issue)

        if verdict == "pass":
            state["phase"] = ResearchPhase.COMPLETED.value
        elif state.get("iteration", 0) >= state.get("max_iterations", 3):
            state["phase"] = ResearchPhase.COMPLETED.value
            state["forced_completed"] = True
            self.add_message(
                state,
                "warning",
                {
                    "title": "达到最大审核迭代次数",
                    "message": (
                        "报告仍存在未解决问题，但已达到最大迭代次数，"
                        "流程将以 completed 状态返回，并在摘要中保留风险暴露。"
                    ),
                    "unresolved_issues": state.get("unresolved_issues", 0),
                    "quality_score": quality_score,
                },
            )
        else:
            route = self.analyze_issues_for_routing(review_result)
            state["iteration"] = int(state.get("iteration", 0)) + 1
            if route["should_research"]:
                state["pending_search_queries"] = route["search_queries"]
                state["phase"] = ResearchPhase.RE_RESEARCHING.value
                self.add_message(
                    state,
                    "thought",
                    {
                        "content": (
                            "审核发现需要补充溯源或更新数据的问题，"
                            "将进入 Scout 补充检索。"
                        ),
                        "search_queries": route["search_queries"],
                    },
                )
            else:
                state["phase"] = ResearchPhase.REVISING.value
                self.add_message(
                    state,
                    "thought",
                    {"content": "审核问题主要可通过报告修订解决，将进入 Writer 修订阶段。"},
                )

        completed_at = datetime.now(timezone.utc)
        output = {
            "quality_score": quality_score,
            "verdict": verdict,
            "issues_count": len(issues),
            "unresolved_issues": state.get("unresolved_issues", 0),
            "next_phase": state.get("phase"),
            "forced_completed": state.get("forced_completed", False),
        }
        state.setdefault("agent_outputs", []).append(
            {
                "agent": self.name,
                "phase": ResearchPhase.REVIEWING.value,
                "status": "completed",
                "output": output,
                "completed_at": completed_at.isoformat(),
            }
        )
        state.setdefault("phase_outputs", []).append(
            {
                "phase": ResearchPhase.REVIEWING.value,
                "status": "completed",
                "agent": self.name,
                "output": output,
                "completed_at": completed_at.isoformat(),
            }
        )
        self.add_log(
            state,
            action="review_content",
            input_summary=f"draft_sections={len(state.get('draft_sections', {}))}",
            output_summary=f"verdict={verdict}, issues={len(issues)}",
            duration_ms=int((completed_at - started_at).total_seconds() * 1000),
        )
        self.add_message(
            state,
            "research_step",
            {
                "step_id": step_id,
                "step_type": "reviewing",
                "title": "报告质量审核",
                "subtitle": "核查事实、引用、逻辑和金融合规边界",
                "status": "completed",
                "completed_at": completed_at.isoformat(),
                "stats": output,
            },
        )
        return state

    async def review_content(self, state: ResearchState) -> dict[str, Any]:
        prompt = REVIEW_PROMPT.format(
            query=state["query"],
            outline_summary=self._format_outline(state),
            draft_content=self._format_draft_content(state),
            facts_summary=self._format_facts(state),
            data_summary=self._format_data_points(state),
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
            return parsed if isinstance(parsed, dict) else {}
        except Exception as exc:
            state.setdefault("errors", []).append(f"Critic review failed: {exc}")
            return {
                "overall_assessment": {
                    "quality_score": 0,
                    "verdict": "needs_revision",
                    "summary": f"审核模型调用失败：{exc}",
                },
                "issues": [
                    {
                        "id": "issue_review_failed",
                        "target_section": "全局",
                        "issue_type": "incomplete",
                        "severity": "major",
                        "location": "Critic 审核阶段",
                        "description": "报告未完成质量审核。",
                        "evidence": str(exc),
                        "suggestion": "重新运行审核或人工检查报告质量。",
                        "requires_new_search": False,
                        "search_query": "",
                    }
                ],
                "fact_check_results": [],
                "missing_aspects": [],
                "strength_points": [],
            }

    def analyze_issues_for_routing(self, result: dict[str, Any]) -> dict[str, Any]:
        issues = self._normalize_issues(result.get("issues", []))
        search_queries: list[str] = []

        for issue in issues:
            issue_type = str(issue.get("issue_type") or "")
            if issue_type in RESEARCH_NEEDED_TYPES or issue.get("requires_new_search"):
                query = str(issue.get("search_query") or "").strip()
                if query:
                    search_queries.append(query)

        for aspect in result.get("missing_aspects", []):
            aspect_text = str(aspect).strip()
            if aspect_text:
                search_queries.append(aspect_text)

        severe_count = sum(
            1 for issue in issues if issue.get("severity") in {"critical", "major"}
        )
        severe_ratio = severe_count / len(issues) if issues else 0.0
        unique_queries = self._dedupe(search_queries)[:6]
        should_research = bool(unique_queries) or severe_ratio > 0.3
        return {
            "should_research": should_research,
            "search_queries": unique_queries,
            "severe_ratio": severe_ratio,
        }

    def _apply_review_result(
        self,
        state: ResearchState,
        result: dict[str, Any],
    ) -> None:
        normalized_issues = self._normalize_issues(result.get("issues", []))
        existing_ids = {
            str(issue.get("id"))
            for issue in state.setdefault("critic_feedback", [])
            if isinstance(issue, dict)
        }
        for index, issue in enumerate(normalized_issues, start=1):
            issue_id = str(issue.get("id") or f"issue_{index}")
            if issue_id in existing_ids:
                issue_id = f"{issue_id}_{uuid.uuid4().hex[:6]}"
            issue["id"] = issue_id
            issue["resolved"] = False
            issue.setdefault("metadata", {})
            state["critic_feedback"].append(issue)
            existing_ids.add(issue_id)

    def _assessment(self, result: dict[str, Any]) -> dict[str, Any]:
        assessment = result.get("overall_assessment")
        return assessment if isinstance(assessment, dict) else {}

    def _normalize_issues(self, issues: Any) -> list[dict[str, Any]]:
        if not isinstance(issues, list):
            return []
        normalized: list[dict[str, Any]] = []
        for index, issue in enumerate(issues, start=1):
            if not isinstance(issue, dict):
                continue
            severity = str(issue.get("severity") or "major")
            if severity not in VALID_SEVERITIES:
                severity = "major"
            issue_type = str(issue.get("issue_type") or "incomplete")
            if issue_type not in VALID_ISSUE_TYPES:
                issue_type = "incomplete"
            normalized.append(
                {
                    "id": str(issue.get("id") or f"issue_{index}"),
                    "target_section": str(issue.get("target_section") or "全局"),
                    "issue_type": issue_type,
                    "severity": severity,
                    "location": str(issue.get("location") or ""),
                    "description": str(issue.get("description") or ""),
                    "evidence": str(issue.get("evidence") or ""),
                    "suggestion": str(issue.get("suggestion") or ""),
                    "requires_new_search": bool(issue.get("requires_new_search", False)),
                    "search_query": str(issue.get("search_query") or "").strip(),
                    "resolved": bool(issue.get("resolved", False)),
                    "metadata": issue.get("metadata") if isinstance(issue.get("metadata"), dict) else {},
                }
            )
        return normalized

    def _top_critical_issues(self, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        severity_rank = {"critical": 0, "major": 1, "minor": 2}
        return sorted(
            issues,
            key=lambda issue: severity_rank.get(str(issue.get("severity")), 3),
        )[:3]

    def _issue_counts(self, issues: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "critical": sum(1 for issue in issues if issue.get("severity") == "critical"),
            "major": sum(1 for issue in issues if issue.get("severity") == "major"),
            "minor": sum(1 for issue in issues if issue.get("severity") == "minor"),
        }

    def _normalize_verdict(self, value: Any) -> str:
        verdict = str(value or "needs_revision").strip()
        return verdict if verdict in VALID_VERDICTS else "needs_revision"

    def _normalize_score(self, value: Any) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            score = 0.0
        return max(0.0, min(10.0, score))

    def _format_outline(self, state: ResearchState) -> str:
        lines = []
        for section in state.get("outline", []):
            if not isinstance(section, dict):
                continue
            lines.append(
                f"- [{section.get('id', '')}] {section.get('title', '')}: "
                f"{section.get('description', '')} "
                f"(status={section.get('status', '')})"
            )
        return "\n".join(lines) or "（暂无大纲）"

    def _format_draft_content(self, state: ResearchState) -> str:
        drafts = state.get("draft_sections", {})
        blocks = []
        for section in state.get("outline", []):
            if not isinstance(section, dict):
                continue
            section_id = str(section.get("id") or "")
            content = str(drafts.get(section_id) or "").strip()
            if content:
                blocks.append(
                    f"## {section.get('title', section_id)} [{section_id}]\n{content}"
                )
        final_report = str(state.get("final_report") or "").strip()
        if final_report:
            blocks.append(f"## 当前整合报告\n{final_report[:12000]}")
        return "\n\n".join(blocks) or "（暂无章节草稿或最终报告）"

    def _format_facts(self, state: ResearchState) -> str:
        lines = []
        for fact in state.get("facts", [])[:40]:
            if not isinstance(fact, dict):
                continue
            lines.append(
                f"- [{fact.get('id', '')}] {fact.get('content', '')} "
                f"来源: {fact.get('source_name', '')} "
                f"({fact.get('source_url', '')}); "
                f"可信度: {fact.get('credibility_score', 'N/A')}; "
                f"关联章节: {fact.get('related_sections', [])}"
            )
        return "\n".join(lines) or "（暂无事实）"

    def _format_data_points(self, state: ResearchState) -> str:
        lines = []
        for point in state.get("data_points", [])[:40]:
            if not isinstance(point, dict):
                continue
            lines.append(
                f"- [{point.get('id', '')}] {point.get('name', '')}: "
                f"{point.get('value', '')}{point.get('unit', '')} "
                f"({point.get('year', 'N/A')}, 来源: {point.get('source', '')}, "
                f"置信度: {point.get('confidence', 'N/A')})"
            )
        return "\n".join(lines) or "（暂无结构化数据）"

    def _dedupe(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for value in values:
            normalized = " ".join(str(value).split())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped


CriticMaster = Critic

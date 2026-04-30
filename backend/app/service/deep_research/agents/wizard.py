from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from typing import Any

from app.service.deep_research.base import BaseAgent
from app.service.deep_research.state import ResearchPhase, ResearchState, to_serializable


ANALYSIS_PROMPT = """你是“金融行业信息报告编写 Agent 助手”的 Python 数据分析与报告图表专家。

## 研究课题
{query}

## 可用数据点
{data_points}

## 任务
请基于这些数据点生成一段可直接执行的 Python 代码，完成金融报告所需的数据清洗、关键指标展示和 PNG 静态图表生成。

## 强约束
1. 只使用已给数据，不要编造年份、数值、公司、政策或指标。
2. 不要写 import 语句，环境已预置 pd、np、plt、sns。
3. 严禁使用反斜杠续行 `\\`，字典/列表/函数参数请用括号自然换行。
4. 使用列字典创建 DataFrame：`data = {{"Year": [...], "Value": [...]}}`。
5. 创建 DataFrame 后必须对数值列执行 `pd.to_numeric(..., errors='coerce')`，并处理空值。
6. 代码总长度控制在 45 行内，只选择最适合报告表达的 5-10 个数据点。
7. 图表适合金融行业报告：趋势用 line，结构占比用 bar/pie，指标比较用 bar。
8. 使用 `plt.figure(figsize=(12, 7), dpi=200)`，标题/坐标轴/刻度清晰，配色克制专业。
9. 必须执行 `plt.savefig('chart.png', dpi=200, bbox_inches='tight', facecolor='white')`。

## 正确示例
```python
sns.set_theme(style='whitegrid')
data = {{
    "Year": [2022, 2023, 2024],
    "Value": [100, 150, 210]
}}
df = pd.DataFrame(data)
df["Value"] = pd.to_numeric(df["Value"], errors="coerce")
df = df.dropna()
plt.figure(figsize=(12, 7), dpi=200)
plt.plot(df["Year"], df["Value"], linewidth=2.5, marker="o", color="#2f6f8f")
plt.title("市场规模趋势", fontsize=18, fontweight="bold", pad=20)
plt.xlabel("年份", fontsize=14)
plt.ylabel("规模", fontsize=14)
plt.grid(True, linestyle="--", alpha=0.3)
sns.despine()
plt.savefig("chart.png", dpi=200, bbox_inches="tight", facecolor="white")
```

## 错误示例
```python
import os
data = {{ \\
  "Year": [2024], "Value": ["未知"]
}}
open("/tmp/a.txt", "w")
```

## 输出格式
严格输出 JSON object，不要 Markdown：
{{
  "analysis_plan": "简要分析计划",
  "code": "sns.set_theme(style='whitegrid')\\ndata = {{'Year': [2022, 2023], 'Value': [100, 120]}}\\ndf = pd.DataFrame(data)\\ndf['Value'] = pd.to_numeric(df['Value'], errors='coerce')\\ndf = df.dropna()\\nplt.figure(figsize=(12, 7), dpi=200)\\nplt.plot(df['Year'], df['Value'], linewidth=2.5, marker='o', color='#2f6f8f')\\nplt.title('市场规模趋势', fontsize=18, fontweight='bold', pad=20)\\nsns.despine()\\nplt.savefig('chart.png', dpi=200, bbox_inches='tight', facecolor='white')",
  "expected_outputs": ["图表描述"]
}}"""


CHART_PROMPT = """你是金融研究报告的数据可视化专家，需要为单个报告章节生成 Python 静态图表代码。

## 章节主题
{topic}

## 图表类型
{chart_type}

## 图表标题
{title}

## 可用数据
{data}

## 代码要求
1. 不要写 import 语句，已预置 pd、np、plt、sns。
2. 严禁反斜杠续行 `\\`。
3. 使用列字典创建 DataFrame，并对数值列使用 `pd.to_numeric(..., errors='coerce')`。
4. 图表用于最终报告，不是 ECharts；必须用 matplotlib/seaborn 生成 PNG。
5. 必须保存 `chart.png`：`plt.savefig('chart.png', dpi=200, bbox_inches='tight', facecolor='white')`。
6. 保持金融报告风格：标题明确、坐标轴单位清楚、数据标签适度、颜色克制。

输出严格 JSON object：
{{
  "code": "sns.set_theme(style='whitegrid')\\ndata = {{'Metric': ['A', 'B'], 'Value': [10, 20]}}\\ndf = pd.DataFrame(data)\\ndf['Value'] = pd.to_numeric(df['Value'], errors='coerce')\\ndf = df.dropna()\\nplt.figure(figsize=(12, 7), dpi=200)\\nplt.bar(df['Metric'], df['Value'], color='#2f6f8f')\\nplt.title('标题', fontsize=18, fontweight='bold', pad=20)\\nplt.xlabel('指标', fontsize=14)\\nplt.ylabel('数值', fontsize=14)\\nsns.despine()\\nplt.savefig('chart.png', dpi=200, bbox_inches='tight', facecolor='white')",
  "chart_description": "图表说明"
}}"""


CODE_FIX_PROMPT = """你是 Python 代码调试专家，需要修复金融报告图表代码。

## 原始代码
{code}

## 错误信息
{error}

## stdout
{stdout}

## 修复要求
1. 不要写 import 语句，已预置 pd、np、plt、sns。
2. 严禁反斜杠续行。
3. 保留真实数据，不要补造数值。
4. 修复语法、DataFrame 列名、类型转换、空值、绘图对象等问题。
5. 最终仍需 `plt.savefig('chart.png', dpi=200, bbox_inches='tight', facecolor='white')`。

输出严格 JSON object：
{{
  "error_analysis": "错误原因分析",
  "fix_description": "修复说明",
  "fixed_code": "修复后的完整 Python 代码"
}}"""


SYSTEM_PROMPT = (
    "你是专业的金融数据分析、Python 可视化和代码调试专家。"
    "输出必须是 JSON object，代码只能基于给定数据。"
)


FORBIDDEN_PATTERNS = [
    r"\bimport\s+os\b",
    r"\bfrom\s+os\b",
    r"\bimport\s+sys\b",
    r"\bfrom\s+sys\b",
    r"\bimport\s+subprocess\b",
    r"\bfrom\s+subprocess\b",
    r"\bos\.",
    r"\bsys\.",
    r"\bsubprocess\.",
    r"\bopen\s*\(",
    r"\bexec\s*\(",
    r"\beval\s*\(",
    r"\bcompile\s*\(",
    r"__import__",
    r"__builtins__",
    r"__globals__",
    r"__code__",
    r"\bimport\s+requests\b",
    r"\brequests\.",
    r"\bimport\s+urllib\b",
    r"\burllib\.",
    r"\bimport\s+socket\b",
    r"\bsocket\.",
    r"\bimport\s+shutil\b",
    r"\bshutil\.",
    r"\bimport\s+pathlib\b",
    r"\bpathlib\.",
    r"\bimport\s+pickle\b",
    r"\bpickle\.",
    r"\bimport\s+glob\b",
    r"\bglob\.",
    r"\bpd\.read_[A-Za-z_]*\s*\(",
    r"\bnp\.load\s*\(",
    r"\bnp\.save[A-Za-z_]*\s*\(",
    r"\.to_csv\s*\(",
    r"\.to_excel\s*\(",
    r"\.to_parquet\s*\(",
    r"\.to_pickle\s*\(",
]


class Wizard(BaseAgent):
    """Generate report-ready static charts by executing safe Python analysis code."""

    def __init__(
        self,
        llm_api_key: str | None = None,
        llm_base_url: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        super().__init__(
            name="Wizard",
            role="报告图表代码执行Agent",
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            model=model,
            client=client,
        )

    async def process(self, state: ResearchState) -> ResearchState:
        """Run Wizard when the state is analyzing or enough data exists."""

        if state["phase"] != ResearchPhase.ANALYZING.value:
            if len(state.get("data_points", [])) >= 3:
                state["phase"] = ResearchPhase.ANALYZING.value
            else:
                self.add_message(
                    state,
                    "observation",
                    {
                        "title": "报告图表生成跳过",
                        "message": "结构化数据点不足，Wizard 暂不执行 Python 图表生成。",
                        "data_points_count": len(state.get("data_points", [])),
                    },
                )
                return state

        started_at = datetime.now(timezone.utc)
        initial_charts = len(state.get("charts", []))
        initial_executions = len(state.get("code_executions", []))
        step_id = f"step_wizard_{uuid.uuid4().hex[:8]}"
        self.add_message(
            state,
            "research_step",
            {
                "step_id": step_id,
                "step_type": "report_charting",
                "title": "报告图表生成",
                "subtitle": "Python 数据分析与静态 PNG 图表",
                "status": "running",
                "started_at": started_at.isoformat(),
                "stats": {"data_points_count": len(state.get("data_points", []))},
            },
        )

        try:
            await self.analyze_data(state)
            await self.generate_charts(state)
        except Exception as exc:
            state.setdefault("errors", []).append(f"Wizard failed: {exc}")
            self.add_message(
                state,
                "research_step",
                {
                    "step_id": step_id,
                    "step_type": "report_charting",
                    "title": "报告图表生成",
                    "status": "failed",
                    "error": str(exc),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            state.setdefault("agent_outputs", []).append(
                {
                    "agent": self.name,
                    "phase": ResearchPhase.ANALYZING.value,
                    "status": "failed",
                    "output": {"error": str(exc)},
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return state

        completed_at = datetime.now(timezone.utc)
        output = {
            "report_charts_count": self._report_charts_count(state),
            "new_charts_count": len(state.get("charts", [])) - initial_charts,
            "code_executions_count": len(state.get("code_executions", [])),
            "new_code_executions_count": len(state.get("code_executions", [])) - initial_executions,
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
            action="report_charting",
            input_summary=f"data_points={len(state.get('data_points', []))}",
            output_summary=f"report_charts={output['report_charts_count']}",
            duration_ms=int((completed_at - started_at).total_seconds() * 1000),
        )
        self.add_message(
            state,
            "research_step",
            {
                "step_id": step_id,
                "step_type": "report_charting",
                "title": "报告图表生成",
                "subtitle": "Python 数据分析与静态 PNG 图表",
                "status": "completed",
                "completed_at": completed_at.isoformat(),
                "stats": output,
            },
        )
        return state

    async def analyze_data(self, state: ResearchState) -> None:
        """Generate and execute an overall analysis chart with self-correction."""

        if not state.get("data_points"):
            self.add_message(
                state,
                "observation",
                {"title": "数据分析跳过", "message": "没有可用于 Python 分析的数据点。"},
            )
            return

        data_summary = self._format_data_points(state.get("data_points", []), limit=30)
        self.add_message(
            state,
            "thought",
            {"content": f"开始基于 {len(state.get('data_points', []))} 个数据点生成报告分析代码。"},
        )
        response = await self.call_llm(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=ANALYSIS_PROMPT.format(
                query=state["query"],
                data_points=data_summary,
            ),
            json_mode=True,
            temperature=0.15,
            max_tokens=12000,
        )
        result = self.parse_json_response(response)
        code = self._coerce_code(result.get("code", ""))
        cleaned_code = self.clean_code(code)
        self._compile_code(cleaned_code)

        self.add_message(
            state,
            "code",
            {
                "language": "python",
                "purpose": result.get("analysis_plan") or "整体数据分析与报告图表",
                "code": cleaned_code,
            },
        )
        execution_result = await self.execute_with_self_correction(cleaned_code, state)
        execution_id = self._record_code_execution(state, execution_result)
        self.add_message(
            state,
            "code_result",
            {
                "execution_id": execution_id,
                "success": execution_result.get("success", False),
                "output": str(execution_result.get("output") or "")[:500],
                "error": execution_result.get("error"),
                "has_chart": bool(execution_result.get("charts")),
                "retries": execution_result.get("retries", 0),
            },
        )
        self._append_report_charts(
            state,
            execution_result.get("charts", []),
            title_prefix="整体数据分析图表",
            section_id="analysis",
            code=str(execution_result.get("final_code") or cleaned_code),
        )

    async def generate_charts(self, state: ResearchState) -> None:
        """Generate report charts for chart-worthy sections."""

        chart_sections = [
            section for section in state.get("outline", []) if section.get("requires_chart")
        ]
        if not chart_sections and state.get("outline"):
            chart_sections = state["outline"][:2]

        for section in chart_sections[:2]:
            section_id = str(section.get("id") or "")
            section_title = str(section.get("title") or "报告章节")
            section_data = self.get_section_data(state, section_id)
            if not section_data:
                self.add_message(
                    state,
                    "observation",
                    {
                        "title": "章节图表跳过",
                        "section_id": section_id,
                        "section": section_title,
                        "message": "该章节缺少可绘图数据。",
                    },
                )
                continue

            chart_type = "bar" if section.get("section_type") == "quantitative" else "line"
            chart_config = await self.generate_chart_code(
                topic=section_title,
                data=section_data,
                chart_type=chart_type,
                title=f"{section_title}分析",
            )
            code = self.clean_code(self._coerce_code(chart_config.get("code", "")))
            if not code:
                continue
            self.add_message(
                state,
                "code",
                {
                    "language": "python",
                    "purpose": f"生成章节报告图表：{section_title}",
                    "code": code,
                },
            )
            result = await self.execute_code(code)
            execution_id = self._record_code_execution(
                state,
                {
                    **result,
                    "final_code": code,
                    "retries": 0,
                },
            )
            self.add_message(
                state,
                "code_result",
                {
                    "execution_id": execution_id,
                    "success": result.get("success", False),
                    "output": str(result.get("output") or "")[:500],
                    "error": result.get("error"),
                    "has_chart": bool(result.get("charts")),
                    "retries": 0,
                },
            )
            if result.get("charts"):
                self._append_report_charts(
                    state,
                    result.get("charts", []),
                    title_prefix=section_title,
                    section_id=section_id,
                    code=code,
                    data=section_data,
                )
            elif not result.get("success", False):
                self.add_message(
                    state,
                    "observation",
                    {
                        "title": "章节图表执行失败",
                        "section_id": section_id,
                        "section": section_title,
                        "error": result.get("error"),
                    },
                )

    async def execute_with_self_correction(
        self,
        code: str,
        state: ResearchState,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        current_code = code
        retries = 0
        while retries <= max_retries:
            result = await self.execute_code(current_code)
            if result.get("success"):
                return {
                    "success": True,
                    "output": result.get("output", ""),
                    "error": result.get("error"),
                    "charts": result.get("charts", []),
                    "retries": retries,
                    "final_code": current_code,
                }

            error = str(result.get("error") or "Unknown error")
            stdout = str(result.get("output") or "")
            if retries >= max_retries:
                return {
                    "success": False,
                    "error": error,
                    "output": stdout,
                    "charts": [],
                    "retries": retries,
                    "final_code": current_code,
                }

            self.add_message(
                state,
                "thought",
                {
                    "content": f"Python 图表代码执行失败，正在进行第 {retries + 1} 次自动修复。",
                    "error": error[:200],
                },
            )
            fixed_result = await self.fix_code(current_code, error, stdout)
            fixed_code = self._coerce_code(fixed_result.get("fixed_code", ""))
            if not fixed_code:
                break
            current_code = self.clean_code(fixed_code)
            self.add_message(
                state,
                "code_fix",
                {
                    "retry": retries + 1,
                    "error_analysis": fixed_result.get("error_analysis", ""),
                    "fix_description": fixed_result.get("fix_description", ""),
                },
            )
            retries += 1

        return {
            "success": False,
            "error": "Max retries exceeded",
            "output": "",
            "charts": [],
            "retries": retries,
            "final_code": current_code,
        }

    async def fix_code(self, code: str, error: str, stdout: str) -> dict[str, Any]:
        try:
            response = await self.call_llm(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=CODE_FIX_PROMPT.format(
                    code=code,
                    error=error,
                    stdout=stdout[:1200],
                ),
                json_mode=True,
                temperature=0.1,
                max_tokens=10000,
            )
            parsed = self.parse_json_response(response)
            return parsed if isinstance(parsed, dict) else {}
        except Exception as exc:
            return {
                "error_analysis": f"代码修复 LLM 调用失败: {exc}",
                "fix_description": "",
                "fixed_code": "",
            }

    async def execute_code(self, code: Any) -> dict[str, Any]:
        raw_code = self._coerce_code(code)
        cleaned_code = self.clean_code(raw_code)
        try:
            self._compile_code(cleaned_code)
        except SyntaxError as exc:
            return {
                "success": False,
                "error": f"SyntaxError: {exc}",
                "output": "",
                "charts": [],
            }
        if not self.is_code_safe(cleaned_code):
            return {
                "success": False,
                "error": "Code contains forbidden operations",
                "output": "",
                "charts": [],
            }
        try:
            return await asyncio.to_thread(self.execute_in_sandbox, cleaned_code)
        except Exception as exc:
            return {"success": False, "error": str(exc), "output": "", "charts": []}

    def execute_in_sandbox(self, code: str) -> dict[str, Any]:
        import matplotlib

        matplotlib.use("Agg")
        from matplotlib.figure import Figure

        import matplotlib.pyplot as plt
        import numpy as np
        import pandas as pd
        import seaborn as sns

        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        charts: list[str] = []
        saved_chart_buffers: list[str] = []

        allowed_modules = {"pandas", "numpy", "matplotlib", "seaborn", "math", "statistics"}
        import builtins

        original_import = builtins.__import__

        def safe_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: ANN001
            if name.split(".")[0] in allowed_modules:
                return original_import(name, globals, locals, fromlist, level)
            raise ImportError(f"Import of '{name}' is not allowed in sandbox")

        exec_globals = {
            "__builtins__": {
                "__import__": safe_import,
                "print": print,
                "len": len,
                "range": range,
                "enumerate": enumerate,
                "zip": zip,
                "map": map,
                "filter": filter,
                "sorted": sorted,
                "sum": sum,
                "min": min,
                "max": max,
                "abs": abs,
                "round": round,
                "int": int,
                "float": float,
                "str": str,
                "list": list,
                "dict": dict,
                "tuple": tuple,
                "set": set,
                "bool": bool,
                "isinstance": isinstance,
                "type": type,
                "all": all,
                "any": any,
                "True": True,
                "False": False,
                "None": None,
            },
            "pd": pd,
            "np": np,
            "plt": plt,
            "sns": sns,
        }

        original_pyplot_savefig = plt.savefig
        original_figure_savefig = Figure.savefig

        def _save_figure_to_base64(fig: Figure, kwargs: dict[str, Any] | None = None) -> str:
            buffer = io.BytesIO()
            save_kwargs = dict(kwargs or {})
            save_kwargs.pop("fname", None)
            save_kwargs.setdefault("format", "png")
            save_kwargs.setdefault("dpi", 150)
            save_kwargs.setdefault("bbox_inches", "tight")
            save_kwargs.setdefault("facecolor", "white")
            original_figure_savefig(fig, buffer, **save_kwargs)
            buffer.seek(0)
            return base64.b64encode(buffer.read()).decode("utf-8")

        def _capture_pyplot_savefig(*_args, **kwargs):  # noqa: ANN002, ANN003
            fig = plt.gcf()
            if fig.get_axes():
                saved_chart_buffers.append(_save_figure_to_base64(fig, kwargs))
            return None

        def _capture_figure_savefig(fig: Figure, *_args, **kwargs):  # noqa: ANN002, ANN003
            if fig.get_axes():
                saved_chart_buffers.append(_save_figure_to_base64(fig, kwargs))
            return None

        try:
            plt.close("all")
            plt.rcParams["font.sans-serif"] = [
                "Heiti TC",
                "STHeiti",
                "PingFang HK",
                "Hiragino Sans GB",
                "SimHei",
                "Microsoft YaHei",
                "Arial Unicode MS",
                "DejaVu Sans",
            ]
            plt.rcParams["axes.unicode_minus"] = False
            plt.savefig = _capture_pyplot_savefig  # type: ignore[method-assign]
            Figure.savefig = _capture_figure_savefig  # type: ignore[method-assign]
            with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                exec(code, exec_globals)

            stdout_value = stdout_capture.getvalue()
            stderr_value = stderr_capture.getvalue()
            if saved_chart_buffers:
                charts.extend(saved_chart_buffers)
            else:
                for figure_number in plt.get_fignums():
                    fig = plt.figure(figure_number)
                    if not fig.get_axes():
                        continue
                    charts.append(_save_figure_to_base64(fig))
                    plt.close(fig)
            plt.close("all")
            return {
                "success": True,
                "output": stdout_value,
                "error": stderr_value or None,
                "charts": charts,
            }
        except Exception as exc:
            plt.close("all")
            return {
                "success": False,
                "output": stdout_capture.getvalue(),
                "error": str(exc),
                "charts": [],
            }
        finally:
            plt.savefig = original_pyplot_savefig  # type: ignore[method-assign]
            Figure.savefig = original_figure_savefig  # type: ignore[method-assign]

    async def generate_chart_code(
        self,
        topic: str,
        data: list[dict[str, Any]],
        chart_type: str,
        title: str,
    ) -> dict[str, Any]:
        response = await self.call_llm(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=CHART_PROMPT.format(
                topic=topic,
                data=json.dumps(to_serializable(data[:12]), ensure_ascii=False, indent=2),
                chart_type=chart_type,
                title=title,
            ),
            json_mode=True,
            temperature=0.15,
            max_tokens=10000,
        )
        parsed = self.parse_json_response(response)
        return parsed if isinstance(parsed, dict) else {}

    def clean_code(self, code: Any) -> str:
        code = self._coerce_code(code)
        code = code.strip()
        code = re.sub(r"^```(?:python|json)?\s*", "", code, flags=re.IGNORECASE)
        code = re.sub(r"```\s*$", "", code)
        code = code.replace('\\"', '"')
        placeholder = "___WIZARD_LITERAL_NL___"
        code = self._protect_string_newlines(code, placeholder)
        code = re.sub(r"\\\\?\[\d+pt\]\s*", "\n", code)
        code = re.sub(r"\\\\?\[换行\]\s*", "\n", code)
        code = code.replace("[换行]", "\n")
        code = code.replace("\\\\[n]", "\n").replace("\\[n]", "\n")
        code = code.replace("\\\\n", "\n").replace("\\n", "\n")
        code = re.sub(
            r"(#[^\n]*?)\s+(data\s*=|df\s*=|plt\.|sns\.|for\s+|if\s+)",
            r"\1\n\2",
            code,
        )
        code = re.sub(
            r"([\]\)'\"])\s+(sns\.|plt\.|df\s*=|data\s*=|for\s+|if\s+)",
            r"\1\n\2",
            code,
        )
        code = re.sub(r"^\\([A-Za-z_])", r"\1", code, flags=re.MULTILINE)
        code = re.sub(r"(\s)\\([A-Za-z_][A-Za-z0-9_]*\s*=)", r"\1\2", code)
        code = code.replace(placeholder, "\\n")
        code = code.replace("\\[", "[").replace("\\]", "]")

        lines = []
        for line in code.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                continue
            if "plt.rcParams" in stripped:
                continue
            line = re.sub(r"```\s*$", "", line)
            line = re.sub(r"\\\s*$", "", line)
            lines.append(line.rstrip())
        return "\n".join(lines).strip()

    def is_code_safe(self, code: str) -> bool:
        return not any(
            re.search(pattern, code, flags=re.IGNORECASE)
            for pattern in FORBIDDEN_PATTERNS
        )

    def get_section_data(
        self,
        state: ResearchState,
        section_id: str,
    ) -> list[dict[str, Any]]:
        section_data: list[dict[str, Any]] = []
        for fact in state.get("facts", []):
            if section_id in fact.get("related_sections", []):
                for data_point in fact.get("data_points", []):
                    if isinstance(data_point, dict):
                        section_data.append(data_point)
        if len(section_data) < 3:
            section_data.extend(
                point
                for point in state.get("data_points", [])[:10]
                if isinstance(point, dict)
            )
        return section_data[:12]

    def _format_data_points(
        self,
        data_points: list[dict[str, Any]],
        limit: int,
    ) -> str:
        lines = []
        for index, data_point in enumerate(data_points[:limit], start=1):
            if not isinstance(data_point, dict):
                continue
            lines.append(
                json.dumps(
                    {
                        "index": index,
                        "name": data_point.get("name"),
                        "value": data_point.get("value"),
                        "unit": data_point.get("unit"),
                        "year": data_point.get("year"),
                        "source": data_point.get("source"),
                        "confidence": data_point.get("confidence"),
                        "metadata": data_point.get("metadata", {}),
                    },
                    ensure_ascii=False,
                )
            )
        return "\n".join(lines)

    def _append_report_charts(
        self,
        state: ResearchState,
        charts: list[str],
        title_prefix: str,
        section_id: str,
        code: str,
        data: list[dict[str, Any]] | None = None,
    ) -> None:
        for index, image_base64 in enumerate(charts[:2], start=1):
            title = title_prefix if len(charts) == 1 else f"{title_prefix} {index}"
            chart_entry = {
                "id": f"chart_report_{uuid.uuid4().hex[:8]}",
                "title": title,
                "chart_type": "generated",
                "type": "generated",
                "artifact_type": "report_image",
                "image_base64": image_base64,
                "section_id": section_id,
                "code": code,
                "data": to_serializable(data or {}),
                "metadata": {
                    "generated_by": self.name,
                    "format": "png",
                    "for_report": True,
                },
            }
            state.setdefault("charts", []).append(chart_entry)
            self.add_message(
                state,
                "chart",
                {
                    "chart": chart_entry,
                    "title": title,
                    "chart_type": "generated",
                    "artifact_type": "report_image",
                    "image_base64": image_base64,
                    "section_id": section_id,
                },
            )

    def _record_code_execution(
        self,
        state: ResearchState,
        execution_result: dict[str, Any],
    ) -> str:
        execution_id = f"exec_{uuid.uuid4().hex[:8]}"
        state.setdefault("code_executions", []).append(
            {
                "id": execution_id,
                "code": execution_result.get("final_code", ""),
                "output": execution_result.get("output", ""),
                "error": execution_result.get("error"),
                "charts": execution_result.get("charts", []),
                "retries": execution_result.get("retries", 0),
                "success": execution_result.get("success", False),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent": self.name,
            }
        )
        return execution_id

    def _coerce_code(self, code: Any) -> str:
        if isinstance(code, list):
            return "\n".join(str(item) for item in code)
        if code is None:
            return ""
        return str(code)

    def _compile_code(self, code: str) -> None:
        compile(code, "<wizard>", "exec")

    def _protect_string_newlines(self, text: str, placeholder: str) -> str:
        result: list[str] = []
        in_string = False
        quote = ""
        escaped = False
        i = 0
        while i < len(text):
            char = text[i]
            if not in_string and char in {"'", '"'}:
                in_string = True
                quote = char
                result.append(char)
                i += 1
                continue
            if in_string:
                if escaped:
                    if char == "n":
                        result.append(placeholder)
                    else:
                        result.append("\\" + char)
                    escaped = False
                    i += 1
                    continue
                if char == "\\":
                    escaped = True
                    i += 1
                    continue
                if char == quote:
                    in_string = False
                    quote = ""
                result.append(char)
                i += 1
                continue
            result.append(char)
            i += 1
        if escaped:
            result.append("\\")
        return "".join(result)

    def _report_charts_count(self, state: ResearchState) -> int:
        return sum(
            1
            for chart in state.get("charts", [])
            if chart.get("artifact_type") == "report_image" or chart.get("image_base64")
        )


CodeWizard = Wizard

__all__ = ["CodeWizard", "Wizard"]

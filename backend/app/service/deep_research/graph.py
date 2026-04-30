from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.redis_client import RedisCache
from app.service.checkpoint_service import (
    load_full_checkpoint,
    save_checkpoint,
    update_status,
)
from app.service.deep_research.agents import (
    Architect,
    Critic,
    DataAnalyst,
    Scout,
    Wizard,
    Writer,
)
from app.service.deep_research.base import BaseAgent
from app.service.deep_research.state import (
    ResearchPhase,
    ResearchState,
    create_initial_state,
    to_serializable,
)


logger = logging.getLogger(__name__)


class DeepResearchGraph:
    """Hand-rolled LangGraph-like orchestration for Deep Research agents."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        redis_cache: RedisCache,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.redis_cache = redis_cache
        self.checkpoint_id: str | None = None

    async def run(
        self,
        session_id: UUID,
        user_id: UUID,
        content: str,
        search_web: bool = True,
        search_local: bool = False,
        resume: bool = False,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream Deep Research events as dictionaries."""

        state: ResearchState | None = None
        try:
            async with self.sessionmaker() as db:
                loaded = await self.load_checkpoint(db, user_id, session_id) if resume else None
                if loaded:
                    state = self._state_from_checkpoint(
                        loaded,
                        user_id=user_id,
                        session_id=session_id,
                        content=content,
                        search_web=search_web,
                        search_local=search_local,
                    )
                    self.checkpoint_id = str(loaded.get("id") or "") or None
                    ui_state = loaded.get("ui_state_json") or self.update_ui_state(state)
                    yield {
                        "type": "research_resumed",
                        "session_id": str(session_id),
                        "phase": state.get("phase"),
                        "checkpoint_id": self.checkpoint_id,
                        "content": {
                            "message": "Deep Research checkpoint restored.",
                            "ui_state": ui_state,
                        },
                        "metadata": {"checkpoint_id": self.checkpoint_id},
                    }
                    if state.get("phase") == ResearchPhase.COMPLETED.value:
                        yield self._done_event(state, session_id)
                        return
                else:
                    state = create_initial_state(
                        content,
                        session_id=session_id,
                        user_id=user_id,
                        search_web=search_web,
                        search_local=search_local,
                    )
                    yield {
                        "type": "research_start",
                        "session_id": str(session_id),
                        "phase": state.get("phase"),
                        "content": {
                            "query": state["query"],
                            "search_web": search_web,
                            "search_local": search_local,
                        },
                    }

                async for event in self._run_simplified(
                    db,
                    state,
                    user_id=user_id,
                    session_id=session_id,
                ):
                    yield event
        except Exception as exc:
            logger.exception("Deep Research graph failed.")
            if state is not None:
                state.setdefault("errors", []).append(f"Deep Research graph failed: {exc}")
            await self._mark_failed(user_id, session_id, state, str(exc))
            yield {
                "type": "error",
                "session_id": str(session_id),
                "phase": state.get("phase") if state else None,
                "error": str(exc),
                "done": True,
                "content": {"message": str(exc)},
                "metadata": {"checkpoint_id": self.checkpoint_id},
            }

    async def save_checkpoint(
        self,
        db: AsyncSession,
        user_id: UUID,
        session_id: UUID,
        state: ResearchState,
        status: str = "running",
    ) -> str | None:
        checkpoint_id = await save_checkpoint(
            db,
            user_id=user_id,
            session_id=session_id,
            state=state,
            ui_state=self.update_ui_state(state),
            status=status,  # type: ignore[arg-type]
        )
        self.checkpoint_id = checkpoint_id or self.checkpoint_id
        return self.checkpoint_id

    async def load_checkpoint(
        self,
        db: AsyncSession,
        user_id: UUID,
        session_id: UUID,
    ) -> dict[str, Any] | None:
        return await load_full_checkpoint(db, user_id, session_id)

    async def _run_simplified(
        self,
        db: AsyncSession,
        state: ResearchState,
        user_id: UUID,
        session_id: UUID,
    ) -> AsyncGenerator[dict[str, Any], None]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        state["_message_queue"] = queue  # type: ignore[typeddict-unknown-key]

        try:
            next_stage = self._next_stage(state)
            if next_stage == "architect":
                state["phase"] = ResearchPhase.INIT.value
                async for event in self._run_agent_checkpoint(
                    db,
                    user_id,
                    session_id,
                    state,
                    Architect(),
                    queue,
                ):
                    yield event
                next_stage = "scout"

            if next_stage == "scout":
                async for event in self._run_agent_checkpoint(
                    db,
                    user_id,
                    session_id,
                    state,
                    Scout(db=db, redis_cache=self.redis_cache, user_id=user_id),
                    queue,
                ):
                    yield event
                next_stage = "data_analyst"

            if next_stage == "data_analyst":
                state["phase"] = ResearchPhase.ANALYZING.value
                async for event in self._run_agent_checkpoint(
                    db,
                    user_id,
                    session_id,
                    state,
                    DataAnalyst(),
                    queue,
                ):
                    yield event
                next_stage = "wizard"

            if next_stage == "wizard":
                async for event in self._run_agent_checkpoint(
                    db,
                    user_id,
                    session_id,
                    state,
                    Wizard(),
                    queue,
                ):
                    yield event
                next_stage = "writer"

            if next_stage == "writer":
                state["phase"] = ResearchPhase.WRITING.value
                async for event in self._run_agent_checkpoint(
                    db,
                    user_id,
                    session_id,
                    state,
                    Writer(),
                    queue,
                ):
                    yield event

            last_checkpoint_status = "running"
            review_loop_count = 0
            max_review_loops = max(1, int(state.get("max_iterations", 3))) * 3 + 4
            while state.get("phase") != ResearchPhase.COMPLETED.value:
                phase = state.get("phase")
                if phase == ResearchPhase.REVIEWING.value:
                    agent: BaseAgent = Critic()
                elif phase == ResearchPhase.RE_RESEARCHING.value:
                    agent = Scout(db=db, redis_cache=self.redis_cache, user_id=user_id)
                elif phase == ResearchPhase.REVISING.value:
                    agent = Writer()
                else:
                    state["forced_completed"] = True
                    state["phase"] = ResearchPhase.COMPLETED.value
                    yield self._graph_event(
                        state,
                        "warning",
                        {
                            "title": "Deep Research 路由异常",
                            "message": (
                                f"未知阶段 {phase}，已停止审核闭环并返回 completed 状态。"
                            ),
                        },
                    )
                    break

                async for event in self._run_agent_checkpoint(
                    db,
                    user_id,
                    session_id,
                    state,
                    agent,
                    queue,
                    status=(
                        "completed"
                        if state.get("phase") == ResearchPhase.COMPLETED.value
                        else "running"
                    ),
                ):
                    yield event
                last_checkpoint_status = (
                    "completed"
                    if state.get("phase") == ResearchPhase.COMPLETED.value
                    else "running"
                )

                review_loop_count += 1
                if (
                    state.get("phase") != ResearchPhase.COMPLETED.value
                    and review_loop_count >= max_review_loops
                ):
                    state["forced_completed"] = True
                    state["phase"] = ResearchPhase.COMPLETED.value
                    yield self._graph_event(
                        state,
                        "warning",
                        {
                            "title": "达到审核闭环安全上限",
                            "message": "审核闭环迭代次数超过安全上限，流程将强制完成。",
                            "iterations": state.get("iteration", 0),
                            "unresolved_issues": state.get("unresolved_issues", 0),
                        },
                    )
                    break

            if last_checkpoint_status != "completed":
                await self.save_checkpoint(
                    db,
                    user_id,
                    session_id,
                    state,
                    status="completed",
                )
                yield self._checkpoint_event(state, agent="DeepResearch", status="completed")

            await update_status(db, user_id, session_id, "completed")
            yield self._done_event(state, session_id)
        finally:
            state.pop("_message_queue", None)  # type: ignore[typeddict-item]

    async def _run_agent_checkpoint(
        self,
        db: AsyncSession,
        user_id: UUID,
        session_id: UUID,
        state: ResearchState,
        agent: BaseAgent,
        queue: asyncio.Queue[dict[str, Any]],
        status: str = "running",
    ) -> AsyncGenerator[dict[str, Any], None]:
        async for event in self._run_agent_with_streaming(agent, state, queue):
            yield event
        checkpoint_status = (
            "completed"
            if state.get("phase") == ResearchPhase.COMPLETED.value
            else status
        )
        await self.save_checkpoint(
            db,
            user_id,
            session_id,
            state,
            status=checkpoint_status,
        )
        yield self._checkpoint_event(state, agent=agent.name, status=checkpoint_status)

    async def _run_agent_with_streaming(
        self,
        agent: BaseAgent,
        state: ResearchState,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> AsyncGenerator[dict[str, Any], None]:
        task = asyncio.create_task(agent.process(state))
        while not task.done():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            yield event

        await task
        while not queue.empty():
            yield queue.get_nowait()

    def update_ui_state(self, state: ResearchState) -> dict[str, Any]:
        facts = [fact for fact in state.get("facts", []) if isinstance(fact, dict)]
        references = self._ui_references(state, facts)
        return {
            "phase": state.get("phase"),
            "research_steps": self._ui_research_steps(state),
            "search_results": self._ui_search_results(facts),
            "charts": state.get("charts", []),
            "knowledge_graph": state.get("knowledge_graph", {"nodes": [], "edges": []}),
            "streaming_report": state.get("final_report", ""),
            "final_report": state.get("final_report", ""),
            "references": references,
            "quality_score": state.get("quality_score", 0.0),
            "unresolved_issues": state.get("unresolved_issues", 0),
            "iterations": state.get("iteration", 0),
            "verdict": state.get("review_verdict", ""),
            "forced_completed": state.get("forced_completed", False),
            "summary": self._summary(state),
        }

    def _next_stage(self, state: ResearchState) -> str:
        phase = state.get("phase")
        last_agent = self._last_agent(state)
        if phase == ResearchPhase.COMPLETED.value:
            return "done"
        if phase == ResearchPhase.INIT.value:
            return "architect"
        if phase == ResearchPhase.PLANNING.value:
            return "scout"
        if phase == ResearchPhase.RESEARCHING.value:
            return "data_analyst"
        if phase == ResearchPhase.ANALYZING.value:
            if last_agent == "DataAnalyst":
                return "wizard"
            if last_agent == "Wizard":
                return "writer"
            return "data_analyst"
        if phase == ResearchPhase.WRITING.value:
            return "writer"
        if phase in {
            ResearchPhase.REVIEWING.value,
            ResearchPhase.RE_RESEARCHING.value,
            ResearchPhase.REVISING.value,
        }:
            return "review_loop"
        return "architect"

    def _last_agent(self, state: ResearchState) -> str:
        outputs = state.get("agent_outputs", [])
        if not outputs:
            return ""
        last = outputs[-1]
        if isinstance(last, dict):
            return str(last.get("agent") or "")
        return ""

    def _state_from_checkpoint(
        self,
        checkpoint: dict[str, Any],
        user_id: UUID,
        session_id: UUID,
        content: str,
        search_web: bool,
        search_local: bool,
    ) -> ResearchState:
        state = create_initial_state(
            content,
            session_id=session_id,
            user_id=user_id,
            search_web=search_web,
            search_local=search_local,
        )
        state_json = checkpoint.get("state_json")
        if isinstance(state_json, dict):
            state.update(to_serializable(state_json))  # type: ignore[arg-type]
        state["query"] = str(state.get("query") or content)
        state["user_id"] = str(user_id)
        state["session_id"] = str(session_id)
        state["search_web"] = search_web
        state["search_local"] = search_local
        return state

    async def _mark_failed(
        self,
        user_id: UUID,
        session_id: UUID,
        state: ResearchState | None,
        error_message: str,
    ) -> None:
        async with self.sessionmaker() as db:
            if state is not None:
                await save_checkpoint(
                    db,
                    user_id=user_id,
                    session_id=session_id,
                    state=state,
                    ui_state=self.update_ui_state(state),
                    status="failed",
                )
            await update_status(db, user_id, session_id, "failed", error_message)

    def _checkpoint_event(
        self,
        state: ResearchState,
        agent: str,
        status: str,
    ) -> dict[str, Any]:
        return {
            "type": "checkpoint_saved",
            "session_id": state.get("session_id"),
            "phase": state.get("phase"),
            "checkpoint_id": self.checkpoint_id,
            "status": status,
            "content": {
                "agent": agent,
                "phase": state.get("phase"),
                "checkpoint_id": self.checkpoint_id,
                "ui_state": self.update_ui_state(state),
            },
            "metadata": {"agent": agent},
        }

    def _graph_event(
        self,
        state: ResearchState,
        event_type: str,
        content: dict[str, Any],
    ) -> dict[str, Any]:
        event = {
            "type": event_type,
            "agent": "DeepResearch",
            "phase": state.get("phase"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "content": content,
            "metadata": {},
        }
        state.setdefault("agent_events", []).append(event)
        return event

    def _done_event(self, state: ResearchState, session_id: UUID) -> dict[str, Any]:
        return {
            "type": "done",
            "session_id": str(session_id),
            "phase": state.get("phase"),
            "done": True,
            "content": {
                "summary": self._summary(state),
                "charts": state.get("charts", []),
                "knowledge_graph": state.get("knowledge_graph", {}),
                "final_report": state.get("final_report", ""),
                "references": state.get("references", []),
            },
            "metadata": {"checkpoint_id": self.checkpoint_id},
        }

    def _summary(self, state: ResearchState) -> dict[str, Any]:
        return {
            "facts_count": len(state.get("facts", [])),
            "data_points_count": len(state.get("data_points", [])),
            "charts_count": len(state.get("charts", [])),
            "report_charts_count": _report_charts_count(state),
            "code_executions_count": len(state.get("code_executions", [])),
            "draft_sections_count": len(state.get("draft_sections", {})),
            "references_count": len(state.get("references", [])),
            "report_word_count": len(state.get("final_report", "")),
            "knowledge_graph_nodes": len(
                state.get("knowledge_graph", {}).get("nodes", [])
            ),
            "quality_score": state.get("quality_score", 0.0),
            "unresolved_issues": state.get("unresolved_issues", 0),
            "iterations": state.get("iteration", 0),
            "verdict": state.get("review_verdict", ""),
            "forced_completed": state.get("forced_completed", False),
        }

    def _ui_research_steps(self, state: ResearchState) -> list[dict[str, Any]]:
        steps = []
        for output in state.get("phase_outputs", []):
            if not isinstance(output, dict):
                continue
            steps.append(
                {
                    "type": output.get("phase"),
                    "status": output.get("status", "completed"),
                    "agent": output.get("agent"),
                    "stats": output.get("output", {}),
                }
            )
        return steps

    def _ui_search_results(self, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results = []
        for fact in facts[-20:]:
            content = str(fact.get("content") or "")
            source = str(fact.get("source_name") or "未知来源")
            results.append(
                {
                    "id": fact.get("id", ""),
                    "title": source if source != "未知来源" else content[:80],
                    "source": source,
                    "url": fact.get("source_url", ""),
                    "snippet": content[:220],
                    "isLocal": fact.get("source_type") == "local",
                }
            )
        return results

    def _ui_references(
        self,
        state: ResearchState,
        facts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        references = []
        seen: set[tuple[str, str]] = set()
        for idx, ref in enumerate(state.get("references", []), start=1):
            if not isinstance(ref, dict):
                continue
            source = str(ref.get("source") or ref.get("title") or f"来源 {idx}")
            url = str(ref.get("url") or "")
            key = (source, url)
            if key in seen:
                continue
            seen.add(key)
            fact = next((item for item in facts if item.get("source_url") == url), None)
            references.append(
                {
                    "id": ref.get("id", idx),
                    "source": source,
                    "title": str(ref.get("title") or source),
                    "url": url,
                    "link": url,
                    "content": str(fact.get("content") or "")[:220] if fact else "",
                    "author": ref.get("author", ""),
                    "date": ref.get("date", ""),
                }
            )
        return references


async def stream_deep_research_response(
    sessionmaker: async_sessionmaker[AsyncSession],
    redis_cache: RedisCache,
    session_id: UUID,
    user_id: UUID,
    content: str,
    search_web: bool = True,
    search_local: bool = False,
    resume: bool = False,
) -> AsyncGenerator[str, None]:
    """Stream Deep Research graph events as JSON SSE."""

    graph = DeepResearchGraph(sessionmaker=sessionmaker, redis_cache=redis_cache)
    async for event in graph.run(
        session_id=session_id,
        user_id=user_id,
        content=content,
        search_web=search_web,
        search_local=search_local,
        resume=resume,
    ):
        yield _sse(event)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(to_serializable(event), ensure_ascii=False)}\n\n"


def _report_charts_count(state: ResearchState) -> int:
    return sum(
        1
        for chart in state.get("charts", [])
        if chart.get("artifact_type") == "report_image" or chart.get("image_base64")
    )


__all__ = ["DeepResearchGraph", "stream_deep_research_response"]

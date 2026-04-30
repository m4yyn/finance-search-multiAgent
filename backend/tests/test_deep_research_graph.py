import asyncio
import json
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import ChatSession, User
from app.service.checkpoint_service import save_checkpoint
from app.service.deep_research import graph as deep_research_graph
from app.service.deep_research.graph import DeepResearchGraph, stream_deep_research_response
from app.service.deep_research.state import ResearchPhase, create_initial_state


class FakeRedisCache:
    pass


class FakeArchitect:
    name = "Architect"

    async def process(self, state):  # noqa: ANN001
        state["phase"] = ResearchPhase.PLANNING.value
        state["outline"] = [{"id": "sec_1", "title": "市场概况", "status": "pending"}]
        _emit(state, self.name, "research_step", {"title": "规划", "status": "completed"})
        return state


class FakeScout:
    name = "Scout"

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        del args, kwargs

    async def process(self, state):  # noqa: ANN001
        if state["phase"] == ResearchPhase.RE_RESEARCHING.value:
            state["facts"].append(
                {
                    "id": "fact_2",
                    "content": "补充检索获得监管来源。",
                    "source_name": "监管来源",
                    "metadata": {"triggered_by": "Critic"},
                }
            )
            state["phase"] = ResearchPhase.REVISING.value
        else:
            state["phase"] = ResearchPhase.RESEARCHING.value
            state["facts"] = [
                {
                    "id": "fact_1",
                    "content": "银行业资产规模增长。",
                    "source_name": "测试来源",
                    "source_url": "https://example.com",
                }
            ]
        _emit(state, self.name, "search_results", {"results": [{"title": "银行业资产规模"}]})
        return state


class FakeDataAnalyst:
    name = "DataAnalyst"

    async def process(self, state):  # noqa: ANN001
        state["charts"] = [
            {
                "id": "chart-1",
                "title": "资产规模",
                "chart_type": "bar",
                "echarts_option": {"title": {"text": "资产规模"}},
            }
        ]
        state["knowledge_graph"] = {"nodes": [{"id": "topic", "label": "银行业"}], "edges": []}
        _emit(state, self.name, "charts", {"charts": state["charts"]})
        return state


class FakeWizard:
    name = "Wizard"

    async def process(self, state):  # noqa: ANN001
        chart = {
            "id": "report-chart-1",
            "title": "报告图表",
            "artifact_type": "report_image",
            "image_base64": "iVBORw0KGgo=",
        }
        state["charts"].append(chart)
        state["code_executions"].append({"id": "exec-1", "success": True})
        _emit(state, self.name, "chart", {"chart": chart})
        return state


class FakeWriter:
    name = "Writer"

    async def process(self, state):  # noqa: ANN001
        state["phase"] = ResearchPhase.REVIEWING.value
        state["draft_sections"] = {"sec_1": "银行业资产规模增长。"}
        state["final_report"] = "## 执行摘要\n\n银行业资产规模增长。"
        state["references"] = [{"id": 1, "source": "测试来源", "url": "https://example.com"}]
        _emit(state, self.name, "report_draft", {"content": state["final_report"]})
        return state


class FakeCritic:
    name = "Critic"
    calls = 0

    async def process(self, state):  # noqa: ANN001
        FakeCritic.calls += 1
        state["phase"] = ResearchPhase.COMPLETED.value
        state["quality_score"] = 8.2
        state["review_verdict"] = "pass"
        _emit(state, self.name, "review", {"quality_score": 8.2, "verdict": "pass"})
        return state


class QueueAgent:
    name = "QueueAgent"

    async def process(self, state):  # noqa: ANN001
        state["_message_queue"].put_nowait(
            {"type": "thought", "agent": self.name, "content": {"content": "queued"}}
        )
        return state


def _emit(state, agent: str, event_type: str, content: dict) -> None:  # noqa: ANN001
    event = {
        "type": event_type,
        "agent": agent,
        "phase": state.get("phase"),
        "content": content,
        "metadata": {},
    }
    state.setdefault("agent_events", []).append(event)
    state["_message_queue"].put_nowait(event)


async def _db() -> tuple[async_sessionmaker[AsyncSession], str, str]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessionmaker() as session:
        user = User(username="graph-user", email="graph@example.com", hashed_password="hashed")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        chat_session = ChatSession(user_id=user.id, title="Graph Test")
        session.add(chat_session)
        await session.commit()
        await session.refresh(chat_session)
        return sessionmaker, str(user.id), str(chat_session.id)


def _parse_sse(chunks: list[str]) -> list[dict]:
    events = []
    for chunk in chunks:
        for block in chunk.strip().split("\n\n"):
            if block.startswith("data: "):
                events.append(json.loads(block.removeprefix("data: ")))
    return events


def test_graph_streams_first_run_and_saves_completed_checkpoint(monkeypatch) -> None:
    async def run_check() -> None:
        sessionmaker, user_id, session_id = await _db()
        monkeypatch.setattr(deep_research_graph, "Architect", FakeArchitect)
        monkeypatch.setattr(deep_research_graph, "Scout", FakeScout)
        monkeypatch.setattr(deep_research_graph, "DataAnalyst", FakeDataAnalyst)
        monkeypatch.setattr(deep_research_graph, "Wizard", FakeWizard)
        monkeypatch.setattr(deep_research_graph, "Writer", FakeWriter)
        monkeypatch.setattr(deep_research_graph, "Critic", FakeCritic)

        chunks = [
            chunk
            async for chunk in stream_deep_research_response(
                sessionmaker=sessionmaker,
                redis_cache=FakeRedisCache(),
                session_id=session_id,
                user_id=user_id,
                content="分析银行业",
            )
        ]
        events = _parse_sse(chunks)

        assert events[0]["type"] == "research_start"
        assert events[-1]["type"] == "done"
        assert events[-1]["content"]["summary"]["quality_score"] == 8.2
        assert any(event["type"] == "checkpoint_saved" for event in events)

    asyncio.run(run_check())


def test_graph_resume_completed_checkpoint_restores_ui_state() -> None:
    async def run_check() -> None:
        sessionmaker, user_id, session_id = await _db()
        async with sessionmaker() as session:
            state = create_initial_state("分析银行业", session_id=session_id, user_id=user_id)
            state["phase"] = ResearchPhase.COMPLETED.value
            state["final_report"] = "## 执行摘要\n\n已恢复。"
            state["charts"] = [{"id": "chart-1", "title": "恢复图表"}]
            await save_checkpoint(
                session,
                user_id=user_id,
                session_id=session_id,
                state=state,
                ui_state={"streaming_report": state["final_report"], "charts": state["charts"]},
                status="completed",
            )

        chunks = [
            chunk
            async for chunk in stream_deep_research_response(
                sessionmaker=sessionmaker,
                redis_cache=FakeRedisCache(),
                session_id=session_id,
                user_id=user_id,
                content="分析银行业",
                resume=True,
            )
        ]
        events = _parse_sse(chunks)

        assert [event["type"] for event in events] == ["research_resumed", "done"]
        assert events[0]["content"]["ui_state"]["streaming_report"].startswith("## 执行摘要")
        assert events[-1]["content"]["final_report"].startswith("## 执行摘要")

    asyncio.run(run_check())


def test_graph_run_agent_with_streaming_drains_message_queue() -> None:
    async def run_check() -> None:
        graph = DeepResearchGraph(sessionmaker=None, redis_cache=FakeRedisCache())  # type: ignore[arg-type]
        queue: asyncio.Queue[dict] = asyncio.Queue()
        state = create_initial_state("分析银行业", session_id="00000000-0000-0000-0000-000000000001")
        state["_message_queue"] = queue  # type: ignore[typeddict-unknown-key]

        events = [
            event
            async for event in graph._run_agent_with_streaming(QueueAgent(), state, queue)  # noqa: SLF001
        ]

        assert events == [
            {"type": "thought", "agent": "QueueAgent", "content": {"content": "queued"}}
        ]

    asyncio.run(run_check())

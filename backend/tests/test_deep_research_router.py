import asyncio
import json
from collections.abc import AsyncGenerator, Generator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db, get_sessionmaker
from app.core.redis_client import get_redis_cache
from app.core.security import create_access_token
from app.main import create_app
from app.models import ChatMessage, ChatSession, DeepResearchCheckpoint, User
from app.service.deep_research import graph as deep_research_graph
from app.service.deep_research.state import ResearchPhase


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        del ex
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        existed = key in self.values or key in self.lists
        self.values.pop(key, None)
        self.lists.pop(key, None)
        return int(existed)

    async def exists(self, key: str) -> int:
        return int(key in self.values or key in self.lists)

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        values = self.lists.get(key, [])
        if end == -1:
            return values[start:]
        return values[start : end + 1]


class FakeRedisCache:
    def __init__(self) -> None:
        self.client = FakeRedis()

    async def get(self, key: str):
        value = await self.client.get(key)
        return json.loads(value) if value is not None else None

    async def set(self, key: str, value, expire_seconds: int | None = None) -> bool:  # noqa: ANN001
        return await self.client.set(key, json.dumps(value), ex=expire_seconds)

    async def delete(self, key: str) -> bool:
        return bool(await self.client.delete(key))

    async def exists(self, key: str) -> bool:
        return bool(await self.client.exists(key))

    async def set_session(
        self,
        session_id: str,
        data: dict[str, str],
        expire_seconds: int | None = None,
    ) -> bool:
        return await self.set(session_id, data, expire_seconds=expire_seconds)

    async def get_session(self, session_id: str) -> dict[str, str] | None:
        value = await self.get(session_id)
        return value if isinstance(value, dict) else None

    async def add_to_list(self, key: str, value) -> int:  # noqa: ANN001
        return await self.client.rpush(key, json.dumps(value))

    async def get_list(self, key: str) -> list:
        values = await self.client.lrange(key, 0, -1)
        return [json.loads(value) for value in values]


class FakeArchitect:
    name = "Architect"

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        del args, kwargs

    async def process(self, state):
        state["phase"] = ResearchPhase.PLANNING.value
        state["outline"] = [
            {
                "id": "sec_1",
                "title": "市场概况",
                "description": "银行业市场规模",
                "status": "pending",
                "search_queries": ["银行业 市场规模"],
            }
        ]
        _emit(state, self.name, "research_step", {"title": "规划", "status": "completed"})
        return state


class FakeScout:
    name = "Scout"

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        del args, kwargs

    async def process(self, state):
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

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        del args, kwargs

    async def process(self, state):
        state["charts"] = [
            {
                "id": "chart-1",
                "title": "资产规模",
                "chart_type": "bar",
                "type": "bar",
                "echarts_option": {"title": {"text": "资产规模"}, "series": []},
            }
        ]
        state["knowledge_graph"] = {
            "nodes": [{"id": "topic", "label": "银行业", "size": 50}],
            "edges": [],
        }
        _emit(state, self.name, "charts", {"charts": state["charts"]})
        return state


class FakeWizard:
    name = "Wizard"

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        del args, kwargs

    async def process(self, state):
        chart = {
            "id": "chart-report-1",
            "title": "报告图表",
            "chart_type": "generated",
            "type": "generated",
            "artifact_type": "report_image",
            "image_base64": "iVBORw0KGgo=",
            "section_id": "analysis",
            "metadata": {"generated_by": "Wizard"},
        }
        state.setdefault("charts", []).append(chart)
        state.setdefault("code_executions", []).append(
            {
                "id": "exec-1",
                "success": True,
                "code": "plt.plot([1], [1])",
                "charts": [chart["image_base64"]],
                "retries": 0,
            }
        )
        _emit(state, self.name, "chart", {"chart": chart, "title": chart["title"]})
        return state


class FakeWriter:
    name = "Writer"

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        del args, kwargs

    async def process(self, state):
        state["phase"] = ResearchPhase.REVIEWING.value
        state["draft_sections"] = {"sec_1": "银行业资产规模增长，但净息差仍需观察。"}
        state["final_report"] = (
            "## 执行摘要\n\n银行业资产规模增长，但盈利能力仍需结合净息差与资产质量观察。\n\n"
            "## 1 市场概况\n\n银行业资产规模增长。\n\n"
            "## 风险与限制\n\n数据存在来源时点限制。\n\n"
            "## 结论与展望\n\n持续跟踪监管和公司公告。\n\n"
            "## 参考文献\n\n1. [测试来源](https://example.com)"
        )
        state["references"] = [
            {
                "id": 1,
                "source": "测试来源",
                "title": "测试来源",
                "url": "https://example.com",
            }
        ]
        _emit(
            state,
            self.name,
            "section_content",
            {
                "section_id": "sec_1",
                "section_title": "市场概况",
                "content": state["draft_sections"]["sec_1"],
            },
        )
        _emit(
            state,
            self.name,
            "report_draft",
            {
                "content": state["final_report"],
                "word_count": len(state["final_report"]),
                "references_count": len(state["references"]),
            },
        )
        return state


class FakeCritic:
    name = "Critic"

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        del args, kwargs

    async def process(self, state):
        state["phase"] = ResearchPhase.COMPLETED.value
        state["quality_score"] = 8.6
        state["unresolved_issues"] = 0
        state["review_verdict"] = "pass"
        _emit(
            state,
            self.name,
            "review",
            {
                "quality_score": 8.6,
                "verdict": "pass",
                "critical_count": 0,
                "major_count": 0,
                "minor_count": 0,
            },
        )
        return state


class FailingDataAnalyst(FakeDataAnalyst):
    async def process(self, state):
        del state
        raise RuntimeError("analyst failed")


def _emit(state, agent: str, event_type: str, content: dict) -> None:  # noqa: ANN001
    event = {
        "type": event_type,
        "agent": agent,
        "phase": state.get("phase"),
        "timestamp": "2026-04-30T00:00:00+00:00",
        "content": content,
        "metadata": {},
    }
    state.setdefault("agent_events", []).append(event)
    state["_message_queue"].put_nowait(event)


@pytest.fixture()
def deep_research_client(monkeypatch) -> Generator[
    tuple[TestClient, async_sessionmaker[AsyncSession], str, str, str],
    None,
    None,
]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    redis_cache = FakeRedisCache()

    async def create_tables_and_data() -> tuple[str, str, str]:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with sessionmaker() as session:
            owner = User(
                username="owner",
                email="owner@example.com",
                hashed_password="hashed",
            )
            other = User(
                username="other",
                email="other@example.com",
                hashed_password="hashed",
            )
            session.add_all([owner, other])
            await session.commit()
            await session.refresh(owner)
            await session.refresh(other)
            chat_session = ChatSession(user_id=owner.id, title="银行业研究")
            session.add(chat_session)
            await session.commit()
            await session.refresh(chat_session)
            return str(owner.id), str(other.id), str(chat_session.id)

    async def drop_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessionmaker() as session:
            yield session

    monkeypatch.setattr(deep_research_graph, "Architect", FakeArchitect)
    monkeypatch.setattr(deep_research_graph, "Scout", FakeScout)
    monkeypatch.setattr(deep_research_graph, "DataAnalyst", FakeDataAnalyst)
    monkeypatch.setattr(deep_research_graph, "Wizard", FakeWizard)
    monkeypatch.setattr(deep_research_graph, "Writer", FakeWriter)
    monkeypatch.setattr(deep_research_graph, "Critic", FakeCritic)

    owner_id, other_id, session_id = asyncio.run(create_tables_and_data())
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_sessionmaker] = lambda: sessionmaker
    app.dependency_overrides[get_redis_cache] = lambda: redis_cache

    with TestClient(app) as client:
        yield (
            client,
            sessionmaker,
            create_access_token(owner_id),
            create_access_token(other_id),
            session_id,
        )
    app.dependency_overrides.clear()
    asyncio.run(drop_tables())


def parse_sse_events(response_text: str) -> list[dict]:
    events = []
    for block in response_text.strip().split("\n\n"):
        if not block.startswith("data: "):
            continue
        events.append(json.loads(block.removeprefix("data: ")))
    return events


def test_deep_research_stream_runs_agents_and_saves_checkpoint(
    deep_research_client,
) -> None:
    client, sessionmaker, owner_token, _, session_id = deep_research_client

    response = client.post(
        "/api/v1/deep-research/stream",
        json={"session_id": session_id, "content": "分析中国银行业2025年投资机会"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    events = parse_sse_events(response.text)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert [event["type"] for event in events] == [
        "research_start",
        "research_step",
        "checkpoint_saved",
        "search_results",
        "checkpoint_saved",
        "charts",
        "checkpoint_saved",
        "chart",
        "checkpoint_saved",
        "section_content",
        "report_draft",
        "checkpoint_saved",
        "review",
        "checkpoint_saved",
        "done",
    ]
    assert events[-1]["done"] is True
    assert events[-1]["content"]["summary"]["charts_count"] == 2
    assert events[-1]["content"]["summary"]["report_charts_count"] == 1
    assert events[-1]["content"]["summary"]["code_executions_count"] == 1
    assert events[-1]["content"]["summary"]["draft_sections_count"] == 1
    assert events[-1]["content"]["summary"]["references_count"] == 1
    assert events[-1]["content"]["summary"]["quality_score"] == 8.6
    assert events[-1]["content"]["summary"]["unresolved_issues"] == 0
    assert events[-1]["content"]["summary"]["verdict"] == "pass"
    assert events[-1]["content"]["final_report"].startswith("## 执行摘要")
    assert events[-1]["content"]["references"][0]["url"] == "https://example.com"

    async def inspect_db() -> tuple[DeepResearchCheckpoint | None, list[ChatMessage]]:
        async with sessionmaker() as session:
            checkpoint = (
                await session.execute(select(DeepResearchCheckpoint))
            ).scalar_one_or_none()
            messages = list((await session.execute(select(ChatMessage))).scalars())
            return checkpoint, messages

    checkpoint, messages = asyncio.run(inspect_db())
    assert checkpoint is not None
    assert checkpoint.status == "completed"
    assert checkpoint.phase == ResearchPhase.COMPLETED.value
    assert checkpoint.state_json["charts"][0]["title"] == "资产规模"
    assert checkpoint.state_json["charts"][1]["artifact_type"] == "report_image"
    assert checkpoint.state_json["final_report"].startswith("## 执行摘要")
    assert messages == []


def test_deep_research_stream_runs_critic_supplementary_loop(
    deep_research_client,
    monkeypatch,
) -> None:
    client, sessionmaker, owner_token, _, session_id = deep_research_client

    class LoopScout(FakeScout):
        async def process(self, state):
            if state.get("phase") == ResearchPhase.RE_RESEARCHING.value:
                state.setdefault("facts", []).append(
                    {
                        "id": "fact_2",
                        "content": "补充检索获得监管来源。",
                        "source_name": "监管补充来源",
                        "source_url": "https://example.com/regulator",
                        "metadata": {"triggered_by": "Critic"},
                    }
                )
                state["phase"] = ResearchPhase.REVISING.value
                _emit(
                    state,
                    self.name,
                    "search_results",
                    {"results": [{"title": "监管补充来源"}]},
                )
                return state
            return await super().process(state)

    class LoopWriter(FakeWriter):
        async def process(self, state):
            if state.get("phase") == ResearchPhase.REVISING.value:
                state["final_report"] += "\n\n补充监管来源后完成修订。"
                state["phase"] = ResearchPhase.REVIEWING.value
                _emit(
                    state,
                    self.name,
                    "revision_complete",
                    {"changes_count": 1, "addressed_issues": ["issue_1"]},
                )
                _emit(
                    state,
                    self.name,
                    "report_draft",
                    {
                        "content": state["final_report"],
                        "word_count": len(state["final_report"]),
                        "references_count": len(state.get("references", [])),
                    },
                )
                return state
            return await super().process(state)

    class LoopCritic(FakeCritic):
        calls = 0

        async def process(self, state):
            if LoopCritic.calls == 0:
                LoopCritic.calls += 1
                state["phase"] = ResearchPhase.RE_RESEARCHING.value
                state["iteration"] = 1
                state["quality_score"] = 5.0
                state["unresolved_issues"] = 1
                state["review_verdict"] = "needs_revision"
                state["pending_search_queries"] = ["银行业 监管数据 官方"]
                state["critic_feedback"] = [
                    {
                        "id": "issue_1",
                        "issue_type": "missing_source",
                        "severity": "major",
                        "description": "缺少官方来源。",
                        "suggestion": "补充监管数据。",
                        "search_query": "银行业 监管数据 官方",
                        "resolved": False,
                    }
                ]
                _emit(
                    state,
                    self.name,
                    "review",
                    {
                        "quality_score": 5.0,
                        "verdict": "needs_revision",
                        "major_count": 1,
                    },
                )
                _emit(state, self.name, "critic_feedback", state["critic_feedback"][0])
                return state
            return await super().process(state)

    monkeypatch.setattr(deep_research_graph, "Scout", LoopScout)
    monkeypatch.setattr(deep_research_graph, "Writer", LoopWriter)
    monkeypatch.setattr(deep_research_graph, "Critic", LoopCritic)

    response = client.post(
        "/api/v1/deep-research/stream",
        json={"session_id": session_id, "content": "分析中国银行业2025年投资机会"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    events = parse_sse_events(response.text)
    event_types = [event["type"] for event in events]

    assert response.status_code == 200
    assert event_types.count("review") == 2
    assert "critic_feedback" in event_types
    assert "revision_complete" in event_types
    assert events[-1]["type"] == "done"
    assert events[-1]["content"]["summary"]["verdict"] == "pass"
    assert events[-1]["content"]["summary"]["iterations"] == 1

    async def inspect_db() -> tuple[DeepResearchCheckpoint | None, list[ChatMessage]]:
        async with sessionmaker() as session:
            checkpoint = (
                await session.execute(select(DeepResearchCheckpoint))
            ).scalar_one_or_none()
            messages = list((await session.execute(select(ChatMessage))).scalars())
            return checkpoint, messages

    checkpoint, messages = asyncio.run(inspect_db())
    assert checkpoint is not None
    assert checkpoint.state_json["facts"][-1]["metadata"]["triggered_by"] == "Critic"
    assert checkpoint.phase == ResearchPhase.COMPLETED.value
    assert messages == []


def test_deep_research_stream_requires_authentication(deep_research_client) -> None:
    client, _, _, _, session_id = deep_research_client

    response = client.post(
        "/api/v1/deep-research/stream",
        json={"session_id": session_id, "content": "分析银行业"},
    )

    assert response.status_code == 401


def test_deep_research_stream_is_user_isolated(deep_research_client) -> None:
    client, _, _, other_token, session_id = deep_research_client

    response = client.post(
        "/api/v1/deep-research/stream",
        json={"session_id": session_id, "content": "分析银行业"},
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 404


def test_deep_research_stream_emits_error_event_on_agent_failure(
    deep_research_client,
    monkeypatch,
) -> None:
    client, sessionmaker, owner_token, _, session_id = deep_research_client
    monkeypatch.setattr(deep_research_graph, "DataAnalyst", FailingDataAnalyst)

    response = client.post(
        "/api/v1/deep-research/stream",
        json={"session_id": session_id, "content": "分析银行业"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    events = parse_sse_events(response.text)

    assert response.status_code == 200
    assert events[-1]["type"] == "error"
    assert events[-1]["error"] == "analyst failed"

    async def inspect_status() -> str:
        async with sessionmaker() as session:
            checkpoint = (
                await session.execute(select(DeepResearchCheckpoint))
            ).scalar_one()
            return str(checkpoint.status)

    assert asyncio.run(inspect_status()) == "failed"

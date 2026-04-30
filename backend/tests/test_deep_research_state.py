from uuid import uuid4

from app.service.deep_research.state import (
    AgentEvent,
    AgentOutput,
    PhaseOutput,
    ResearchPhase,
    clean_state_for_checkpoint,
    create_initial_state,
)


def test_create_initial_state_uses_deep_research_memory_fields_only() -> None:
    user_id = uuid4()
    session_id = uuid4()

    state = create_initial_state(
        "分析银行业净息差趋势",
        session_id=session_id,
        user_id=user_id,
        search_web=True,
        search_local=True,
    )

    assert state["query"] == "分析银行业净息差趋势"
    assert state["user_id"] == str(user_id)
    assert state["session_id"] == str(session_id)
    assert state["phase"] == ResearchPhase.INIT.value
    assert state["search_web"] is True
    assert state["search_local"] is True
    assert state["phase_outputs"] == []
    assert state["agent_outputs"] == []
    assert state["agent_events"] == []
    assert "messages" not in state


def test_clean_state_for_checkpoint_removes_runtime_objects() -> None:
    state = create_initial_state("研究贵州茅台", session_id=uuid4())
    state["phase"] = ResearchPhase.PLANNING.value
    state["phase_outputs"].append(
        PhaseOutput(
            phase="planning",
            status="running",
            output={"sections": 3},
        )
    )
    state["agent_outputs"].append(
        AgentOutput(
            agent="architect",
            phase="planning",
            status="completed",
            output={"outline_ready": True},
        )
    )
    state["agent_events"].append(
        AgentEvent(
            type="agent_done",
            agent="architect",
            phase="planning",
            content={"summary": "完成大纲"},
        )
    )
    state["messages"] = [{"role": "user", "content": "普通聊天内容"}]  # type: ignore[typeddict-unknown-key]
    state["_message_queue"] = object()  # type: ignore[typeddict-unknown-key]
    state["runtime"] = {"task": object()}

    clean_state = clean_state_for_checkpoint(state)

    assert clean_state["phase"] == "planning"
    assert clean_state["phase_outputs"][0]["output"] == {"sections": 3}
    assert clean_state["agent_outputs"][0]["output"] == {"outline_ready": True}
    assert clean_state["agent_events"][0]["content"] == {"summary": "完成大纲"}
    assert "messages" not in clean_state
    assert "_message_queue" not in clean_state
    assert "runtime" not in clean_state

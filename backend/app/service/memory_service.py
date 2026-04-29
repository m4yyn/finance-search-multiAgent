import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatMessage, ChatSession, LongTermMemory
from app.models.chat import utc_now
from app.schemas.memory import MemorySearchResult
from app.service import llm_service
from app.service.embedding_service import generate_embedding
from app.service.milvus_service import (
    CONTENT_FIELD,
    MEMORY_CREATED_AT_FIELD,
    MEMORY_ID_FIELD,
    MEMORY_SESSION_ID_FIELD,
    MEMORY_SUMMARY_FIELD,
    MEMORY_USER_ID_FIELD,
    MEMORY_VECTOR_ID_FIELD,
    delete_memory_vectors,
    insert_memory_vectors,
    search_memory_vectors,
)
from app.service.session_service import (
    MAX_MESSAGES,
    count_message_tokens,
    get_chat_session,
    get_session_messages,
)


logger = logging.getLogger(__name__)
AUTO_MEMORY_MESSAGE_THRESHOLD = MAX_MESSAGES
DEFAULT_MEMORY_TOP_K = 3

SUMMARY_SYSTEM_PROMPT = """你是金融行业信息报告助手的长期记忆压缩器。

任务：
把一段用户与助手的历史对话压缩成可复用的长期记忆，供后续金融研究、资料检索、报告写作时参考。

严格要求：
1. 只总结对后续研究有用的信息，包括用户研究偏好、关注公司/行业/指标、已形成的分析结论、资料使用习惯。
2. 不要保留闲聊、重复确认、无关内容。
3. 不要编造对话里没有出现的事实、数据或结论。
4. 输出必须是 JSON object，不要输出 Markdown 或解释。

输出 JSON Schema：
{
  "summary": "100 到 500 字中文摘要",
  "key_insights": ["关键洞察1", "关键洞察2"]
}
"""


def _message_to_line(message: ChatMessage) -> str:
    return f"{message.role}: {message.content}"


def _format_conversation(messages: list[ChatMessage]) -> str:
    return "\n".join(_message_to_line(message) for message in messages)


def _parse_summary_payload(payload: str) -> tuple[str, dict[str, Any] | list[Any] | None]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LLM memory summary returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("LLM memory summary must be a JSON object.")

    summary = str(data.get("summary") or "").strip()
    if not summary:
        raise RuntimeError("LLM memory summary is empty.")

    key_insights = data.get("key_insights")
    if key_insights is not None and not isinstance(key_insights, dict | list):
        key_insights = [str(key_insights)]
    return summary, key_insights


def _memory_score(result: dict[str, Any]) -> float:
    if "distance" in result:
        return float(result["distance"])
    if "score" in result:
        return float(result["score"])
    return 0.0


def _memory_entity(result: dict[str, Any]) -> dict[str, Any]:
    entity = result.get("entity")
    return entity if isinstance(entity, dict) else {}


def _format_key_insights(key_insights: dict[str, Any] | list[Any] | None) -> str:
    if not key_insights:
        return ""
    if isinstance(key_insights, list):
        return "；".join(str(item) for item in key_insights if str(item).strip())
    return "；".join(
        f"{key}: {value}" for key, value in key_insights.items() if str(value).strip()
    )


def _memory_vector_content(
    summary: str,
    key_insights: dict[str, Any] | list[Any] | None,
) -> str:
    insights = _format_key_insights(key_insights)
    if insights:
        return f"摘要：{summary}\n关键洞察：{insights}"
    return f"摘要：{summary}"


def _memory_vector_id(memory_id: UUID) -> str:
    return f"memory_{memory_id.hex}"


async def summarize_conversation(
    messages: list[ChatMessage],
) -> tuple[str, dict[str, Any] | list[Any] | None]:
    """Summarize chat messages into one long-term memory payload."""
    if not messages:
        raise ValueError("Cannot summarize an empty conversation.")

    payload = await llm_service.complete_chat_json(
        [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "请压缩以下对话为长期记忆：\n\n"
                    f"{_format_conversation(messages)}"
                ),
            },
        ]
    )
    return _parse_summary_payload(payload)


async def _create_memory_from_messages(
    db: AsyncSession,
    user_id: UUID,
    chat_session: ChatSession,
    messages: list[ChatMessage],
) -> LongTermMemory:
    if not messages:
        raise ValueError("Chat session has no messages to summarize.")

    summary, key_insights = await summarize_conversation(messages)
    token_count = sum(
        message.tokens
        if message.tokens is not None
        else count_message_tokens(message.content)
        for message in messages
    )
    memory = LongTermMemory(
        user_id=user_id,
        session_id=chat_session.id,
        summary=summary,
        key_insights=key_insights,
        milvus_ids=[],
        token_count=token_count,
    )
    db.add(memory)
    await db.flush()

    vector_content = _memory_vector_content(summary, key_insights)
    vector = await generate_embedding(vector_content)
    vector_id = _memory_vector_id(memory.id)
    try:
        insert_memory_vectors(
            [
                {
                    MEMORY_VECTOR_ID_FIELD: vector_id,
                    MEMORY_ID_FIELD: str(memory.id),
                    MEMORY_USER_ID_FIELD: str(user_id),
                    MEMORY_SESSION_ID_FIELD: str(chat_session.id),
                    MEMORY_SUMMARY_FIELD: summary,
                    CONTENT_FIELD: vector_content,
                    MEMORY_CREATED_AT_FIELD: memory.created_at.isoformat()
                    if memory.created_at
                    else utc_now().isoformat(),
                    "vector": vector,
                }
            ]
        )
    except Exception:
        delete_memory_vectors(str(memory.id))
        raise

    memory.milvus_ids = [vector_id]
    await db.commit()
    await db.refresh(memory)
    return memory


async def create_memory(
    db: AsyncSession,
    user_id: UUID,
    session_id: UUID,
) -> LongTermMemory | None:
    """Create one long-term memory from all messages in a user-owned session."""
    chat_session = await get_chat_session(db, user_id, session_id)
    if chat_session is None:
        return None
    messages = await get_session_messages(db, session_id)
    return await _create_memory_from_messages(db, user_id, chat_session, messages)


async def user_has_memories(db: AsyncSession, user_id: UUID) -> bool:
    result = await db.execute(
        select(LongTermMemory.id).where(LongTermMemory.user_id == user_id).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def retrieve_memories(
    db: AsyncSession,
    user_id: UUID,
    query: str,
    top_k: int = DEFAULT_MEMORY_TOP_K,
) -> list[MemorySearchResult]:
    """Retrieve user-owned memories relevant to a query."""
    if top_k < 1:
        raise ValueError("top_k must be greater than 0.")
    if not query.strip():
        raise ValueError("query cannot be blank.")
    if not await user_has_memories(db, user_id):
        return []

    query_vector = await generate_embedding(query)
    milvus_results = search_memory_vectors(query_vector, str(user_id), limit=top_k)
    memory_ids: list[UUID] = []
    scores_by_id: dict[UUID, float] = {}
    for result in milvus_results:
        entity = _memory_entity(result)
        memory_id_value = entity.get(MEMORY_ID_FIELD)
        if not memory_id_value:
            continue
        try:
            memory_id = UUID(str(memory_id_value))
        except ValueError:
            continue
        memory_ids.append(memory_id)
        scores_by_id[memory_id] = _memory_score(result)

    if not memory_ids:
        return []

    rows = await db.execute(
        select(LongTermMemory).where(
            LongTermMemory.user_id == user_id,
            LongTermMemory.id.in_(memory_ids),
        )
    )
    memories_by_id = {memory.id: memory for memory in rows.scalars().all()}
    results: list[MemorySearchResult] = []
    for memory_id in memory_ids:
        memory = memories_by_id.get(memory_id)
        if memory is None:
            continue
        results.append(
            MemorySearchResult(
                id=memory.id,
                user_id=memory.user_id,
                session_id=memory.session_id,
                summary=memory.summary,
                key_insights=memory.key_insights,
                milvus_ids=memory.milvus_ids,
                token_count=memory.token_count,
                created_at=memory.created_at,
                score=scores_by_id.get(memory.id, 0.0),
            )
        )
    results.sort(key=lambda item: item.score, reverse=True)
    return results[:top_k]


def build_memory_context(memories: list[MemorySearchResult]) -> str:
    """Build prompt-ready context from retrieved long-term memories."""
    if not memories:
        return ""

    blocks = ["[相关历史记忆]"]
    for index, memory in enumerate(memories, start=1):
        insights = _format_key_insights(memory.key_insights)
        lines = [f"[{index}] score={memory.score:.4f} memory_id={memory.id}"]
        lines.append(f"摘要：{memory.summary}")
        if insights:
            lines.append(f"关键洞察：{insights}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def build_memory_context_for_query(
    db: AsyncSession,
    user_id: UUID,
    query: str,
    top_k: int = DEFAULT_MEMORY_TOP_K,
) -> str:
    memories = await retrieve_memories(db, user_id, query, top_k=top_k)
    return build_memory_context(memories)


async def maybe_create_memory_from_session(
    db: AsyncSession,
    user_id: UUID,
    session_id: UUID,
    threshold: int = AUTO_MEMORY_MESSAGE_THRESHOLD,
) -> LongTermMemory | None:
    """Create a memory when enough new chat messages have accumulated."""
    if threshold < 1:
        raise ValueError("threshold must be greater than 0.")

    chat_session = await get_chat_session(db, user_id, session_id)
    if chat_session is None:
        return None

    latest_memory_result = await db.execute(
        select(LongTermMemory)
        .where(
            LongTermMemory.user_id == user_id,
            LongTermMemory.session_id == session_id,
        )
        .order_by(LongTermMemory.created_at.desc())
        .limit(1)
    )
    latest_memory = latest_memory_result.scalar_one_or_none()

    conditions = [ChatMessage.session_id == session_id]
    if latest_memory is not None:
        conditions.append(ChatMessage.created_at > latest_memory.created_at)
    count_result = await db.execute(
        select(func.count(ChatMessage.id)).where(*conditions)
    )
    new_message_count = int(count_result.scalar_one() or 0)
    if new_message_count < threshold:
        return None

    message_result = await db.execute(
        select(ChatMessage)
        .where(*conditions)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
    )
    messages = list(message_result.scalars().all())
    return await _create_memory_from_messages(db, user_id, chat_session, messages)


async def safe_maybe_create_memory_from_session(
    db: AsyncSession,
    user_id: UUID,
    session_id: UUID,
) -> None:
    """Best-effort automatic memory creation that never breaks chat streaming."""
    try:
        await maybe_create_memory_from_session(db, user_id, session_id)
    except Exception:
        logger.exception("Failed to create long-term memory for session %s.", session_id)
        await db.rollback()

import json
from collections.abc import AsyncGenerator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.redis_client import RedisCache
from app.models import ChatMessage, ChatSession
from app.schemas.chat import ChatReference, ChatSSEChunk
from app.schemas.knowledge import RetrivalChunk
from app.service import llm_service
from app.service.retrieval_service import retrieve_from_kbs
from app.service.session_service import (
    add_message,
    create_chat_session,
    get_chat_session,
    get_formatted_history_messages,
    get_session_messages,
    list_chat_sessions,
)


RAG_PROMPT_TEMPLATE = """你是金融行业信息助手。请严格基于以下参考资料回答用户问题。

要求：
1. 回答必须使用中文，结论先行，必要时给出简短计算过程。
2. 凡引用参考资料中的事实或数字，必须用 [编号] 标注来源。
3. 如果参考资料不足以回答，请明确说明“本地知识库未提供足够依据”，不要编造数字。
4. 如果不同参考资料冲突，请指出冲突并说明你采用的依据。

参考资料：
{formatted_refs}

用户问题：
{question}
"""


def format_sse_chunk(chunk: ChatSSEChunk) -> str:
    payload = chunk.model_dump(mode="json", exclude_none=True)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def create_user_chat_session(
    db: AsyncSession,
    user_id: UUID,
    title: str | None = None,
) -> ChatSession:
    return await create_chat_session(db, user_id, title)


async def get_user_chat_session(
    db: AsyncSession,
    user_id: UUID,
    session_id: UUID,
) -> ChatSession | None:
    return await get_chat_session(db, user_id, session_id)


async def list_user_chat_sessions(
    db: AsyncSession,
    user_id: UUID,
) -> list[ChatSession]:
    return await list_chat_sessions(db, user_id)


async def get_user_chat_messages(
    db: AsyncSession,
    user_id: UUID,
    session_id: UUID,
) -> list[ChatMessage] | None:
    chat_session = await get_user_chat_session(db, user_id, session_id)
    if chat_session is None:
        return None
    return await get_session_messages(db, session_id)


def build_chat_references(chunks: list[RetrivalChunk]) -> list[ChatReference]:
    """Convert retrieval chunks into stable frontend citation references."""
    return [
        ChatReference(
            index=index,
            content=chunk.content,
            filename=chunk.filename,
            score=chunk.score,
            kb_id=chunk.kb_id,
            document_id=chunk.document_id,
            chunk_id=chunk.chunk_id,
            chunk_index=chunk.chunk_index,
            page_number=chunk.page_number,
            row_number=chunk.row_number,
            sheet_name=chunk.sheet_name,
            row_start=chunk.row_start,
            row_end=chunk.row_end,
            metadata=chunk.metadata,
        )
        for index, chunk in enumerate(chunks, start=1)
    ]


def format_references_for_prompt(references: list[ChatReference]) -> str:
    """Format retrieved chunks so model citations match returned reference indexes."""
    if not references:
        return "未检索到可用参考资料。"

    formatted_refs: list[str] = []
    for reference in references:
        chunk_label = (
            str(reference.chunk_index)
            if reference.chunk_index is not None
            else reference.chunk_id
        )
        formatted_refs.append(
            "\n".join(
                [
                    (
                        f"[{reference.index}] {reference.filename} | "
                        f"score={reference.score:.4f} | chunk={chunk_label}"
                    ),
                    reference.content,
                ]
            )
        )
    return "\n\n".join(formatted_refs)


def build_rag_prompt(question: str, references: list[ChatReference]) -> str:
    return RAG_PROMPT_TEMPLATE.format(
        formatted_refs=format_references_for_prompt(references),
        question=question,
    )


async def stream_chat_response(
    sessionmaker: async_sessionmaker[AsyncSession],
    redis_cache: RedisCache,
    session_id: UUID,
    user_id: UUID,
    user_content: str,
    kb_ids: list[UUID] | None = None,
) -> AsyncGenerator[str, None]:
    """Persist a user message, stream the LLM reply, then persist assistant output."""
    async with sessionmaker() as db:
        await add_message(db, redis_cache, session_id, "user", user_content)
        history_messages = await get_formatted_history_messages(
            db,
            redis_cache,
            session_id,
        )
        references: list[ChatReference] = []
        llm_messages = history_messages
        if kb_ids:
            retrieved_chunks = await retrieve_from_kbs(
                db,
                user_id,
                kb_ids,
                user_content,
                top_k=5,
            )
            references = build_chat_references(retrieved_chunks)
            rag_prompt = build_rag_prompt(user_content, references)
            llm_messages = [
                *history_messages[:-1],
                {"role": "user", "content": rag_prompt},
            ]

        assistant_content_parts: list[str] = []
        try:
            async for delta in llm_service.stream_chat_completion(llm_messages):
                assistant_content_parts.append(delta)
                yield format_sse_chunk(
                    ChatSSEChunk(type="delta", session_id=session_id, delta=delta)
                )

            assistant_message = await add_message(
                db,
                redis_cache,
                session_id,
                "assistant",
                "".join(assistant_content_parts),
            )
            yield format_sse_chunk(
                ChatSSEChunk(
                    type="done",
                    session_id=session_id,
                    message_id=assistant_message.id,
                    done=True,
                    references=references or None,
                )
            )
        except Exception as exc:
            yield format_sse_chunk(
                ChatSSEChunk(
                    type="error",
                    session_id=session_id,
                    done=True,
                    error=str(exc) or "LLM stream failed.",
                )
            )

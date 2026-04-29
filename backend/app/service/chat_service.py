import json
import logging
from collections.abc import AsyncGenerator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.redis_client import RedisCache
from app.models import ChatMessage, ChatSession
from app.schemas.chat import ChatReference, ChatSSEChunk
from app.schemas.knowledge import RetrivalChunk
from app.schemas.search import WebSearchResult
from app.service import llm_service
from app.service.local_file_router_service import (
    list_local_document_candidates,
    route_query_to_local_files,
)
from app.service.retrieval_service import (
    retrieve_from_all_user_kbs,
    retrieve_from_documents,
    retrieve_from_kbs,
)
from app.service.search_service import format_web_references_for_prompt, search_web
from app.service.memory_service import (
    build_memory_context_for_query,
    safe_maybe_create_memory_from_session,
)
from app.service.session_service import (
    add_message,
    create_chat_session,
    delete_chat_session,
    get_chat_session,
    get_formatted_history_messages,
    get_session_messages,
    list_chat_sessions,
)


logger = logging.getLogger(__name__)

RAG_PROMPT_TEMPLATE = """你是金融行业信息助手。请严格基于以下参考资料回答用户问题。

要求：
1. 回答必须使用中文，结论先行，必要时给出简短计算过程。
2. 凡引用参考资料中的事实或数字，必须用 [编号] 标注来源。
3. 如果参考资料不足以回答，请明确说明“本地知识库未提供足够依据”，不要编造数字。
4. 如果不同参考资料冲突，请指出冲突并说明你采用的依据。
5. 相关历史记忆只用于理解用户偏好和历史上下文，不能替代参考资料作为事实依据。

相关历史记忆：
{memory_context}

参考资料：
{formatted_refs}

用户问题：
{question}
"""

WEB_SEARCH_PROMPT_TEMPLATE = """你是金融行业信息助手。请基于以下联网搜索资料回答用户问题。

要求：
1. 回答必须使用中文，结论先行，必要时给出简短背景。
2. 凡引用联网搜索资料中的事实、观点或数字，必须用 [编号] 标注来源。
3. 如果联网搜索资料不足以回答，请明确说明“联网搜索未提供足够依据”，不要编造事实或数据。
4. 如果资料之间存在时间或口径差异，请说明差异并优先使用更新、更权威的来源。
5. 不要声称读取了用户本地文件；本回答只基于联网搜索资料。
6. 相关历史记忆只用于理解用户偏好和历史上下文，不能替代联网搜索资料作为事实依据。

相关历史记忆：
{memory_context}

联网搜索资料：
{formatted_refs}

用户问题：
{question}
"""

ORDINARY_CHAT_SYSTEM_PROMPT = """你是“金融行业信息报告编写 Agent 助手”的普通聊天入口，不是通用聊天机器人。

你的主要职责：
1. 帮助用户围绕金融行业、上市公司、宏观经济、行业研究、财务分析、投研资料整理与研究报告写作开展工作。
2. 帮助用户理解本系统能力，包括：上传 PDF/XLSX 到本地知识库、使用本地搜索进行 RAG 问答、使用网络搜索获取公开信息、使用 Deep Research 生成更完整的研究报告。
3. 帮助用户设计研究问题、报告大纲、分析框架、指标口径、资料清单、尽调问题和金融研究工作流。
4. 在没有启用本地搜索或网络搜索时，你只能提供通用金融研究方法、概念解释和系统使用引导；不得声称读取了用户文件、不得编造实时数据、不得编造具体财务数字。

严格限制：
1. 拒绝回答与金融研究、信息检索、报告写作、本系统使用无关的问题。
2. 拒绝通用写作、娱乐闲聊、代码生成、情感陪聊、考试作业、营销文案、小说创作等无关请求。
3. 如果用户询问具体文件、具体公司财报数字、实时新闻、最新行情或需要引用来源的信息，应引导用户切换到“本地搜索”或“网络搜索”。
4. 如果用户请求生成正式研究报告，应建议使用 Deep Research 或先上传资料并启用本地搜索。
5. 回答必须使用中文，语气专业、简洁、直接。

无关请求的固定处理方式：
- 先简短说明：这个问题超出金融行业信息报告助手的普通聊天范围。
- 再引导用户改问一个合适的问题，或建议启用本地搜索/网络搜索/Deep Research。
- 不要继续回答无关请求的实质内容。

当用户请求在范围内时：
- 可以回答金融研究方法、概念解释、报告结构、指标口径、资料准备和系统使用建议。
- 如果缺少必要数据，明确说明需要用户上传资料或启用对应搜索模式。"""


def build_ordinary_chat_messages(
    history_messages: list[dict[str, str]],
    memory_context: str = "",
) -> list[dict[str, str]]:
    """Prepend the ordinary-chat guardrail prompt without persisting it to history."""
    messages = [
        {"role": "system", "content": ORDINARY_CHAT_SYSTEM_PROMPT},
    ]
    if memory_context:
        messages.append(
            {
                "role": "system",
                "content": (
                    f"{memory_context}\n\n"
                    "以上长期记忆只用于理解用户偏好、历史研究方向和上下文，"
                    "不得把它当作实时事实来源或用户本地文件内容。"
                ),
            }
        )
    messages.extend(history_messages)
    return messages


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


async def delete_user_chat_session(
    db: AsyncSession,
    redis_cache: RedisCache,
    user_id: UUID,
    session_id: UUID,
) -> bool:
    return await delete_chat_session(db, redis_cache, user_id, session_id)


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


def build_web_search_references(results: list[WebSearchResult]) -> list[ChatReference]:
    """Convert web search results into the shared frontend reference shape."""
    return [
        ChatReference(
            index=result.index,
            content=result.summary or result.snippet,
            filename=result.title,
            source_type="web",
            score=0.0,
            url=result.url,
            site_name=result.site_name,
            site_icon=result.site_icon,
            date_published=result.date_published,
            display_url=result.display_url,
        )
        for result in results
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


def build_rag_prompt(
    question: str,
    references: list[ChatReference],
    memory_context: str = "",
) -> str:
    return RAG_PROMPT_TEMPLATE.format(
        memory_context=memory_context or "无相关历史记忆。",
        formatted_refs=format_references_for_prompt(references),
        question=question,
    )


def build_web_search_prompt(
    question: str,
    results: list[WebSearchResult],
    memory_context: str = "",
) -> str:
    return WEB_SEARCH_PROMPT_TEMPLATE.format(
        memory_context=memory_context or "无相关历史记忆。",
        formatted_refs=format_web_references_for_prompt(results),
        question=question,
    )


async def _build_memory_context_safely(
    db: AsyncSession,
    user_id: UUID,
    user_content: str,
) -> str:
    try:
        return await build_memory_context_for_query(db, user_id, user_content)
    except Exception:
        logger.exception("Failed to retrieve long-term memory context.")
        return ""


async def stream_chat_response(
    sessionmaker: async_sessionmaker[AsyncSession],
    redis_cache: RedisCache,
    session_id: UUID,
    user_id: UUID,
    user_content: str,
    search_mode: str = "none",
) -> AsyncGenerator[str, None]:
    """Persist a user message, stream the LLM reply, then persist assistant output."""
    async with sessionmaker() as db:
        await add_message(db, redis_cache, session_id, "user", user_content)
        history_messages = await get_formatted_history_messages(
            db,
            redis_cache,
            session_id,
        )
        assistant_content_parts: list[str] = []
        try:
            references: list[ChatReference] = []
            memory_context = await _build_memory_context_safely(
                db,
                user_id,
                user_content,
            )
            llm_messages = build_ordinary_chat_messages(
                history_messages,
                memory_context,
            )
            if search_mode == "local":
                candidates = await list_local_document_candidates(db, user_id)
                route = await route_query_to_local_files(user_content, candidates)
                if route.route == "documents":
                    retrieved_chunks = await retrieve_from_documents(
                        db,
                        user_id,
                        route.document_ids,
                        user_content,
                        top_k=5,
                    )
                elif route.route == "knowledge_bases":
                    retrieved_chunks = await retrieve_from_kbs(
                        db,
                        user_id,
                        route.kb_ids,
                        user_content,
                        top_k=5,
                    )
                elif route.route == "all":
                    retrieved_chunks = await retrieve_from_all_user_kbs(
                        db,
                        user_id,
                        user_content,
                        top_k=5,
                    )
                else:
                    retrieved_chunks = []
                references = build_chat_references(retrieved_chunks)
                rag_prompt = build_rag_prompt(user_content, references, memory_context)
                llm_messages = [
                    *history_messages[:-1],
                    {"role": "user", "content": rag_prompt},
                ]
            elif search_mode == "web":
                web_response = await search_web(redis_cache, user_content, count=5)
                references = build_web_search_references(web_response.results)
                web_prompt = build_web_search_prompt(
                    user_content,
                    web_response.results,
                    memory_context,
                )
                llm_messages = [
                    *history_messages[:-1],
                    {"role": "user", "content": web_prompt},
                ]

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
            await safe_maybe_create_memory_from_session(db, user_id, session_id)
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

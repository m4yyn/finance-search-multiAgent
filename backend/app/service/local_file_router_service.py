import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, KnowledgeBase
from app.service import llm_service


LOCAL_FILE_ROUTER_SYSTEM_PROMPT = """你是金融行业本地知识库的文件路由器，不负责回答用户问题。

任务：
根据用户问题，从“候选文件列表”中选择最可能需要检索的文件或知识库。

严格规则：
1. 只能从候选文件列表中选择已有的 document_id 或 kb_id。
2. 禁止编造、改写、猜测任何 id、文件名或知识库名。
3. 如果用户问题明确指向某个文件、公司、年份、报表或文件名，优先选择相关 document_ids。
4. 如果用户问题只指向某类资料或某个知识库，选择相关 kb_ids。
5. 如果无法可靠判断具体文件或知识库，必须输出 route="all"。
6. 如果候选文件为空，必须输出 route="none"。
7. 只输出 JSON，不要输出解释、Markdown 或多余文本。

输出 JSON Schema：
{
  "route": "documents" | "knowledge_bases" | "all" | "none",
  "document_ids": ["只能来自候选文件列表的 document_id"],
  "kb_ids": ["只能来自候选文件列表的 kb_id"]
}
"""


class LocalDocumentCandidate(BaseModel):
    """Non-sensitive metadata used by the local file router."""

    document_id: UUID
    kb_id: UUID
    kb_name: str
    filename: str
    mime_type: str
    chunk_count: int | None
    created_at: datetime

    def to_prompt_dict(self) -> dict[str, str | int | None]:
        return {
            "document_id": str(self.document_id),
            "kb_id": str(self.kb_id),
            "kb_name": self.kb_name,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "chunk_count": self.chunk_count,
            "created_at": self.created_at.isoformat(),
        }


class LocalFileRoute(BaseModel):
    route: Literal["documents", "knowledge_bases", "all", "none"]
    document_ids: list[UUID] = Field(default_factory=list)
    kb_ids: list[UUID] = Field(default_factory=list)


async def list_local_document_candidates(
    db: AsyncSession,
    user_id: UUID,
) -> list[LocalDocumentCandidate]:
    result = await db.execute(
        select(Document, KnowledgeBase)
        .join(KnowledgeBase, Document.kb_id == KnowledgeBase.id)
        .where(
            KnowledgeBase.user_id == user_id,
            Document.status == "success",
        )
        .order_by(KnowledgeBase.name.asc(), Document.created_at.desc())
    )
    return [
        LocalDocumentCandidate(
            document_id=document.id,
            kb_id=knowledge_base.id,
            kb_name=knowledge_base.name,
            filename=document.filename,
            mime_type=document.mime_type,
            chunk_count=document.chunk_count,
            created_at=document.created_at,
        )
        for document, knowledge_base in result.all()
    ]


def build_local_file_router_messages(
    query: str,
    candidates: list[LocalDocumentCandidate],
) -> list[dict[str, str]]:
    documents_json = json.dumps(
        [candidate.to_prompt_dict() for candidate in candidates],
        ensure_ascii=False,
    )
    user_prompt = "\n".join(
        [
            "候选文件列表：",
            documents_json,
            "",
            "用户问题：",
            query,
        ]
    )
    return [
        {"role": "system", "content": LOCAL_FILE_ROUTER_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def fallback_route(candidates: list[LocalDocumentCandidate]) -> LocalFileRoute:
    if not candidates:
        return LocalFileRoute(route="none")
    return LocalFileRoute(route="all")


def parse_and_validate_route(
    raw_content: str,
    candidates: list[LocalDocumentCandidate],
) -> LocalFileRoute:
    if not candidates:
        return LocalFileRoute(route="none")

    allowed_document_ids = {candidate.document_id for candidate in candidates}
    allowed_kb_ids = {candidate.kb_id for candidate in candidates}
    try:
        payload = json.loads(raw_content)
        route = LocalFileRoute.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValidationError):
        return fallback_route(candidates)

    if route.route == "none":
        return fallback_route(candidates)
    if route.route == "all":
        return LocalFileRoute(route="all")

    if route.route == "documents":
        selected_document_ids = list(dict.fromkeys(route.document_ids))
        if not selected_document_ids:
            return fallback_route(candidates)
        if any(document_id not in allowed_document_ids for document_id in selected_document_ids):
            return fallback_route(candidates)
        return LocalFileRoute(route="documents", document_ids=selected_document_ids)

    selected_kb_ids = list(dict.fromkeys(route.kb_ids))
    if not selected_kb_ids:
        return fallback_route(candidates)
    if any(kb_id not in allowed_kb_ids for kb_id in selected_kb_ids):
        return fallback_route(candidates)
    return LocalFileRoute(route="knowledge_bases", kb_ids=selected_kb_ids)


async def route_query_to_local_files(
    query: str,
    candidates: list[LocalDocumentCandidate],
) -> LocalFileRoute:
    if not candidates:
        return LocalFileRoute(route="none")
    try:
        raw_content = await llm_service.complete_chat_json(
            build_local_file_router_messages(query, candidates)
        )
    except Exception:
        return fallback_route(candidates)
    return parse_and_validate_route(raw_content, candidates)

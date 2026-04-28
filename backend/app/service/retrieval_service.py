from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, KnowledgeBase
from app.schemas.knowledge import RetrivalChunk
from app.service.embedding_service import generate_embedding
from app.service.milvus_service import vector_search


RETRIEVAL_OUTPUT_FIELDS = [
    "chunk_id",
    "document_id",
    "kb_id",
    "filename",
    "content",
    "chunk_index",
    "page_number",
    "row_number",
    "sheet_name",
    "row_start",
    "row_end",
    "source_type",
]
KNOWN_ENTITY_FIELDS = set(RETRIEVAL_OUTPUT_FIELDS)


async def _get_owned_kb(
    db: AsyncSession,
    user_id: UUID,
    kb_id: UUID,
) -> KnowledgeBase | None:
    knowledge_base = await db.get(KnowledgeBase, kb_id)
    if knowledge_base is None or knowledge_base.user_id != user_id:
        return None
    return knowledge_base


def _result_score(result: dict[str, Any]) -> float:
    if "distance" in result:
        return float(result["distance"])
    if "score" in result:
        return float(result["score"])
    return 0.0


def _result_entity(result: dict[str, Any]) -> dict[str, Any]:
    entity = result.get("entity")
    return entity if isinstance(entity, dict) else {}


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _to_chunk(result: dict[str, Any]) -> RetrivalChunk:
    entity = _result_entity(result)
    metadata = {
        key: value
        for key, value in entity.items()
        if key not in KNOWN_ENTITY_FIELDS and value is not None
    }
    source_type = entity.get("source_type")
    if source_type is not None:
        metadata["source_type"] = source_type

    chunk_id = str(entity.get("chunk_id") or result.get("id"))
    return RetrivalChunk(
        kb_id=UUID(str(entity["kb_id"])),
        document_id=UUID(str(entity["document_id"])),
        filename=str(entity["filename"]),
        content=str(entity["content"]),
        score=_result_score(result),
        chunk_id=chunk_id,
        chunk_index=_to_int(entity.get("chunk_index")),
        page_number=_to_int(entity.get("page_number")),
        row_number=_to_int(entity.get("row_number")),
        sheet_name=entity.get("sheet_name"),
        row_start=_to_int(entity.get("row_start")),
        row_end=_to_int(entity.get("row_end")),
        metadata=metadata,
    )


def _escape_expr_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _document_filter(document_ids: list[UUID]) -> str:
    values = ", ".join(
        f'"{_escape_expr_value(str(document_id))}"' for document_id in document_ids
    )
    return f"document_id in [{values}]"


async def retrieve_from_kb(
    db: AsyncSession,
    user_id: UUID,
    kb_id: UUID,
    query: str,
    top_k: int = 5,
) -> list[RetrivalChunk]:
    knowledge_base = await _get_owned_kb(db, user_id, kb_id)
    if knowledge_base is None:
        return []

    query_vector = await generate_embedding(query)
    return _search_knowledge_base(knowledge_base, query_vector, top_k)


def _search_knowledge_base(
    knowledge_base: KnowledgeBase,
    query_vector: list[float],
    top_k: int,
    document_ids: list[UUID] | None = None,
) -> list[RetrivalChunk]:
    results = vector_search(
        knowledge_base.collection_name,
        query_vector,
        limit=top_k,
        filter_expr=_document_filter(document_ids) if document_ids else "",
        output_fields=RETRIEVAL_OUTPUT_FIELDS,
    )
    return [_to_chunk(result) for result in results]


async def list_user_knowledge_bases(
    db: AsyncSession,
    user_id: UUID,
) -> list[KnowledgeBase]:
    result = await db.execute(
        select(KnowledgeBase)
        .where(KnowledgeBase.user_id == user_id)
        .order_by(KnowledgeBase.created_at.desc())
    )
    return list(result.scalars().all())


async def _get_owned_kbs(
    db: AsyncSession,
    user_id: UUID,
    kb_ids: list[UUID],
) -> list[KnowledgeBase]:
    unique_kb_ids = list(dict.fromkeys(kb_ids))
    if not unique_kb_ids:
        return []
    result = await db.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.user_id == user_id,
            KnowledgeBase.id.in_(unique_kb_ids),
        )
    )
    return list(result.scalars().all())


async def _get_owned_success_documents(
    db: AsyncSession,
    user_id: UUID,
    document_ids: list[UUID],
) -> list[tuple[Document, KnowledgeBase]]:
    unique_document_ids = list(dict.fromkeys(document_ids))
    if not unique_document_ids:
        return []
    result = await db.execute(
        select(Document, KnowledgeBase)
        .join(KnowledgeBase, Document.kb_id == KnowledgeBase.id)
        .where(
            KnowledgeBase.user_id == user_id,
            Document.status == "success",
            Document.id.in_(unique_document_ids),
        )
    )
    return list(result.all())


async def retrieve_from_kbs(
    db: AsyncSession,
    user_id: UUID,
    kb_ids: list[UUID],
    query: str,
    top_k: int = 5,
) -> list[RetrivalChunk]:
    knowledge_bases = await _get_owned_kbs(db, user_id, kb_ids)
    return await retrieve_from_knowledge_bases(knowledge_bases, query, top_k)


async def retrieve_from_documents(
    db: AsyncSession,
    user_id: UUID,
    document_ids: list[UUID],
    query: str,
    top_k: int = 5,
) -> list[RetrivalChunk]:
    owned_documents = await _get_owned_success_documents(db, user_id, document_ids)
    if not owned_documents:
        return []

    query_vector = await generate_embedding(query)
    document_ids_by_kb: dict[UUID, list[UUID]] = {}
    knowledge_bases_by_id: dict[UUID, KnowledgeBase] = {}
    for document, knowledge_base in owned_documents:
        knowledge_bases_by_id[knowledge_base.id] = knowledge_base
        document_ids_by_kb.setdefault(knowledge_base.id, []).append(document.id)

    merged: list[RetrivalChunk] = []
    for kb_id, kb_document_ids in document_ids_by_kb.items():
        merged.extend(
            _search_knowledge_base(
                knowledge_bases_by_id[kb_id],
                query_vector,
                top_k,
                kb_document_ids,
            )
        )
    merged.sort(key=lambda chunk: chunk.score, reverse=True)
    return merged[:top_k]


async def retrieve_from_all_user_kbs(
    db: AsyncSession,
    user_id: UUID,
    query: str,
    top_k: int = 5,
) -> list[RetrivalChunk]:
    knowledge_bases = await list_user_knowledge_bases(db, user_id)
    return await retrieve_from_knowledge_bases(knowledge_bases, query, top_k)


async def retrieve_from_knowledge_bases(
    knowledge_bases: list[KnowledgeBase],
    query: str,
    top_k: int = 5,
) -> list[RetrivalChunk]:
    if not knowledge_bases:
        return []

    query_vector = await generate_embedding(query)
    merged: list[RetrivalChunk] = []
    for knowledge_base in knowledge_bases:
        merged.extend(_search_knowledge_base(knowledge_base, query_vector, top_k))
    merged.sort(key=lambda chunk: chunk.score, reverse=True)
    return merged[:top_k]

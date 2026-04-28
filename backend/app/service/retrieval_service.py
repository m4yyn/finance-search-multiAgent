from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeBase
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
    results = vector_search(
        knowledge_base.collection_name,
        query_vector,
        limit=top_k,
        output_fields=RETRIEVAL_OUTPUT_FIELDS,
    )
    return [_to_chunk(result) for result in results]


async def retrieve_from_kbs(
    db: AsyncSession,
    user_id: UUID,
    kb_ids: list[UUID],
    query: str,
    top_k: int = 5,
) -> list[RetrivalChunk]:
    merged: list[RetrivalChunk] = []
    for kb_id in kb_ids:
        merged.extend(await retrieve_from_kb(db, user_id, kb_id, query, top_k))
    merged.sort(key=lambda chunk: chunk.score, reverse=True)
    return merged[:top_k]

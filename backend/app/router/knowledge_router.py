import shutil
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.core.database import get_db
from app.models import Document, KnowledgeBase, User
from app.router.auth_router import get_current_user_required
from app.schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeBaseStats,
    RetrievalRequest,
    RetrivalChunk,
)
from app.service.milvus_service import count_collection_rows, delete_collection
from app.service.retrieval_service import retrieve_from_kb, retrieve_from_kbs


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


async def get_owned_knowledge_base(
    db: AsyncSession,
    user_id: UUID,
    kb_id: UUID,
) -> KnowledgeBase | None:
    knowledge_base = await db.get(KnowledgeBase, kb_id)
    if knowledge_base is None or knowledge_base.user_id != user_id:
        return None
    return knowledge_base


def _delete_local_files(documents: list[Document], kb_id: UUID) -> None:
    for document in documents:
        Path(document.file_path).unlink(missing_ok=True)
    kb_dir = Path(get_settings().upload_dir) / str(kb_id)
    shutil.rmtree(kb_dir, ignore_errors=True)


@router.post(
    "/bases",
    response_model=KnowledgeBaseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    current_user: Annotated[User, Depends(get_current_user_required)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> KnowledgeBase:
    kb_id = uuid4()
    knowledge_base = KnowledgeBase(
        id=kb_id,
        user_id=current_user.id,
        name=payload.name,
        description=payload.description,
        collection_name=f"kb_{kb_id.hex}",
    )
    db.add(knowledge_base)
    await db.commit()
    await db.refresh(knowledge_base)
    return knowledge_base


@router.get("/bases", response_model=list[KnowledgeBaseResponse])
async def list_knowledge_bases(
    current_user: Annotated[User, Depends(get_current_user_required)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[KnowledgeBase]:
    result = await db.execute(
        select(KnowledgeBase)
        .where(KnowledgeBase.user_id == current_user.id)
        .order_by(KnowledgeBase.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/bases/{kb_id}/stats", response_model=KnowledgeBaseStats)
async def get_knowledge_base_stats(
    kb_id: UUID,
    current_user: Annotated[User, Depends(get_current_user_required)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> KnowledgeBaseStats:
    knowledge_base = await get_owned_knowledge_base(db, current_user.id, kb_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found.",
        )

    result = await db.execute(
        select(func.coalesce(func.sum(Document.chunk_count), 0)).where(
            Document.kb_id == kb_id,
            Document.status == "success",
        )
    )
    return KnowledgeBaseStats(
        kb_id=knowledge_base.id,
        collection_name=knowledge_base.collection_name,
        pg_chunk_count=int(result.scalar_one()),
        milvus_chunk_count=count_collection_rows(knowledge_base.collection_name),
    )


@router.delete("/bases/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    kb_id: UUID,
    current_user: Annotated[User, Depends(get_current_user_required)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    knowledge_base = await get_owned_knowledge_base(db, current_user.id, kb_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found.",
        )

    result = await db.execute(select(Document).where(Document.kb_id == kb_id))
    documents = list(result.scalars().all())
    delete_collection(knowledge_base.collection_name)
    _delete_local_files(documents, kb_id)
    await db.execute(delete(Document).where(Document.kb_id == kb_id))
    await db.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/retrieve", response_model=list[RetrivalChunk])
async def retrieve_knowledge_chunks(
    payload: RetrievalRequest,
    current_user: Annotated[User, Depends(get_current_user_required)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[RetrivalChunk]:
    if payload.kb_id is not None:
        return await retrieve_from_kb(
            db,
            current_user.id,
            payload.kb_id,
            payload.query,
            payload.top_k,
        )
    return await retrieve_from_kbs(
        db,
        current_user.id,
        payload.kb_ids or [],
        payload.query,
        payload.top_k,
    )

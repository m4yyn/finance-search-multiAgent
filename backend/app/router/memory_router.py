from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User
from app.router.auth_router import get_current_user_required
from app.schemas.memory import (
    MemoryContextResponse,
    MemoryCreateRequest,
    MemoryResponse,
    MemorySearchRequest,
    MemorySearchResult,
)
from app.service.memory_service import (
    build_memory_context,
    create_memory,
    retrieve_memories,
)


router = APIRouter(prefix="/memories", tags=["memories"])


@router.post(
    "/create",
    response_model=MemoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_long_term_memory(
    payload: MemoryCreateRequest,
    current_user: Annotated[User, Depends(get_current_user_required)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MemoryResponse:
    try:
        memory = await create_memory(db, current_user.id, payload.session_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    if memory is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found.",
        )
    return MemoryResponse.model_validate(memory)


@router.post("/search", response_model=list[MemorySearchResult])
async def search_long_term_memories(
    payload: MemorySearchRequest,
    current_user: Annotated[User, Depends(get_current_user_required)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[MemorySearchResult]:
    return await retrieve_memories(db, current_user.id, payload.query, payload.top_k)


@router.get("/context/{query:path}", response_model=MemoryContextResponse)
async def read_memory_context(
    query: str,
    current_user: Annotated[User, Depends(get_current_user_required)],
    db: Annotated[AsyncSession, Depends(get_db)],
    top_k: Annotated[int, Query(ge=1, le=20)] = 3,
) -> MemoryContextResponse:
    memories = await retrieve_memories(db, current_user.id, query, top_k)
    return MemoryContextResponse(
        query=query,
        top_k=top_k,
        context=build_memory_context(memories),
        memories=memories,
    )

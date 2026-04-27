from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import get_db, get_sessionmaker
from app.core.redis_client import RedisCache, get_redis_cache
from app.models import User
from app.router.auth_router import get_current_user_required
from app.schemas.chat import (
    ChatSessionCreate,
    ChatSessionResponse,
    SendMessageRequest,
)
from app.service.chat_service import (
    create_user_chat_session,
    get_user_chat_session,
    stream_chat_response,
)


router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "/sessions",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    payload: ChatSessionCreate,
    current_user: Annotated[User, Depends(get_current_user_required)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChatSessionResponse:
    return await create_user_chat_session(db, current_user.id, payload.title)


@router.get("/sessions/{session_id}", response_model=ChatSessionResponse)
async def read_session(
    session_id: UUID,
    current_user: Annotated[User, Depends(get_current_user_required)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChatSessionResponse:
    chat_session = await get_user_chat_session(db, current_user.id, session_id)
    if chat_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found.",
        )
    return chat_session


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: UUID,
    payload: SendMessageRequest,
    current_user: Annotated[User, Depends(get_current_user_required)],
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)],
    redis_cache: Annotated[RedisCache, Depends(get_redis_cache)],
) -> StreamingResponse:
    async with sessionmaker() as db:
        chat_session = await get_user_chat_session(db, current_user.id, session_id)
    if chat_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found.",
        )

    return StreamingResponse(
        stream_chat_response(sessionmaker, redis_cache, session_id, payload.content),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

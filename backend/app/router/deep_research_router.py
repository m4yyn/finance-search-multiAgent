from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import get_sessionmaker
from app.core.redis_client import RedisCache, get_redis_cache
from app.models import User
from app.router.auth_router import get_current_user_required
from app.schemas.deep_research import DeepResearchStreamRequest
from app.service.chat_service import get_user_chat_session
from app.service.deep_research.graph import stream_deep_research_response


router = APIRouter(prefix="/deep-research", tags=["deep-research"])


@router.post("/stream")
async def stream_deep_research(
    payload: DeepResearchStreamRequest,
    current_user: Annotated[User, Depends(get_current_user_required)],
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)],
    redis_cache: Annotated[RedisCache, Depends(get_redis_cache)],
) -> StreamingResponse:
    async with sessionmaker() as db:
        chat_session = await get_user_chat_session(
            db,
            current_user.id,
            payload.session_id,
        )
    if chat_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found.",
        )

    return StreamingResponse(
        stream_deep_research_response(
            sessionmaker=sessionmaker,
            redis_cache=redis_cache,
            session_id=payload.session_id,
            user_id=current_user.id,
            content=payload.content,
            search_web=payload.search_web,
            search_local=payload.search_local,
            resume=payload.resume,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

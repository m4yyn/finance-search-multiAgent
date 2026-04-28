from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.redis_client import RedisCache, get_redis_cache
from app.models import User
from app.router.auth_router import get_current_user_required
from app.schemas.search import WebSearchRequest, WebSearchResponse
from app.service.search_service import BochaSearchError, search_web


router = APIRouter(prefix="/search", tags=["search"])


@router.post("/web", response_model=WebSearchResponse)
async def web_search(
    payload: WebSearchRequest,
    current_user: Annotated[User, Depends(get_current_user_required)],
    redis_cache: Annotated[RedisCache, Depends(get_redis_cache)],
) -> WebSearchResponse:
    del current_user
    try:
        return await search_web(
            redis_cache,
            payload.query,
            payload.count,
            payload.freshness,
            payload.summary,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except BochaSearchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.core.database import get_db
from app.core.redis_client import RedisCache, get_redis_cache
from app.core.security import create_access_token, decode_access_token
from app.models import User
from app.schemas.user import Token, UserCreate, UserLogin, UserResponse
from app.service.auth_service import (
    authenticate_user,
    create_user,
    get_existing_user_for_registration,
    get_user_by_id,
)


router = APIRouter(prefix="/auth", tags=["auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user_required(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Return the active authenticated user or raise the correct auth error."""
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
    except JWTError as exc:
        raise credentials_error from exc

    if not user_id:
        raise credentials_error

    user = await get_user_by_id(db, user_id)
    if user is None:
        raise credentials_error
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive.",
        )
    return user


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_user(
    payload: UserCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    existing_user = await get_existing_user_for_registration(
        db,
        payload.username,
        payload.email,
    )
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already registered.",
        )
    return await create_user(db, payload)


@router.post("/login", response_model=Token)
async def login_user(
    payload: UserLogin,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_cache: Annotated[RedisCache, Depends(get_redis_cache)],
) -> Token:
    user = await authenticate_user(db, payload.username_or_email, payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username, email, or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive.",
        )

    settings = get_settings()
    token = create_access_token(str(user.id))
    token_payload = decode_access_token(token)
    await redis_cache.set_session(
        token_payload["jti"],
        {"user_id": str(user.id), "username": user.username},
        expire_seconds=settings.jwt_access_token_expire_minutes * 60,
    )
    return Token(access_token=token)


@router.get("/me", response_model=UserResponse)
async def read_current_user(
    current_user: Annotated[User, Depends(get_current_user_required)],
) -> User:
    return current_user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    current_user: Annotated[User, Depends(get_current_user_required)],
    redis_cache: Annotated[RedisCache, Depends(get_redis_cache)],
) -> Response:
    """Delete the Redis session record for the current JWT when present."""
    del current_user
    payload = decode_access_token(token)
    token_id = payload.get("jti")
    if token_id:
        await redis_cache.delete(token_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

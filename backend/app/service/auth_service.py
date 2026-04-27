from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models import User
from app.schemas.user import UserCreate


async def get_user_by_id(session: AsyncSession, user_id: str | UUID) -> User | None:
    try:
        user_uuid = UUID(str(user_id))
    except ValueError:
        return None
    return await session.get(User, user_uuid)


async def get_user_by_username_or_email(
    session: AsyncSession,
    username_or_email: str,
) -> User | None:
    result = await session.execute(
        select(User).where(
            or_(
                User.username == username_or_email,
                User.email == username_or_email,
            )
        )
    )
    return result.scalar_one_or_none()


async def get_existing_user_for_registration(
    session: AsyncSession,
    username: str,
    email: str,
) -> User | None:
    result = await session.execute(
        select(User).where(or_(User.username == username, User.email == email))
    )
    return result.scalar_one_or_none()


async def create_user(session: AsyncSession, user_create: UserCreate) -> User:
    """Create and persist a user with a bcrypt password hash."""
    user = User(
        username=user_create.username,
        email=user_create.email,
        hashed_password=hash_password(user_create.password),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate_user(
    session: AsyncSession,
    username_or_email: str,
    password: str,
) -> User | None:
    user = await get_user_by_username_or_email(session, username_or_email)
    if user is None or not verify_password(password, user.hashed_password):
        return None
    return user

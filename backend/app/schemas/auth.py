from app.schemas.user import Token, UserCreate, UserLogin, UserResponse


UserRead = UserResponse
TokenResponse = Token


__all__ = [
    "Token",
    "TokenResponse",
    "UserCreate",
    "UserLogin",
    "UserRead",
    "UserResponse",
]

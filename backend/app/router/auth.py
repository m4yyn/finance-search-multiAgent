from app.router.auth_router import (
    get_current_user_required,
    login_user,
    logout_user,
    oauth2_scheme,
    read_current_user,
    register_user,
    router,
)


get_current_user = get_current_user_required


__all__ = [
    "get_current_user",
    "get_current_user_required",
    "login_user",
    "logout_user",
    "oauth2_scheme",
    "read_current_user",
    "register_user",
    "router",
]

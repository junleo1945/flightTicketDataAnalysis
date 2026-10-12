from functools import lru_cache

from fastapi import Depends, HTTPException, Request

from app.auth.erp import ErpClient, ErpUpstreamError
from app.auth.limiter import FixedWindowLimiter
from app.auth.service import AuthService, CurrentUser
from app.core.config import get_settings
from app.core.db import SessionLocal

SESSION_COOKIE = "fta_sid"
AUTH_EXPIRED_CODE = 40001


class AuthRequiredError(Exception):
    """未登录或会话失效。由 main.py 的异常处理器转为 401，前端据此跳转登录页。"""


@lru_cache
def get_auth_service() -> AuthService:
    settings = get_settings()
    return AuthService(SessionLocal, ErpClient(settings.erp_base_url), settings.session_verify_interval_seconds)


@lru_cache
def get_login_limiter() -> FixedWindowLimiter:
    # 每 5 分钟最多 10 次：同时作用于 IP 和用户名
    return FixedWindowLimiter(limit=10, window_seconds=300)


def current_user(request: Request, auth: AuthService = Depends(get_auth_service)) -> CurrentUser:
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        raise AuthRequiredError()
    try:
        user = auth.verify(sid)
    except ErpUpstreamError as exc:
        raise HTTPException(502, f"SSO 认证服务不可用: {exc}") from exc
    if user is None:
        raise AuthRequiredError()
    return user

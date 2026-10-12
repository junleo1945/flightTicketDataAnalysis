from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import SESSION_COOKIE, current_user, get_auth_service, get_login_limiter
from app.auth.erp import ErpAuthError, ErpUpstreamError
from app.auth.limiter import FixedWindowLimiter
from app.auth.service import AuthService, CurrentUser
from app.core.config import get_settings

router = APIRouter(prefix="/api/user", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "direct"


@router.post("/login")
def login(
    body: LoginBody,
    request: Request,
    response: Response,
    auth: AuthService = Depends(get_auth_service),
    limiter: FixedWindowLimiter = Depends(get_login_limiter),
) -> dict:
    if not limiter.hit(f"ip:{_client_ip(request)}") or not limiter.hit(f"user:{body.username.lower()}"):
        raise HTTPException(429, "登录尝试过于频繁，请稍后再试")

    try:
        result = auth.login(body.username, body.password)
    except ErpAuthError as exc:
        raise HTTPException(401, str(exc) or "用户名或密码错误") from exc
    except ErpUpstreamError as exc:
        raise HTTPException(502, f"ERP 认证服务不可用: {exc}") from exc

    response.set_cookie(
        SESSION_COOKIE,
        result.sid,
        max_age=result.max_age_seconds,
        httponly=True,
        secure=get_settings().session_cookie_secure,
        samesite="lax",
        path="/",
    )
    auth.purge_expired()
    return {"username": result.user.username, "display_name": result.user.display_name}


@router.get("/info")
def info(user: CurrentUser = Depends(current_user)) -> dict:
    return {"id": user.id, "username": user.username, "display_name": user.display_name}


@router.post("/logout")
def logout(request: Request, response: Response, auth: AuthService = Depends(get_auth_service)) -> dict:
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        auth.destroy(sid)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}

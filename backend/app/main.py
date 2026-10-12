import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import auth, chat, health, uploads
from app.api.deps import AUTH_EXPIRED_CODE, AuthRequiredError
from app.core.config import get_settings

logging.basicConfig(level=get_settings().log_level)

app = FastAPI(title="机票数据智能分析", version="0.3.0")
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(uploads.router)
app.include_router(chat.router)


@app.exception_handler(AuthRequiredError)
def _auth_required(_: Request, __: AuthRequiredError) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": "身份信息验证失败，请重新登录", "code": AUTH_EXPIRED_CODE},
    )

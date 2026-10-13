import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import auth, chat, grants, health, uploads
from app.api.deps import AUTH_EXPIRED_CODE, AuthRequiredError
from app.core.config import get_settings
from app.erp.sync import start_scheduler

logging.basicConfig(level=get_settings().log_level)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if get_settings().query_sync_enabled:
        start_scheduler()
    yield


app = FastAPI(title="机票数据智能分析", version="0.3.0", lifespan=lifespan)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(uploads.router)
app.include_router(grants.router)
app.include_router(chat.router)


@app.exception_handler(AuthRequiredError)
def _auth_required(_: Request, __: AuthRequiredError) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": "身份信息验证失败，请重新登录", "code": AUTH_EXPIRED_CODE},
    )

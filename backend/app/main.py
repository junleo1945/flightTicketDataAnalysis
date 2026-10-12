import logging

from fastapi import FastAPI

from app.api import chat, health, uploads
from app.core.config import get_settings

logging.basicConfig(level=get_settings().log_level)

app = FastAPI(title="机票数据智能分析", version="0.3.0")
app.include_router(health.router)
app.include_router(uploads.router)
app.include_router(chat.router)

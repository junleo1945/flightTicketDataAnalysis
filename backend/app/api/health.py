from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.core.db import engine

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict:
    """存活探针：进程能响应即返回 ok。"""
    return {"status": "ok"}


@router.get("/ready")
def ready() -> dict:
    """就绪探针：数据库可连通才返回 ok。"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ready"}

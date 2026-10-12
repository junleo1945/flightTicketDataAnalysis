import hashlib
from datetime import datetime

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from sqlalchemy import select

from app.core.db import SessionLocal
from app.importers.common import ImportValidationError, Window, build_window
from app.importers.order import import_orders
from app.importers.query import import_query
from app.importers.quality import import_quality
from app.models.upload import UploadBatch

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


# 统一签名：(session, batch, 文件字节, 时间窗口)。订单表不需要时间窗口，由表内时间字段决定
IMPORTERS = {
    "order": lambda s, b, d, w: import_orders(s, b, d),
    "query": import_query,
    "quality": import_quality,
}


def _serialize(b: UploadBatch) -> dict:
    def iso(v):
        return v.isoformat() if v is not None else None

    return {
        "id": b.id,
        "file_type": b.file_type,
        "file_name": b.file_name,
        "range_type": b.range_type,
        "biz_date": iso(b.biz_date),
        "period_start": b.period_start.strftime("%H:%M") if b.period_start else None,
        "period_end": b.period_end.strftime("%H:%M") if b.period_end else None,
        "data_min_time": iso(b.data_min_time),
        "data_max_time": iso(b.data_max_time),
        "row_count": b.row_count,
        "summary": b.summary,
        "uploaded_at": iso(b.uploaded_at),
    }


@router.get("")
def list_batches(limit: int = 50) -> list[dict]:
    with SessionLocal() as session:
        rows = session.scalars(select(UploadBatch).order_by(UploadBatch.id.desc()).limit(min(limit, 200)))
        return [_serialize(b) for b in rows]


@router.post("", status_code=201)
def create_upload(
    file: UploadFile = File(...),
    file_type: str = Form(...),
    range_type: str | None = Form(None),
    biz_date: str | None = Form(None),
    period_start: str | None = Form(None),
    period_end: str | None = Form(None),
) -> dict:
    if file_type not in IMPORTERS:
        raise HTTPException(400, "file_type 只能是 order / query / quality")
    data = file.file.read()
    if not data:
        raise HTTPException(400, "文件为空")

    window: Window | None = None
    if file_type != "order":
        try:
            window = build_window(range_type or "", biz_date or "", period_start, period_end)
        except ImportValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    file_hash = hashlib.sha256(data).hexdigest()
    try:
        with SessionLocal() as session, session.begin():
            dup = session.scalar(
                select(UploadBatch.id).where(UploadBatch.file_hash == file_hash, UploadBatch.file_type == file_type)
            )
            if dup is not None:
                raise HTTPException(409, f"该文件已导入过（批次 #{dup}），未重复写入")
            batch = UploadBatch(
                file_type=file_type,
                file_name=file.filename or "",
                file_hash=file_hash,
                range_type=window.range_type if window else None,
                biz_date=window.biz_date if window else None,
                period_start=window.start if window else None,
                period_end=window.end if window else None,
                uploaded_at=datetime.now(),
            )
            session.add(batch)
            session.flush()
            batch.summary = IMPORTERS[file_type](session, batch, data, window)
            result = _serialize(batch)
        return result
    except ImportValidationError as exc:
        raise HTTPException(400, str(exc)) from exc

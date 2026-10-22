import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.auth.service import CurrentUser
from app.core.db import SessionLocal
from app.importers.common import ImportValidationError, Window, build_window
from app.importers.order import import_orders
from app.importers.query import import_query
from app.models.analysis import QueryStat
from app.models.grant import DataGrant
from app.models.upload import RawOrderRow, UploadBatch

router = APIRouter(prefix="/api/uploads", tags=["uploads"], dependencies=[Depends(current_user)])


# 统一签名：(session, batch, 文件字节, 时间窗口)。订单表不需要时间窗口，由表内时间字段决定
IMPORTERS = {
    "order": lambda s, b, d, w: import_orders(s, b, d),
    "query": import_query,
}


def _serialize(b: UploadBatch) -> dict:
    def iso(v):
        return v.isoformat() if v is not None else None

    def iso_utc(v):
        # uploaded_at 存的是 UTC 时间（无时区列），输出带 +00:00，前端才能正确转成浏览器本地时间
        return v.replace(tzinfo=timezone.utc).isoformat() if v is not None else None

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
        "uploaded_at": iso_utc(b.uploaded_at),
    }


@router.get("")
def list_batches(limit: int = 50, user: CurrentUser = Depends(current_user)) -> list[dict]:
    """只返回当前用户自己的上传批次；无归属的历史批次不展示。"""
    with SessionLocal() as session:
        rows = session.scalars(
            select(UploadBatch)
            .where(UploadBatch.user_id == user.id)
            .order_by(UploadBatch.id.desc())
            .limit(min(limit, 200))
        )
        return [_serialize(b) for b in rows]


DETAIL_PAGE_SIZE = 50
# 明细表不展示的内部字段
_INTERNAL_COLS = {"id", "user_id", "batch_id", "source"}


def _can_view_batch(session: Session, user_id: int, batch: UploadBatch) -> bool:
    """本人上传的批次，或拥有者授权给本人的批次可查看明细；分析与问答不受此授权影响。"""
    if batch.user_id is None:
        return False
    if batch.user_id == user_id:
        return True
    return session.scalar(
        select(DataGrant.id).where(DataGrant.owner_user_id == batch.user_id, DataGrant.grantee_user_id == user_id)
    ) is not None


@router.get("/{batch_id}/rows")
def batch_rows(
    batch_id: int,
    page: int = Query(1, ge=1),
    user: CurrentUser = Depends(current_user),
) -> dict:
    """批次明细（分页）。订单表返回脱敏后的原始行；查询量表返回解析后的行。"""
    with SessionLocal() as session:
        batch = session.get(UploadBatch, batch_id)
        if batch is None or not _can_view_batch(session, user.id, batch):
            raise HTTPException(404, "批次不存在或无权查看")

        offset = (page - 1) * DETAIL_PAGE_SIZE
        if batch.file_type == "order":
            total = session.scalar(select(func.count()).select_from(RawOrderRow).where(RawOrderRow.batch_id == batch_id))
            raw = session.scalars(
                select(RawOrderRow).where(RawOrderRow.batch_id == batch_id)
                .order_by(RawOrderRow.sheet_name, RawOrderRow.row_no).offset(offset).limit(DETAIL_PAGE_SIZE)
            ).all()
            rows = [{"sheet_name": r.sheet_name, "row_no": r.row_no, **(r.data or {})} for r in raw]
            columns: list[str] = ["sheet_name", "row_no"]
            for r in rows:
                columns += [k for k in r if k not in columns]
        else:
            model = QueryStat
            total = session.scalar(select(func.count()).select_from(model).where(model.batch_id == batch_id))
            items = session.scalars(
                select(model).where(model.batch_id == batch_id).order_by(model.id).offset(offset).limit(DETAIL_PAGE_SIZE)
            ).all()
            columns = [c.name for c in model.__table__.columns if c.name not in _INTERNAL_COLS]
            rows = [{c: _jsonable(getattr(it, c)) for c in columns} for it in items]

        return {
            "batch_id": batch_id,
            "file_type": batch.file_type,
            "total": total,
            "page": page,
            "page_size": DETAIL_PAGE_SIZE,
            "columns": columns,
            "rows": rows,
        }


def _jsonable(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (datetime,)):
        return v.isoformat()
    return str(v)  # date / time / Decimal 等统一转为字符串


@router.post("", status_code=201)
def create_upload(
    user: CurrentUser = Depends(current_user),
    file: UploadFile = File(...),
    file_type: str = Form(...),
    range_type: str | None = Form(None),
    biz_date: str | None = Form(None),
    period_start: str | None = Form(None),
    period_end: str | None = Form(None),
) -> dict:
    if file_type not in IMPORTERS:
        raise HTTPException(400, "file_type 只能是 order / query")
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
                select(UploadBatch.id).where(
                    UploadBatch.file_hash == file_hash, UploadBatch.file_type == file_type, UploadBatch.user_id == user.id
                )
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
                uploaded_at=datetime.now(timezone.utc).replace(tzinfo=None),  # 无时区列，统一存 UTC，不依赖容器时区
                user_id=user.id,
            )
            session.add(batch)
            session.flush()
            batch.summary = IMPORTERS[file_type](session, batch, data, window)
            result = _serialize(batch)
        return result
    except ImportValidationError as exc:
        raise HTTPException(400, str(exc)) from exc

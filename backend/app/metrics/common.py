from datetime import date, datetime, time, timedelta

from app.importers.common import ImportValidationError

STATUS_DONE = "出票完成"
DEFAULT_THRESHOLD = -0.10  # 异常下降阈值（方案 3.1）
MAX_X = 60


def parse_date(s: str | None) -> date:
    if not s:
        raise ImportValidationError("请提供业务日期（YYYY-MM-DD）")
    try:
        return date.fromisoformat(s)
    except ValueError as exc:
        raise ImportValidationError(f"日期格式应为 YYYY-MM-DD：{s}") from exc


def parse_time(s: str | None) -> time | None:
    if not s:
        return None
    try:
        return time.fromisoformat(s)
    except ValueError as exc:
        raise ImportValidationError(f"时间格式应为 HH:MM：{s}") from exc


def parse_x(x) -> int:
    try:
        n = int(x)
    except (TypeError, ValueError) as exc:
        raise ImportValidationError("x（历史对比天数）必须是整数") from exc
    if not 1 <= n <= MAX_X:
        raise ImportValidationError(f"x 必须在 1～{MAX_X} 之间")
    return n


def parse_threshold(t) -> float:
    if t is None:
        return DEFAULT_THRESHOLD
    v = float(t)
    if v >= 0:
        raise ImportValidationError("阈值应为负数，如 -0.1 表示下降 10%")
    return v


def history_range(day: date, x: int) -> tuple[date, date]:
    """历史窗口：前 x 天，不含当天。返回 (起始日, 结束日)。"""
    return day - timedelta(days=x), day - timedelta(days=1)


def pct(v: float | None) -> str | None:
    return None if v is None else f"{v * 100:.2f}%"


def window_text(day: date, wend: time, h0: date, h1: date) -> dict:
    return {
        "当前窗口": f"{day} 00:00～{wend.strftime('%H:%M:%S')}（北京时间）",
        "历史窗口": f"{h0}～{h1}，每天同样的时段",
    }


def latest_time(session, sql: str, params: dict) -> time | None:
    """用于默认截止时间：当天最晚一笔的时间（Q19 建议值）。"""
    from sqlalchemy import text

    value = session.execute(text(sql), params).scalar()
    return value.time() if isinstance(value, datetime) else value

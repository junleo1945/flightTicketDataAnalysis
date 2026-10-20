"""订单数据覆盖情况：除了最早/最晚日期，还要找出中间没有数据的日期，以及最晚一天是否只传了一部分。

只看首尾日期会掩盖缺口（如 10-01～10-06 没传），模型会把"没数据"误当成"没订单"。
"""

from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

PARTIAL_RATIO = 0.3  # 最晚一天的单数低于前 7 天日均的该比例，认为可能没传完
PARTIAL_WINDOW = 7


def missing_date_ranges(counts: dict[date, int]) -> list[str]:
    """最早到最晚之间没有任何订单的日期，连续的合并成区间，如 ['2026-10-01～2026-10-06', '2026-10-11']。"""
    if not counts:
        return []
    lo, hi = min(counts), max(counts)
    ranges: list[tuple[date, date]] = []
    day = lo
    while day <= hi:
        if counts.get(day, 0) > 0:
            day += timedelta(days=1)
            continue
        start = day
        while day <= hi and counts.get(day, 0) == 0:
            day += timedelta(days=1)
        ranges.append((start, day - timedelta(days=1)))
    return [str(a) if a == b else f"{a}～{b}" for a, b in ranges]


def latest_day_looks_partial(counts: dict[date, int]) -> bool:
    """最晚一天的单数远低于此前 7 天（含空缺日按 0 计）的日均，可能只上传了半天。"""
    if not counts:
        return False
    last = max(counts)
    prev = [counts.get(last - timedelta(days=i), 0) for i in range(1, PARTIAL_WINDOW + 1)]
    avg = sum(prev) / PARTIAL_WINDOW
    return avg > 0 and counts[last] < avg * PARTIAL_RATIO


def describe_coverage(counts: dict[date, int]) -> dict:
    """某类订单的覆盖情况：最早、最晚、缺失日期、最晚一天是否可能不完整。"""
    days = [d for d, n in counts.items() if n > 0]
    if not days:
        return {"最早": None, "最晚": None}
    lo, hi = min(days), max(days)
    non_empty = {d: n for d, n in counts.items() if n > 0}
    out: dict = {"最早": str(lo), "最晚": str(hi)}
    gaps = missing_date_ranges(non_empty)
    if gaps:
        out["缺失日期（范围内没有任何订单数据，说明没上传，不是没有订单）"] = gaps
    out["最晚一天订单数"] = non_empty[hi]
    if latest_day_looks_partial(non_empty):
        out["提示"] = f"最晚一天（{hi}）订单数远低于此前几天，可能只上传了一部分，该日统计不完整。"
    return out


def _load_counts(session: Session, sql: str, uid: int) -> dict[date, int]:
    return {r[0]: int(r[1]) for r in session.execute(text(sql), {"uid": uid}).all() if r[0] is not None}


def order_coverage(session: Session, uid: int) -> dict:
    """当前用户渠道订单（支付日期）与供应订单（兜底后的支付日期）的按日覆盖情况。"""
    return {
        "订单（渠道订单，支付日期）": describe_coverage(
            _load_counts(session, "SELECT pay_date, COUNT(*) FROM tm_order WHERE user_id = :uid GROUP BY pay_date", uid)
        ),
        "供应订单（支付日期，兜底后）": describe_coverage(
            _load_counts(
                session,
                "SELECT CAST(pay_time_effective AS DATE), COUNT(*) FROM supplier_order WHERE user_id = :uid "
                "GROUP BY CAST(pay_time_effective AS DATE)",
                uid,
            )
        ),
    }

"""验价生单数据质量接口：POST /flight/data-quality/query。

内网接口，免鉴权，不需要 Cookie 或 Authorization。支持按任意时间区间（精确到秒）查询，
返回按「日期 × 渠道 × 供应商 × 航司」汇总的数量字段。
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

import httpx

from app.auth.erp import ErpUpstreamError

PATH = "/flight/data-quality/query"
PAGE_SIZE = 500
MAX_PAGES = 200  # 防御性上限，正常情况下一天的记录远少于这个数
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
FULL_DAY_START, FULL_DAY_END = time(0, 0, 0), time(23, 59, 59)

COUNT_FIELDS = (
    "total", "timeout_5_10", "timeout_gt_10", "verify_success", "verify_fail", "supplier_success",
    "supplier_fail", "supplier_changed", "unbook", "booking_success", "booking_fail",
)
RATE_FIELDS = ("booking_rate", "booking_success_rate", "timeout_rate")


@dataclass(frozen=True)
class DataQualityRow:
    stat_date: date
    agent_code: str  # 渠道（客户）
    supplier_code: str
    ticket_carrier: str  # 航司
    total: int  # 渠道侧总验价数
    timeout_5_10: int  # 验价耗时超过 5 秒且不超过 10 秒
    timeout_gt_10: int  # 验价耗时超过 10 秒
    verify_success: int
    verify_fail: int
    supplier_success: int  # 供应商返回成功数，包含变价
    supplier_fail: int
    supplier_changed: int  # 供应商变价数，是 supplier_success 的子集
    unbook: int  # 验价成功后渠道未发起预定
    booking_success: int
    booking_fail: int
    booking_rate: Decimal  # 以下三个是接口给的比率，只用于与反算值对照
    booking_success_rate: Decimal
    timeout_rate: Decimal

    @property
    def key(self) -> tuple[date, str, str, str]:
        return (self.stat_date, self.agent_code, self.supplier_code, self.ticket_carrier)


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ErpUpstreamError(f"数据质量接口返回了无法识别的数量：{value!r}") from exc


def _rate(value) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0))
    except InvalidOperation as exc:
        raise ErpUpstreamError(f"数据质量接口返回了无法识别的比率：{value!r}") from exc


def _parse_row(item: dict) -> DataQualityRow:
    try:
        day = date.fromisoformat(str(item.get("stat_date") or "")[:10])
    except ValueError as exc:
        raise ErpUpstreamError(f"数据质量接口返回了无法识别的日期：{item.get('stat_date')!r}") from exc
    return DataQualityRow(
        stat_date=day,
        agent_code=str(item.get("agent_code") or ""),
        supplier_code=str(item.get("supplier_code") or ""),
        ticket_carrier=str(item.get("ticket_carrier") or ""),
        **{f: _int(item.get(f)) for f in COUNT_FIELDS},
        **{f: _rate(item.get(f)) for f in RATE_FIELDS},
    )


class DataQualityClient:
    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None, timeout_seconds: float = 30.0):
        self._base = base_url.rstrip("/")
        self._http = httpx.Client(timeout=timeout_seconds, follow_redirects=False, transport=transport)

    def close(self) -> None:
        self._http.close()

    def _post(self, payload: dict) -> dict:
        try:
            res = self._http.post(
                f"{self._base}{PATH}",
                json=payload,
                headers={"Cloud-Trace-Id": str(uuid.uuid4())},
            )
        except httpx.HTTPError as exc:
            raise ErpUpstreamError(f"数据质量接口请求失败：{exc}") from exc
        if res.status_code != 200:
            raise ErpUpstreamError(f"数据质量接口 HTTP {res.status_code}")
        try:
            body = res.json()
        except ValueError as exc:
            raise ErpUpstreamError("数据质量接口返回的不是 JSON") from exc
        if not isinstance(body, dict):
            raise ErpUpstreamError("数据质量接口返回结构不符合预期")
        if body.get("code") != 0:
            raise ErpUpstreamError(f"数据质量接口返回失败：{body.get('message') or body.get('code')}")
        data = body.get("data")
        if not isinstance(data, dict):
            raise ErpUpstreamError("数据质量接口返回结构不符合预期")
        return data

    def fetch_range(self, start: datetime, end: datetime) -> list[DataQualityRow]:
        """拉取任意时间区间（北京时间，精确到秒）内的全部记录。同一键重复出现时取最后一条。"""
        if start > end:
            raise ErpUpstreamError("数据质量查询的开始时间不能晚于结束时间")
        by_key: dict[tuple, DataQualityRow] = {}
        fetched = 0  # 原始返回条数（含重复），用来判断是否取完
        for page in range(1, MAX_PAGES + 1):
            data = self._post({
                "start_time": start.strftime(TIME_FORMAT),
                "end_time": end.strftime(TIME_FORMAT),
                "page_no": page,
                "page_size": PAGE_SIZE,
            })
            items = data.get("items") or []
            total = _int(data.get("total"))
            for item in items:
                row = _parse_row(item)
                by_key[row.key] = row
            fetched += len(items)
            if not items or fetched >= total:
                return list(by_key.values())
        raise ErpUpstreamError("数据质量接口分页超过上限，数据可能不完整")

    def fetch_day(self, day: date) -> list[DataQualityRow]:
        """完整一天：00:00:00 ～ 23:59:59。"""
        return self.fetch_range(datetime.combine(day, FULL_DAY_START), datetime.combine(day, FULL_DAY_END))

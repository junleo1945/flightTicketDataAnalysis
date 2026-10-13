"""ERP 查订比接口：/flight/config/look2book/records，只支持按日期查询（完整一天）。

使用调用者的 tm_sso_sid（来自其登录会话），不保存密码，会话失效时提示重新登录。
"""

from dataclasses import dataclass
from datetime import date

import httpx

from app.auth.erp import ErpAuthError, ErpUpstreamError

PATH = "/flight/config/look2book/records"
PAGE_SIZE = 500
MAX_PAGES = 200  # 防御性上限，正常情况下一天的记录远少于这个数
CTYPE = {1: "channel", 2: "supplier"}  # 接口的合作类型：1 渠道，2 供应商


@dataclass(frozen=True)
class Look2BookRow:
    partner_type: str  # channel / supplier
    partner_code: str
    query_count: int  # LookNum
    quote_count: int  # LookSuccNum，有报价量
    order_count: int  # BookNum
    segment_count: int  # SegmentNum
    blocked_count: int  # InterceptNum，被拦截次数


def _parse_row(item: dict) -> Look2BookRow:
    ctype = item.get("CType")
    if ctype not in CTYPE:
        raise ErpUpstreamError(f"查订比接口返回了未知的合作类型：{ctype}")
    return Look2BookRow(
        partner_type=CTYPE[ctype],
        partner_code=str(item.get("Code") or ""),
        query_count=int(item.get("LookNum") or 0),
        quote_count=int(item.get("LookSuccNum") or 0),
        order_count=int(item.get("BookNum") or 0),
        segment_count=int(item.get("SegmentNum") or 0),
        blocked_count=int(item.get("InterceptNum") or 0),
    )


class Look2BookClient:
    """使用当前登录用户自己的 tm_sso_sid 调用查订比接口，权限与该用户在 ERP 中的一致。"""

    def __init__(self, base_url: str, erp_sid: str, transport: httpx.BaseTransport | None = None, timeout_seconds: float = 15.0):
        self._base = base_url.rstrip("/")
        self._sid = erp_sid
        self._http = httpx.Client(timeout=timeout_seconds, follow_redirects=False, transport=transport)

    def close(self) -> None:
        self._http.close()

    def _post(self, payload: dict) -> dict:
        try:
            res = self._http.post(
                f"{self._base}{PATH}",
                json=payload,
                headers={"Cookie": f"tm_sso_sid={self._sid}", "X-Requested-With": "XMLHttpRequest"},
            )
        except httpx.HTTPError as exc:
            raise ErpUpstreamError(f"查订比接口请求失败：{exc}") from exc

        if res.status_code in (401, 302, 307):
            raise ErpAuthError("ERP 登录已失效，请重新登录后再查询")
        if res.status_code != 200:
            raise ErpUpstreamError(f"查订比接口 HTTP {res.status_code}")

        try:
            body = res.json()
        except ValueError as exc:
            raise ErpUpstreamError("查订比接口返回的不是 JSON") from exc
        if isinstance(body, dict) and "Success" in body and not body["Success"]:
            raise ErpUpstreamError(body.get("UserMsg") or body.get("ErrMsg") or "查订比接口返回失败")
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise ErpUpstreamError("查订比接口返回结构不符合预期")
        return data

    def fetch_day(self, day: date) -> list[Look2BookRow]:
        """拉取某一天（完整一天）全部合作方的查订比数据。同一合作方重复出现时取最后一条。"""
        day_text = day.isoformat()
        by_key: dict[tuple[str, str], Look2BookRow] = {}
        fetched = 0  # 原始返回条数（含重复），用来判断是否取完
        for page in range(1, MAX_PAGES + 1):
            data = self._post({"Code": "", "CType": 0, "Start": day_text, "End": day_text, "PageNo": page, "PageSize": PAGE_SIZE})
            items = data.get("List") or []
            total = int(data.get("Total") or 0)
            for item in items:
                row = _parse_row(item)
                by_key[(row.partner_type, row.partner_code)] = row
            fetched += len(items)
            if not items or fetched >= total:
                return list(by_key.values())
        raise ErpUpstreamError("查订比接口分页超过上限，数据可能不完整")

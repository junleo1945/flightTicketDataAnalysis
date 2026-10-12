"""ERP 公网接口登录（参考 all-in-flow 的 apps/server/src/auth/erp.ts）。

不保存密码：密码只在 authenticate 中透传给 ERP。
"""

from dataclasses import dataclass

import httpx


class ErpAuthError(Exception):
    """账号或密码错误、ERP 会话已失效。"""


class ErpUpstreamError(Exception):
    """ERP 不可用（网络错误、超时、非预期的状态码）。"""


@dataclass(frozen=True)
class ErpUser:
    user_id: int
    username: str
    display_name: str
    # ERP 会话剩余秒数（上游可能返回纳秒，这里已换算）
    expire_seconds: int
    # 仅服务端保存，用于后续复核
    erp_sid: str


class ErpClient:
    def __init__(self, base_url: str, timeout_seconds: float = 5.0, transport: httpx.BaseTransport | None = None):
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_seconds, follow_redirects=False, transport=transport)

    def authenticate(self, username: str, password: str) -> ErpUser:
        try:
            res = self._client.post(
                f"{self._base}/api/login",
                json={"UserName": username, "Password": password},
            )
        except httpx.HTTPError as exc:
            raise ErpUpstreamError(f"ERP request failed: {exc}") from exc

        if res.status_code in (401, 403):
            raise ErpAuthError("用户名或密码错误")
        if res.status_code != 200:
            raise ErpUpstreamError(f"ERP /api/login HTTP {res.status_code}")

        body = _json(res)
        if isinstance(body, dict):
            if body.get("ErrCode") not in (None, 0):
                raise ErpAuthError(body.get("UserMsg") or body.get("ErrMsg") or "用户名或密码错误")
            if "Success" in body and not body["Success"]:
                raise ErpAuthError(body.get("UserMsg") or body.get("ErrMsg") or "用户名或密码错误")

        sid = _extract_sid(res.headers.get_list("set-cookie"))
        if not sid:
            raise ErpUpstreamError("ERP 登录成功但未返回 tm_sso_sid")
        return self.verify(sid)

    def verify(self, erp_sid: str) -> ErpUser:
        try:
            res = self._client.get(
                f"{self._base}/user/api/get",
                headers={"Cookie": f"tm_sso_sid={erp_sid}", "X-Requested-With": "XMLHttpRequest"},
            )
        except httpx.HTTPError as exc:
            raise ErpUpstreamError(f"ERP request failed: {exc}") from exc

        if res.status_code in (401, 302, 307):
            raise ErpAuthError("未登录")
        if res.status_code != 200:
            raise ErpUpstreamError(f"ERP /user/api/get HTTP {res.status_code}")

        body = _json(res)
        if not isinstance(body, dict) or not body.get("Success"):
            raise ErpAuthError("ERP 获取用户信息失败")

        data = body.get("Data") or {}
        user_id = int(data.get("UserId") or 0)
        if not user_id:
            raise ErpAuthError("ERP 返回的用户无效")
        username = str(data.get("UserName") or "")
        raw = float(data.get("ExpireDuration") or 0)
        # 上游可能返回纳秒，也可能返回秒；与 all-in-flow 的换算规则一致
        expire = int(raw // 1_000_000_000) if raw > 1_000_000_000 else int(raw)
        if expire <= 0:
            expire = 86400

        return ErpUser(
            user_id=user_id,
            username=username,
            display_name=str(data.get("FullName") or username),
            expire_seconds=expire,
            erp_sid=erp_sid,
        )

    def close(self) -> None:
        self._client.close()


def _json(res: httpx.Response) -> object:
    try:
        return res.json()
    except ValueError:
        return None


def _extract_sid(set_cookies: list[str]) -> str | None:
    for cookie in set_cookies:
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "tm_sso_sid" and value:
                return value
    return None

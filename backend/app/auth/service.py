import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.auth.erp import ErpAuthError, ErpClient, ErpUser
from app.models.auth import AuthSession, User

SESSION_MAX_AGE_SECONDS = 86400


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    display_name: str


@dataclass(frozen=True)
class LoginResult:
    sid: str
    max_age_seconds: int
    user: CurrentUser


class AuthService:
    """本地会话管理：登录写入 users / auth_sessions；校验时定期向 ERP 复核。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        erp: ErpClient,
        verify_interval_seconds: int,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._session_factory = session_factory
        self._erp = erp
        self._verify_interval = verify_interval_seconds
        self._now = now

    def login(self, username: str, password: str) -> LoginResult:
        erp_user = self._erp.authenticate(username, password)  # 账号错误抛出 ErpAuthError，上游故障抛出 ErpUpstreamError
        t = self._now()
        sid = secrets.token_urlsafe(32)
        max_age = min(erp_user.expire_seconds, SESSION_MAX_AGE_SECONDS)
        with self._session_factory() as db, db.begin():
            self._upsert_user(db, erp_user, t)
            db.flush()  # 先写入 users，再写 auth_sessions 外键引用
            db.add(
                AuthSession(
                    sid=sid,
                    user_id=erp_user.user_id,
                    erp_sid=erp_user.erp_sid,
                    expires_at=t + timedelta(seconds=max_age),
                    verified_at=t,
                )
            )
        return LoginResult(
            sid=sid,
            max_age_seconds=max_age,
            user=CurrentUser(id=erp_user.user_id, username=erp_user.username, display_name=erp_user.display_name),
        )

    def verify(self, sid: str) -> CurrentUser | None:
        """本地会话未过期；距上次复核超过间隔则向 ERP 复核。

        ERP 明确判定失效时删除会话并返回 None；ERP 故障时抛出 ErpUpstreamError，不踢人。
        """
        t = self._now()
        with self._session_factory() as db:
            row = db.execute(
                select(AuthSession, User)
                .join(User, User.id == AuthSession.user_id)
                .where(AuthSession.sid == sid, AuthSession.expires_at > t)
            ).first()
            if row is None:
                return None
            session_row, user = row
            current = CurrentUser(id=user.id, username=user.username, display_name=user.display_name)
            verified_at = _aware(session_row.verified_at)
            erp_sid = session_row.erp_sid

        if (t - verified_at).total_seconds() < self._verify_interval:
            return current

        try:
            fresh = self._erp.verify(erp_sid)
            if fresh.user_id != current.id:
                raise ErpAuthError("身份不一致")
        except ErpAuthError:
            self.destroy(sid)
            return None

        with self._session_factory() as db, db.begin():
            db.execute(update(AuthSession).where(AuthSession.sid == sid).values(verified_at=t))
        return current

    def destroy(self, sid: str) -> None:
        with self._session_factory() as db, db.begin():
            db.execute(delete(AuthSession).where(AuthSession.sid == sid))

    def purge_expired(self) -> None:
        t = self._now()
        with self._session_factory() as db, db.begin():
            db.execute(delete(AuthSession).where(AuthSession.expires_at <= t))

    @staticmethod
    def _upsert_user(db: Session, erp_user: ErpUser, t: datetime) -> None:
        user = db.get(User, erp_user.user_id)
        if user is None:
            db.add(User(id=erp_user.user_id, username=erp_user.username, display_name=erp_user.display_name, last_login_at=t))
            return
        user.username = erp_user.username
        user.display_name = erp_user.display_name
        user.last_login_at = t


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

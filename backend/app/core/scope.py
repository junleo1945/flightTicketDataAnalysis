from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.grant import DataGrant


def visible_user_ids(session: Session, user_id: int) -> list[int]:
    """当前用户可读取的数据归属 user_id：自己的 + 别人授权给自己的。
    读取查询用 `user_id = ANY(:uids)`；写入（上传、ERP 同步）仍只用 user_id 本人。"""
    owners = session.scalars(select(DataGrant.owner_user_id).where(DataGrant.grantee_user_id == user_id)).all()
    return sorted({int(user_id), *(int(o) for o in owners)})

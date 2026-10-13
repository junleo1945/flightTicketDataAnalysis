from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.deps import current_user
from app.auth.service import CurrentUser
from app.core.db import SessionLocal
from app.models.auth import User
from app.models.grant import DataGrant

router = APIRouter(prefix="/api/data-grants", tags=["data-grants"], dependencies=[Depends(current_user)])


class GrantIn(BaseModel):
    username: str = Field(min_length=1, max_length=128)


def _brief(u: User) -> dict:
    return {"user_id": u.id, "username": u.username, "display_name": u.display_name}


@router.get("")
def list_grants(user: CurrentUser = Depends(current_user)) -> dict:
    """granted_to_me：别人授权给我的数据；granted_by_me：我授权给了谁。"""
    with SessionLocal() as session:
        to_me = session.scalars(
            select(User).join(DataGrant, DataGrant.owner_user_id == User.id).where(DataGrant.grantee_user_id == user.id)
        ).all()
        by_me = session.scalars(
            select(User).join(DataGrant, DataGrant.grantee_user_id == User.id).where(DataGrant.owner_user_id == user.id)
        ).all()
        return {"granted_to_me": [_brief(u) for u in to_me], "granted_by_me": [_brief(u) for u in by_me]}


@router.post("", status_code=201)
def add_grant(body: GrantIn, user: CurrentUser = Depends(current_user)) -> dict:
    """把自己的数据授权给指定用户（按 ERP 用户名查找，对方需至少登录过一次）。重复授权不报错。"""
    with SessionLocal() as session, session.begin():
        grantee = session.scalar(select(User).where(User.username == body.username.strip()))
        if grantee is None:
            raise HTTPException(404, "用户不存在，对方需要先登录过本系统")
        if grantee.id == user.id:
            raise HTTPException(400, "不能授权给自己")
        exists = session.scalar(
            select(DataGrant.id).where(DataGrant.owner_user_id == user.id, DataGrant.grantee_user_id == grantee.id)
        )
        if exists is None:
            session.add(DataGrant(owner_user_id=user.id, grantee_user_id=grantee.id))
        return _brief(grantee)


@router.delete("/{grantee_user_id}", status_code=204)
def revoke_grant(grantee_user_id: int, user: CurrentUser = Depends(current_user)) -> None:
    """撤销自己授予某用户的数据权限，立即生效。"""
    with SessionLocal() as session, session.begin():
        session.execute(
            delete(DataGrant).where(DataGrant.owner_user_id == user.id, DataGrant.grantee_user_id == grantee_user_id)
        )

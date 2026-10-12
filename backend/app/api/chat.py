import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.auth.service import CurrentUser
from app.core.db import SessionLocal
from app.llm.chat import ask_stream
from app.models.chat import ChatLog, Conversation

router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(current_user)])

TITLE_MAX_CHARS = 30


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[HistoryItem] = Field(default_factory=list, max_length=20)
    # 不传则新建会话
    conversation_id: int | None = None


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    pinned: bool | None = None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _conversation_json(c: Conversation) -> dict:
    return {
        "id": c.id,
        "title": c.title,
        "pinned": c.pinned,
        "created_at": _iso(c.created_at),
        "updated_at": _iso(c.updated_at),
    }


def _owned_conversation(session: Session, user_id: int, conversation_id: int) -> Conversation:
    conv = session.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id))
    if conv is None:
        raise HTTPException(404, "会话不存在")
    return conv


def _title_from(question: str) -> str:
    title = " ".join(question.split())
    return title[:TITLE_MAX_CHARS] or "新对话"


def _save_turn(user_id: int, conversation_id: int, question: str, result: dict) -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as session, session.begin():
        session.add(
            ChatLog(
                conversation_id=conversation_id,
                question=question,
                answer=result["answer"],
                tool_calls=result["tool_calls"],
                trace=result["trace"],
                model=result["model"],
                latency_ms=result["latency_ms"],
                user_id=user_id,
            )
        )
        session.execute(update(Conversation).where(Conversation.id == conversation_id).values(updated_at=now))


def _discard_conversation(user_id: int, conversation_id: int) -> None:
    """新建的会话没有产生任何问答（如模型调用失败），删除空会话。"""
    with SessionLocal() as session, session.begin():
        session.execute(delete(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id))


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


@router.get("/conversations")
def list_conversations(user: CurrentUser = Depends(current_user)) -> list[dict]:
    """当前用户的会话：收藏的在前，其次按最近更新排序。"""
    with SessionLocal() as session:
        rows = session.scalars(
            select(Conversation)
            .where(Conversation.user_id == user.id)
            .order_by(Conversation.pinned.desc(), Conversation.updated_at.desc())
        )
        return [_conversation_json(c) for c in rows]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, user: CurrentUser = Depends(current_user)) -> dict:
    with SessionLocal() as session:
        conv = _owned_conversation(session, user.id, conversation_id)
        logs = session.scalars(
            select(ChatLog).where(ChatLog.conversation_id == conv.id).order_by(ChatLog.id.asc())
        )
        return {
            **_conversation_json(conv),
            "messages": [
                {
                    "id": log.id,
                    "question": log.question,
                    "answer": log.answer,
                    "model": log.model,
                    "latency_ms": log.latency_ms,
                    "trace": log.trace,
                    "created_at": _iso(log.created_at),
                }
                for log in logs
            ],
        }


@router.patch("/conversations/{conversation_id}")
def update_conversation(
    conversation_id: int, body: ConversationPatch, user: CurrentUser = Depends(current_user)
) -> dict:
    with SessionLocal() as session, session.begin():
        conv = _owned_conversation(session, user.id, conversation_id)
        if body.title is not None:
            title = body.title.strip()
            if not title:
                raise HTTPException(400, "标题不能为空")
            conv.title = title
        if body.pinned is not None:
            conv.pinned = body.pinned
        session.flush()
        return _conversation_json(conv)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, user: CurrentUser = Depends(current_user)) -> dict:
    """删除会话及其全部问答记录（由外键级联删除）。"""
    with SessionLocal() as session, session.begin():
        conv = _owned_conversation(session, user.id, conversation_id)
        session.delete(conv)
    return {"ok": True}


@router.post("/stream")
def chat_stream(req: ChatRequest, user: CurrentUser = Depends(current_user)) -> StreamingResponse:
    """SSE 推送：每个事件一行 `data: {json}`，以空行结束。

    首个事件为 {"type": "conversation", "id"}，告诉前端本次问答所属的会话。
    """
    question = req.question.strip()
    is_new = req.conversation_id is None
    with SessionLocal() as session, session.begin():
        if is_new:
            conv = Conversation(user_id=user.id, title=_title_from(question))
            session.add(conv)
            session.flush()
            conversation_id = conv.id
        else:
            conversation_id = _owned_conversation(session, user.id, req.conversation_id).id

    def events():
        saved = False
        yield _sse({"type": "conversation", "id": conversation_id})
        try:
            for ev in ask_stream(question, [h.model_dump() for h in req.history]):
                if ev["type"] == "done":
                    _save_turn(user.id, conversation_id, question, ev)
                    saved = True
                yield _sse(ev)
        except RuntimeError as exc:
            yield _sse({"type": "error", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "error", "message": f"模型调用失败：{type(exc).__name__}: {str(exc)[:300]}"})
        finally:
            if is_new and not saved:
                _discard_conversation(user.id, conversation_id)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

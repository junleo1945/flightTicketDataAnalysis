import json
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.db import SessionLocal
from app.llm.chat import ask_stream
from app.models.chat import ChatLog

router = APIRouter(prefix="/api/chat", tags=["chat"])


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[HistoryItem] = Field(default_factory=list, max_length=20)


def _save_log(question: str, result: dict) -> None:
    with SessionLocal() as session, session.begin():
        session.add(ChatLog(question=question, answer=result["answer"], tool_calls=result["tool_calls"],
                            model=result["model"], latency_ms=result["latency_ms"]))


@router.post("/stream")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    """SSE 推送：每个事件一行 `data: {json}`，以空行结束。"""

    def events():
        question = req.question.strip()
        try:
            for ev in ask_stream(question, [h.model_dump() for h in req.history]):
                if ev["type"] == "done":
                    _save_log(question, ev)
                yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
        except RuntimeError as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            msg = f"模型调用失败：{type(exc).__name__}: {str(exc)[:300]}"
            yield f"data: {json.dumps({'type': 'error', 'message': msg}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

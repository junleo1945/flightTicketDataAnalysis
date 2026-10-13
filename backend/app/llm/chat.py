import json
import time
from datetime import datetime, timedelta, timezone
from typing import Iterator

from openai import OpenAI

from app.core.config import get_settings
from app.llm.tools import TOOL_SPECS, ToolContext, run_tool

MAX_STEPS = 6
TZ = timezone(timedelta(hours=8))  # 北京时间，固定偏移，不依赖系统时区数据

SYSTEM_PROMPT = """你是公司内部的机票经营数据分析助手。当前时间：{now}（北京时间）。

规则：
1. 所有数字必须来自工具返回结果，不得自行计算、估算或编造。需要数字时调用工具。
2. 异常类问题（订单量、查订比、利润异常）必须明确 x（历史对比天数）和日期。用户没有给出 x 时，先追问 x，不要假定 x=7 等默认值，也不要调用异常工具。
3. 金额必须同时给出 CNY 和 USD。
4. 回答末尾注明：口径（渠道订单 / 供应订单）、时间窗口、阈值。
5. 工具返回 error 或数据不足时，如实说明，不推测原因。
6. 不确定数据是否已上传时，先调用 get_data_coverage 查看。
7. 回答用中文，结论先行，必要时用简短表格列出异常明细。合计行请在行首加粗（如 **合计**）。
8. 回答面向业务人员：不要提及工具或函数名（如 get_data_coverage）、参数名或内部字段名；口径用业务语言描述。"""


def _client() -> OpenAI:
    s = get_settings()
    if not s.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")
    return OpenAI(api_key=s.deepseek_api_key, base_url=s.deepseek_base_url, timeout=120)


def ask_stream(question: str, history: list[dict] | None, user_id: int, erp_sid: str) -> Iterator[dict]:
    """问答主循环（流式）。逐步产出事件：

    - {"type": "token", "text"}：模型输出的文字片段
    - {"type": "tool_start", "name", "arguments"}：开始执行某个计算函数
    - {"type": "step", "step"}：一步完成（模型一轮 / 计算函数一次），step 结构见 trace
    - {"type": "done", "answer", "trace", "tool_calls", "model", "latency_ms"}：最终结果

    trace 按时间顺序记录完整计算过程：
    - {"type": "llm", "round", "latency_ms", "tool_calls"}
    - {"type": "tool", "name", "arguments", "result", "error", "latency_ms"}
    history 为之前的文字对话（user / assistant），工具结果不回放。
    """
    s = get_settings()
    client = _client()
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT.format(now=now)},
        *[{"role": h["role"], "content": h["content"]} for h in (history or [])],
        {"role": "user", "content": question},
    ]
    trace: list[dict] = []
    started = time.monotonic()

    def done(answer: str) -> dict:
        return {
            "type": "done",
            "answer": answer,
            "trace": trace,
            "tool_calls": [t for t in trace if t["type"] == "tool"],
            "model": s.agent_model,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }

    for round_no in range(1, MAX_STEPS + 1):
        t0 = time.monotonic()
        stream = client.chat.completions.create(
            model=s.agent_model, messages=messages, tools=TOOL_SPECS, temperature=0, stream=True,
        )
        content_parts: list[str] = []
        acc: dict[int, dict] = {}  # 工具调用的增量按 index 拼接
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                content_parts.append(delta.content)
                yield {"type": "token", "text": delta.content}
            for tc in delta.tool_calls or []:
                slot = acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        slot["name"] += tc.function.name
                    if tc.function.arguments:
                        slot["arguments"] += tc.function.arguments
        content = "".join(content_parts)
        calls = [acc[i] for i in sorted(acc)]

        llm_step = {"type": "llm", "round": round_no, "latency_ms": int((time.monotonic() - t0) * 1000), "tool_calls": len(calls)}
        trace.append(llm_step)
        yield {"type": "step", "step": llm_step}

        if not calls:
            yield done(content)
            return

        messages.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls
            ],
        })
        for c in calls:
            try:
                args = json.loads(c["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {"_raw": c["arguments"]}
            yield {"type": "tool_start", "name": c["name"], "arguments": args}
            t1 = time.monotonic()
            result = run_tool(c["name"], args, ToolContext(user_id=user_id, erp_sid=erp_sid))
            tool_step = {
                "type": "tool",
                "name": c["name"],
                "arguments": args,
                "result": result,
                "error": result.get("error"),
                "latency_ms": int((time.monotonic() - t1) * 1000),
            }
            trace.append(tool_step)
            yield {"type": "step", "step": tool_step}
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result, ensure_ascii=False, default=str)})

    yield done("问题涉及的查询步骤过多，请把问题拆小一点再问。")

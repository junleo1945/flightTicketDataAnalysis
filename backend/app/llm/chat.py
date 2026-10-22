import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Iterator

from openai import OpenAI

from app.core.config import get_settings
from app.llm.answer_check import check_answer
from app.llm.tools import TOOL_SPECS, ToolContext, run_tool

log = logging.getLogger(__name__)
MAX_STEPS = 6
MAX_FORMAT_RETRIES = 2  # 回答结构不合格时最多让模型重写的次数，超过后直接修复返回
FORMAT_RETRY_PROMPT = (
    "上一版回答的结构有问题：{issues}。请基于同一批工具结果重新输出完整回答，内容和数字保持不变，"
    "表格的分隔行列数必须与表头一致。"
)
TZ = timezone(timedelta(hours=8))  # 北京时间，固定偏移，不依赖系统时区数据

SYSTEM_PROMPT = """你是公司内部的机票经营数据分析助手。当前时间：{now}（北京时间）。

规则：
1. 所有数字必须来自工具返回结果，不得自行计算、估算或编造。需要数字时调用工具。
2. 只有「异常」「下降」「环比」「与前几天比」这类需要历史基线的问题才是异常对比分析，必须明确日期和 x（历史对比天数）；用户没有给出 x 时先追问 x，不要假定 x=7 等默认值，也不要调用异常工具。
   用户问「某天查订比表现不好/差/高的渠道或供应是哪家」这类指定日期的问题，不是异常对比，不要追问 x：直接用查订比汇总工具查这一天，渠道、供应各查一次，按「查询多、订单少」排序（查订比从高到低，有查询无订单的排最前），列出表现最差的若干家，并用返回的有报价量、拦截量、订单量说明原因。
   用户没有给日期时，先问清楚：是看哪一天的查订比表现，还是哪一天与前 x 天对比。
3. 金额必须同时给出 CNY 和 USD。
4. 回答末尾注明：口径（渠道订单 / 供应订单）、时间窗口、阈值。
5. 工具返回 error 或数据不足时，如实说明，不推测原因。
6. 不确定数据是否已上传时，先调用 get_data_coverage 查看。涉及订单、利润的日期范围，统计前也要先看覆盖情况。
7. 用户问订单号、亏损订单明细、某航司/航线的具体订单时，调用利润订单明细工具列出途灵订单号，不要以「无法提供订单号」为由拒绝；列出的订单数超过返回条数时，说明只展示前若干条。
8. 渠道/供应商的范围以数据本身为准：问渠道数据表现、查询量、订单量、某天查订比好坏时用查订比汇总工具，它会列出数据中出现的全部渠道；不要用佣金规则圈定渠道范围，也不要因某渠道未配置佣金而排除它。佣金规则只在算利润时按渠道套用，有规则的渠道扣佣金，没有的直接用销售额减成本，这由利润工具自动完成。
9. 回答用中文，结论先行，必要时用简短表格列出异常明细。合计行请在行首加粗（如 **合计**）。
10. 回答面向业务人员：不要提及工具或函数名（如 get_data_coverage）、参数名或内部字段名；口径用业务语言描述。
11. 订单或利润统计结果为 0、偏少，或涉及对比时，先核对覆盖情况里的缺失日期和最晚一天是否完整：落在缺失日期的，只能说「这几天没有上传订单数据」，不能说成没有订单；对比的某一期包含缺失或不完整的日期时，必须明确告诉用户该对比不可靠，建议先补传数据。
12. 数据对不上时（例如两个来源的订单量不一致），只陈述各自的数值和来源，不要编造原因：不得把原因归结为节假日、业务为零、统计口径包含未出票或已取消等没有工具结果支撑的说法，不确定就说「原因需要进一步核实」。
13. 问「某几天的查询量、订单量、查订比分别是多少」这类取数问题，用按日取值的工具一次取回；不要用异常判断工具逐日调用来凑数据，异常工具只返回被判定异常的分组。
14. 数据质量（验价、供应商校验、预定）问题：问某渠道失败率及「主要是哪个供应商、哪个航司出了问题」用数据质量问题定位工具；取数、排名、对比用数据质量汇总工具。问某个时间段（如「上午 10 点到 12 点」）时直接用时间段参数查，不要追问是否要整天。
15. 数据质量只有汇总数据，没有错误码、接口日志或事故结论：只能说「异常主要集中在」「主要异常对象」，不能说「根因是某供应商/航司」。小样本（样本量不足 20）的高失败率只展示、不要提醒运营关注；分母为 0 的指标说「无有效数据」，不要说成 0%。多日汇总以工具返回的先累计再计算的结果为准，不要自己平均各日比率。
16. 用户问「报表中的 timeout_rate 是怎么计算的」时，直接回答公式：（验价耗时超过 5～10 秒的数量 + 超过 10 秒的数量）÷ 总验价数 × 100%。回答超时问题时同时给出报表超时率和校验成功内超时率（超时数 ÷ 验价成功数，更需要业务关注），并给出 5～10 秒和超过 10 秒的数量及占比。工具返回「数据提示」（口径不一致）时，如实告诉用户。"""


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
    - {"type": "retry", "attempt"}：回答结构不合格，模型重新生成，前端应清空已输出的文字
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
        # 入库和返回前校验回答结构（如表格列数），有问题就修复并记录，统计模型出错频率
        checked = check_answer(answer)
        for issue in checked.issues:
            log.warning("回答结构校验：%s（问题：%s）", issue, question[:80])
        return {
            "type": "done",
            "answer": checked.answer,
            "trace": trace,
            "tool_calls": [t for t in trace if t["type"] == "tool"],
            "model": s.agent_model,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }

    format_retries = 0
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
            # 回答结构不合格（如表格列数错误）：让模型按问题重新输出，最多重试 MAX_FORMAT_RETRIES 次
            checked = check_answer(content)
            if checked.issues and format_retries < MAX_FORMAT_RETRIES:
                format_retries += 1
                log.warning("回答结构不合格，第 %d 次重新生成：%s", format_retries, "；".join(checked.issues))
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": FORMAT_RETRY_PROMPT.format(issues="；".join(checked.issues))})
                yield {"type": "retry", "attempt": format_retries}
                continue
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

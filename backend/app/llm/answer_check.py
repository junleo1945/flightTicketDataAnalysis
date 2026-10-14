"""模型回答的结构校验与修复（不依赖提示词，每次回答都检查）。

模型写的 Markdown 表格偶尔会出错，例如分隔行列数与表头不一致，前端 GFM 解析时整张表就不会渲染。
这里在回答入库、返回前检查：分隔行列数不一致的按表头修复；数据行列数不一致的只记录问题。
修复和问题都写入日志，便于统计模型的出错频率。
"""

import re
from dataclasses import dataclass, field

_SEP_LINE = re.compile(r"^\|[\s:|-]+\|?$")
_SEP_CELL = re.compile(r"^\s*:?-+:?\s*$")


@dataclass
class AnswerCheck:
    answer: str
    fixed_tables: int = 0
    issues: list[str] = field(default_factory=list)


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return s.split("|")


def check_answer(answer: str) -> AnswerCheck:
    lines = answer.split("\n")
    result = AnswerCheck(answer=answer)
    i = 0
    while i + 1 < len(lines):
        header, sep = lines[i].strip(), lines[i + 1].strip()
        if not (header.startswith("|") and sep.startswith("|") and _SEP_LINE.match(sep) and "-" in sep):
            i += 1
            continue
        cols = len(_cells(header))
        cells = _cells(sep)
        if len(cells) != cols:
            # 按表头列数重写分隔行，保留原有对齐（:---、---: 等），缺的列补 ---，多出的列丢弃
            fixed = [cells[j].strip() if j < len(cells) and _SEP_CELL.match(cells[j]) else "---" for j in range(cols)]
            lines[i + 1] = "|" + "|".join(fixed) + "|"
            result.fixed_tables += 1
            result.issues.append(f"表格分隔行 {len(cells)} 列与表头 {cols} 列不一致，已按表头修复")
        j = i + 2
        while j < len(lines) and lines[j].strip().startswith("|"):
            n = len(_cells(lines[j]))
            if n != cols:
                result.issues.append(f"表格第 {j + 1} 行 {n} 列与表头 {cols} 列不一致")
            j += 1
        i = j
    result.answer = "\n".join(lines)
    return result

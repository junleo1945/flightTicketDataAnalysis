import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

from app.llm.answer_check import check_answer  # noqa: E402

# 与线上出错的回答同形：表头 6 列，分隔行写成 7 列（多了一个 ---: 对齐列）
BAD_TABLE = """## 明细

| 日期 | 航线 | 订单数 | 亏损单数 | 利润(CNY) | 利润(USD) |
|---|---|---:|---:|---:|---:|---:|
| 09-03 | DMK-KKC | 1 | 1 | -3.16 | -0.47 |
| 09-03 | HKT-DMK | 3 | 1 | 2.23 | 0.33 |
"""

GOOD_TABLE = """| 日期 | 金额 |
|:---|---:|
| 09-03 | 1.00 |
"""


def test_mismatched_separator_is_repaired_to_header_width():
    r = check_answer(BAD_TABLE)
    assert r.fixed_tables == 1
    lines = r.answer.split("\n")
    assert lines[3] == "|---|---|---:|---:|---:|---:|"
    assert len(lines[3].strip("|").split("|")) == 6
    assert r.issues and "分隔行 7 列与表头 6 列" in r.issues[0]


def test_alignment_is_kept_when_repairing():
    broken = "| a | b | c |\n|:---|---|---:|---:|\n| 1 | 2 | 3 |"
    r = check_answer(broken)
    assert r.answer.split("\n")[1] == "|:---|---|---:|"


def test_correct_table_is_untouched():
    r = check_answer(GOOD_TABLE)
    assert r.fixed_tables == 0
    assert r.issues == []
    assert r.answer == GOOD_TABLE


def test_row_with_wrong_column_count_is_reported_not_changed():
    text = "| a | b |\n|---|---|\n| 1 | 2 | 3 |\n| 4 | 5 |"
    r = check_answer(text)
    assert r.answer == text
    assert r.fixed_tables == 0
    assert r.issues == ["表格第 3 行 3 列与表头 2 列不一致"]


def test_plain_text_without_tables_passes_through():
    text = "## 结论\n\n亏损集中在 9 月 21 日至 24 日。"
    r = check_answer(text)
    assert r.answer == text
    assert r.issues == []

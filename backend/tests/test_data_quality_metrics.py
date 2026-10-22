import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

from datetime import date  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402

from app.erp.data_quality import DataQualityRow  # noqa: E402
from app.importers.common import ImportValidationError  # noqa: E402
from app.metrics import data_quality as dq  # noqa: E402

D = date(2026, 10, 21)


def row(agent="AH", supplier="S1", airline="CA", day=D, **over):
    base = dict(
        total=0, timeout_5_10=0, timeout_gt_10=0, verify_success=0, verify_fail=0, supplier_success=0, supplier_fail=0,
        supplier_changed=0, unbook=0, booking_success=0, booking_fail=0,
    )
    base.update(over)
    # 默认让数量自洽：总数 = 成功 + 失败
    if "total" not in over:
        base["total"] = base["verify_success"] + base["verify_fail"]
    return DataQualityRow(stat_date=day, agent_code=agent, supplier_code=supplier, ticket_carrier=airline,
                          booking_rate=Decimal(0), booking_success_rate=Decimal(0), timeout_rate=Decimal(0), **base)


# ---- 规则第四节：问题判定 ----

@pytest.mark.parametrize(
    "sample,fail,expected",
    [
        (0, 0, dq.NO_DATA),
        (2, 2, dq.SMALL_SAMPLE),  # 校验 2 次失败 2 次：展示，不提醒
        (19, 19, dq.SMALL_SAMPLE),
        (20, 20, "需要关注"),  # 校验 20 次失败 20 次
        (20, 2, None),  # 恰好 10% 且样本不足 101：不标记
        (20, 3, "需要关注"),
        (100, 10, None),
        (100, 11, "需要关注"),
        (101, 11, "重点关注"),
        (101, 10, None),  # 10.1/101 < 10%
        (110, 11, "重点关注"),  # 恰好 10%，样本 > 100
        (1000, 99, None),
    ],
)
def test_attention_levels(sample, fail, expected):
    assert dq.attention(sample, fail) == expected


def test_denominator_zero_is_not_zero_percent():
    assert dq.ratio(0, 0) is None
    assert dq.pct_text(None) == dq.NO_DATA
    m = dq.full_metrics(dq.aggregate([]))
    assert m["渠道校验失败率"] == dq.NO_DATA and m["供应校验失败率"] == dq.NO_DATA and m["渠道预定率"] == dq.NO_DATA


# ---- 规则第二节：公式 ----

def test_formulas_sum_counts_before_dividing():
    rows = [
        row(supplier="S1", total=100, verify_success=90, verify_fail=10, timeout_5_10=9, timeout_gt_10=9,
            supplier_success=80, supplier_fail=20, supplier_changed=8, unbook=60, booking_success=20, booking_fail=10),
        row(supplier="S2", total=300, verify_success=300, verify_fail=0, timeout_5_10=0, timeout_gt_10=0,
            supplier_success=300, supplier_fail=0, supplier_changed=0, unbook=290, booking_success=10, booking_fail=0),
    ]
    m = dq.full_metrics(dq.aggregate(rows))
    assert m["验价总数"] == 400 and m["渠道校验失败率"] == "2.50%"  # 10/400，不是 (10% + 0%) / 2
    assert m["报表超时率（超时数/总验价数）"] == "4.50%"  # 18/400
    assert m["校验成功内超时率（超时数/验价成功数，主要关注）"] == "4.62%"  # 18/390
    assert m["成功内5～10秒超时率"] == "2.31%" and m["成功内超过10秒超时率"] == "2.31%"
    assert m["供应校验总数"] == 400 and m["供应校验失败率"] == "5.00%"
    assert m["供应变价率"] == "2.11%"  # 8/380，分母是供应成功数，不重复加变价
    # 预定成功 30、预定失败 10、未预定 350：预定率分母是三者之和 390，不是总验价数
    assert m["渠道预定率"] == "10.26%"  # 40/390
    assert m["预定成功率"] == "75.00%" and m["预定失败率"] == "25.00%" and m["未预定率"] == "89.74%"  # 30/40、10/40、350/390


def test_summary_by_day_calculates_each_day_separately():
    rows = [
        row(day=date(2026, 10, 20), verify_success=90, verify_fail=10),
        row(day=date(2026, 10, 21), verify_success=50, verify_fail=50),
    ]
    daily, total = dq.summarize(rows, "overall", by_day=True)
    assert [g["日期"] for g in daily] == ["2026-10-20", "2026-10-21"]
    assert daily[0]["渠道校验失败率"] == "10.00%" and daily[1]["渠道校验失败率"] == "50.00%"
    assert total["渠道校验失败率"] == "30.00%"
    merged, _ = dq.summarize(rows, "overall", by_day=False)
    assert len(merged) == 1 and merged[0]["渠道校验失败率"] == "30.00%"


def test_summary_groups_and_sorting():
    rows = [
        row(agent="A", verify_success=10, verify_fail=0),
        row(agent="B", verify_success=50, verify_fail=50),
        row(agent="C", verify_success=0, verify_fail=0),
    ]
    groups, _ = dq.summarize(rows, "channel", sort_by="verify_fail_rate")
    assert [g["渠道"] for g in groups] == ["B", "A", "C"]  # 无有效数据的排最后
    groups, _ = dq.summarize(rows, "channel", sort_by="verify_fail_rate", ascending=True)
    assert [g["渠道"] for g in groups] == ["A", "B", "C"]
    with pytest.raises(ImportValidationError):
        dq.summarize(rows, "nope")
    with pytest.raises(ImportValidationError):
        dq.summarize(rows, "channel", sort_by="nope")


# ---- 规则第五、六节：主要问题对象与诊断 ----

def _diag_rows():
    return [
        # 供应商 S1：样本 200，供应失败 60（重点关注）；S2：样本 30，失败 30（需要关注）；S3：样本 5，失败 5（小样本）
        row(supplier="S1", airline="CA", verify_success=140, verify_fail=60, supplier_success=140, supplier_fail=60),
        row(supplier="S2", airline="MU", verify_success=0, verify_fail=30, supplier_success=0, supplier_fail=30),
        row(supplier="S3", airline="CA", verify_success=0, verify_fail=5, supplier_success=0, supplier_fail=5),
        row(agent="OTHER", supplier="S9", airline="ZZ", verify_success=1, verify_fail=0),
    ]


def test_diagnose_ranks_by_level_then_contribution_and_keeps_small_sample_last():
    out = dq.diagnose(_diag_rows(), "AH")
    sups = out["供应商层（按问题程度排序）"]
    assert [s["供应商"] for s in sups] == ["S1", "S2", "S3"]
    assert [s["供应侧判定"] for s in sups] == ["重点关注", "需要关注", dq.SMALL_SAMPLE]
    assert sups[0]["失败贡献率（占该渠道全部供应商供应失败）"] == "63.16%"  # 60 / 95
    assert out["主要异常供应商"] == ["S1", "S2"]  # 小样本不会进入主要异常对象
    assert out["渠道层"]["验价总数"] == 235 and out["渠道层"]["渠道侧判定"] == "重点关注"
    assert "OTHER" not in str(out)


def test_diagnose_drills_down_and_names_combinations():
    out = dq.diagnose(_diag_rows(), "AH")
    assert out["主要异常航司"][0] == "CA"  # CA 验价失败 65，占 68.4%
    assert list(out["供应商下钻（该供应商下各航司，按供应失败数）"]) == ["S1", "S2"]
    assert out["供应商下钻（该供应商下各航司，按供应失败数）"]["S1"][0]["航司"] == "CA"
    combos = out["主要异常组合（供应商－航司）"]
    assert (combos[0]["供应商"], combos[0]["航司"]) == ("S1", "CA")
    assert "根因" in out["措辞要求"]


def test_diagnose_without_flagged_objects_and_without_data():
    ok = dq.diagnose([row(verify_success=1000, verify_fail=1, supplier_success=1000, supplier_fail=1)], "AH")
    assert ok["主要异常供应商"] == "没有达到关注等级的供应商" and ok["主要异常组合（供应商－航司）"] == "没有达到关注等级的组合"
    assert "没有渠道 NONE" in dq.diagnose([row()], "NONE")["结论"]


# ---- 规则第七节：数据校验 ----

def test_check_rows_flags_inconsistencies_with_source_and_recomputed_values():
    bad = DataQualityRow(
        stat_date=D, agent_code="AH", supplier_code="S1", ticket_carrier="CA", total=10, timeout_5_10=5, timeout_gt_10=5,
        verify_success=6, verify_fail=3, supplier_success=2, supplier_fail=0, supplier_changed=3, unbook=1,
        booking_success=1, booking_fail=0, booking_rate=Decimal("0.5"), booking_success_rate=Decimal("0.5"), timeout_rate=Decimal("0.1"),
    )
    text = "\n".join(dq.check_rows([bad]))
    assert "总验价数不等于" in text
    assert "超时数量大于验价成功数" in text
    assert "变价数大于供应商成功数" in text
    assert "报表超时率" in text and "源字段 0.1000、反算 1.0000" in text
    assert "预定成功率" in text  # 源 0.5，按规则反算 1/1 = 1
    assert "不作为错误" in text  # 验价成功 6 != 1 + 1 + 0


def test_check_rows_clean_data_has_no_notes():
    good = DataQualityRow(
        stat_date=D, agent_code="AH", supplier_code="S1", ticket_carrier="CA", total=10, timeout_5_10=1, timeout_gt_10=0,
        verify_success=10, verify_fail=0, supplier_success=10, supplier_fail=0, supplier_changed=1, unbook=5,
        booking_success=5, booking_fail=0, booking_rate=Decimal("0.5"), booking_success_rate=Decimal("1"), timeout_rate=Decimal("0.1"),
    )
    assert dq.check_rows([good]) == []


def test_parse_scope_validation():
    assert dq.parse_scope("2026-10-20", "2026-10-21", None, None, None)[2] is None
    assert dq.parse_scope("2026-10-20", "2026-10-21", "period", "10:00", "12:00")[3] == "每天 10:00～12:00"
    for args in [
        ("2026-10-21", "2026-10-20", None, None, None),
        ("2026-10-20", "2026-10-21", "period", None, None),
        ("2026-10-20", "2026-10-21", "period", "12:00", "10:00"),
        ("2026-10-01", "2026-10-21", "period", "10:00", "12:00"),
        ("2026-06-01", "2026-10-21", None, None, None),
        ("2026-10-20", "2026-10-21", "weird", None, None),
    ]:
        with pytest.raises(ImportValidationError):
            dq.parse_scope(*args)

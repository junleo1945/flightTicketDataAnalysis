import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

from decimal import Decimal  # noqa: E402

from app.importers.order import EXCLUDED_FIELDS, build_order_records  # noqa: E402


def _p(row_no, **kw):
    base = {"途灵订单号": "T1", "采购客户": "AH", "订单状态": "出票完成", "支付时间": "2026-10-10 10:00:00",
            "订单总价": "0", "币种": "USD", "航司": "VJ", "行程": "CXR-HPH", "航段数": "1",
            "乘机人姓名": "ZHANG/SAN", "票号": "123", "PNR": "ABC", "航司大编码": "VJ"}
    base.update(kw)
    return (row_no, base)


def _s(row_no, **kw):
    base = {"途灵订单号": "T1", "TM供应商订单号": "S1", "供应商订单号": "9001", "供应商代码": "DBFL",
            "供应商订单状态": "出票完成", "供应商订单支付时间": "", "供应商订单出票时间": "2026-10-10 10:05:00",
            "供应商币种": "USD", "供应商总价": "0", "供应商总价CNY": "0", "供应商总价USD": "0",
            "供应币种兑CNY汇率": "6.7", "供应币种兑USD汇率": "1", "订单总价": "0", "订单总价CNY": "0",
            "订单总价USD": "0", "币种兑CNY汇率": "6.7", "币种兑USD汇率": "1"}
    base.update(kw)
    return (row_no, base)


def test_sales_counted_once_per_order_across_passengers():
    purchase = [_p(2, 订单总价="57.2"), _p(3, 订单总价="0"), _p(4, 订单总价="0")]
    supply = [_s(2, 供应商总价="56.92", 供应商总价USD="56.92", 订单总价="57.2", 订单总价USD="57.2", 订单总价CNY="383.04")]
    rec = build_order_records(purchase, supply, batch_id=1, user_id=1)
    assert rec.tm_orders[0]["total_price"] == Decimal("57.2")
    assert rec.tm_orders[0]["passenger_count"] == 3


def test_duplicated_order_total_on_supplier_rows_is_not_summed():
    # 同一途灵订单下两个供应订单（S 取消、M 出票），整单金额在两行都重复出现
    purchase = [_p(2, 订单总价="65.71")]
    supply = [
        _s(2, TM供应商订单号="S1", 供应商订单状态="订单取消", 订单总价="65.71", 订单总价CNY="441.53", 订单总价USD="65.71"),
        _s(3, TM供应商订单号="M1", 供应商订单状态="出票完成", 订单总价="65.71", 订单总价CNY="441.53", 订单总价USD="65.71"),
    ]
    rec = build_order_records(purchase, supply, batch_id=1, user_id=1)
    assert rec.tm_orders[0]["total_cny"] == Decimal("441.53")
    assert rec.tm_orders[0]["total_usd"] == Decimal("65.71")
    assert len(rec.supplier_orders) == 2
    assert rec.summary["其中订单取消"] == 1


def test_supplier_cost_is_sum_of_passenger_rows():
    purchase = [_p(2, 订单总价="57.2"), _p(3, 订单总价="0")]
    supply = [_s(2, 供应商总价="56.92", 供应商总价USD="56.92"), _s(3, 供应商总价="0", 供应商总价USD="0")]
    rec = build_order_records(purchase, supply, batch_id=1, user_id=1)
    assert rec.supplier_orders[0]["supplier_total_usd"] == Decimal("56.92")


def test_sensitive_fields_never_reach_raw_rows():
    rec = build_order_records([_p(2, 订单总价="1")], [_s(2)], batch_id=1, user_id=1)
    for raw in rec.raw_rows:
        assert not (EXCLUDED_FIELDS & set(raw["data"]))
        assert "ZHANG/SAN" not in str(raw["data"])


def test_route_and_airline_split_by_comma():
    purchase = [_p(2, 订单总价="1", 行程="KUL-HRB,HRB-KUL", 航司="MU")]
    rec = build_order_records(purchase, [_s(2)], batch_id=1, user_id=1)
    assert sorted(r["route"] for r in rec.routes) == ["HRB-KUL", "KUL-HRB"]
    assert [a["airline_code"] for a in rec.airlines] == ["MU"]


def test_supplier_pay_time_falls_back_per_q16():
    purchase = [_p(2, 订单总价="1", 支付时间="2026-10-10 09:00:00")]
    supply = [
        _s(2, TM供应商订单号="S1", 供应商订单支付时间="2026-10-10 09:01:00"),
        _s(3, TM供应商订单号="M1", 供应商订单支付时间="", 供应商订单出票时间="2026-10-10 09:30:00"),
    ]
    rec = build_order_records(purchase, supply, batch_id=1, user_id=1)
    by_id = {s["tm_supplier_order_no"]: s for s in rec.supplier_orders}
    assert by_id["S1"]["pay_time_source"] == "supplier_pay"
    assert by_id["M1"]["pay_time_source"] == "tm_pay"
    assert by_id["M1"]["pay_time_effective"].hour == 9 and by_id["M1"]["pay_time_effective"].minute == 0

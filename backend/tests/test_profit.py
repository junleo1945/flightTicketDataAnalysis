import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

from datetime import date  # noqa: E402
from decimal import Decimal as D  # noqa: E402

from app.metrics.profit import OrderFacts, Rule, allocations, order_profit  # noqa: E402


def _order(**kw):
    base = dict(tm_order_no="T1", channel_code="AH", day=date(2026, 10, 10), currency="USD",
                total_price=D("100"), sales_cny=D("700"), sales_usd=D("100"), rate_cny=D("7"), rate_usd=D("1"))
    base.update(kw)
    return OrderFacts(**base)


def test_no_commission_is_sales_minus_cost_per_currency():
    f = _order(suppliers=[("DBFL", D("560"), D("80"))])
    pc, pu, cc, cu = order_profit(f, None)
    assert (pc, pu) == (D("140"), D("20"))


def test_percent_commission_applies_to_sales_not_cost():
    # 销售额 × (1 − 2%) − 成本
    f = _order(suppliers=[("DBFL", D("560"), D("80"))])
    pc, pu, _, _ = order_profit(f, Rule("percent", D("0.02")))
    assert pu == D("100") * D("0.98") - D("80")
    assert pc == D("700") * D("0.98") - D("560")


def test_fmove_fixed_fee_converted_from_usd_then_to_cny():
    # 订单 USD：利润(原币) = 100 − 成本(80/1) − 0.2 = 19.8 USD；CNY = 19.8 × 7
    f = _order(channel_code="FMOVE", suppliers=[("DBFL", D("560"), D("80"))])
    pc, pu, _, _ = order_profit(f, Rule("fixed_usd", D("0.2")))
    assert pu == D("19.8")
    assert pc == D("19.8") * D("7")


def test_cost_split_across_two_suppliers_is_summed():
    f = _order(suppliers=[("A", D("300"), D("40")), ("B", D("260"), D("40"))])
    pc, pu, cc, cu = order_profit(f, None)
    assert cu == D("80") and cc == D("560")


def test_airline_allocation_is_even_and_sums_to_whole():
    f = _order(airlines=["MU", "CZ"])
    shares = allocations(f, "airline")
    assert sum(s[1] for s in shares) == D("1")
    assert len(shares) == 2


def test_supplier_allocation_follows_cost_share():
    f = _order(suppliers=[("A", D("300"), D("30")), ("B", D("100"), D("10"))])
    shares = {k: (fc, fu) for k, fc, fu, _ in allocations(f, "supplier")}
    assert shares["A"][1] == D("30") / D("40")
    assert shares["B"][0] == D("100") / D("400")


def test_describe_rule_in_business_language():
    from app.llm.tools import describe_rule

    assert describe_rule("percent", D("0.02")) == "佣金 2%"
    assert describe_rule("percent", D("0.005")) == "佣金 0.5%"
    assert describe_rule("fixed_usd", D("0.2")) == "每单固定扣费 0.2 USD"

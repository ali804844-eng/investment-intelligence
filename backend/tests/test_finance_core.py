"""Hand-computed reference examples for the deterministic finance library."""
from datetime import date
from decimal import Decimal as D

import pytest

from app.core.finance import accounting as acc
from app.core.finance import indicators as ind
from app.core.finance import ratios, returns, risk_metrics, valuation


def T(i, tp, d, **kw):
    return acc.Txn(id=i, type=tp, trade_date=d, **kw)


def base_txns():
    return [
        T(1, "deposit", date(2024, 1, 1), amount=D("10000")),
        T(2, "buy", date(2024, 1, 2), security_id=1, quantity=D("10"), price=D("100"), fees=D("5")),
        T(3, "buy", date(2024, 2, 1), security_id=1, quantity=D("10"), price=D("120"), fees=D("5")),
        T(4, "sell", date(2024, 3, 1), security_id=1, quantity=D("15"), price=D("130"), fees=D("5")),
    ]


class TestAccounting:
    def test_fifo_realized_and_remaining_basis(self):
        led = acc.build_ledger(base_txns(), "FIFO")
        # proceeds 15*130-5 = 1945; FIFO cost = 1005 + 5*(1205/10) = 1607.5
        assert led.realized_pnl == D("337.5")
        pos = led.positions[1]
        assert pos.quantity == D("5")
        assert pos.cost_basis == D("602.5")
        assert led.cash_base == D("10000") - D("1005") - D("1205") + D("1945")
        assert led.total_fees == D("15")

    def test_average_cost(self):
        led = acc.build_ledger(base_txns(), "AVG")
        # avg = 2210/20 = 110.5 ; cost of 15 = 1657.5 ; pnl = 1945-1657.5
        assert led.realized_pnl == D("287.5")
        assert led.positions[1].cost_basis == D("552.5")

    def test_split_preserves_cost(self):
        txns = base_txns()[:2] + [T(9, "split", date(2024, 1, 10), security_id=1, split_ratio=D("2"))]
        led = acc.build_ledger(txns)
        assert led.positions[1].quantity == D("20")
        assert led.positions[1].cost_basis == D("1005")

    def test_oversell_rejected(self):
        txns = base_txns()[:2] + [T(5, "sell", date(2024, 1, 3), security_id=1, quantity=D("11"), price=D("1"))]
        with pytest.raises(acc.AccountingError):
            acc.build_ledger(txns)

    def test_fx_gain_included_in_realized(self):
        txns = [
            T(1, "deposit", date(2024, 1, 1), amount=D("2000"), currency="EUR", fx_rate=D("1.1")),
            T(2, "buy", date(2024, 1, 2), security_id=7, quantity=D("10"), price=D("100"), currency="EUR", fx_rate=D("1.1")),
            T(3, "sell", date(2024, 6, 1), security_id=7, quantity=D("10"), price=D("100"), currency="EUR", fx_rate=D("1.2")),
        ]
        led = acc.build_ledger(txns)
        assert led.realized_pnl == D("100.0")  # pure currency gain in base terms
        assert led.cash_by_currency["EUR"] == D("2000")

    def test_dividend_net_of_withholding(self):
        txns = base_txns()[:2] + [T(6, "dividend", date(2024, 1, 20), security_id=1, amount=D("50"), fees=D("7.5"))]
        led = acc.build_ledger(txns)
        assert led.total_income == D("42.5")
        assert led.positions[1].dividends == D("42.5")

    def test_validation(self):
        with pytest.raises(acc.AccountingError):
            acc.build_ledger([T(1, "buy", date(2024, 1, 1), security_id=1, quantity=D("-1"), price=D("1"))])
        with pytest.raises(acc.AccountingError):
            acc.build_ledger([T(1, "teleport", date(2024, 1, 1))])

    def test_negative_cash_guard(self):
        txns = [T(1, "buy", date(2024, 1, 1), security_id=1, quantity=D("1"), price=D("10"))]
        with pytest.raises(acc.AccountingError):
            acc.build_ledger(txns, allow_negative_cash=False)

    def test_valuation_marks_missing_prices(self):
        led = acc.build_ledger(base_txns())
        v = acc.value_positions(led, {1: D("140")})
        assert v["market_value"] == D("700")
        assert v["positions"][0]["unrealized_pnl"] == D("97.5")
        v2 = acc.value_positions(led, {})
        assert v2["complete"] is False and v2["unpriced_security_ids"] == [1]


class TestReturns:
    def test_twr_neutralizes_flows(self):
        values = [100, 110, 220, 231]
        flows = [0, 0, 100, 0]
        assert returns.twr_from_valuations(values, flows) == pytest.approx(0.26)

    def test_xirr_one_year(self):
        cfs = [(date(2021, 1, 1), -1000.0), (date(2022, 1, 1), 1100.0)]
        assert returns.xirr(cfs) == pytest.approx(0.10, abs=1e-9)

    def test_xirr_multiple_flows_npv_zero(self):
        cfs = [(date(2020, 1, 1), -1000.0), (date(2020, 7, 1), -500.0), (date(2021, 3, 1), 1700.0)]
        r = returns.xirr(cfs)
        assert r is not None and returns.xnpv(r, cfs) == pytest.approx(0, abs=1e-6)

    def test_xirr_no_sign_change(self):
        assert returns.xirr([(date(2020, 1, 1), 100.0), (date(2021, 1, 1), 100.0)]) is None

    def test_annualize(self):
        assert returns.annualize(0.21, 730) == pytest.approx(0.1, abs=2e-4)


class TestRisk:
    def test_max_drawdown(self):
        r = risk_metrics.max_drawdown([100, 120, 90, 130])
        assert r["max_drawdown"] == pytest.approx(-0.25)
        assert (r["peak_index"], r["trough_index"]) == (1, 2)

    def test_small_sample_returns_none(self):
        assert risk_metrics.annualized_volatility([0.01, 0.02]) is None
        assert risk_metrics.sharpe_ratio([0.01] * 5) is None

    def test_beta_of_scaled_series(self):
        m = [0.01 * ((i % 7) - 3) for i in range(60)]
        a = [2 * x for x in m]
        assert risk_metrics.beta(a, m) == pytest.approx(2.0)

    def test_concentration(self):
        c = risk_metrics.concentration({"a": 0.5, "b": 0.5})
        assert c["hhi"] == pytest.approx(0.5) and c["effective_n"] == pytest.approx(2)

    def test_historical_var(self):
        rets = [-0.05] * 5 + [0.01] * 95
        v = risk_metrics.historical_var(rets, 0.95)
        assert v["var"] == pytest.approx(0.05, abs=0.03)
        assert v["cvar"] >= v["var"]


class TestIndicators:
    def test_sma_ema(self):
        x = [1, 2, 3, 4, 5]
        assert ind.sma(x, 3) == [None, None, 2, 3, 4]
        e = ind.ema(x, 3)
        assert e[2] == 2 and e[3] == pytest.approx(3.0) and e[4] == pytest.approx(4.0)

    def test_rsi_extremes(self):
        up = list(range(1, 30))
        assert ind.rsi(up)[-1] == 100.0
        down = list(range(30, 1, -1))
        assert ind.rsi(down)[-1] == pytest.approx(0.0)

    def test_momentum_no_lookahead(self):
        x = [100, 110, 121, 133.1]
        m = ind.momentum(x, 2, 0)
        assert m[:2] == [None, None] and m[2] == pytest.approx(0.21)

    def test_atr_constant_range(self):
        h = [11.0] * 20
        lo = [9.0] * 20
        c = [10.0] * 20
        assert ind.atr(h, lo, c, 14)[-1] == pytest.approx(2.0)


class TestValuation:
    def test_dcf_perpetuity_identity(self):
        r = valuation.dcf(base_fcf=100, growth_rates=[0.0], discount_rate=0.10, terminal_growth=0.0,
                          net_debt=0, shares_outstanding=10)
        assert r["enterprise_value"] == pytest.approx(1000.0)
        assert r["value_per_share"] == pytest.approx(100.0)

    def test_dcf_net_debt_and_guard(self):
        r = valuation.dcf(100, [0.0], 0.10, 0.0, net_debt=200, shares_outstanding=10)
        assert r["value_per_share"] == pytest.approx(80.0)
        with pytest.raises(valuation.ValuationError):
            valuation.dcf(100, [0.0], 0.03, 0.04, 0, 10)

    def test_sensitivity_grid_shape(self):
        base = dict(base_fcf=100, growth_rates=[0.05] * 5, discount_rate=0.09, terminal_growth=0.02,
                    net_debt=0, shares_outstanding=10)
        s = valuation.dcf_sensitivity(base, [0.08, 0.09, 0.10], [0.01, 0.02, 0.10])
        assert len(s["values"]) == 3 and s["values"][0][2] is None  # r <= g is invalid
        assert s["values"][0][0] > s["values"][2][0]  # higher discount -> lower value

    def test_margin_of_safety(self):
        m = valuation.margin_of_safety(100, 75)
        assert m["margin_of_safety"] == pytest.approx(0.25)

    def test_comps(self):
        res = valuation.comparable_valuation(
            {"eps": 2.0, "revenue": 100, "shares": 10, "ebitda": 20, "net_debt": 0},
            [{"symbol": "A", "pe": 10, "ps": 1, "ev_ebitda": 5}, {"symbol": "B", "pe": 20, "ps": 3, "ev_ebitda": 7}],
        )
        assert res["estimates"]["pe"]["value_per_share"] == pytest.approx(30.0)
        assert res["estimates"]["ps"]["value_per_share"] == pytest.approx(20.0)

    def test_wacc(self):
        w = valuation.wacc(0.04, 0.05, 1.2, 0.06, 0.25, 800, 200)
        assert w["wacc"] == pytest.approx(0.8 * 0.10 + 0.2 * 0.045)


class TestRatios:
    stm = {
        "revenue": {"2021-12-31": 100.0, "2022-12-31": 110.0, "2023-12-31": 121.0, "2024-12-31": 133.1},
        "gross_profit": {"2024-12-31": 60.0},
        "operating_income": {"2024-12-31": 20.0},
        "net_income": {"2024-12-31": 15.0},
        "operating_cash_flow": {"2024-12-31": 22.0},
        "capex": {"2024-12-31": 4.0},
        "total_assets": {"2024-12-31": 200.0},
        "total_liabilities": {"2024-12-31": 120.0},
        "total_equity": {"2024-12-31": 80.0},
        "long_term_debt": {"2024-12-31": 50.0},
        "cash": {"2024-12-31": 10.0},
        "interest_expense": {"2024-12-31": 4.0},
    }

    def test_ratios(self):
        d = ratios.derived_for_period(self.stm, "2024-12-31")
        assert d["fcf"] == 18.0
        assert d["operating_margin"] == pytest.approx(20 / 133.1)
        assert d["net_debt"] == 40.0
        assert d["interest_coverage"] == 5.0
        assert d["roe"] == pytest.approx(15 / 80)

    def test_growth(self):
        assert ratios.growth(self.stm, "revenue", 3) == pytest.approx(0.10)
        assert ratios.growth(self.stm, "revenue", 5) is None

    def test_consistency(self):
        checks = ratios.consistency_checks(self.stm)
        assert all(c["ok"] for c in checks if c["check"].startswith("assets"))

    def test_formula(self):
        assert ratios.eval_formula("(a - b) / c", {"a": 10, "b": 4, "c": 3}) == pytest.approx(2.0)
        assert ratios.eval_formula("a / b", {"a": 1, "b": 0}) is None
        assert ratios.eval_formula("a + b", {"a": 1, "b": None}) is None
        with pytest.raises(ratios.FormulaError):
            ratios.eval_formula("__import__('os')", {})
        with pytest.raises(ratios.FormulaError):
            ratios.eval_formula("a.b", {"a": 1})

"""Bias controls in the feature panel and backtester, tested on small hand-built panels."""
from datetime import date, timedelta

import pandas as pd
import pytest

from app.core.backtest.engine import Costs, run_backtest, walk_forward
from app.services.panel import Panel, SecMeta


def make_panel(n_days=900, filed=date(2021, 3, 1)):
    days = [d.date() for d in pd.bdate_range("2020-01-01", periods=n_days)]
    metas = {1: SecMeta(1, "AAA", "A", "equity", "USD", "US", "Tech", "x", True),
             2: SecMeta(2, "BBB", "B", "equity", "USD", "US", "Tech", "x", True),
             9: SecMeta(9, "BMK", "Bench", "etf", "USD", "US", None, None, True)}
    a = [100 * (1.0006 ** i) for i in range(n_days)]
    b = [100 * (0.9998 ** i) for i in range(n_days)]
    m = [100 * (1.0002 ** i) for i in range(n_days)]
    close = pd.DataFrame({1: a, 2: b, 9: m}, index=days)
    vol = pd.DataFrame({1: [1e6] * n_days, 2: [1e6] * n_days, 9: [1e6] * n_days}, index=days)
    facts = {1: [(filed, date(2020, 12, 31), "revenue", 100.0), (filed, date(2020, 12, 31), "net_income", 10.0),
                 (filed, date(2020, 12, 31), "shares_outstanding", 10.0)],
             2: [(filed, date(2020, 12, 31), "revenue", 100.0), (filed, date(2020, 12, 31), "net_income", 1.0),
                 (filed, date(2020, 12, 31), "shares_outstanding", 10.0)]}
    return Panel(metas, close, vol, facts)


def test_fundamentals_not_visible_before_filing_date():
    p = make_panel()
    before = p.features(1, date(2021, 2, 26))
    after = p.features(1, date(2021, 3, 2))
    assert before["pe"] is None and before["fundamentals_age_days"] is None
    assert after["pe"] is not None


def test_prices_not_visible_after_as_of():
    p = make_panel()
    d = p.dates[400]
    f = p.features(1, d)
    assert f["price"] == pytest.approx(p.close.loc[d, 1])
    assert f["price_age_days"] == 0


def test_execution_lag_and_costs():
    p = make_panel()
    params = {"criteria": [{"feature": "mom_6m", "op": ">=", "value": 0.0, "required": True}], "weights": {"mom_6m": 1.0},
              "min_pass_ratio": 1.0, "top_n": 1, "universe": {"asset_classes": ["equity"], "min_adv_usd": 0, "min_price": 0}}
    start, end = p.dates[300], p.dates[-1]
    r = run_backtest(p, params, start, end, benchmark_id=9, costs=Costs(0, 0, 0))
    first = r["rebalances"][0]
    assert first["exec_date"] > first["signal_date"]  # executed the next trading day, never on the signal bar
    assert first["holdings"] == ["AAA"]
    r_cost = run_backtest(p, params, start, end, benchmark_id=9, costs=Costs(10, 20, 10))
    assert r_cost["metrics"]["total_return"] < r["metrics"]["total_return"]
    assert r["metrics"]["excess_cagr"] > 0  # AAA trends up faster than benchmark in this construction


def test_walk_forward_uses_only_out_of_sample_segments():
    p = make_panel(n_days=1400)
    params = {"criteria": [{"feature": "mom_6m", "op": ">=", "value": -1.0, "required": True}], "weights": {"mom_6m": 1.0},
              "min_pass_ratio": 1.0, "top_n": 1, "universe": {"asset_classes": ["equity"], "min_adv_usd": 0, "min_price": 0}}
    wf = walk_forward(p, params, p.dates[260], p.dates[-1], train_years=1, test_years=1, grid=[{"top_n": 1}, {"top_n": 2}],
                      benchmark_id=9)
    assert wf["n_folds"] >= 2
    for f in wf["folds"]:
        assert f["train"][1] == f["test"][0]  # test window starts where training ends: no overlap
    assert wf["oos_metrics"]["start"] == wf["folds"][0]["test"][0][:10] or wf["oos_metrics"]["start"] >= wf["folds"][0]["test"][0]
    _ = timedelta

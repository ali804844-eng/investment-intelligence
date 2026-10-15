"""Prediction scoring and calibration. Pure functions."""
from __future__ import annotations

import math


def score_prediction(direction: str, kind: str, probability: float, start_price: float, end_price: float,
                     start_bench: float | None = None, end_bench: float | None = None) -> dict:
    ret = end_price / start_price - 1
    if kind == "outperform":
        if start_bench is None or end_bench is None:
            raise ValueError("benchmark prices required for outperform predictions")
        bret = end_bench / start_bench - 1
        measure = ret - bret
    else:
        bret = (end_bench / start_bench - 1) if (start_bench and end_bench) else None
        measure = ret
    correct = measure > 0 if direction == "up" else measure < 0
    p = min(max(probability, 1e-6), 1 - 1e-6)
    o = 1.0 if correct else 0.0
    return {"return": ret, "benchmark_return": bret, "measure": measure, "correct": correct,
            "brier": (p - o) ** 2, "log_loss": -(o * math.log(p) + (1 - o) * math.log(1 - p))}


def calibration_table(rows: list[tuple[float, bool]], bins: tuple[float, ...] = (0.5, 0.55, 0.6, 0.7, 0.8, 0.9, 1.0001)) -> list[dict]:
    """rows: (stated probability, correct). Groups into probability buckets and compares stated vs realized."""
    out = []
    lo = 0.0
    for hi in bins:
        sel = [(p, c) for p, c in rows if lo <= p < hi]
        if sel:
            out.append({"bucket": f"{lo:.2f}–{min(hi, 1):.2f}", "n": len(sel),
                        "avg_stated": sum(p for p, _ in sel) / len(sel),
                        "realized": sum(1 for _, c in sel if c) / len(sel)})
        lo = hi
    return out


def summarize(rows: list[dict]) -> dict:
    """rows: dicts with probability, correct, brier, measure."""
    n = len(rows)
    if n == 0:
        return {"n": 0}
    hit = sum(1 for r in rows if r["correct"]) / n
    brier = sum(r["brier"] for r in rows) / n
    base = sum((r["probability"] - 0.5) ** 2 for r in rows) / n  # unused but informative
    se = math.sqrt(hit * (1 - hit) / n) if n > 1 else None
    return {"n": n, "hit_rate": hit, "hit_rate_ci95": [max(0, hit - 1.96 * se), min(1, hit + 1.96 * se)] if se else None,
            "brier": brier, "brier_uninformed": 0.25, "avg_stated_probability": sum(r["probability"] for r in rows) / n,
            "avg_excess_measure": sum(r["measure"] for r in rows) / n, "_dispersion": base,
            "calibration": calibration_table([(r["probability"], r["correct"]) for r in rows]),
            "note": "Brier 0.25 = coin-flip at p=0.5; lower is better. Small samples are noisy."}


def acceptance(baseline: dict, candidate: dict, criteria: dict) -> dict:
    """Compare walk-forward OOS results of candidate vs baseline against predefined acceptance criteria."""
    checks = []

    def add(name, ok, detail):
        checks.append({"criterion": name, "passed": bool(ok), "detail": detail})

    bs, cs = baseline.get("oos_metrics", {}), candidate.get("oos_metrics", {})
    min_sh = criteria.get("min_sharpe_improvement", 0.05)
    if bs.get("sharpe") is not None and cs.get("sharpe") is not None:
        add("sharpe_improvement", cs["sharpe"] - bs["sharpe"] >= min_sh,
            f"OOS Sharpe {cs['sharpe']:.2f} vs {bs['sharpe']:.2f} (need +{min_sh})")
    else:
        add("sharpe_improvement", False, "Sharpe unavailable")
    tol = criteria.get("max_drawdown_tolerance", 0.02)
    if bs.get("max_drawdown") is not None and cs.get("max_drawdown") is not None:
        add("drawdown_not_worse", cs["max_drawdown"] >= bs["max_drawdown"] - tol,
            f"OOS max DD {cs['max_drawdown']:.1%} vs {bs['max_drawdown']:.1%} (tolerance {tol:.0%})")
    min_frac = criteria.get("min_fold_win_fraction", 0.6)
    folds_b = {tuple(f["test"]): f for f in baseline.get("folds", [])}
    wins = n = 0
    for f in candidate.get("folds", []):
        b = folds_b.get(tuple(f["test"]))
        if b and f["test_metrics"].get("total_return") is not None and b["test_metrics"].get("total_return") is not None:
            n += 1
            wins += f["test_metrics"]["total_return"] > b["test_metrics"]["total_return"]
    add("fold_consistency", n > 0 and wins / n >= min_frac, f"Candidate beat baseline in {wins}/{n} test folds (need ≥{min_frac:.0%})")
    max_to = criteria.get("max_turnover_ratio", 1.5)
    tb = baseline.get("avg_fold_turnover")
    tc = candidate.get("avg_fold_turnover")
    if tb and tc:
        add("turnover", tc <= tb * max_to, f"Turnover {tc:.2f} vs {tb:.2f} (max ratio {max_to})")
    passed = all(c["passed"] for c in checks)
    return {"passed": passed, "checks": checks}

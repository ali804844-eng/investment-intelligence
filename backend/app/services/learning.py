"""Prediction registry, outcome evaluation, strategy versioning, backtests and controlled experiments.

Self-improvement is deliberately constrained:
  * The system may PROPOSE new strategy parameter versions (status 'candidate').
  * Candidates are tested against the production version on identical walk-forward windows using acceptance
    criteria fixed before the test runs.
  * Promotion to production requires an explicit, audited user approval. Rollback re-promotes an earlier version.
  * Nothing here can modify code, risk limits, or trading permissions.
"""
from __future__ import annotations

import copy
from datetime import date, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.backtest.engine import Costs, run_backtest, sensitivity, walk_forward
from app.core.evaluation import scoring
from app.core.screening.strategies import STRATEGIES, default_params, validate_params
from app.db import utcnow
from app.models import AgentMemory, Backtest, Experiment, Prediction, Security, StrategyVersion
from app.providers.sample import last_business_day
from app.services import market_data as md
from app.services.panel import load_panel
from app.services.regime import pick_benchmark

DEFAULT_ACCEPTANCE = {"min_sharpe_improvement": 0.05, "max_drawdown_tolerance": 0.02, "min_fold_win_fraction": 0.6,
                      "max_turnover_ratio": 1.5}


# ------------------------------------------------------------------ predictions
def create_prediction(db: Session, user_id: int, sec: Security, direction: str, probability: float, horizon_days: int,
                      kind: str = "direction", rationale: str = "", source: str = "user",
                      model_version: str = "user", evaluation_criteria: str | None = None) -> Prediction:
    if direction not in ("up", "down"):
        raise ValueError("direction must be 'up' or 'down'")
    if not 0.5 <= probability <= 0.99:
        raise ValueError("probability must be between 0.5 and 0.99 (probability that the stated direction is right)")
    if not 1 <= horizon_days <= 3650:
        raise ValueError("horizon must be 1..3650 days")
    q = md.get_quote(db, sec)
    if q["price"] is None:
        raise ValueError("Cannot register a prediction without a current price (baseline must be recorded)")
    bench = pick_benchmark(db) if kind == "outperform" else None
    if kind == "outperform" and (bench is None or bench.is_sample != sec.is_sample):
        raise ValueError("No compatible benchmark available for an outperform prediction")
    bq = md.get_quote(db, bench) if bench else None
    start = last_business_day()
    p = Prediction(user_id=user_id, security_id=sec.id, benchmark_security_id=bench.id if bench else None, kind=kind,
                   direction=direction, probability=probability, horizon_days=horizon_days, start_date=start,
                   evaluate_on=start + timedelta(days=horizon_days), baseline_price=q["price"],
                   baseline_benchmark=bq["price"] if bq else None, rationale=rationale[:4000],
                   data_snapshot={"price": q["price"], "price_as_of": q.get("as_of"), "quality": q.get("quality"),
                                  "benchmark": bench.symbol if bench else None},
                   evaluation_criteria=evaluation_criteria or (
                       f"Correct if {sec.symbol} price {'rises' if direction == 'up' else 'falls'}"
                       + (f" relative to {bench.symbol}" if bench else "") + f" over {horizon_days} calendar days."),
                   model_version=model_version, source=source,
                   data_quality="sample" if sec.is_sample else (q.get("quality") or "unknown"))
    db.add(p)
    db.flush()
    return p


def evaluate_due_predictions(db: Session, user_id: int | None = None, today: date | None = None) -> dict:
    today = today or last_business_day()
    q = select(Prediction).where(Prediction.status == "open", Prediction.evaluate_on <= today)
    if user_id is not None:
        q = q.where(Prediction.user_id == user_id)
    done, skipped = [], []
    for p in db.scalars(q).all():
        end = md.price_on(db, p.security_id, p.evaluate_on)
        if end is None or end[0] < p.evaluate_on - timedelta(days=7):
            skipped.append({"id": p.id, "reason": "no price near evaluation date"})
            continue
        eb = md.price_on(db, p.benchmark_security_id, p.evaluate_on) if p.benchmark_security_id else None
        try:
            s = scoring.score_prediction(p.direction, p.kind, p.probability, p.baseline_price, end[1],
                                         p.baseline_benchmark, eb[1] if eb else None)
        except ValueError as e:
            skipped.append({"id": p.id, "reason": str(e)})
            continue
        p.outcome = {**s, "end_price": end[1], "end_price_date": end[0].isoformat(),
                     "end_benchmark": eb[1] if eb else None}
        p.status = "evaluated"
        p.evaluated_at = utcnow()
        sec = db.get(Security, p.security_id)
        db.add(AgentMemory(user_id=p.user_id, kind="lesson", security_id=p.security_id, ref_type="prediction", ref_id=p.id,
                           tags=["prediction", "outcome", "correct" if s["correct"] else "incorrect"],
                           content=f"Prediction #{p.id} on {sec.symbol} ({p.kind}, {p.direction}, p={p.probability:.2f}, "
                                   f"{p.horizon_days}d) was {'CORRECT' if s['correct'] else 'INCORRECT'}: measure {s['measure']:+.2%}. "
                                   f"Rationale was: {p.rationale[:300]}"))
        done.append(p.id)
    db.flush()
    return {"evaluated": done, "skipped": skipped}


def prediction_scorecard(db: Session, user_id: int) -> dict:
    preds = db.scalars(select(Prediction).where(Prediction.user_id == user_id, Prediction.status == "evaluated")).all()
    rows = [{"probability": p.probability, "correct": bool(p.outcome.get("correct")), "brier": p.outcome.get("brier", 0.25),
             "measure": p.outcome.get("measure", 0.0), "source": p.source, "model_version": p.model_version,
             "data_quality": p.data_quality} for p in preds if p.outcome]
    by_source = {}
    for src in sorted({r["source"] for r in rows}):
        by_source[src] = scoring.summarize([r for r in rows if r["source"] == src])
    by_model = {}
    for mv in sorted({r["model_version"] for r in rows}):
        by_model[mv] = scoring.summarize([r for r in rows if r["model_version"] == mv])
    return {"overall": scoring.summarize(rows), "by_source": by_source, "by_model_version": by_model,
            "n_open": len(db.scalars(select(Prediction.id).where(Prediction.user_id == user_id, Prediction.status == "open")).all()),
            "includes_sample_data": any(r["data_quality"] == "sample" for r in rows),
            "methodology": "Every prediction is recorded with its baseline price and evaluation rule before its window "
                           "starts; all evaluated predictions are included (no selective reporting)."}


# ------------------------------------------------------------------ strategy versions
def ensure_strategy_versions(db: Session, user_id: int) -> None:
    for key in STRATEGIES:
        exists = db.scalar(select(StrategyVersion.id).where(StrategyVersion.user_id == user_id, StrategyVersion.strategy_key == key).limit(1))
        if not exists:
            db.add(StrategyVersion(user_id=user_id, strategy_key=key, version="1.0", params=default_params(key),
                                   status="production", notes="Initial default parameters", proposed_by="system",
                                   approved_at=utcnow()))
    db.flush()


def production_version(db: Session, user_id: int, key: str) -> StrategyVersion:
    ensure_strategy_versions(db, user_id)
    v = db.scalar(select(StrategyVersion).where(StrategyVersion.user_id == user_id, StrategyVersion.strategy_key == key,
                                                StrategyVersion.status == "production"))
    if v is None:
        raise ValueError(f"No production version for strategy '{key}'")
    return v


def propose_version(db: Session, user_id: int, key: str, changes: dict, notes: str, proposed_by: str = "user") -> StrategyVersion:
    base = production_version(db, user_id, key)
    params = copy.deepcopy(base.params)
    for k, v in changes.items():
        if k == "criteria_values":
            for c in params["criteria"]:
                if c["feature"] in v:
                    c["value"] = float(v[c["feature"]])
        elif k in ("weights", "criteria", "min_pass_ratio", "top_n", "universe"):
            params[k] = v
        else:
            raise ValueError(f"'{k}' cannot be changed through a proposal")
    validate_params(params)
    existing = db.scalars(select(StrategyVersion.version).where(StrategyVersion.user_id == user_id, StrategyVersion.strategy_key == key)).all()
    major = max(int(v.split(".")[0]) for v in existing)
    minor = max(int(v.split(".")[1]) for v in existing if int(v.split(".")[0]) == major) + 1
    v = StrategyVersion(user_id=user_id, strategy_key=key, version=f"{major}.{minor}", params=params, status="candidate",
                        parent_id=base.id, notes=notes[:2000], proposed_by=proposed_by)
    db.add(v)
    db.flush()
    return v


def _panel_and_bench(db: Session, include_sample: bool | None = None):
    panel = load_panel(db, include_sample=include_sample)
    bench = pick_benchmark(db)
    return panel, (bench.id if bench else None)


def _universe_is_sample(db: Session) -> bool:
    real = db.scalar(select(Security.id).where(Security.is_sample.is_(False), Security.asset_class == "equity").limit(1))
    return real is None


def run_backtest_job(db: Session, user_id: int, strategy_key: str, version_id: int | None, start: date, end: date,
                     method: str = "single", costs: dict | None = None, grid: list[dict] | None = None,
                     include_sensitivity: bool = True) -> Backtest:
    v = db.get(StrategyVersion, version_id) if version_id else production_version(db, user_id, strategy_key)
    if v is None or v.user_id != user_id:
        raise ValueError("strategy version not found")
    sample = _universe_is_sample(db)
    panel, bench = _panel_and_bench(db, include_sample=sample)
    c = Costs(**(costs or {}))
    warnings: list[str] = []
    folds: list = []
    if method == "walk_forward":
        res = walk_forward(panel, v.params, start, end, grid=grid, benchmark_id=bench, costs=c)
        metrics = {**res["oos_metrics"], "folds_beating_benchmark": res["folds_beating_benchmark"], "n_folds": res["n_folds"],
                   "avg_fold_turnover": float(np.mean([f["test_metrics"].get("annual_turnover") or 0 for f in res["folds"]]))}
        curve = {"equity": res["equity_curve"], "benchmark": res["benchmark_curve"]}
        folds = res["folds"]
        warnings.append("Walk-forward: headline metrics are stitched out-of-sample test windows only.")
        if sample:
            warnings.append("Backtest uses SAMPLE (synthetic) data: results illustrate the method only.")
        warnings.append("Universe = securities currently in the database; survivorship bias cannot be ruled out.")
    else:
        res = run_backtest(panel, v.params, start, end, bench, c)
        metrics = res["metrics"]
        curve = {"equity": res["equity_curve"], "benchmark": res["benchmark_curve"], "rebalances": res["rebalances"][-24:]}
        warnings = res["warnings"]
        if include_sensitivity:
            metrics["sensitivity"] = sensitivity(panel, v.params, start, end, bench, c)
    bt = Backtest(user_id=user_id, strategy_key=strategy_key, strategy_version_id=v.id,
                  config={"method": method, "start": start.isoformat(), "end": end.isoformat(), "costs": c.__dict__,
                          "version": v.version, "grid": grid, "params": v.params, "execution": "next-day close",
                          "point_in_time": True, "benchmark_security_id": bench},
                  metrics=_clean(metrics), equity_curve=_clean(curve), folds=_clean(folds), warnings=warnings,
                  data_quality="sample" if sample else "eod")
    db.add(bt)
    db.flush()
    return bt


def run_experiment(db: Session, user_id: int, candidate_id: int, start: date, end: date, criteria: dict | None = None) -> Experiment:
    cand = db.get(StrategyVersion, candidate_id)
    if cand is None or cand.user_id != user_id or cand.status != "candidate":
        raise ValueError("candidate version not found")
    base = production_version(db, user_id, cand.strategy_key)
    crit = {**DEFAULT_ACCEPTANCE, **(criteria or {})}
    exp = Experiment(user_id=user_id, strategy_key=cand.strategy_key, baseline_version_id=base.id, candidate_version_id=cand.id,
                     acceptance_criteria=crit, status="pending")  # criteria fixed BEFORE running
    db.add(exp)
    db.flush()
    sample = _universe_is_sample(db)
    panel, bench = _panel_and_bench(db, include_sample=sample)
    rb = walk_forward(panel, base.params, start, end, benchmark_id=bench)
    rc = walk_forward(panel, cand.params, start, end, benchmark_id=bench)
    for r in (rb, rc):
        r["avg_fold_turnover"] = float(np.mean([f["test_metrics"].get("annual_turnover") or 0 for f in r["folds"]]))
    verdict = scoring.acceptance(rb, rc, crit)
    exp.results = _clean({"baseline": {"version": base.version, "oos_metrics": rb["oos_metrics"], "folds": rb["folds"]},
                          "candidate": {"version": cand.version, "oos_metrics": rc["oos_metrics"], "folds": rc["folds"]},
                          "verdict": verdict, "window": [start.isoformat(), end.isoformat()],
                          "data_quality": "sample" if sample else "eod"})
    exp.status = "passed" if verdict["passed"] else "failed"
    db.flush()
    return exp


def promote(db: Session, user_id: int, experiment_id: int) -> StrategyVersion:
    exp = db.get(Experiment, experiment_id)
    if exp is None or exp.user_id != user_id:
        raise ValueError("experiment not found")
    if exp.status != "passed":
        raise ValueError("Only experiments that passed their acceptance criteria can be promoted")
    cand = db.get(StrategyVersion, exp.candidate_version_id)
    cur = production_version(db, user_id, exp.strategy_key)
    cur.status = "retired"
    cand.status = "production"
    cand.approved_at = utcnow()
    exp.status = "promoted"
    exp.decided_at = utcnow()
    db.flush()
    return cand


def reject_experiment(db: Session, user_id: int, experiment_id: int) -> Experiment:
    exp = db.get(Experiment, experiment_id)
    if exp is None or exp.user_id != user_id:
        raise ValueError("experiment not found")
    exp.status = "rejected"
    exp.decided_at = utcnow()
    cand = db.get(StrategyVersion, exp.candidate_version_id)
    if cand.status == "candidate":
        cand.status = "rejected"
    db.flush()
    return exp


def rollback(db: Session, user_id: int, version_id: int) -> StrategyVersion:
    v = db.get(StrategyVersion, version_id)
    if v is None or v.user_id != user_id or v.status not in ("retired",):
        raise ValueError("Only a retired (previously production) version can be restored")
    cur = production_version(db, user_id, v.strategy_key)
    cur.status = "retired"
    v.status = "production"
    v.approved_at = utcnow()
    db.flush()
    return v


def _clean(x):
    from app.services.research import _jsonable

    return _jsonable(x)

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import owned, security_by_symbol
from app.core.screening.strategies import STRATEGIES
from app.db import get_db
from app.models import Backtest, Experiment, Prediction, Security, StrategyVersion, User
from app.security import audit, client_ip, get_current_user, rate_limit
from app.services import learning
from app.worker.queue import enqueue

router = APIRouter(prefix="/api/learning", tags=["learning"])


class PredictionIn(BaseModel):
    symbol: str
    direction: Literal["up", "down"]
    probability: float = Field(ge=0.5, le=0.99)
    horizon_days: int = Field(ge=1, le=3650)
    kind: Literal["direction", "outperform"] = "direction"
    rationale: str = Field(default="", max_length=4000)


def pred_out(p: Prediction, db: Session) -> dict:
    s = db.get(Security, p.security_id)
    b = db.get(Security, p.benchmark_security_id) if p.benchmark_security_id else None
    return {"id": p.id, "symbol": s.symbol, "is_sample": s.is_sample, "benchmark": b.symbol if b else None, "kind": p.kind,
            "direction": p.direction, "probability": p.probability, "horizon_days": p.horizon_days,
            "start_date": p.start_date.isoformat(), "evaluate_on": p.evaluate_on.isoformat(), "baseline_price": p.baseline_price,
            "rationale": p.rationale, "evaluation_criteria": p.evaluation_criteria, "model_version": p.model_version,
            "source": p.source, "status": p.status, "outcome": p.outcome, "data_quality": p.data_quality,
            "assumptions": p.assumptions, "created_at": p.created_at.isoformat()}


@router.get("/predictions")
def list_predictions(status: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = select(Prediction).where(Prediction.user_id == user.id)
    if status:
        q = q.where(Prediction.status == status)
    return [pred_out(p, db) for p in db.scalars(q.order_by(Prediction.created_at.desc()).limit(500)).all()]


@router.post("/predictions")
def create_prediction(body: PredictionIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = security_by_symbol(db, body.symbol)
    try:
        p = learning.create_prediction(db, user.id, s, body.direction, body.probability, body.horizon_days, body.kind, body.rationale)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    audit(db, user.id, "prediction.create", "prediction", p.id, body.model_dump(), ip=client_ip(request))
    db.commit()
    return pred_out(p, db)


@router.post("/predictions/evaluate")
def evaluate(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = learning.evaluate_due_predictions(db, user.id)
    db.commit()
    return r


@router.get("/scorecard")
def scorecard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return learning.prediction_scorecard(db, user.id)


# ------------------------------------------------------------------ strategies & backtests
def ver_out(v: StrategyVersion) -> dict:
    return {"id": v.id, "strategy_key": v.strategy_key, "version": v.version, "status": v.status, "params": v.params,
            "notes": v.notes, "proposed_by": v.proposed_by, "parent_id": v.parent_id,
            "approved_at": v.approved_at.isoformat() if v.approved_at else None, "created_at": v.created_at.isoformat()}


@router.get("/strategies")
def strategy_versions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    learning.ensure_strategy_versions(db, user.id)
    db.commit()
    rows = db.scalars(select(StrategyVersion).where(StrategyVersion.user_id == user.id)
                      .order_by(StrategyVersion.strategy_key, StrategyVersion.created_at)).all()
    return [ver_out(v) for v in rows]


class ProposeIn(BaseModel):
    strategy_key: str
    changes: dict
    notes: str = Field(default="", max_length=2000)


@router.post("/strategies/propose")
def propose(body: ProposeIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.strategy_key not in STRATEGIES:
        raise HTTPException(400, "unknown strategy")
    try:
        v = learning.propose_version(db, user.id, body.strategy_key, body.changes, body.notes)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    audit(db, user.id, "strategy.propose", "strategy_version", v.id, body.model_dump(), ip=client_ip(request))
    db.commit()
    return ver_out(v)


@router.post("/strategies/{vid}/rollback")
def rollback(vid: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        v = learning.rollback(db, user.id, vid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    audit(db, user.id, "strategy.rollback", "strategy_version", v.id, {"version": v.version}, ip=client_ip(request))
    db.commit()
    return ver_out(v)


class BacktestIn(BaseModel):
    strategy_key: str
    version_id: int | None = None
    start: date
    end: date
    method: Literal["single", "walk_forward"] = "single"
    commission_bps: float = Field(default=1.0, ge=0, le=100)
    spread_bps: float = Field(default=10.0, ge=0, le=500)
    slippage_bps: float = Field(default=5.0, ge=0, le=500)
    grid: list[dict] | None = None

    @model_validator(mode="after")
    def check(self):
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if (self.end - self.start).days < 180:
            raise ValueError("window must be at least 6 months")
        return self


@router.post("/backtests", dependencies=[Depends(rate_limit("bt", 30, 3600))])
def create_backtest(body: BacktestIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.strategy_key not in STRATEGIES:
        raise HTTPException(400, "unknown strategy")
    if body.version_id:
        owned(db.get(StrategyVersion, body.version_id), user.id, "Strategy version")
    jr = enqueue(db, "backtest", {"strategy_key": body.strategy_key, "version_id": body.version_id, "start": body.start.isoformat(),
                                  "end": body.end.isoformat(), "method": body.method, "grid": body.grid,
                                  "costs": {"commission_bps": body.commission_bps, "spread_bps": body.spread_bps,
                                            "slippage_bps": body.slippage_bps}},
                 user.id, trigger="manual", max_attempts=1, timeout_seconds=1800)
    audit(db, user.id, "backtest.request", "job_run", jr.id, body.model_dump(mode="json"), ip=client_ip(request))
    db.commit()
    return {"job_run_id": jr.id, "status": jr.status}


def bt_out(b: Backtest, full: bool) -> dict:
    d = {"id": b.id, "strategy_key": b.strategy_key, "config": b.config, "data_quality": b.data_quality, "warnings": b.warnings,
         "created_at": b.created_at.isoformat(),
         "metrics": b.metrics if full else {k: b.metrics.get(k) for k in ("cagr", "sharpe", "max_drawdown", "excess_cagr", "benchmark_cagr", "n_folds", "folds_beating_benchmark")}}
    if full:
        d |= {"equity_curve": b.equity_curve, "folds": b.folds}
    return d


@router.get("/backtests")
def list_backtests(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [bt_out(b, False) for b in db.scalars(select(Backtest).where(Backtest.user_id == user.id).order_by(Backtest.created_at.desc()).limit(100)).all()]


@router.get("/backtests/{bid}")
def get_backtest(bid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return bt_out(owned(db.get(Backtest, bid), user.id, "Backtest"), True)


class ExperimentIn(BaseModel):
    candidate_version_id: int
    start: date
    end: date
    criteria: dict | None = None


@router.post("/experiments")
def create_experiment(body: ExperimentIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v = owned(db.get(StrategyVersion, body.candidate_version_id), user.id, "Strategy version")
    if v.status != "candidate":
        raise HTTPException(400, "Only candidate versions can be tested")
    jr = enqueue(db, "experiment", {"candidate_version_id": v.id, "start": body.start.isoformat(), "end": body.end.isoformat(),
                                    "criteria": body.criteria}, user.id, trigger="manual", max_attempts=1, timeout_seconds=3600)
    audit(db, user.id, "experiment.request", "job_run", jr.id, body.model_dump(mode="json"), ip=client_ip(request))
    db.commit()
    return {"job_run_id": jr.id}


def exp_out(e: Experiment, db: Session) -> dict:
    b, c = db.get(StrategyVersion, e.baseline_version_id), db.get(StrategyVersion, e.candidate_version_id)
    return {"id": e.id, "strategy_key": e.strategy_key, "baseline": ver_out(b), "candidate": ver_out(c),
            "acceptance_criteria": e.acceptance_criteria, "results": e.results, "status": e.status,
            "decided_at": e.decided_at.isoformat() if e.decided_at else None, "created_at": e.created_at.isoformat()}


@router.get("/experiments")
def list_experiments(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [exp_out(e, db) for e in db.scalars(select(Experiment).where(Experiment.user_id == user.id).order_by(Experiment.created_at.desc())).all()]


@router.post("/experiments/{eid}/promote")
def promote(eid: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        v = learning.promote(db, user.id, eid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    audit(db, user.id, "strategy.promote", "experiment", eid, {"version": v.version, "strategy": v.strategy_key}, ip=client_ip(request))
    db.commit()
    return ver_out(v)


@router.post("/experiments/{eid}/reject")
def reject(eid: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        e = learning.reject_experiment(db, user.id, eid)
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
    audit(db, user.id, "experiment.reject", "experiment", eid, {}, ip=client_ip(request))
    db.commit()
    return exp_out(e, db)

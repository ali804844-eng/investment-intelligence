from __future__ import annotations

import csv
import io
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import owned, owned_portfolio, security_by_symbol
from app.core.finance import ratios
from app.core.screening.engine import run_screen
from app.core.screening.strategies import OPS, STRATEGIES, UNIVERSE_DEFAULT, validate_params
from app.db import get_db
from app.models import ResearchCandidate, ResearchReport, ResearchRun, ScreenDefinition, Security, User
from app.providers.sample import last_business_day
from app.security import audit, client_ip, get_current_user, rate_limit
from app.services import learning
from app.services import market_data as md
from app.services import research
from app.services.panel import FEATURES, load_panel

router = APIRouter(prefix="/api", tags=["research"])


# ------------------------------------------------------------------ screener
@router.get("/screener/strategies")
def strategies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    out = []
    for key, spec in STRATEGIES.items():
        v = learning.production_version(db, user.id, key)
        out.append({"key": key, "title": spec["title"], "description": spec["description"], "horizon": spec["horizon"],
                    "version": v.version, "params": v.params})
    db.commit()
    return {"strategies": out, "features": FEATURES, "operators": list(OPS)}


class Criterion(BaseModel):
    feature: str
    op: Literal[">=", "<=", ">", "<", "=="]
    value: float
    required: bool = False


class ScreenIn(BaseModel):
    strategy_key: str | None = None
    criteria: list[Criterion] | None = None
    weights: dict[str, float] | None = None
    min_pass_ratio: float | None = Field(default=None, gt=0, le=1)
    top_n: int = Field(default=25, ge=1, le=100)
    include_sample: bool | None = None
    country: str | None = None
    sector: str | None = None
    min_adv_usd: float | None = Field(default=None, ge=0)
    asset_classes: list[str] | None = None


@router.post("/screener/run", dependencies=[Depends(rate_limit("screen", 60, 3600))])
def run(body: ScreenIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.strategy_key:
        if body.strategy_key not in STRATEGIES:
            raise HTTPException(400, "unknown strategy")
        params = dict(learning.production_version(db, user.id, body.strategy_key).params)
        version = learning.production_version(db, user.id, body.strategy_key).version
    else:
        params = {"title": "Custom screen", "criteria": [], "weights": {}, "min_pass_ratio": 1.0, "universe": dict(UNIVERSE_DEFAULT)}
        version = "custom"
    if body.criteria is not None:
        params["criteria"] = [c.model_dump() for c in body.criteria]
    if body.weights is not None:
        params["weights"] = body.weights
    if body.min_pass_ratio is not None:
        params["min_pass_ratio"] = body.min_pass_ratio
    params["universe"] = dict(params.get("universe") or UNIVERSE_DEFAULT)
    if body.min_adv_usd is not None:
        params["universe"]["min_adv_usd"] = body.min_adv_usd
    if body.asset_classes:
        params["universe"]["asset_classes"] = body.asset_classes
    params["top_n"] = body.top_n
    try:
        validate_params(params)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    real = db.scalar(select(Security.id).where(Security.is_sample.is_(False), Security.asset_class == "equity").limit(1))
    sample = body.include_sample if body.include_sample is not None else real is None
    stmt = select(Security.id).where(Security.is_sample.is_(sample))
    if body.country:
        stmt = stmt.where(Security.country == body.country)
    if body.sector:
        stmt = stmt.where(Security.sector == body.sector)
    ids = list(db.scalars(stmt).all())
    panel = load_panel(db, ids)
    evs = {sid: research.upcoming_events(db, sid) for sid in ids}
    res = run_screen(panel, last_business_day(), params, events=evs)
    res["candidates"] = res["candidates"][: body.top_n]
    res["strategy"] = {"key": body.strategy_key, "version": version, "title": params.get("title")}
    res["universe"] = "SAMPLE" if sample else "real"
    return res


@router.get("/screener/queue")
def queue(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    run_ = db.scalars(select(ResearchRun).where(ResearchRun.user_id == user.id).order_by(ResearchRun.created_at.desc()).limit(1)).first()
    if run_ is None:
        return {"run": None, "candidates": []}
    cands = db.scalars(select(ResearchCandidate).where(ResearchCandidate.run_id == run_.id)
                       .order_by(ResearchCandidate.strategy_key, ResearchCandidate.rank)).all()
    return {"run": {"id": run_.id, "kind": run_.kind, "created_at": run_.created_at.isoformat(), "summary": run_.summary},
            "candidates": [{"id": c.id, "strategy": c.strategy_key, "version": c.strategy_version, "rank": c.rank, "score": c.score,
                            "status": c.status, "horizon": c.horizon, "data_quality": c.data_quality, **c.explanation} for c in cands]}


class CandidatePatch(BaseModel):
    status: Literal["new", "reviewing", "dismissed", "promoted"]


@router.patch("/screener/candidates/{cid}")
def patch_candidate(cid: int, body: CandidatePatch, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = owned(db.get(ResearchCandidate, cid), user.id, "Candidate")
    c.status = body.status
    db.commit()
    return {"ok": True}


class SavedScreenIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    strategy_key: str | None = None
    criteria: list[Criterion] = []
    filters: dict = {}


@router.get("/screener/saved")
def saved(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [{"id": s.id, "name": s.name, "strategy_key": s.strategy_key, "criteria": s.criteria, "filters": s.filters}
            for s in db.scalars(select(ScreenDefinition).where(ScreenDefinition.user_id == user.id)).all()]


@router.post("/screener/saved")
def save_screen(body: SavedScreenIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    for c in body.criteria:
        if c.feature not in FEATURES:
            raise HTTPException(400, f"unknown feature {c.feature}")
    s = ScreenDefinition(user_id=user.id, name=body.name, strategy_key=body.strategy_key,
                         criteria=[c.model_dump() for c in body.criteria], filters=body.filters)
    db.add(s)
    db.commit()
    return {"id": s.id}


@router.delete("/screener/saved/{sid}")
def delete_screen(sid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = owned(db.get(ScreenDefinition, sid), user.id, "Screen")
    db.delete(s)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ reports
class ReportIn(BaseModel):
    symbol: str
    horizon: Literal["long", "short"] = "long"
    strategy: str | None = None
    portfolio_id: int | None = None


def report_out(r: ResearchReport, db: Session, full: bool = True) -> dict:
    s = db.get(Security, r.security_id)
    d = {"id": r.id, "symbol": s.symbol, "name": s.name, "is_sample": s.is_sample, "title": r.title, "horizon": r.horizon,
         "conclusion": r.conclusion, "confidence": r.confidence, "data_quality": r.data_quality, "narrative_by": r.narrative_by,
         "model_version": r.model_version, "created_by": r.created_by, "created_at": r.created_at.isoformat(),
         "summary": r.sections.get("executive_summary")}
    if full:
        d |= {"sections": r.sections, "sources": r.sources, "calculations": r.calculations}
    return d


@router.get("/research/reports")
def list_reports(symbol: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = select(ResearchReport).where(ResearchReport.user_id == user.id)
    if symbol:
        q = q.where(ResearchReport.security_id == security_by_symbol(db, symbol).id)
    return [report_out(r, db, False) for r in db.scalars(q.order_by(ResearchReport.created_at.desc()).limit(200)).all()]


@router.get("/research/reports/{rid}")
def get_report(rid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return report_out(owned(db.get(ResearchReport, rid), user.id, "Report"), db)


@router.post("/research/reports", dependencies=[Depends(rate_limit("report", 60, 3600))])
def create_report(body: ReportIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = security_by_symbol(db, body.symbol)
    if body.portfolio_id:
        owned_portfolio(db, user.id, body.portfolio_id)
    if body.strategy and body.strategy not in STRATEGIES:
        raise HTTPException(400, "unknown strategy")
    r = research.generate_report(db, user.id, s, body.horizon, body.strategy, body.portfolio_id, created_by="user")
    audit(db, user.id, "report.generate", "research_report", r.id, {"symbol": s.symbol}, ip=client_ip(request))
    db.commit()
    return report_out(r, db)


@router.get("/research/runs")
def runs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [{"id": r.id, "kind": r.kind, "created_at": r.created_at.isoformat(), "job_run_id": r.job_run_id,
             "n_reports": len((r.summary or {}).get("reports", [])), "top": [q["symbol"] for q in (r.summary or {}).get("queue", [])[:5]]}
            for r in db.scalars(select(ResearchRun).where(ResearchRun.user_id == user.id).order_by(ResearchRun.created_at.desc()).limit(100)).all()]


@router.get("/research/runs/{rid}")
def get_run(rid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = owned(db.get(ResearchRun, rid), user.id, "Run")
    return {"id": r.id, "kind": r.kind, "created_at": r.created_at.isoformat(), "summary": r.summary, "steps": r.steps}


# ------------------------------------------------------------------ financial lab
class CompareIn(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=10)
    metrics: list[str] = Field(min_length=1, max_length=30)
    years: int = Field(default=5, ge=1, le=20)


@router.post("/lab/compare")
def lab_compare(body: CompareIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    out = {}
    for sym in body.symbols:
        s = security_by_symbol(db, sym)
        st = md.get_statements(db, s.id)["statements"]
        an = ratios.analyze(st) if st else None
        ps = (an["periods"] if an else [])[-body.years:]
        row = {}
        for m in body.metrics:
            if m in ratios.METRICS:
                row[m] = {p: st.get(m, {}).get(p) for p in ps}
            elif an and ps and m in an["by_period"][ps[-1]]:
                row[m] = {p: an["by_period"][p].get(m) for p in ps}
            else:
                row[m] = None
        out[s.symbol] = {"currency": s.currency, "is_sample": s.is_sample, "periods": ps, "values": row,
                         "checks": [c for c in (an["checks"] if an else []) if c["period"] in ps]}
    derived = sorted(ratios.derived_for_period({}, "x").keys())
    return {"results": out, "available_metrics": {"reported": list(ratios.METRICS), "derived": derived}}


class FormulaIn(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=10)
    formula: str = Field(min_length=1, max_length=300)
    years: int = Field(default=5, ge=1, le=20)


@router.post("/lab/formula")
def lab_formula(body: FormulaIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        variables = ratios.formula_variables(body.formula)
    except ratios.FormulaError as e:
        raise HTTPException(400, str(e)) from e
    known = set(ratios.METRICS) | set(ratios.derived_for_period({}, "x").keys())
    unknown = variables - known
    if unknown:
        raise HTTPException(400, f"Unknown variables: {sorted(unknown)}. Use reported metric names or derived ratios.")
    out = {}
    for sym in body.symbols:
        s = security_by_symbol(db, sym)
        st = md.get_statements(db, s.id)["statements"]
        ps = ratios.periods(st)[-body.years:]
        vals = {}
        for i, p in enumerate(ps):
            env = {m: st.get(m, {}).get(p) for m in ratios.METRICS}
            env |= ratios.derived_for_period(st, p, ps[i - 1] if i > 0 else None)
            vals[p] = ratios.eval_formula(body.formula, {k: env.get(k) for k in variables})
        out[s.symbol] = {"values": vals, "is_sample": s.is_sample, "currency": s.currency}
    return {"formula": body.formula, "variables": sorted(variables), "results": out,
            "note": "None = an input was missing or the expression was undefined (e.g. division by zero)."}


@router.get("/lab/export/{symbol}.csv")
def export_csv(symbol: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = security_by_symbol(db, symbol)
    st = md.get_statements(db, s.id)
    ps = ratios.periods(st["statements"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"# {s.symbol} {s.name} | currency {s.currency} | sources {','.join(st['sources'])} | "
                f"{'SAMPLE DATA - synthetic' if s.is_sample else 'as reported'}"])
    w.writerow(["metric", *ps])
    for m in ratios.METRICS:
        if m in st["statements"]:
            w.writerow([m, *[st["statements"][m].get(p, "") for p in ps]])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{s.symbol}_statements.csv"'})

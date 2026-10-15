"""End-to-end API tests against PostgreSQL with the SAMPLE universe loaded."""
from datetime import date, timedelta

import pytest


def run_job(job_id: int) -> None:
    """Run exactly this queued job through the real worker path (other queued jobs are cancelled first)."""
    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models import JobRun
    from app.worker import runner

    db = SessionLocal()
    db.execute(update(JobRun).where(JobRun.status == "queued", JobRun.id != job_id).values(status="cancelled"))
    db.commit()
    db.close()
    assert runner.run_one("test-worker") is True


def test_health(app_client):
    r = app_client.get("/api/health")
    assert r.status_code == 200 and r.json()["database"] is True


def test_auth_required_and_csrf(client_factory):
    from fastapi.testclient import TestClient

    c, _ = client_factory("auth@example.com")
    anon = TestClient(c.app)
    assert anon.get("/api/dashboard").status_code == 401
    # cookie-authenticated mutation without CSRF header is rejected
    del c.headers["X-Requested-With"]
    r = c.post("/api/watchlists", json={"name": "x"})
    assert r.status_code == 403
    c.headers["X-Requested-With"] = "ii"
    assert c.post("/api/watchlists", json={"name": "x"}).status_code == 200


def test_weak_password_and_duplicate(app_client):
    r = app_client.post("/api/auth/register", json={"email": "weak@example.com", "password": "short"})
    assert r.status_code == 422
    r = app_client.post("/api/auth/register", json={"email": "weak@example.com", "password": "alllowercase1234"})
    assert r.status_code == 400


def test_login_lockout(client_factory):
    from fastapi.testclient import TestClient

    c, _ = client_factory("lock@example.com", with_demo=False)
    anon = TestClient(c.app)
    for _ in range(5):
        assert anon.post("/api/auth/login", json={"email": "lock@example.com", "password": "wrongWrong123"}).status_code == 401
    r = anon.post("/api/auth/login", json={"email": "lock@example.com", "password": "Str0ngPassw0rd!"})
    assert r.status_code == 423


def test_user_isolation(client_factory):
    a, _ = client_factory("alice@example.com")
    b, _ = client_factory("bob@example.com")
    pa = a.get("/api/portfolios").json()[0]["id"]
    assert b.get(f"/api/portfolios/{pa}").status_code == 404
    assert b.post(f"/api/portfolios/{pa}/transactions", json={"type": "deposit", "trade_date": "2024-01-02", "amount": 5}).status_code == 404
    wl = a.get("/api/watchlists").json()[0]["id"]
    assert b.delete(f"/api/watchlists/{wl}").status_code == 404


def test_dashboard_and_labels(client_factory):
    c, _ = client_factory("dash@example.com")
    d = c.get("/api/dashboard").json()
    assert d["sample_mode"] is True
    pf = d["portfolios"][0]
    assert pf["data_quality"] == "sample" and pf["is_hypothetical"] is True
    assert pf["total_value"] > 0
    assert d["regime"]["data_quality"] == "sample"
    assert d["agent"]["worker_alive"] is False  # no worker running in tests: must not be claimed


def test_portfolio_accounting_through_api(client_factory):
    c, _ = client_factory("acct@example.com", with_demo=False)
    pid = c.post("/api/portfolios", json={"name": "Paper", "kind": "paper", "benchmark_symbol": "SMBR"}).json()["id"]
    d0 = date.today() - timedelta(days=400)
    while d0.weekday() >= 5:
        d0 += timedelta(days=1)
    assert c.post(f"/api/portfolios/{pid}/transactions", json={"type": "deposit", "trade_date": d0.isoformat(), "amount": 10000}).status_code == 200
    r = c.post(f"/api/portfolios/{pid}/transactions", json={"type": "buy", "trade_date": d0.isoformat(), "symbol": "NWND",
                                                           "quantity": 10, "price": 100, "fees": 5})
    assert r.status_code == 200, r.text
    # oversell rejected and ledger stays consistent
    r = c.post(f"/api/portfolios/{pid}/transactions", json={"type": "sell", "trade_date": d0.isoformat(), "symbol": "NWND",
                                                           "quantity": 11, "price": 100})
    assert r.status_code == 400
    st = c.get(f"/api/portfolios/{pid}").json()["state"]
    assert st["cash"] == pytest.approx(10000 - 1005)
    pos = st["positions"][0]
    assert pos["quantity"] == 10 and pos["cost_basis"] == pytest.approx(1005)
    assert pos["quality"] == "sample"
    h = c.get(f"/api/portfolios/{pid}/history").json()
    assert h["metrics"]["twr_total"] is not None and h["benchmark"]["symbol"] == "SMBR"
    # real instruments cannot be mixed in (none exist) and future dates are rejected
    r = c.post(f"/api/portfolios/{pid}/transactions", json={"type": "deposit", "trade_date": (date.today() + timedelta(days=3)).isoformat(), "amount": 1})
    assert r.status_code == 422
    # sample instruments cannot go into an actual (non-hypothetical) portfolio
    rid = c.post("/api/portfolios", json={"name": "Real", "kind": "manual", "is_hypothetical": False}).json()["id"]
    r = c.post(f"/api/portfolios/{rid}/transactions", json={"type": "buy", "trade_date": d0.isoformat(), "symbol": "NWND", "quantity": 1, "price": 1})
    assert r.status_code == 400
    # audit trail
    actions = [a["action"] for a in c.get("/api/audit").json()]
    assert "transaction.create" in actions and "portfolio.create" in actions


def test_live_portfolio_blocked(client_factory):
    c, _ = client_factory("live@example.com", with_demo=False)
    assert c.post("/api/portfolios", json={"name": "Broker", "kind": "live", "is_hypothetical": False}).status_code == 400


def test_risk_and_pretrade(client_factory):
    c, _ = client_factory("risk@example.com")
    pid = c.get("/api/portfolios").json()[0]["id"]
    rr = c.get(f"/api/portfolios/{pid}/risk").json()
    assert "checks" in rr and rr["engine"].startswith("deterministic")
    # a huge order breaches cash and position limits
    chk = c.post(f"/api/portfolios/{pid}/pretrade-check", json={"symbol": "NWND", "side": "buy", "quantity": 100000}).json()
    assert chk["approved"] is False
    rules = {x["rule"] for x in chk["checks"] if x["status"] == "breach"}
    assert {"cash_available", "max_position_pct"} <= rules
    small = c.post(f"/api/portfolios/{pid}/pretrade-check", json={"symbol": "SMGD", "side": "buy", "quantity": 1}).json()
    assert small["approved"] is True, small
    # risk limits editable by user only, audited
    pol = c.get("/api/risk/policy").json()
    pol["max_position_pct"] = 0.5
    pol.pop("note")
    assert c.put("/api/risk/policy", json=pol).status_code == 200
    assert c.get("/api/risk/policy").json()["max_position_pct"] == 0.5
    st = c.post(f"/api/portfolios/{pid}/stress", json={"scenario": "equity_crash"}).json()
    assert st["pnl"] < 0


def test_screener_and_reports(client_factory):
    c, _ = client_factory("research@example.com")
    s = c.post("/api/screener/run", json={"strategy_key": "quality"}).json()
    assert s["universe"] == "SAMPLE" and s["n_candidates"] >= 1
    cand = s["candidates"][0]
    assert cand["criteria_passed"] and "invalidation" in cand and cand["disclaimer"]
    custom = c.post("/api/screener/run", json={"criteria": [{"feature": "pe", "op": "<=", "value": 12, "required": True}]}).json()
    assert all(x["key_metrics"]["pe"] <= 12 for x in custom["candidates"])
    bad = c.post("/api/screener/run", json={"criteria": [{"feature": "__class__", "op": "<=", "value": 1}]})
    assert bad.status_code == 400
    r = c.post("/api/research/reports", json={"symbol": "GRPH"}).json()
    assert r["conclusion"] in ("consider", "monitor", "reject", "insufficient_evidence")
    secs = r["sections"]
    for k in ("executive_summary", "thesis", "valuation", "risks", "counterarguments", "data_limitations", "confidence",
              "reconsider_if", "horizon"):
        assert k in secs
    assert any("SAMPLE" in x for x in secs["data_limitations"])
    assert r["sources"] and r["calculations"]
    assert secs["confidence"]["calibrated"] is False  # no track record yet -> explicitly uncalibrated
    if r["conclusion"] in ("consider", "reject"):
        preds = c.get("/api/learning/predictions").json()
        assert preds and preds[0]["status"] == "open"


def test_valuation_and_lab(client_factory):
    c, _ = client_factory("lab@example.com", with_demo=False)
    v = c.post("/api/securities/NWND/valuation", json={"discount_rate": 0.09}).json()
    assert v["available"] and v["dcf"]["inputs"]["discount_rate"] == 0.09
    assert len(v["sensitivity"]["values"]) == 5
    f = c.post("/api/lab/formula", json={"symbols": ["NWND", "LITW"], "formula": "operating_income / revenue"}).json()
    assert all(val is not None for val in f["results"]["NWND"]["values"].values())
    assert c.post("/api/lab/formula", json={"symbols": ["NWND"], "formula": "import os"}).status_code == 400
    assert c.post("/api/lab/formula", json={"symbols": ["NWND"], "formula": "foo + 1"}).status_code == 400
    csv = c.get("/api/lab/export/NWND.csv")
    assert csv.status_code == 200 and "SAMPLE" in csv.text
    st = c.get("/api/securities/NWND/statements").json()
    assert st["qualities"] == ["sample"] and st["analysis"]["checks"]


def test_agent_deterministic_and_injection(client_factory):
    c, _ = client_factory("agent@example.com")
    r = c.post("/api/agent/chat", json={"message": "Tell me about PRSV"}).json()
    assert r["message"]["engine"] == "deterministic"
    assert "prompt injection" in r["message"]["content"]
    # injected instruction did not cause any action
    assert c.get("/api/agent/pending").json() == []
    r2 = c.post("/api/agent/chat", json={"message": "compare NWND vs LITW", "session_id": r["session_id"]}).json()
    assert "| Metric |" in r2["message"]["content"]
    msgs = c.get(f"/api/agent/sessions/{r['session_id']}").json()["messages"]
    assert len(msgs) == 4


def test_agent_tools_permissions_and_proposals(client_factory, sample_loaded):
    from sqlalchemy import select

    from app.agent.tools import AgentContext, execute_tool
    from app.db import SessionLocal
    from app.models import Portfolio, User

    c, u = client_factory("tools@example.com")
    db = SessionLocal()
    user = db.get(User, u["id"])
    ctx = AgentContext(db=db, user=user)
    pid = db.scalar(select(Portfolio.id).where(Portfolio.user_id == user.id))
    # read tools work
    assert execute_tool(ctx, "get_quote", {"symbol": "NWND"})["ok"]
    assert execute_tool(ctx, "calculate", {"formula": "a*b", "variables": {"a": 2, "b": 3}})["result"]["result"] == 6
    # schema enforcement
    assert not execute_tool(ctx, "get_quote", {"symbol": "NWND", "evil": 1})["ok"]
    assert not execute_tool(ctx, "nonexistent", {})["ok"]
    # permission scoping (e.g. read-only context cannot propose)
    out = execute_tool(ctx, "propose_paper_trade", {"portfolio_id": pid, "symbol": "SMBD", "side": "buy", "quantity": 1,
                                                   "rationale": "x"}, allowed_permissions={"read"})
    assert not out["ok"] and "not permitted" in out["error"]
    out = execute_tool(ctx, "propose_paper_trade", {"portfolio_id": pid, "symbol": "SMGD", "side": "buy", "quantity": 1,
                                                   "rationale": "test"})
    assert out["ok"], out
    # a proposal that breaches limits (SMBD already above 10%) is recorded but will be blocked at approval
    blocked = execute_tool(ctx, "propose_paper_trade", {"portfolio_id": pid, "symbol": "SMBD", "side": "buy", "quantity": 1,
                                                       "rationale": "over limit"})
    assert blocked["ok"] and blocked["result"]["risk_check"]["approved"] is False
    db.commit()
    db.close()
    pend = sorted(c.get("/api/agent/pending").json(), key=lambda p: p["id"])
    assert len(pend) == 2 and pend[0]["action_type"] == "paper_trade"
    fail = c.post(f"/api/agent/pending/{pend[1]['id']}", json={"decision": "approve", "confirm": True}).json()
    assert fail["status"] == "failed" and "max_position_pct" in fail["result"]["reason"]
    # approval requires explicit confirmation
    assert c.post(f"/api/agent/pending/{pend[0]['id']}", json={"decision": "approve"}).status_code == 400
    done = c.post(f"/api/agent/pending/{pend[0]['id']}", json={"decision": "approve", "confirm": True}).json()
    assert done["status"] == "executed" and done["result"]["transaction_id"]
    # duplicate execution blocked
    assert c.post(f"/api/agent/pending/{pend[0]['id']}", json={"decision": "approve", "confirm": True}).status_code == 400
    txns = c.get(f"/api/portfolios/{pid}/transactions").json()
    assert txns[0]["origin"] == "agent_paper"


def test_alerts_and_notifications(client_factory):
    c, _ = client_factory("alerts@example.com")
    a = c.post("/api/alerts", json={"kind": "price_above", "symbol": "NWND", "params": {"threshold": 0.01}}).json()
    assert c.post("/api/alerts", json={"kind": "price_above", "params": {"threshold": 1}}).status_code == 400
    r = c.post("/api/alerts/evaluate").json()
    assert any(f["alert_id"] == a["id"] for f in r["fired"])
    # cooldown prevents repeat
    r2 = c.post("/api/alerts/evaluate").json()
    assert not any(f["alert_id"] == a["id"] for f in r2["fired"])
    n = c.get("/api/notifications").json()
    assert n["unread"] >= 1 and "[SAMPLE]" in n["items"][0]["title"]
    c.post("/api/notifications/read-all")
    assert c.get("/api/notifications").json()["unread"] == 0


def test_job_queue_worker_cycle(client_factory):
    """Exercise the real worker code path: enqueue -> claim -> run -> succeed; failures retry and are inspectable."""
    from app.worker import runner

    c, u = client_factory("worker@example.com")
    j = c.post("/api/agent/jobs", json={"job_type": "research_cycle"}).json()
    assert j["status"] == "queued"
    run_job(j["id"])
    job = c.get(f"/api/agent/jobs/{j['id']}").json()
    assert job["status"] == "succeeded", job
    steps = [s["step"] for s in job["log"]]
    for k in ("1_priorities", "2_retrieve", "3_validate", "4_changes", "6_analyse", "8_portfolio", "10_rank", "11_notify", "12_outcomes"):
        assert k in steps
    assert c.get("/api/screener/queue").json()["candidates"]
    assert c.get("/api/research/reports").json()  # cycle produced reports
    # failing job: unknown strategy param makes handler raise -> retry scheduled -> eventually failed
    bad = c.post("/api/agent/jobs", json={"job_type": "screening", "params": {"strategies": ["nope"]}}).json()
    from app.db import SessionLocal
    from app.models import JobRun

    for _ in range(3):
        db = SessionLocal()
        jr = db.get(JobRun, bad["id"])
        jr.run_after = jr.run_after.replace(year=2000)  # make retry immediately runnable
        db.commit()
        db.close()
        runner.run_one("test-worker")
    jb = c.get(f"/api/agent/jobs/{bad['id']}").json()
    assert jb["status"] == "failed" and len(jb["error_history"]) == 3
    assert any("failed" in n["title"].lower() for n in c.get("/api/notifications").json()["items"])
    rt = c.post(f"/api/agent/jobs/{bad['id']}/retry").json()
    assert rt["status"] == "queued"
    assert c.post(f"/api/agent/jobs/{rt['id']}/cancel").json()["status"] == "cancelled"


def test_scheduler_respects_pause(client_factory):
    from sqlalchemy import select

    from app.db import SessionLocal, utcnow
    from app.models import JobRun, ScheduledJob
    from app.worker import runner

    c, u = client_factory("sched@example.com", with_demo=False)
    assert len(c.get("/api/agent/schedules").json()) >= 6
    c.put("/api/agent/settings", json={"state": "paused"})
    db = SessionLocal()
    for sj in db.scalars(select(ScheduledJob).where(ScheduledJob.user_id == u["id"])).all():
        sj.next_run_at = utcnow().replace(year=2000)
    db.commit()
    runner.scheduler_tick()
    n = len(db.scalars(select(JobRun).where(JobRun.user_id == u["id"], JobRun.trigger == "schedule")).all())
    assert n == 0
    statuses = {s["last_status"] for s in c.get("/api/agent/schedules").json()}
    assert "skipped (agent paused)" in statuses
    c.put("/api/agent/settings", json={"state": "running"})
    for sj in db.scalars(select(ScheduledJob).where(ScheduledJob.user_id == u["id"])).all():
        sj.next_run_at = utcnow().replace(year=2000)
    db.commit()
    runner.scheduler_tick()
    db.expire_all()
    n = len(db.scalars(select(JobRun).where(JobRun.user_id == u["id"], JobRun.trigger == "schedule")).all())
    assert n >= 6
    db.close()
    assert c.post("/api/agent/schedules", json={"name": "too often", "job_type": "data_health", "cron": "* * * * *"}).status_code == 400


def test_prediction_lifecycle(client_factory):
    from app.db import SessionLocal
    from app.models import Prediction

    c, _ = client_factory("pred@example.com", with_demo=False)
    p = c.post("/api/learning/predictions", json={"symbol": "NWND", "direction": "up", "probability": 0.6, "horizon_days": 30,
                                                "rationale": "test"}).json()
    assert p["baseline_price"] > 0 and p["status"] == "open"
    assert c.post("/api/learning/predictions", json={"symbol": "NWND", "direction": "up", "probability": 0.3,
                                                     "horizon_days": 30}).status_code == 422
    # simulate a prediction registered in the past whose window has ended
    db = SessionLocal()
    pr = db.get(Prediction, p["id"])
    pr.start_date = date.today() - timedelta(days=120)
    pr.evaluate_on = date.today() - timedelta(days=90)
    db.commit()
    db.close()
    ev = c.post("/api/learning/predictions/evaluate").json()
    assert p["id"] in ev["evaluated"]
    sc = c.get("/api/learning/scorecard").json()
    assert sc["overall"]["n"] == 1 and sc["includes_sample_data"] is True
    mem = c.get("/api/agent/memory?kind=lesson").json()
    assert mem and "Prediction" in mem[0]["content"]


def test_backtest_and_experiment_workflow(client_factory):
    from app.worker import runner

    c, _ = client_factory("learn@example.com", with_demo=False)
    end = date.today() - timedelta(days=5)
    start = end - timedelta(days=365 * 5)
    bt = c.post("/api/learning/backtests", json={"strategy_key": "value", "start": start.isoformat(), "end": end.isoformat()}).json()
    run_job(bt["job_run_id"])
    job = c.get(f"/api/agent/jobs/{bt['job_run_id']}").json()
    assert job["status"] == "succeeded", job
    b = c.get(f"/api/learning/backtests/{job['result']['backtest_id']}").json()
    assert b["config"]["point_in_time"] and b["config"]["execution"] == "next-day close"
    assert any("SAMPLE" in w for w in b["warnings"]) and any("survivorship" in w for w in b["warnings"])
    assert b["metrics"]["total_costs"] > 0 and "sensitivity" in b["metrics"]
    vers = c.get("/api/learning/strategies").json()
    assert {v["strategy_key"] for v in vers if v["status"] == "production"} >= {"quality", "value"}
    cand = c.post("/api/learning/strategies/propose", json={"strategy_key": "value", "changes": {"top_n": 4}, "notes": "test"}).json()
    assert cand["status"] == "candidate" and cand["version"] == "1.1"
    assert c.post("/api/learning/strategies/propose", json={"strategy_key": "value", "changes": {"risk_limits": 1}}).status_code == 400
    ex = c.post("/api/learning/experiments", json={"candidate_version_id": cand["id"], "start": start.isoformat(), "end": end.isoformat()}).json()
    run_job(ex["job_run_id"])
    exps = c.get("/api/learning/experiments").json()
    e = exps[0]
    assert e["status"] in ("passed", "failed") and e["results"]["verdict"]["checks"]
    if e["status"] == "failed":
        assert c.post(f"/api/learning/experiments/{e['id']}/promote").status_code == 400
    else:
        v = c.post(f"/api/learning/experiments/{e['id']}/promote").json()
        assert v["status"] == "production"
        prod_old = next(x for x in c.get("/api/learning/strategies").json() if x["strategy_key"] == "value" and x["version"] == "1.0")
        assert prod_old["status"] == "retired"
        assert c.post(f"/api/learning/strategies/{prod_old['id']}/rollback").json()["status"] == "production"
    assert ex["job_run_id"]


def test_provider_status_never_leaks_secrets(app_client, client_factory):
    c, _ = client_factory("prov@example.com", with_demo=False)
    body = c.get("/api/settings/providers").text + c.get("/api/agent/status").text + c.get("/api/settings/system").text
    for needle in ("sk-ant", "api_key\":\"", "password"):
        assert needle not in body
    st = c.get("/api/agent/status").json()
    assert st["continuous_operation"]["verified"] is False

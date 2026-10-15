"""Admin CLI.

  python -m app.cli seed-sample            # load/refresh the synthetic SAMPLE universe
  python -m app.cli create-user EMAIL      # create a user (prompts for password)
  python -m app.cli check-providers        # call each configured provider once and report status
  python -m app.cli enqueue JOB_TYPE EMAIL # queue a background job for a user
"""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import date, timedelta

from sqlalchemy import select

from app.db import session_scope
from app.models import User


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed-sample")
    cu = sub.add_parser("create-user")
    cu.add_argument("email")
    cu.add_argument("--demo", action="store_true", help="also create the SAMPLE demo portfolio")
    sub.add_parser("check-providers")
    eq = sub.add_parser("enqueue")
    eq.add_argument("job_type")
    eq.add_argument("email")
    a = p.parse_args(argv)

    if a.cmd == "seed-sample":
        from app.services.bootstrap import ensure_sample_data

        with session_scope() as db:
            n = ensure_sample_data(db)
        print(f"SAMPLE universe ready: {n} instruments")
    elif a.cmd == "create-user":
        from app.security import hash_password, validate_password_strength
        from app.services.bootstrap import bootstrap_user, create_demo_portfolio, ensure_sample_data

        pw = getpass.getpass("Password: ")
        try:
            validate_password_strength(pw)
        except Exception as e:  # noqa: BLE001
            print(getattr(e, "detail", str(e)))
            return 1
        with session_scope() as db:
            if db.scalar(select(User.id).where(User.email == a.email.lower())):
                print("user exists")
                return 1
            u = User(email=a.email.lower(), password_hash=hash_password(pw))
            db.add(u)
            db.flush()
            bootstrap_user(db, u)
            if a.demo:
                ensure_sample_data(db)
                create_demo_portfolio(db, u)
        print("created")
    elif a.cmd == "check-providers":
        from app.providers.registry import get_registry

        reg = get_registry()
        probes = {
            "sec_edgar": lambda pr: pr.cik_for("AAPL"),
            "tiingo": lambda pr: len(pr.daily_bars("SPY", date.today() - timedelta(days=10), date.today())),
            "alphavantage": lambda pr: len(pr.daily_bars("IBM", date.today() - timedelta(days=10), date.today())),
            "fred": lambda pr: len(pr.macro_series("DGS10", date.today() - timedelta(days=20))),
            "frankfurter": lambda pr: len(pr.fx_series("USD", ["EUR"], date.today() - timedelta(days=10), date.today())),
        }
        for name, fn in probes.items():
            prov = reg.get(name)
            if not prov.is_configured():
                print(f"{name:13s} NOT CONFIGURED - {prov.setup}")
                continue
            try:
                print(f"{name:13s} OK ({fn(prov)})")
            except Exception as e:  # noqa: BLE001
                print(f"{name:13s} FAILED - {e}")
    elif a.cmd == "enqueue":
        from app.worker.queue import enqueue

        with session_scope() as db:
            u = db.scalar(select(User).where(User.email == a.email.lower()))
            if u is None:
                print("no such user")
                return 1
            jr = enqueue(db, a.job_type, {}, u.id, trigger="manual")
            print(f"queued job {jr.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

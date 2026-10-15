"""Portfolio accounting: lots, cost basis (FIFO / average), cash, realized and unrealized P&L.

Pure functions on plain dataclasses so they can be unit-tested against hand-computed examples.
All monetary math uses ``Decimal``. Amounts are converted to the portfolio base currency with the
transaction's own ``fx_rate`` (1 unit of transaction currency = fx_rate units of base currency), which
means realized P&L includes the FX effect between purchase and sale dates.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

ZERO = Decimal(0)

CASH_TYPES = {"deposit", "withdrawal", "dividend", "interest", "fee"}
TRADE_TYPES = {"buy", "sell"}
VALID_TYPES = CASH_TYPES | TRADE_TYPES | {"split"}


class AccountingError(ValueError):
    pass


@dataclass(frozen=True)
class Txn:
    id: int
    type: str
    trade_date: date
    security_id: int | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    amount: Decimal | None = None
    fees: Decimal = ZERO
    currency: str = "USD"
    fx_rate: Decimal = Decimal(1)
    split_ratio: Decimal | None = None


@dataclass
class Lot:
    quantity: Decimal
    cost_base: Decimal  # total cost incl. fees, base currency
    open_date: date
    txn_id: int


@dataclass
class Position:
    security_id: int
    lots: list[Lot] = field(default_factory=list)
    realized_pnl: Decimal = ZERO
    dividends: Decimal = ZERO
    currency: str = "USD"

    @property
    def quantity(self) -> Decimal:
        return sum((lot.quantity for lot in self.lots), ZERO)

    @property
    def cost_basis(self) -> Decimal:
        return sum((lot.cost_base for lot in self.lots), ZERO)

    @property
    def avg_cost(self) -> Decimal | None:
        q = self.quantity
        return (self.cost_basis / q) if q else None


@dataclass
class Ledger:
    positions: dict[int, Position]
    cash_by_currency: dict[str, Decimal]
    cash_base: Decimal
    net_contributions: Decimal  # deposits - withdrawals, base currency
    total_fees: Decimal
    total_income: Decimal
    realized_pnl: Decimal
    closed_trades: list[dict]
    flows: list[tuple[date, Decimal]]  # external cash flows (+deposit, -withdrawal), base currency


def validate_txn(t: Txn) -> None:
    if t.type not in VALID_TYPES:
        raise AccountingError(f"Unknown transaction type '{t.type}'")
    if t.fx_rate is None or t.fx_rate <= 0:
        raise AccountingError("fx_rate must be positive")
    if t.fees < 0:
        raise AccountingError("fees cannot be negative")
    if t.type in TRADE_TYPES:
        if t.security_id is None:
            raise AccountingError(f"{t.type} requires a security")
        if t.quantity is None or t.quantity <= 0:
            raise AccountingError(f"{t.type} requires a positive quantity")
        if t.price is None or t.price < 0:
            raise AccountingError(f"{t.type} requires a non-negative price")
    elif t.type == "split":
        if t.security_id is None or t.split_ratio is None or t.split_ratio <= 0:
            raise AccountingError("split requires a security and a positive split_ratio")
    else:
        if t.amount is None or t.amount < 0:
            raise AccountingError(f"{t.type} requires a non-negative amount")
        if t.type == "dividend" and t.security_id is None:
            raise AccountingError("dividend requires a security")


def build_ledger(txns: list[Txn], method: str = "FIFO", allow_negative_cash: bool = True) -> Ledger:
    """Replay transactions chronologically (stable on id for same-day ordering)."""
    method = method.upper()
    if method not in {"FIFO", "AVG"}:
        raise AccountingError("method must be FIFO or AVG")
    positions: dict[int, Position] = {}
    cash: dict[str, Decimal] = defaultdict(lambda: ZERO)
    cash_base = ZERO
    contributions = ZERO
    fees_total = ZERO
    income = ZERO
    realized = ZERO
    closed: list[dict] = []
    flows: list[tuple[date, Decimal]] = []

    for t in txns:
        validate_txn(t)
    for t in sorted(txns, key=lambda x: (x.trade_date, _type_order(x.type), x.id)):
        fx = t.fx_rate
        if t.type == "deposit":
            cash[t.currency] += t.amount
            cash_base += t.amount * fx
            contributions += t.amount * fx
            flows.append((t.trade_date, t.amount * fx))
        elif t.type == "withdrawal":
            cash[t.currency] -= t.amount
            cash_base -= t.amount * fx
            contributions -= t.amount * fx
            flows.append((t.trade_date, -t.amount * fx))
        elif t.type in ("dividend", "interest"):
            net = t.amount - t.fees
            cash[t.currency] += net
            cash_base += net * fx
            income += net * fx
            fees_total += t.fees * fx
            if t.type == "dividend" and t.security_id is not None:
                pos = positions.setdefault(t.security_id, Position(t.security_id, currency=t.currency))
                pos.dividends += net * fx
        elif t.type == "fee":
            cash[t.currency] -= t.amount
            cash_base -= t.amount * fx
            fees_total += t.amount * fx
        elif t.type == "buy":
            gross = t.quantity * t.price
            cost_local = gross + t.fees
            cash[t.currency] -= cost_local
            cash_base -= cost_local * fx
            fees_total += t.fees * fx
            pos = positions.setdefault(t.security_id, Position(t.security_id, currency=t.currency))
            pos.lots.append(Lot(t.quantity, cost_local * fx, t.trade_date, t.id))
            if method == "AVG":
                _merge_avg(pos)
        elif t.type == "sell":
            pos = positions.get(t.security_id)
            held = pos.quantity if pos else ZERO
            if pos is None or held < t.quantity:
                raise AccountingError(
                    f"Sell of {t.quantity} exceeds held quantity {held} for security {t.security_id} "
                    f"on {t.trade_date} (short selling is not supported)"
                )
            proceeds_local = t.quantity * t.price - t.fees
            cash[t.currency] += proceeds_local
            cash_base += proceeds_local * fx
            fees_total += t.fees * fx
            cost_removed = _consume(pos, t.quantity)
            pnl = proceeds_local * fx - cost_removed
            pos.realized_pnl += pnl
            realized += pnl
            closed.append(
                {
                    "txn_id": t.id,
                    "security_id": t.security_id,
                    "date": t.trade_date.isoformat(),
                    "quantity": str(t.quantity),
                    "proceeds": str(proceeds_local * fx),
                    "cost": str(cost_removed),
                    "pnl": str(pnl),
                }
            )
        elif t.type == "split":
            pos = positions.get(t.security_id)
            if pos:
                for lot in pos.lots:
                    lot.quantity = lot.quantity * t.split_ratio
        if not allow_negative_cash and cash[t.currency] < 0:
            raise AccountingError(f"Cash in {t.currency} would go negative on {t.trade_date}")

    return Ledger(
        positions=positions,
        cash_by_currency=dict(cash),
        cash_base=cash_base,
        net_contributions=contributions,
        total_fees=fees_total,
        total_income=income,
        realized_pnl=realized,
        closed_trades=closed,
        flows=flows,
    )


def _type_order(tp: str) -> int:
    # Same-day: cash in first, then splits, buys, sells, income, outflows.
    return {"deposit": 0, "split": 1, "buy": 2, "sell": 3, "dividend": 4, "interest": 4, "fee": 5, "withdrawal": 6}[tp]


def _merge_avg(pos: Position) -> None:
    q = pos.quantity
    c = pos.cost_basis
    first = min(pos.lots, key=lambda lot: lot.open_date)
    pos.lots = [Lot(q, c, first.open_date, first.txn_id)] if q else []


def _consume(pos: Position, qty: Decimal) -> Decimal:
    remaining = qty
    cost_removed = ZERO
    new_lots: list[Lot] = []
    for lot in pos.lots:
        if remaining <= 0:
            new_lots.append(lot)
            continue
        take = min(lot.quantity, remaining)
        unit_cost = lot.cost_base / lot.quantity
        cost_removed += unit_cost * take
        left = lot.quantity - take
        remaining -= take
        if left > 0:
            new_lots.append(Lot(left, unit_cost * left, lot.open_date, lot.txn_id))
    pos.lots = new_lots
    return cost_removed


def value_positions(ledger: Ledger, prices_base: dict[int, Decimal | None]) -> dict:
    """Mark positions to market. ``prices_base`` are per-unit prices already in base currency.
    Positions with no price are reported with ``market_value=None`` rather than guessed."""
    rows = []
    total_mv = ZERO
    missing = []
    for sid, pos in ledger.positions.items():
        q = pos.quantity
        if q == 0:
            continue
        px = prices_base.get(sid)
        mv = q * px if px is not None else None
        upnl = (mv - pos.cost_basis) if mv is not None else None
        if mv is None:
            missing.append(sid)
        else:
            total_mv += mv
        rows.append(
            {
                "security_id": sid,
                "quantity": q,
                "avg_cost": pos.avg_cost,
                "cost_basis": pos.cost_basis,
                "price": px,
                "market_value": mv,
                "unrealized_pnl": upnl,
                "unrealized_pct": (upnl / pos.cost_basis) if (upnl is not None and pos.cost_basis) else None,
                "realized_pnl": pos.realized_pnl,
                "dividends": pos.dividends,
            }
        )
    equity = total_mv + ledger.cash_base
    return {
        "positions": rows,
        "market_value": total_mv,
        "cash": ledger.cash_base,
        "total_value": equity,
        "unpriced_security_ids": missing,
        "complete": not missing,
    }

"""SEC EDGAR adapter (official US regulatory filings; free, no key).

Fair-access policy: identify yourself with a descriptive User-Agent containing contact details and stay
below 10 requests/second (we throttle to ~8/s). Endpoints:
  https://www.sec.gov/files/company_tickers.json             ticker -> CIK map
  https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json   XBRL facts from filings
  https://data.sec.gov/submissions/CIK##########.json            filing index
"""
from __future__ import annotations

from datetime import date

from app.providers.base import Fact, FilingInfo, Provider, ProviderError, SecurityInfo, parse_date

# canonical metric -> ordered list of us-gaap tags (first one with data wins per period)
TAGS: dict[str, list[str]] = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "net_income": ["NetIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "d_and_a": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                "DepreciationAmortizationAndAccretionNet"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "inventory": ["InventoryNet"],
    "current_assets": ["AssetsCurrent"],
    "total_assets": ["Assets"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "short_term_debt": ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "total_liabilities": ["Liabilities"],
    "total_equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "shares_outstanding": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "dividends_paid": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
}
INSTANT = {"cash", "receivables", "inventory", "current_assets", "total_assets", "current_liabilities",
           "short_term_debt", "long_term_debt", "total_liabilities", "total_equity"}
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A", "10-KT"}


class SecEdgarProvider(Provider):
    name = "sec_edgar"
    title = "SEC EDGAR (official filings, XBRL company facts)"
    capabilities = {"search", "reference", "fundamentals", "filings"}
    quality = "reported"
    requires_key = False
    min_interval_seconds = 0.125
    homepage = "https://www.sec.gov/edgar/sec-api-documentation"
    licensing = "US government public data. Subject to SEC fair-access policy (declared User-Agent, <=10 req/s)."
    setup = ("Set SEC_USER_AGENT to 'YourName your-email@example.com' (required by SEC). Requires outbound access "
             "to www.sec.gov and data.sec.gov.")

    def __init__(self, user_agent: str | None, **kw):
        super().__init__(**kw)
        self.user_agent = user_agent
        self._ticker_map: dict[str, dict] | None = None

    def is_configured(self) -> bool:
        return bool(self.user_agent and "@" in self.user_agent)

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent or "", "Accept-Encoding": "gzip, deflate"}

    def ticker_map(self) -> dict[str, dict]:
        if self._ticker_map is None:
            raw = self._get("https://www.sec.gov/files/company_tickers.json", headers=self._headers())
            if not isinstance(raw, dict):
                raise ProviderError("sec_edgar: unexpected ticker map format")
            self._ticker_map = {v["ticker"].upper(): v for v in raw.values()}
        return self._ticker_map

    def cik_for(self, symbol: str) -> str | None:
        row = self.ticker_map().get(symbol.upper().replace(".", "-"))
        return str(row["cik_str"]).zfill(10) if row else None

    def search(self, query: str) -> list[SecurityInfo]:
        q = query.upper()
        out = []
        for t, row in self.ticker_map().items():
            if t == q or q in row["title"].upper():
                out.append(SecurityInfo(t, row["title"], "US", "equity", "US", "USD", cik=str(row["cik_str"]).zfill(10)))
            if len(out) >= 15:
                break
        out.sort(key=lambda s: (s.symbol != q, len(s.symbol)))
        return out

    def security_info(self, symbol: str) -> SecurityInfo | None:
        row = self.ticker_map().get(symbol.upper())
        if not row:
            return None
        cik = str(row["cik_str"]).zfill(10)
        sub = self._get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=self._headers())
        exch = (sub.get("exchanges") or [""])[0] or "US"
        return SecurityInfo(symbol.upper(), sub.get("name") or row["title"], exch, "equity", "US", "USD",
                            industry=sub.get("sicDescription"), cik=cik,
                            meta={"sic": sub.get("sic"), "fiscal_year_end": sub.get("fiscalYearEnd")})

    def fundamentals(self, symbol: str, cik: str | None = None) -> list[Fact]:
        cik = cik or self.cik_for(symbol)
        if not cik:
            return []
        data = self._get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", headers=self._headers())
        return parse_company_facts(data)

    def filings(self, symbol: str, cik: str | None = None, limit: int = 40) -> list[FilingInfo]:
        cik = cik or self.cik_for(symbol)
        if not cik:
            return []
        sub = self._get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=self._headers())
        return parse_submissions(sub, int(cik), limit)


def parse_company_facts(data: dict) -> list[Fact]:
    """Extract annual (FY) facts. Flow items require a ~1-year duration; restatements: latest filing wins."""
    gaap = (data.get("facts") or {}).get("us-gaap") or {}
    out: list[Fact] = []
    for metric, tags in TAGS.items():
        chosen: dict[date, Fact] = {}
        for tag in tags:
            node = gaap.get(tag)
            if not node:
                continue
            units = node.get("units") or {}
            unit_key = "USD/shares" if metric == "eps_diluted" else ("shares" if metric == "shares_outstanding" else "USD")
            rows = units.get(unit_key) or []
            for r in rows:
                if r.get("form") not in ANNUAL_FORMS or r.get("val") is None or not r.get("end"):
                    continue
                end = parse_date(r["end"])
                if metric not in INSTANT:
                    if not r.get("start"):
                        continue
                    dur = (end - parse_date(r["start"])).days
                    if not 340 <= dur <= 380:
                        continue
                filed = parse_date(r["filed"]) if r.get("filed") else None
                prev = chosen.get(end)
                if prev is not None and prev.source_ref and not prev.source_ref.startswith(tag + "|"):
                    continue  # an earlier (preferred) tag already supplied this period
                if prev is None or (filed and prev.filed_date and filed > prev.filed_date):
                    chosen[end] = Fact(metric, end, float(r["val"]), r.get("fy"), "FY", unit_key,
                                       f"{tag}|{r.get('accn', '')}", filed, "reported")
        out.extend(chosen.values())
    # Derived: total liabilities when not reported directly but L&SE and equity are (labelled 'derived').
    have = {(f.metric, f.period_end) for f in out}
    lse = (gaap.get("LiabilitiesAndStockholdersEquity") or {}).get("units", {}).get("USD", [])
    eq = {f.period_end: f for f in out if f.metric == "total_equity"}
    for r in lse:
        if r.get("form") not in ANNUAL_FORMS or not r.get("end"):
            continue
        end = parse_date(r["end"])
        if ("total_liabilities", end) in have or end not in eq:
            continue
        out.append(Fact("total_liabilities", end, float(r["val"]) - eq[end].value, r.get("fy"), "FY", "USD",
                        "derived:LiabilitiesAndStockholdersEquity-StockholdersEquity",
                        parse_date(r["filed"]) if r.get("filed") else None, "derived"))
        have.add(("total_liabilities", end))
    return out


def parse_submissions(sub: dict, cik_int: int, limit: int) -> list[FilingInfo]:
    rec = (sub.get("filings") or {}).get("recent") or {}
    forms = rec.get("form") or []
    out = []
    for i, form in enumerate(forms):
        if form not in {"10-K", "10-Q", "8-K", "20-F", "6-K", "40-F", "10-K/A", "10-Q/A", "DEF 14A", "S-1"}:
            continue
        acc = rec["accessionNumber"][i]
        doc = (rec.get("primaryDocument") or [""] * len(forms))[i]
        url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc.replace('-', '')}/{doc}" if doc else None
        rd = (rec.get("reportDate") or [""] * len(forms))[i]
        out.append(FilingInfo(form, parse_date(rec["filingDate"][i]), acc, parse_date(rd) if rd else None, url,
                              (rec.get("primaryDocDescription") or [""] * len(forms))[i] or None))
        if len(out) >= limit:
            break
    return out

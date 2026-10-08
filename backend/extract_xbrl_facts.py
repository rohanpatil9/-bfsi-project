"""
SEC XBRL financial fact extractor for the SEC Financial Analyst Copilot.

Purpose
-------
Build a reliable, period-aware financial_facts_final table from SEC's
Company Facts API instead of trying to infer financial values from PDF
column positions.

The PDF remains the evidence/citation source. SEC XBRL is the numerical
source of truth for the structured financial facts.

Usage
-----
1. Add to backend/.env:
       SEC_USER_AGENT=Your Name your.email@example.com

2. Create the table:
       Get-Content .\backend\sql\financial_facts_final.sql -Raw |
           bq query --use_legacy_sql=false

3. Run:
       python .\backend\extract_xbrl_facts.py

The extractor automatically finds the 10-Q accession matching each target
period from the SEC submissions API, then reads SEC Company Facts and keeps
the 3-month quarterly facts for Q1/Q2/Q3. This avoids the Q2/Q3 6-month and
9-month column ambiguity that affected the PDF extractor.

SEC API documentation:
https://www.sec.gov/search-filings/edgar-application-programming-interfaces
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from google.cloud import bigquery


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "backend" / ".env")

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "bfsi-project-510910")
DATASET = os.getenv("BQ_DATASET", "sec_analyst")
TABLE = "financial_facts_final"

SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "").strip()

if not SEC_USER_AGENT:
    raise RuntimeError(
        "SEC_USER_AGENT is not configured. Add this to backend/.env:\n"
        "SEC_USER_AGENT=Your Name your.email@example.com"
    )

COMPANIES: Dict[str, Dict[str, str]] = {
    "amazon": {"cik": "0001018724"},
    "alphabet": {"cik": "0001652044"},
    "meta": {"cik": "0001326801"},
    "nvidia": {"cik": "0001045810"},
}

# Fiscal quarter end dates used by the 12 PDFs already downloaded.
# NVIDIA uses a non-calendar fiscal year.
TARGET_FILINGS: Dict[str, List[Tuple[int, str, str]]] = {
    "amazon": [
        (2025, "Q1", "2025-03-31"),
        (2025, "Q2", "2025-06-30"),
        (2025, "Q3", "2025-09-30"),
    ],
    "alphabet": [
        (2025, "Q1", "2025-03-31"),
        (2025, "Q2", "2025-06-30"),
        (2025, "Q3", "2025-09-30"),
    ],
    "meta": [
        (2025, "Q1", "2025-03-31"),
        (2025, "Q2", "2025-06-30"),
        (2025, "Q3", "2025-09-30"),
    ],
    "nvidia": [
        (2026, "Q1", "2025-04-27"),
        (2026, "Q2", "2025-07-27"),
        (2026, "Q3", "2025-10-26"),
    ],
}

# Core metrics needed for the analyst-copilot use cases.
# The aliases are standard us-gaap tags seen across issuers.
METRIC_ALIASES: Dict[str, List[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "research_and_development": [
        "ResearchAndDevelopmentExpense",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
    ],
    "cost_of_revenue": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
    ],
    "selling_general_and_administrative": [
        "SellingGeneralAndAdministrativeExpense",
    ],
    "assets": [
        "Assets",
    ],
    "liabilities": [
        "Liabilities",
    ],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
    ],
    "stockholders_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
}


# ---------------------------------------------------------------------------
# SEC HTTP client
# ---------------------------------------------------------------------------

class SECClient:
    BASE = "https://data.sec.gov"
    ARCHIVES = "https://www.sec.gov/Archives/edgar/data"

    def __init__(self, user_agent: str):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Host": "data.sec.gov",
            }
        )

    def get_json(self, url: str) -> Dict[str, Any]:
        # Be polite to SEC infrastructure.
        time.sleep(0.25)

        response = self.session.get(url, timeout=45)
        response.raise_for_status()
        return response.json()

    def submissions(self, cik: str) -> Dict[str, Any]:
        return self.get_json(
            f"{self.BASE}/submissions/CIK{cik}.json"
        )

    def companyfacts(self, cik: str) -> Dict[str, Any]:
        return self.get_json(
            f"{self.BASE}/api/xbrl/companyfacts/CIK{cik}.json"
        )


# ---------------------------------------------------------------------------
# SEC filing discovery
# ---------------------------------------------------------------------------

def find_target_10q(
    submissions: Dict[str, Any],
    target_end: str,
    fiscal_quarter: str,
) -> Dict[str, str]:
    """
    Find the 10-Q whose reportDate equals the requested fiscal period end.

    Returns:
        accession, accession_no_dash, filing_date, report_date, primary_document
    """
    recent = submissions.get("filings", {}).get("recent", {})

    fields = [
        "form",
        "filingDate",
        "reportDate",
        "accessionNumber",
        "primaryDocument",
        "primaryDocDescription",
        "isXBRL",
    ]

    rows = []
    for field in fields:
        rows.append(recent.get(field, []))

    if not rows or not rows[0]:
        raise RuntimeError("SEC submissions response contains no recent filings.")

    records = [
        dict(zip(fields, values))
        for values in zip(*rows)
    ]

    candidates = [
        r
        for r in records
        if r["form"] == "10-Q"
        and r["reportDate"] == target_end
    ]

    if not candidates:
        # Some issuers may have a report-date representation that differs
        # slightly. Give a useful diagnostic rather than silently selecting
        # the wrong filing.
        nearby = [
            (
                r["form"],
                r["filingDate"],
                r["reportDate"],
                r["accessionNumber"],
                r["primaryDocument"],
            )
            for r in records
            if r["form"] == "10-Q"
        ][:20]

        raise RuntimeError(
            f"Could not find a 10-Q for {fiscal_quarter} ending {target_end}. "
            f"Recent 10-Qs seen: {nearby}"
        )

    # Prefer the XBRL filing if several candidates exist.
    candidates.sort(
        key=lambda r: (
            str(r.get("isXBRL", "0")) == "1",
            r["filingDate"],
        ),
        reverse=True,
    )

    selected = candidates[0]
    accession = selected["accessionNumber"]
    return {
        "accession": accession,
        "accession_no_dash": accession.replace("-", ""),
        "filing_date": selected["filingDate"],
        "report_date": selected["reportDate"],
        "primary_document": selected["primaryDocument"],
        "is_xbrl": str(selected.get("isXBRL", "")),
    }


# ---------------------------------------------------------------------------
# XBRL fact selection
# ---------------------------------------------------------------------------

def iso_days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def duration_months_from_days(days: int) -> Optional[int]:
    """
    SEC quarterly filings commonly use approximately:
      3 months = ~80-110 days
      6 months = ~170-200 days
      9 months = ~260-290 days
    """
    if 80 <= days <= 110:
        return 3
    if 170 <= days <= 200:
        return 6
    if 260 <= days <= 290:
        return 9
    if 350 <= days <= 380:
        return 12
    return None


def unit_to_display(unit: str, value: float) -> Tuple[str, float]:
    """
    Normalize USD values to USD millions because SEC financial statements
    typically present these statements in millions.

    Other units are retained as-is.
    """
    if unit == "USD":
        return "USD millions", value / 1_000_000.0

    if unit == "USD/shares":
        return "USD/share", value

    if unit == "shares":
        return "shares", value

    if unit == "pure":
        return "pure", value

    return unit, value


def iter_concept_facts(
    companyfacts: Dict[str, Any],
    concept: str,
) -> Iterable[Tuple[str, Dict[str, Any]]]:
    """
    Yield (unit, fact) for a us-gaap concept.
    """
    facts = companyfacts.get("facts", {})
    us_gaap = facts.get("us-gaap", {})
    concept_data = us_gaap.get(concept)

    if not concept_data:
        return

    units = concept_data.get("units", {})
    for unit_name, fact_rows in units.items():
        for fact in fact_rows:
            yield unit_name, fact


def choose_facts(
    companyfacts: Dict[str, Any],
    metric: str,
    target_end: str,
    accession: str,
    fiscal_year: int,
    fiscal_quarter: str,
) -> List[Dict[str, Any]]:
    """
    Return the current-quarter and prior-year-quarter XBRL facts for a metric.

    Important:
    - Q2 filings contain both 3-month and 6-month facts.
    - Q3 filings contain both 3-month and 9-month facts.
    - We keep ONLY ~3-month facts here.
    - We select facts by the filing accession, so the current filing's
      comparative prior-year values can also be captured.
    """
    target_date = date.fromisoformat(target_end)
    concepts = METRIC_ALIASES[metric]

    for concept in concepts:
        candidates: List[Tuple[str, Dict[str, Any], int]] = []

        for unit_name, fact in iter_concept_facts(companyfacts, concept):
            if fact.get("form") != "10-Q":
                continue

            if fact.get("accn") != accession:
                continue

            fact_fp = str(fact.get("fp", "")).upper()
            if fact_fp != fiscal_quarter:
                continue

            end = fact.get("end")
            start = fact.get("start")

            if not end:
                continue

            # Balance sheet facts are instantaneous.
            if not start:
                # Only use the current period-end for balance sheet metrics.
                if end != target_end:
                    continue

                candidates.append((unit_name, fact, 0))
                continue

            try:
                days = iso_days(start, end)
            except Exception:
                continue

            # Keep quarterly duration only.
            if duration_months_from_days(days) != 3:
                continue

            end_date = date.fromisoformat(end)
            distance_from_current = abs((end_date - target_date).days)

            # Keep the current quarter and the comparative prior-year quarter.
            # A fiscal quarter can shift by a few days (notably NVIDIA).
            if distance_from_current > 370:
                continue

            candidates.append((unit_name, fact, days))

        if not candidates:
            continue

        # Prefer USD for monetary metrics.
        candidates.sort(
            key=lambda item: (
                item[0] == "USD",
                item[1].get("filed", ""),
            ),
            reverse=True,
        )

        # Deduplicate by end date. If multiple facts exist for the same
        # end date, retain the first preferred unit/concept.
        selected: List[Dict[str, Any]] = []
        seen_ends = set()

        for unit_name, fact, days in candidates:
            end = fact["end"]
            if end in seen_ends:
                continue

            seen_ends.add(end)
            selected.append(
                {
                    "concept": concept,
                    "unit_name": unit_name,
                    "fact": fact,
                    "duration_months": (
                        3 if fact.get("start") else None
                    ),
                }
            )

        # We expect current + comparative for P&L metrics. Sort chronologically.
        selected.sort(key=lambda item: item["fact"]["end"])

        return selected

    return []


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def period_label(
    fiscal_year: int,
    fiscal_quarter: str,
) -> str:
    return f"{fiscal_year}-{fiscal_quarter}"


def prior_year_period(
    fiscal_year: int,
    fiscal_quarter: str,
) -> str:
    return f"{fiscal_year - 1}-{fiscal_quarter}"


def make_rows(
    company: str,
    fiscal_year: int,
    fiscal_quarter: str,
    target_end: str,
    metric: str,
    selected_facts: List[Dict[str, Any]],
    source_file: str,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    target_date = date.fromisoformat(target_end)

    for selected in selected_facts:
        fact = selected["fact"]
        unit_name = selected["unit_name"]

        raw_value = float(fact["val"])
        display_unit, display_value = unit_to_display(unit_name, raw_value)

        fact_end = date.fromisoformat(fact["end"])

        if fact_end == target_date:
            period = period_label(fiscal_year, fiscal_quarter)
        else:
            # The comparison fact belongs to the same fiscal quarter in the
            # prior year. This also handles NVIDIA's shifted fiscal dates.
            period = period_label(fiscal_year - 1, fiscal_quarter)

        rows.append(
            {
                "company": company,
                "fiscal_year": fiscal_year,
                "fiscal_quarter": fiscal_quarter,
                "period_end": fact["end"],
                "metric": metric,
                "source_label": metric.replace("_", " ").title(),
                "period": period,
                "duration_months": selected["duration_months"],
                "value": display_value,
                "unit": display_unit,
                "pdf_page": None,
                "section": None,
                "table_name": None,
                "source_file": source_file,
                "validation_source": "SEC_XBRL",
                "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            }
        )

    return rows


# ---------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------

def table_id() -> str:
    return f"{PROJECT_ID}.{DATASET}.{TABLE}"


def ensure_table(client: bigquery.Client) -> None:
    sql = f"""
    CREATE TABLE IF NOT EXISTS `{table_id()}`
    (
      company STRING,
      fiscal_year INT64,
      fiscal_quarter STRING,
      period_end DATE,

      metric STRING,
      source_label STRING,

      period STRING,
      duration_months INT64,

      value FLOAT64,
      unit STRING,

      pdf_page INT64,
      section STRING,
      table_name STRING,

      source_file STRING,

      validation_source STRING,

      created_at TIMESTAMP
    )
    """
    client.query(sql).result()


def insert_rows(client: bigquery.Client, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return

    errors = client.insert_rows_json(
        table_id(),
        rows,
        skip_invalid_rows=False,
        ignore_unknown_values=False,
    )

    if errors:
        raise RuntimeError(f"BigQuery insert errors: {errors}")


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

def extract() -> None:
    sec = SECClient(SEC_USER_AGENT)
    bq = bigquery.Client(project=PROJECT_ID)

    ensure_table(bq)

    # We rebuild this final table on each explicit extraction run.
    bq.query(f"TRUNCATE TABLE `{table_id()}`").result()

    all_rows: List[Dict[str, Any]] = []
    filing_count = 0

    for company, config in COMPANIES.items():
        cik = config["cik"]

        print(f"\nLoading SEC submissions: {company}")
        submissions = sec.submissions(cik)

        print(f"Loading SEC Company Facts: {company}")
        companyfacts = sec.companyfacts(cik)

        for fiscal_year, fiscal_quarter, target_end in TARGET_FILINGS[company]:
            filing = find_target_10q(
                submissions,
                target_end,
                fiscal_quarter,
            )

            accession = filing["accession"]
            source_file = (
                f"FY{fiscal_year}_{fiscal_quarter}_10Q.pdf"
            )

            filing_count += 1

            print(
                f"  {company.upper()} {fiscal_quarter} "
                f"FY{fiscal_year} | {target_end} | "
                f"{accession}"
            )

            for metric in METRIC_ALIASES:
                selected_facts = choose_facts(
                    companyfacts=companyfacts,
                    metric=metric,
                    target_end=target_end,
                    accession=accession,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                )

                if not selected_facts:
                    print(f"      WARNING: no XBRL fact for {metric}")
                    continue

                rows = make_rows(
                    company=company,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                    target_end=target_end,
                    metric=metric,
                    selected_facts=selected_facts,
                    source_file=source_file,
                )

                all_rows.extend(rows)

                for row in rows:
                    print(
                        f"      {metric}: "
                        f"{row['period']} = "
                        f"{row['value']} {row['unit']} "
                        f"({row['duration_months']} months)"
                    )

    insert_rows(bq, all_rows)

    print("\n" + "=" * 70)
    print("XBRL FINANCIAL FACT INGESTION COMPLETE")
    print("=" * 70)
    print(f"Filings processed : {filing_count}")
    print(f"Facts inserted    : {len(all_rows)}")
    print(f"BigQuery table    : {table_id()}")
    print("=" * 70)


if __name__ == "__main__":
    try:
        extract()
    except Exception as exc:
        print("\nXBRL EXTRACTION FAILED")
        print(f"{type(exc).__name__}: {exc}")
        sys.exit(1)

#!/usr/bin/env python
"""HomeCommand Capstone — Cost Analysis model.

Every input below is either (a) read off a vendor invoice, (b) queried read-only
from the production database, (c) measured by direct experiment, or (d) supplied
by the operator and labeled as such. Nothing is estimated silently: each
constant carries its provenance, and anything not yet known is ``None`` and
surfaces in the output as a named gap rather than a plausible-looking number.

Run:  python scripts/capstone_cost_model.py         # model only, no database access
      python scripts/capstone_cost_model.py --db    # additionally re-query production

The --db path issues SELECT statements on a read-only session and writes nothing.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Foreign exchange
# ---------------------------------------------------------------------------
# European Central Bank euro reference rate, 21 August 2026 (the most recent
# publication before this model was built). Used for every EUR->USD conversion
# so the report has one rate rather than several.
EUR_USD = 1.1699
EUR_USD_DATE = "2026-08-21"

# ECB reference rates bracketing 9 August 2026, which fell on a weekend and so
# has no rate of its own. Friday 7 Aug = 1.1535, Monday 10 Aug = 1.1555.
EUR_USD_AUG_09 = 1.1555
EUR_USD_AUG_09_BASIS = "ECB reference rate for Mon 10 Aug 2026; 9 Aug was a weekend"


def eur(amount: float, rate: float = EUR_USD) -> float:
    """Convert euros to US dollars at a stated rate."""
    return amount * rate


# ---------------------------------------------------------------------------
# (a) Fixed platform cost — read off invoices
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CostLine:
    name: str
    monthly_usd: Optional[float]
    basis: str


PLATFORM_FIXED = [
    CostLine("Supabase Pro (production + staging)", 35.00,
             "invoice 11, 16 Jul - 15 Aug 2026, read directly"),
    CostLine("Railway Hobby (production + staging)", 9.75,
             "invoice 7, 28 Jun - 28 Jul 2026, read directly"),
    CostLine("Cloudflare Pages", 0.00,
             "free tier; no invoice is generated"),
    CostLine("Postmark (transactional email)", 0.00,
             "free tier, operator-confirmed 2026-08-24"),
    CostLine("Domain homecommand.ai", 82.70 / 12,
             "operator-supplied: $82.70 per year, amortized"),
    # Add further platform lines here as they are discovered. A line with
    # monthly_usd=None is reported as an explicit gap, never as zero.
]

# ---------------------------------------------------------------------------
# (b) Build and team overhead — NOT part of unit economics
# ---------------------------------------------------------------------------
# Kept separate because none of these scale with the number of customers, and
# because the Gemini invoices demonstrably measure development rather than
# production inference (see BUILD_SPEND_NOTE).
BUILD_OVERHEAD = [
    CostLine("Claude Code Max 20x", eur(180.00),
             f"operator-supplied: EUR 180.00/month at {EUR_USD} ({EUR_USD_DATE})"),
    CostLine("Google Workspace (2 users)", 14.00,
             "operator-supplied: $14.00/month"),
    CostLine("Google Cloud / Gemini API", eur(249.77 / 8),
             "8 statements Dec 2025 - Jul 2026 totaling EUR 249.77; monthly mean"),
]

BUILD_SPEND_NOTE = (
    "The Gemini line is development-dominated and cannot presently be split. "
    "Pearson r between documents persisted and monthly spend is 0.313 across the "
    "eight statements, and December 2025 cost EUR 21.50 in a month when no "
    "document was uploaded. A production-only billing project is what would "
    "separate the two."
)

# ---------------------------------------------------------------------------
# (c) Revenue — verified against the live Stripe account, 2026-08-24
# ---------------------------------------------------------------------------
PRICE_MONTHLY_USD = 15.00     # price_1TqfCuBJeSr0PHlRmOdacZ6S, lookup pro_property_monthly
PRICE_ANNUAL_USD = 100.00     # price_1TqfDdBJeSr0PHlR4h5nkaqm, lookup pro_property_annual

# ---------------------------------------------------------------------------
# (d) Payment processing — published US schedule, NOT read off a settled charge
# ---------------------------------------------------------------------------
# The live account has zero charges, so no realized fee exists to read. The
# operator confirmed the US schedule applies (HomeCommand, Inc., USD prices,
# incorporated through Stripe Atlas). Treat as an assumption, not a measurement.
CARD_RATE = 0.029
CARD_FIXED_USD = 0.30
BILLING_RATE = 0.007          # Stripe Billing surcharge on recurring volume

# Verified present in the live account on 2026-08-24 as an available fee credit.
ATLAS_FEE_CREDIT_USD = 2500.00

# ---------------------------------------------------------------------------
# (e) Marginal AI cost
# ---------------------------------------------------------------------------
# Metered floor, production llm_cost_events. Re-based 2026-08-27 on the report's
# single source of truth: the corpus with real customers excluded (the one customer
# document, uploaded 2026-08-17, carried 3 events / $0.32 that the 2026-08-24 figures
# 615 / 27 / $11.9755 / $0.4435 / $0.2732 silently included). The same numbers are
# published in the capstone evidence set as EDA/figures_data/cost_events.csv.
METERED_EVENTS = 612
METERED_DOCUMENTS = 26
METERED_TOTAL_USD = 11.6555
METERED_MEAN_USD = 0.4483
METERED_MEDIAN_USD = 0.2216

# Recorded token totals by model, needed to apply the measured thinking ratios.
METERED_PRO_IN, METERED_PRO_OUT = 4_688_322, 266_353
METERED_FLASH_IN, METERED_FLASH_OUT = 2_724_488, 950_667

# Gemini list prices per 1M tokens, re-verified against the official pricing
# page on 2026-08-24 and matching backend/analysis/cost_tracking.py exactly.
PRICE = {
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
}

# Measured by direct experiment on 2026-08-24: one 26-page public inspection
# report through the real Call 3 extraction, both production configurations.
# thinking_ratio = (answer + thinking) / answer, i.e. the factor by which
# billable output exceeds what the instrumentation recorded before PR #984.
# The experiment was run twice, independently, on the same document. Both runs
# are kept: the mean is what the model uses, and the spread is what the report
# quotes, because a single run of a non-deterministic model is not a measurement.
MEASURED_THINKING_RUNS = {
    # model: [(answer_tokens, thinking_tokens), ...]
    "gemini-2.5-flash": [(17_621, 8_190), (15_061, 8_188)],  # cap 8,192 — binds both times
    "gemini-2.5-pro":   [(12_498, 12_432), (13_436, 11_495)],  # uncapped
}
# Input tokens for the same calls, captured from the in-memory cost tracker
# (they never reach stream_meta). Run 2 only; run 1 did not capture them.
MEASURED_INPUT_TOKENS = {"gemini-2.5-flash": 21_985, "gemini-2.5-pro": 22_027}

# The external anchor: an operator reading of the Google Cloud console daily
# cost report for 9 August 2026, against what the table recorded that day.
INVOICE_ANCHOR_EUR = 63.97
RECORDED_ON_ANCHOR_DAY_USD = 10.1476

# The least-evidenced input in the whole model.
DOCS_PER_HOME_ASSUMED = 3.1   # the figure the earlier model used
MODELED_ONBOARDING_USD = 1.10


def ratios(model: str) -> list:
    """Per-run output-token ratio: (answer + thinking) / answer."""
    return [(a + t) / a for a, t in MEASURED_THINKING_RUNS[model]]


def ratio(model: str) -> float:
    """Mean output-token ratio across runs. Applied to the OUTPUT term only —
    thinking bills at the output rate and does not touch input."""
    r = ratios(model)
    return sum(r) / len(r)


def money(x: Optional[float], width: int = 10) -> str:
    return "        --" if x is None else f"${x:>{width - 1},.2f}"


def rule(char: str = "-", n: int = 78) -> None:
    print(char * n)


def section(title: str) -> None:
    print()
    rule("=")
    print(title)
    rule("=")


def main(use_db: bool) -> None:
    section("1. FIXED PLATFORM COST (cost to run the deployed product)")
    total_fixed = 0.0
    gaps = []
    for line in PLATFORM_FIXED:
        print(f"  {line.name:<42}{money(line.monthly_usd)}   {line.basis}")
        if line.monthly_usd is None:
            gaps.append(line.name)
        else:
            total_fixed += line.monthly_usd
    rule()
    print(f"  {'TOTAL fixed platform cost, monthly':<42}{money(total_fixed)}")
    if gaps:
        print(f"  UNPRICED LINES: {', '.join(gaps)}")

    section("2. BUILD AND TEAM OVERHEAD (excluded from unit economics)")
    total_build = 0.0
    for line in BUILD_OVERHEAD:
        print(f"  {line.name:<42}{money(line.monthly_usd)}   {line.basis}")
        total_build += line.monthly_usd or 0.0
    rule()
    print(f"  {'TOTAL build overhead, monthly':<42}{money(total_build)}")
    print(f"\n  Note: {BUILD_SPEND_NOTE}")

    section("3. THINKING TOKENS — measured, and what they do to the floor")
    for model, runs_ in MEASURED_THINKING_RUNS.items():
        rs = ratios(model)
        for i, ((answer, thinking), r) in enumerate(zip(runs_, rs), 1):
            print(f"  {model:<20} run {i}  answer {answer:>8,}  "
                  f"thinking {thinking:>8,}  output ratio {r:.3f}x")
        print(f"  {'':<20} mean {ratio(model):.3f}x   "
              f"range {min(rs):.3f}-{max(rs):.3f}x")
        print()
    print("\n  Applying those ratios to the recorded token totals:")
    corrected = 0.0
    for label, (tin, tout, model) in {
        "Pro": (METERED_PRO_IN, METERED_PRO_OUT, "gemini-2.5-pro"),
        "Flash": (METERED_FLASH_IN, METERED_FLASH_OUT, "gemini-2.5-flash"),
    }.items():
        p = PRICE[model]
        as_recorded = tin / 1e6 * p["input"] + tout / 1e6 * p["output"]
        with_thinking = tin / 1e6 * p["input"] + tout * ratio(model) / 1e6 * p["output"]
        corrected += with_thinking
        print(f"    {label:<6} recorded {money(as_recorded)}   "
              f"thinking-corrected {money(with_thinking)}")
    rule()
    print(f"  Recorded total {money(METERED_TOTAL_USD)}  ->  "
          f"thinking-corrected {money(corrected)}  "
          f"({corrected / METERED_TOTAL_USD:.2f}x)")
    print(f"  Corrected floor per document: "
          f"{money(corrected / METERED_DOCUMENTS)} over {METERED_DOCUMENTS} documents")

    section("4. THE EXTERNAL ANCHOR — 9 August 2026")
    anchor_usd = INVOICE_ANCHOR_EUR * EUR_USD_AUG_09
    print(f"  Provider charge      EUR {INVOICE_ANCHOR_EUR:,.2f} = {money(anchor_usd)}")
    print(f"    conversion basis:  {EUR_USD_AUG_09_BASIS}")
    print(f"  Recorded that day    {money(RECORDED_ON_ANCHOR_DAY_USD)}")
    print(f"  Gap                  {anchor_usd / RECORDED_ON_ANCHOR_DAY_USD:.2f}x")
    print(f"  Uncorrected EUR-vs-USD comparison would read "
          f"{INVOICE_ANCHOR_EUR / RECORDED_ON_ANCHOR_DAY_USD:.2f}x -- currency-inconsistent.")
    share = (corrected - METERED_TOTAL_USD) / METERED_TOTAL_USD
    print(f"\n  Thinking alone raises recorded spend by {share * 100:.0f}%. "
          f"It therefore explains part of the\n  gap, not most of it: the missing "
          f"stages and the deleted re-uploads carry the rest.")

    section("5. UNIT ECONOMICS — both billing cadences")
    for label, price, charges, months in (
        ("Monthly $15.00", PRICE_MONTHLY_USD, 12, 12),
        ("Annual $100.00", PRICE_ANNUAL_USD, 1, 12),
    ):
        annual_revenue = price * charges
        fee = charges * (price * CARD_RATE + CARD_FIXED_USD) + annual_revenue * BILLING_RATE
        print(f"\n  {label} per property")
        print(f"    Revenue per property-year          {money(annual_revenue)}")
        print(f"    Payment processing ({charges} charge(s))  {money(-fee)}")
        print(f"    Net of processing                  {money(annual_revenue - fee)}")
        print(f"    Processing as % of revenue         {fee / annual_revenue * 100:>9.1f}%")

    section("6. BREAKEVEN ON FIXED PLATFORM COST")
    print(f"  Fixed platform cost, monthly: {money(total_fixed)}")
    for label, price, charges in (("Monthly", PRICE_MONTHLY_USD, 12),
                                  ("Annual", PRICE_ANNUAL_USD, 1)):
        annual_revenue = price * charges
        fee = charges * (price * CARD_RATE + CARD_FIXED_USD) + annual_revenue * BILLING_RATE
        net_monthly = (annual_revenue - fee) / 12
        n = total_fixed / net_monthly
        print(f"  {label:<8} net {money(net_monthly)}/property/month "
              f"-> breakeven at {n:.2f} properties, i.e. {int(n) + 1} paying properties")
    print("\n  Serving cost per property is NOT included above; it is the open"
          "\n  measurement named in section 7.")

    section("7. WHAT IS STILL NOT MEASURED")
    for item in (
        "Marginal AI cost per home. The metered floor covers inspection reports only "
        "(all 26 cost-bearing documents are that type) and omits 11 of 14 instrumented "
        "stages.",
        f"Documents per home. The model assumes {DOCS_PER_HOME_ASSUMED}; the production "
        "corpus today gives 71/41 = 1.73, or 1.97 across homes that hold a document.",
        "Founder time invested to date. Required for Capital Requirements.",
        "Customer acquisition cost. No paying customer exists, so no CAC exists.",
        "Stripe Atlas incorporation cost and other formation costs.",
        "Churn and conversion, which the Risk & Reward section needs.",
    ):
        print(f"  - {item}")

    if use_db:
        section("8. RE-QUERIED FROM PRODUCTION (read-only)")
        _requery()


def _requery() -> None:
    import psycopg2
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
    uri = os.environ.get("POSTGRES_URI")
    if not uri:
        raise SystemExit("POSTGRES_URI is not set - cannot re-query (this is read-only against production)")
    uri = uri.replace("postgresql+psycopg2://", "postgresql://")
    ref = uri.split("postgres.")[1].split(":")[0] if "postgres." in uri else "unknown"
    print(f"  project ref: {ref}")
    expected = os.environ.get("CAPSTONE_PRODUCTION_REF")
    if not expected:
        raise SystemExit(
            "CAPSTONE_PRODUCTION_REF is not set - refusing to re-query. The figures "
            "this model checks were measured against one specific database; without "
            "naming it, a run against any other would silently produce different numbers."
        )
    if ref != expected:
        # Same guard as capstone_heterogeneity.py: the figures this model checks were
        # measured on production; any other target would silently produce different
        # numbers and read as drift.
        raise SystemExit(f"refusing to run: POSTGRES_URI resolves to project {ref!r}, not production")
    conn = psycopg2.connect(uri)
    conn.set_session(readonly=True, autocommit=True)
    # Scope of the exclusion, exactly: ONLY the cost aggregate below is customer-excluded,
    # because it is the one figure the report quotes as the metered floor. The two counts
    # after it are deliberate WHOLE-CORPUS sanity checks and are NOT excluded - filtering
    # them would move numbers the report already cites (section 7's 71/41 = 1.73), so they
    # stay corpus-wide and say so in their labels. Do not paste a [CORPUS-WIDE] line into
    # report prose without re-filtering it first.
    # The ids come from the environment, never from a file (see EDA/eda_homecommand_v4.py).
    excluded = [s.strip() for s in os.environ.get("EDA_EXCLUDE_USER_IDS", "").split(",") if s.strip()]
    if not excluded:
        raise SystemExit("EDA_EXCLUDE_USER_IDS is not set - refusing to re-query with customers included")
    with conn.cursor() as cur:
        # Bound as an array (`!= ALL`) rather than interpolated into the SQL text. For a
        # NULL-free id list this is exactly equivalent to `not in (...)`, so no figure moves.
        for label, sql, params in (
            ("events / documents / total  [excluded]",
             "select count(*), count(distinct e.document_id), "
             "round(sum(e.total_cost_usd)::numeric,4) from public.llm_cost_events e "
             "left join raw.documents d on d.id::text = e.document_id::text "
             "where (e.user_id is null or e.user_id::text != ALL(%s)) "
             "and (d.id is null or d.user_id::text != ALL(%s))",
             (excluded, excluded)),
            ("distinct operation types  [CORPUS-WIDE]",
             "select count(distinct operation_type) from public.llm_cost_events",
             None),
            ("homes / documents  [CORPUS-WIDE]",
             "select (select count(*) from public.homes), "
             "(select count(*) from raw.documents)",
             None),
        ):
            cur.execute(sql, params)
            print(f"  {label:<42}{cur.fetchone()}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", action="store_true",
                    help="also re-query the production database, read-only")
    main(ap.parse_args().db)

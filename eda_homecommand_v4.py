"""
HomeCommand — Exploratory Data Analysis and criteria measurement set (v4)
========================================================================

One script, one read-only run, one basis. It writes:

  * the seven EDA figures and their aggregate tables (unchanged from v3), and
  * the measurement set every later section of the report cites — corpus counts,
    the technical success criteria T1–T6, the traceability replay, and the metered
    cost — so that the report has exactly one source of truth and a reader holding
    figures_data/ can check every number in it without a database.

BASIS
    The production corpus at the moment the script runs, with real customers
    excluded, de-duplicated on extracted text. The exclusion list is read from the
    EDA_EXCLUDE_USER_IDS environment variable (comma-separated) and is never written
    to a file; with the variable unset the script refuses to run. The run date and
    the basis are written to figures_data/measurement.csv.

De-duplication rule (unchanged since v2)
    Two document records are the SAME document if their distinct extracted text is
    identical. For each distinct document we keep one representative: the extraction
    run that produced the most entities (issues + equipment). Run-to-run variance is
    reported separately in Figure 2 rather than averaged away.

SAFETY
    Read-only. The session is opened read-only at the connection level. No file
    containing property data is written to disk — only aggregates. Documents appear
    in the tables as D01… labels. Credentials come from POSTGRES_URI and are never
    printed. Document text is held in memory only, for the de-duplication hash and
    for the traceability replay.

USAGE
    export POSTGRES_URI="postgresql://..."       # or set it in a local .env
    export EDA_EXCLUDE_USER_IDS="<uuid>[,<uuid>]" # real customers; never committed
    export EDA_OUT="/path/to/output"             # optional; defaults to this folder
    python eda_homecommand_v4.py

OUTPUT (figures_data/)
    fig01_corpus.csv … fig07_rag.csv      per-figure aggregates (as v3)
    measurement.csv                       run date, basis, script version
    corpus_counts.csv                     records / active / text / distinct / hashes / status
    criteria_t1_completion.csv            both bases, numerator and denominator
    criteria_t2_latency.csv               blocking-phase runs by upload route
    criteria_t3_yield.csv                 per distinct inspection report (D-labels) + summary
    criteria_t4_cost_coverage.csv         priced issues over the de-duplicated corpus
    criteria_t5_retrieval.csv             unembedded canonical rows by entity type
    criteria_t6_traceability.csv          the viewer's matcher replayed, by population
    criteria_t6_by_document.csv           per-document resolve rates (issue evidence)
    cost_events.csv                       metered cost, the 9 August anchor day, the
                                          reasoning-token correction
"""

from __future__ import annotations

import collections
import csv
import datetime as dt
import hashlib
import itertools
import os
import re
import statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import psycopg2

SCRIPT_VERSION = "v4"
OUT = Path(os.environ.get("EDA_OUT") or Path(__file__).parent)
OUT.mkdir(parents=True, exist_ok=True)
DATA = OUT / "figures_data"
DATA.mkdir(exist_ok=True)

INK = "#1f2430"
ACCENT = "#5142F0"
SEV_COLORS = {"Low": "#9aa4b2", "Medium": "#f0b429", "High": "#e8590c", "Critical": "#c92a2a"}
SEV_ORDER = ["Low", "Medium", "High", "Critical"]

plt.rcParams.update({
    "figure.dpi": 150, "savefig.dpi": 150, "font.size": 9,
    "axes.edgecolor": "#d0d5dd", "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": INK, "ytick.color": INK, "axes.grid": True,
    "grid.color": "#eceff3", "grid.linewidth": 0.8, "axes.axisbelow": True,
})


def excluded_users() -> tuple[str, ...]:
    raw = os.environ.get("EDA_EXCLUDE_USER_IDS", "")
    ids = tuple(s.strip() for s in raw.split(",") if s.strip())
    if not ids:
        raise SystemExit(
            "EDA_EXCLUDE_USER_IDS is not set. The corpus contains real customers; "
            "refusing to run without the exclusion list."
        )
    return ids


EXCLUDED = excluded_users()
RUN_DATE = dt.date.today().isoformat()


def connect():
    uri = os.environ.get("POSTGRES_URI")
    if not uri:
        try:
            from dotenv import load_dotenv, find_dotenv
            load_dotenv(find_dotenv())
            uri = os.environ.get("POSTGRES_URI")
        except ImportError:
            pass
    if not uri:
        raise SystemExit("POSTGRES_URI is not set.")
    conn = psycopg2.connect(uri)
    conn.set_session(readonly=True, autocommit=True)
    return conn


CONN = connect()


def q(sql, params=None):
    with CONN.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def save(fig, name):
    fig.tight_layout()
    fig.savefig(OUT / name, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  wrote {name}")


def write_csv(name, header, rows):
    with open(DATA / name, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    print(f"  wrote figures_data/{name}")


def label_bars(ax, bars, fmt="{:,.0f}"):
    for b in bars:
        h = b.get_height()
        if h:
            ax.text(b.get_x() + b.get_width() / 2, h, fmt.format(h),
                    ha="center", va="bottom", fontsize=8)


def pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


# Documents that are customers' are excluded at the SQL level everywhere below.
NOT_CUSTOMER = "d.user_id::text NOT IN %s"

# ---------------------------------------------------------------------------
# Step 0 - build the de-duplicated corpus (customers excluded)
# ---------------------------------------------------------------------------
print(f"Building de-duplicated corpus ({len(EXCLUDED)} customer account(s) excluded)...")

groups: dict[str, list] = collections.defaultdict(list)
text_rows = q(f"""
    SELECT s.document_id,
           COALESCE(d.doc_type::text, 'unclassified (legacy)'),
           d.created_at, d.bulk_batch_id IS NOT NULL, d.processing_status::text,
           d.content_hash,
           string_agg(DISTINCT s.content, '||')
    FROM document_sections s
    JOIN raw.documents d ON d.id = s.document_id
    WHERE d.archived_at IS NULL AND {NOT_CUSTOMER}
    GROUP BY 1, 2, 3, 4, 5, 6
""", (EXCLUDED,))
for row in text_rows:
    key = hashlib.sha256((row[6] or "").encode()).hexdigest()[:16]
    groups[key].append(row)

reps, replication = [], []
for key, docs in groups.items():
    ids = tuple(d[0] for d in docs)
    best = q("""
        SELECT d.id, COALESCE(d.doc_type::text,'unclassified (legacy)'),
               (SELECT COUNT(*) FROM home_issues i
                 WHERE i.source_document_id = d.id AND i.archived_at IS NULL)
             + (SELECT COUNT(*) FROM home_equipment e
                 WHERE e.source_document_id = d.id AND e.archived_at IS NULL)
        FROM raw.documents d WHERE d.id IN %s ORDER BY 3 DESC LIMIT 1
    """, (ids,))[0]
    reps.append(best)
    replication.append((best[1], len(docs)))

REP_IDS = tuple(r[0] for r in reps)
n_records = q(f"""SELECT COUNT(*) FROM raw.documents d
                 WHERE d.archived_at IS NULL AND {NOT_CUSTOMER}""", (EXCLUDED,))[0][0]
print(f"  {n_records} active records -> {len(reps)} distinct documents")

# ---------------------------------------------------------------------------
# Figure 1 - corpus composition, distinct vs replicated
# ---------------------------------------------------------------------------
print("Figure 1: document corpus")
by_type = collections.Counter(t for t, _ in replication)
rep_by_type = collections.defaultdict(int)
for t, n in replication:
    rep_by_type[t] += n

types = sorted(by_type, key=lambda t: -by_type[t])
x = range(len(types))
fig, ax = plt.subplots(figsize=(8, 4.2))
b1 = ax.bar([i - 0.2 for i in x], [by_type[t] for t in types], 0.4,
            label="distinct documents", color=ACCENT)
b2 = ax.bar([i + 0.2 for i in x], [rep_by_type[t] for t in types], 0.4,
            label="document records (as uploaded)", color="#c7c9f5")
label_bars(ax, b1); label_bars(ax, b2)
ax.set_xticks(list(x))
ax.set_xticklabels([t.replace("_", " ") for t in types], rotation=20, ha="right")
ax.set_ylabel("count")
ax.set_title(f"Corpus composition: {len(reps)} distinct documents behind "
             f"{sum(rep_by_type.values())} records", loc="left", fontweight="bold")
ax.legend(frameon=False)
save(fig, "01_document_corpus.png")
write_csv("fig01_corpus.csv", ["doc_type", "distinct_documents", "records"],
          [(t, by_type[t], rep_by_type[t]) for t in types])

# ---------------------------------------------------------------------------
# Figure 2 - extraction agreement across repeat ingestions
# ---------------------------------------------------------------------------
print("Figure 2: extraction agreement")


def norm_quote(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split())[:60]


def productive_runs(docs) -> int:
    ids_ = tuple(d[0] for d in docs)
    return q("""
        SELECT COUNT(*) FROM (
          SELECT d.id FROM raw.documents d
          JOIN home_issues i ON i.source_document_id = d.id AND i.archived_at IS NULL
          WHERE d.id IN %s GROUP BY d.id
        ) t
    """, (ids_,))[0][0]


biggest = max(groups.values(), key=productive_runs)
ids = tuple(d[0] for d in biggest)
runs: dict[str, set] = {}
created: dict[str, str] = {}
for did, cat, quote, desc in q("""
    SELECT d.id, d.created_at, i.verbatim_quote, i.description
    FROM raw.documents d
    JOIN home_issues i ON i.source_document_id = d.id AND i.archived_at IS NULL
    WHERE d.id IN %s
""", (ids,)):
    k = str(did)[:8]
    created[k] = cat.strftime("%Y-%m-%d")
    runs.setdefault(k, set()).add(norm_quote(quote) or norm_quote(desc))

if len(runs) >= 2:
    order = sorted(runs, key=lambda k: created[k])
    labels = [created[k] for k in order]
    n = len(order)
    mat = [[0.0] * n for _ in range(n)]
    for i, j in itertools.product(range(n), repeat=2):
        a, b = runs[order[i]], runs[order[j]]
        mat[i][j] = len(a & b) / len(a | b) if (a | b) else 0.0

    fig, (axl, axr) = plt.subplots(1, 2, figsize=(11, 4.4),
                                   gridspec_kw={"width_ratios": [1.15, 1]})
    counts = [len(runs[k]) for k in order]
    bars = axl.bar(labels, counts, color=ACCENT)
    label_bars(axl, bars)
    axl.set_ylabel("distinct issues extracted")
    axl.set_title("Same document, repeat ingestions", loc="left", fontweight="bold")
    axl.tick_params(axis="x", rotation=30)

    im = axr.imshow(mat, cmap="viridis", vmin=0, vmax=1)
    axr.set_xticks(range(n)); axr.set_xticklabels(labels, rotation=90, fontsize=7)
    axr.set_yticks(range(n)); axr.set_yticklabels(labels, fontsize=7)
    for i, j in itertools.product(range(n), repeat=2):
        axr.text(j, i, f"{mat[i][j]:.2f}", ha="center", va="center", fontsize=7,
                 color="white" if mat[i][j] < 0.6 else "black")
    axr.grid(False)
    axr.set_title("Pairwise agreement (Jaccard)", loc="left", fontweight="bold")
    fig.colorbar(im, ax=axr, fraction=0.046)
    save(fig, "02_extraction_agreement.png")
    write_csv("fig02_agreement.csv", ["run_date", "issues"] + labels,
              [[labels[i], counts[i]] + [f"{v:.3f}" for v in mat[i]] for i in range(n)])
else:
    print("  skipped - fewer than 2 repeat ingestions in corpus")

# ---------------------------------------------------------------------------
# Figure 3 - issues by category and severity
# ---------------------------------------------------------------------------
print("Figure 3: issues by category and severity")
rows = q("""
    SELECT category::text, severity::text, COUNT(*)
    FROM home_issues
    WHERE archived_at IS NULL AND source_document_id IN %s
    GROUP BY 1, 2
""", (REP_IDS,))
cats = sorted({r[0] for r in rows if r[0]})
grid = {(c, s): 0 for c in cats for s in SEV_ORDER}
for c, s, n in rows:
    if c and s:
        grid[(c, s)] = n

fig, ax = plt.subplots(figsize=(8, 4.2))
bottom = [0] * len(cats)
for s in SEV_ORDER:
    vals = [grid[(c, s)] for c in cats]
    ax.bar(cats, vals, bottom=bottom, label=s, color=SEV_COLORS[s])
    bottom = [b + v for b, v in zip(bottom, vals)]
for i, tot in enumerate(bottom):
    ax.text(i, tot, f"{tot:,}", ha="center", va="bottom", fontsize=8, fontweight="bold")
ax.set_ylabel("issues")
ax.set_title(f"{sum(bottom):,} issues by category and severity "
             f"({len(reps)} distinct documents)", loc="left", fontweight="bold")
ax.legend(frameon=False, title="severity")
save(fig, "03_issues_category_severity.png")
write_csv("fig03_issues.csv", ["category", "severity", "count"],
          [(c, s, grid[(c, s)]) for c in cats for s in SEV_ORDER])
ISSUES_DEDUP = sum(bottom)

# ---------------------------------------------------------------------------
# Figure 4 - cost exposure by severity
# ---------------------------------------------------------------------------
print("Figure 4: cost exposure")
rows = q("""
    SELECT severity::text,
           COUNT(*),
           SUM((COALESCE(estimated_cost_min, estimated_cost_max)
              + COALESCE(estimated_cost_max, estimated_cost_min)) / 2.0)
    FROM home_issues
    WHERE archived_at IS NULL AND source_document_id IN %s
      AND (estimated_cost_min IS NOT NULL OR estimated_cost_max IS NOT NULL)
    GROUP BY 1
""", (REP_IDS,))
costs = {r[0]: (r[1], float(r[2] or 0)) for r in rows if r[0]}
present = [s for s in SEV_ORDER if s in costs]
vals = [costs[s][1] for s in present]

fig, ax = plt.subplots(figsize=(7.5, 4.2))
bars = ax.bar(present, vals, color=[SEV_COLORS[s] for s in present])
for b, s in zip(bars, present):
    ax.text(b.get_x() + b.get_width() / 2, b.get_height(),
            f"${costs[s][1]:,.0f}\n({costs[s][0]} issues)",
            ha="center", va="bottom", fontsize=8)
ax.set_ylabel("estimated repair exposure (midpoint, USD)")
ax.set_title(f"${sum(vals):,.0f} identified repair exposure across "
             f"{len(reps)} distinct documents", loc="left", fontweight="bold")
ax.text(0.0, -0.22, "Model-estimated ranges, not contractor quotes or completed "
        "repair costs.", transform=ax.transAxes, fontsize=8, color="#667085")
save(fig, "04_cost_exposure_severity.png")
write_csv("fig04_cost.csv", ["severity", "issues", "midpoint_exposure_usd"],
          [(s, costs[s][0], round(costs[s][1], 2)) for s in present])
ISSUES_COSTED = sum(costs[s][0] for s in present)

# ---------------------------------------------------------------------------
# Figure 5 - equipment age and service life consumed
# ---------------------------------------------------------------------------
print("Figure 5: equipment age")
ages = [r[0] for r in q("""
    SELECT 2026 - manufacture_year
    FROM home_equipment
    WHERE archived_at IS NULL AND source_document_id IN %s
      AND manufacture_year BETWEEN 1980 AND 2026
""", (REP_IDS,))]
life = q("""
    SELECT COUNT(*) FILTER (WHERE frac >= 0.75), COUNT(*)
    FROM (
      SELECT (2026 - manufacture_year)::float / NULLIF(expected_life_years, 0) frac
      FROM home_equipment
      WHERE archived_at IS NULL AND source_document_id IN %s
        AND manufacture_year BETWEEN 1980 AND 2026 AND expected_life_years > 0
    ) t
""", (REP_IDS,))[0]
total_eq = q("""SELECT COUNT(*) FROM home_equipment
               WHERE archived_at IS NULL AND source_document_id IN %s""", (REP_IDS,))[0][0]

fig, (axl, axr) = plt.subplots(1, 2, figsize=(11, 4.2))
if ages:
    axl.hist(ages, bins=range(0, max(ages) + 3, 2), color=ACCENT, edgecolor="white")
    axl.axvline(sorted(ages)[len(ages) // 2], color="#c92a2a", ls="--", lw=1.5,
                label=f"median {sorted(ages)[len(ages)//2]} yrs")
    axl.legend(frameon=False)
axl.set_xlabel("age (years)"); axl.set_ylabel("equipment items")
axl.set_title(f"Age of dated equipment (n={len(ages)} of {total_eq}; "
              f"{100*len(ages)/max(total_eq,1):.0f}% carry a year)",
              loc="left", fontweight="bold", fontsize=9)

near, tot = life[0] or 0, life[1] or 0
axr.bar(["≥75% of life\nconsumed", "under 75%"], [near, max(tot - near, 0)],
        color=["#e8590c", "#9aa4b2"])
axr.text(0, near, str(near), ha="center", va="bottom", fontweight="bold")
axr.text(1, max(tot - near, 0), str(max(tot - near, 0)), ha="center", va="bottom")
axr.set_ylabel("equipment items")
axr.set_title(f"Service life consumed (n={tot} with age and expected life)",
              loc="left", fontweight="bold", fontsize=9)
save(fig, "05_equipment_age.png")
write_csv("fig05_equipment.csv", ["metric", "value"],
          [("equipment_total", total_eq), ("with_manufacture_year", len(ages)),
           ("median_age_years", sorted(ages)[len(ages) // 2] if ages else ""),
           ("with_age_and_expected_life", tot), ("at_or_over_75pct_life", near)])

# ---------------------------------------------------------------------------
# Figure 6 - bundle split-detection time (instrumented; customers excluded)
# ---------------------------------------------------------------------------
print("Figure 6: bundle split-detection time")
rows = q(f"""
    SELECT m.size_bucket, COUNT(*), AVG(m.analysis_time_ms) / 1000.0,
           MIN(m.analysis_time_ms) / 1000.0, MAX(m.analysis_time_ms) / 1000.0
    FROM document_processing_metrics m
    JOIN raw.documents d ON d.id = m.document_id
    WHERE m.analysis_time_ms IS NOT NULL AND {NOT_CUSTOMER}
    GROUP BY 1 ORDER BY 1
""", (EXCLUDED,))
overall = q(f"""SELECT COUNT(*), AVG(m.analysis_time_ms)/1000.0, MAX(m.analysis_time_ms)/1000.0
               FROM document_processing_metrics m
               JOIN raw.documents d ON d.id = m.document_id
               WHERE m.analysis_time_ms IS NOT NULL AND {NOT_CUSTOMER}""", (EXCLUDED,))[0]

fig, ax = plt.subplots(figsize=(8, 4.2))
if rows:
    buckets = [str(r[0]) for r in rows]
    means = [float(r[2]) for r in rows]
    lo = [float(r[2]) - float(r[3]) for r in rows]
    hi = [float(r[4]) - float(r[2]) for r in rows]
    bars = ax.bar(buckets, means, yerr=[lo, hi], capsize=4, color=ACCENT,
                  error_kw={"ecolor": "#667085", "lw": 1})
    for b, r in zip(bars, rows):
        ax.text(b.get_x() + b.get_width() / 2, float(r[2]),
                f"{float(r[2]):.1f}s\n(n={r[1]})", ha="center", va="bottom", fontsize=8)
ax.set_ylabel("seconds")
ax.set_xlabel("upload size bucket")
ax.set_title(f"Bundle split-detection time — mean {float(overall[1]):.1f}s, "
             f"max {float(overall[2]):.1f}s across {overall[0]} bundle uploads",
             loc="left", fontweight="bold")
save(fig, "06_processing_time.png")
write_csv("fig06_timing.csv", ["size_bucket", "runs", "mean_s", "min_s", "max_s"],
          [(r[0], r[1], round(float(r[2]), 2), round(float(r[3]), 2),
            round(float(r[4]), 2)) for r in rows])
SPLIT_RUNS = overall[0]

# ---------------------------------------------------------------------------
# Figure 7 - retrieval corpus, rows vs distinct content (customers excluded)
# ---------------------------------------------------------------------------
print("Figure 7: retrieval corpus")
rows = q("""
    SELECT c.entity_type, COUNT(*), COUNT(DISTINCT md5(c.content))
    FROM entity_chunks c
    LEFT JOIN homes h ON h.id = c.home_id
    WHERE h.user_id IS NULL OR h.user_id::text NOT IN %s
    GROUP BY 1 ORDER BY 2 DESC
""", (EXCLUDED,))
ents = [str(r[0]) for r in rows]
xs = range(len(ents))
fig, ax = plt.subplots(figsize=(8.5, 4.2))
b1 = ax.bar([i - 0.2 for i in xs], [r[1] for r in rows], 0.4, label="rows", color="#c7c9f5")
b2 = ax.bar([i + 0.2 for i in xs], [r[2] for r in rows], 0.4,
            label="distinct content", color=ACCENT)
label_bars(ax, b1); label_bars(ax, b2)
ax.set_xticks(list(xs)); ax.set_xticklabels([e.replace("_", " ") for e in ents],
                                            rotation=20, ha="right")
ax.set_ylabel("embedded chunks")
ax.set_title(f"Retrieval corpus: {sum(r[1] for r in rows):,} rows, "
             f"{sum(r[2] for r in rows):,} distinct", loc="left", fontweight="bold")
ax.legend(frameon=False)
save(fig, "07_rag_corpus.png")
write_csv("fig07_rag.csv", ["entity_type", "rows", "distinct_content"],
          [(r[0], r[1], r[2]) for r in rows])

# ===========================================================================
# THE MEASUREMENT SET — what the other sections cite
# ===========================================================================
print("Measurement set: corpus counts")
active = "d.archived_at IS NULL AND " + NOT_CUSTOMER
docs_total = q(f"SELECT COUNT(*) FROM raw.documents d WHERE {NOT_CUSTOMER}", (EXCLUDED,))[0][0]
docs_active = n_records
docs_text = len(text_rows)
hash_null = q(f"SELECT COUNT(*) FROM raw.documents d WHERE {active} AND d.content_hash IS NULL",
              (EXCLUDED,))[0][0]
by_status = dict(q(f"SELECT d.processing_status::text, COUNT(*) FROM raw.documents d "
                   f"WHERE {active} GROUP BY 1", (EXCLUDED,)))
failed = by_status.get("FAILED", 0)
terminal = by_status.get("COMPLETED", 0) + failed
completed_no_text = q(f"""SELECT COUNT(*) FROM raw.documents d WHERE {active}
    AND d.processing_status::text = 'COMPLETED'
    AND NOT EXISTS (SELECT 1 FROM document_sections s WHERE s.document_id = d.id)""",
                      (EXCLUDED,))[0][0]
text_by_status = collections.Counter(r[4] for r in text_rows)
non_completed_with_text = sum(v for k, v in text_by_status.items() if k != "COMPLETED")
bulk_text = sum(1 for r in text_rows if r[3])
single_text = docs_text - bulk_text
# FAILED records whose file hash never succeeded anywhere = documents lost outright
lost = q(f"""SELECT COUNT(*) FROM raw.documents d WHERE {active}
    AND d.processing_status::text = 'FAILED'
    AND NOT EXISTS (SELECT 1 FROM raw.documents d2 WHERE d2.content_hash = d.content_hash
                    AND d2.id <> d.id AND d2.processing_status::text <> 'FAILED')""",
         (EXCLUDED,))[0][0]
failed_single = q(f"""SELECT COUNT(*) FROM raw.documents d WHERE {active}
    AND d.processing_status::text = 'FAILED' AND d.bulk_batch_id IS NULL""", (EXCLUDED,))[0][0]
sections_rows, sections_distinct = q(f"""SELECT COUNT(*), COUNT(DISTINCT md5(s.content))
    FROM document_sections s JOIN raw.documents d ON d.id = s.document_id
    WHERE {NOT_CUSTOMER}""", (EXCLUDED,))[0]
homes_total, homes_active = q("""SELECT COUNT(*), COUNT(*) FILTER (WHERE archived_at IS NULL)
    FROM homes WHERE user_id IS NULL OR user_id::text NOT IN %s""", (EXCLUDED,))[0]
homes_august = q("""SELECT COUNT(*) FROM homes WHERE (user_id IS NULL OR user_id::text NOT IN %s)
    AND created_at >= '2026-08-01' AND created_at < '2026-09-01'""", (EXCLUDED,))[0][0]
records_three_days = q(f"""SELECT COUNT(*) FROM raw.documents d WHERE {NOT_CUSTOMER}
    AND (d.created_at AT TIME ZONE 'Europe/Amsterdam')::date IN ('2026-08-06','2026-08-08','2026-08-09')""",
                       (EXCLUDED,))[0][0]
distinct_inspection = by_type.get("inspection_report", 0)
attempted = len(reps) + lost
issues_active = q("SELECT COUNT(*) FROM home_issues i JOIN raw.documents d ON d.id = i.source_document_id "
                  f"WHERE i.archived_at IS NULL AND {NOT_CUSTOMER}", (EXCLUDED,))[0][0]
issues_active_all = q("SELECT COUNT(*) FROM home_issues WHERE archived_at IS NULL")[0][0]
equipment_active_all = q("SELECT COUNT(*) FROM home_equipment WHERE archived_at IS NULL")[0][0]
components_active_all = q("SELECT COUNT(*) FROM home_components WHERE archived_at IS NULL")[0][0] \
    if q("SELECT 1 FROM information_schema.tables WHERE table_name='home_components'") else 0

write_csv("corpus_counts.csv", ["metric", "value"], [
    ("documents_total", docs_total),
    ("documents_active", docs_active),
    ("documents_with_text", docs_text),
    ("distinct_documents", len(reps)),
    ("distinct_inspection_reports", distinct_inspection),
    ("documents_lost_outright", lost),
    ("documents_attempted", attempted),
    ("content_hash_null_active", hash_null),
    ("content_hash_null_pct", pct(hash_null, docs_active)),
    ("failed_records", failed),
    ("failed_records_single_route", failed_single),
    ("terminal_records", terminal),
    ("completed_records", by_status.get("COMPLETED", 0)),
    ("completed_without_text", completed_no_text),
    ("non_completed_with_text", non_completed_with_text),
    ("with_text_bulk_route", bulk_text),
    ("with_text_single_route", single_text),
    ("document_sections_rows", sections_rows),
    ("document_sections_distinct", sections_distinct),
    ("issues_active", issues_active_all),
    ("equipment_active", equipment_active_all),
    ("components_active", components_active_all),
    ("issues_deduplicated", ISSUES_DEDUP),
    ("equipment_deduplicated", total_eq),
    ("homes_total", homes_total),
    ("homes_active", homes_active),
    ("homes_created_august_2026", homes_august),
    ("records_created_6_8_9_august", records_three_days),
    ("split_detection_runs", SPLIT_RUNS),
])

# ---- T1 completion --------------------------------------------------------
print("Measurement set: T1")
write_csv("criteria_t1_completion.csv",
          ["basis", "numerator", "denominator", "rate_pct", "meets_90"], [
    ("distinct documents by extracted text (adopted)", len(reps), attempted,
     pct(len(reps), attempted), pct(len(reps), attempted) >= 90),
    ("records by processing_status over terminal records",
     by_status.get("COMPLETED", 0), terminal, pct(by_status.get("COMPLETED", 0), terminal),
     pct(by_status.get("COMPLETED", 0), terminal) >= 90),
])

# ---- T2 blocking-phase latency -------------------------------------------
print("Measurement set: T2")
lat = q(f"""SELECT (d.layer_2_metadata->>'processing_time_seconds')::float,
                   d.bulk_batch_id IS NOT NULL,
                   COALESCE(d.doc_type::text, 'unclassified'),
                   d.id,
                   (d.layer_2_metadata->'processing_phases_seconds'->>'extraction')::float
            FROM raw.documents d
            WHERE d.layer_2_metadata ? 'processing_time_seconds' AND {NOT_CUSTOMER}""",
        (EXCLUDED,))
extraction_s = [r[4] for r in lat if r[4] is not None]
groups_t2 = collections.OrderedDict()
groups_t2["all runs"] = [r[0] for r in lat]
groups_t2["single upload, inspection report"] = [r[0] for r in lat if not r[1] and r[2] == "inspection_report"]
groups_t2["single upload, other document types"] = [r[0] for r in lat if not r[1] and r[2] != "inspection_report"]
groups_t2["bulk batch, inspection report"] = [r[0] for r in lat if r[1] and r[2] == "inspection_report"]
groups_t2["bulk batch, other document types"] = [r[0] for r in lat if r[1] and r[2] != "inspection_report"]
groups_t2["single upload, all document types"] = [r[0] for r in lat if not r[1]]
extraction_single = [r[4] for r in lat if not r[1] and r[4] is not None]
t2_distinct = len({hashlib.sha256((next((tr[6] for tr in text_rows if tr[0] == r[3]), "") or "").encode()).hexdigest()[:16]
                   for r in lat})


def quart(vals):
    if not vals:
        return ("", "", "", "", "", "")
    s = sorted(vals)
    qs = statistics.quantiles(s, n=4) if len(s) >= 2 else [s[0], s[0], s[0]]
    return (round(min(s), 1), round(qs[0], 1), round(statistics.median(s), 1),
            round(statistics.mean(s), 1), round(qs[2], 1), round(max(s), 1))


write_csv("criteria_t2_latency.csv",
          ["population", "runs", "min_s", "p25_s", "median_s", "mean_s", "p75_s", "max_s",
           "under_60_s", "distinct_documents"], [
    (name, len(v), *quart(v), sum(1 for x in v if x <= 60),
     t2_distinct if name == "all runs" else "")
    for name, v in groups_t2.items() if v
] + [
    ("extraction phase only, all runs", len(extraction_s), *quart(extraction_s),
     sum(1 for x in extraction_s if x <= 50), ""),
    ("extraction phase only, single upload", len(extraction_single), *quart(extraction_single),
     sum(1 for x in extraction_single if x <= 50), ""),
])

# ---- T3 yield per distinct inspection report ------------------------------
print("Measurement set: T3")
ins = [r for r in reps if r[1] == "inspection_report"]
yields = []
for r in ins:
    yields.append(q("SELECT COUNT(*) FROM home_issues WHERE archived_at IS NULL AND source_document_id = %s",
                    (r[0],))[0][0])
ys = sorted(yields)
n3 = len(ys)
write_csv("criteria_t3_yield.csv", ["document", "issues"],
          [(f"D{i+1:02d}", y) for i, y in enumerate(ys)] + [
              ("n_reports", n3), ("min", ys[0]), ("max", ys[-1]),
              ("median_lower", ys[(n3 - 1) // 2]), ("median_conventional", statistics.median(ys)),
              ("mean", round(statistics.mean(ys), 1)),
              ("at_or_above_30", sum(1 for y in ys if y >= 30)),
              ("zero_yield", sum(1 for y in ys if y == 0)),
          ])

# ---- T4 cost-estimate coverage --------------------------------------------
print("Measurement set: T4")
write_csv("criteria_t4_cost_coverage.csv", ["metric", "value"], [
    ("issues_deduplicated", ISSUES_DEDUP), ("issues_with_cost_range", ISSUES_COSTED),
    ("coverage_pct", pct(ISSUES_COSTED, ISSUES_DEDUP)),
    ("exposure_midpoint_usd", round(sum(vals), 2)),
])

# ---- T5 retrieval coverage ------------------------------------------------
print("Measurement set: T5")
t5 = []
for etype, table in (("issue", "home_issues"), ("equipment", "home_equipment"),
                     ("component", "home_components")):
    if not q("SELECT 1 FROM information_schema.tables WHERE table_name=%s", (table,)):
        continue
    total_e, unemb = q(f"""SELECT COUNT(*), COUNT(*) FILTER (WHERE NOT EXISTS (
            SELECT 1 FROM entity_chunks c WHERE c.entity_type = %s AND c.entity_id = t.id))
        FROM {table} t LEFT JOIN homes h ON h.id = t.home_id
        WHERE t.archived_at IS NULL AND (h.user_id IS NULL OR h.user_id::text NOT IN %s)""",
                       (etype, EXCLUDED))[0]
    t5.append((etype, total_e, unemb))
t5.append(("total", sum(r[1] for r in t5), sum(r[2] for r in t5)))
write_csv("criteria_t5_retrieval.csv", ["entity_type", "active_rows", "unembedded"], t5)

# ---- T6 traceability replay ----------------------------------------------
print("Measurement set: T6 (traceability replay)")
_ALNUM = re.compile(r"[A-Za-z0-9]")


def normalize_for_match(text: str) -> str:
    """Port of the viewer's normalizeText (document-viewer.tsx): lowercase, every
    non-[a-z0-9\\s] to a space, collapse whitespace, trim."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", text.lower())).strip()


def normalized_page(text: str) -> str:
    """The viewer's buildNormalizedMapping, text part only."""
    parts, last_space, i, n = [], True, 0, len(text)
    while i < n:
        if _ALNUM.match(text[i]):
            start = i
            while i < n and _ALNUM.match(text[i]):
                i += 1
            parts.append(text[start:i].lower()); last_space = False
        else:
            if not last_space:
                parts.append(" ")
            i += 1; last_space = True
    s = "".join(parts)
    return s[:-1] if s.endswith(" ") else s


secs = q(f"""SELECT s.document_id::text, s.page_number, s.char_start, s.content
             FROM document_sections s JOIN raw.documents d ON d.id = s.document_id
             WHERE {NOT_CUSTOMER} ORDER BY 1, 2, 3""", (EXCLUDED,))
pages = collections.defaultdict(list)
for did, pg, cs, content in secs:
    pages[(did, pg)].append((cs or 0, content or ""))
page_text, doc_text = {}, collections.defaultdict(str)
for key, chunks in pages.items():
    uniq, seen_c, text, cur_end = [], set(), "", None
    for c in sorted(chunks):
        if c in seen_c:
            continue
        seen_c.add(c); uniq.append(c)
    for cs, content in uniq:
        if cur_end is None:
            text, cur_end = content, cs + len(content)
        elif cs < cur_end:
            overlap = cur_end - cs
            text += content[overlap:] if overlap < len(content) else ""
            cur_end = max(cur_end, cs + len(content))
        else:
            text += "\n" + content; cur_end = cs + len(content)
    page_text[key] = text
for (did, pg), t in sorted(page_text.items()):
    doc_text[did] += "\n" + t

LIG = ("ffi", "ffl", "ff", "fi", "fl")


def degrade(s: str) -> str:
    for l in LIG:
        s = s.replace(l, "")
    return s


def match(quote, text, mode):
    if not quote or not text:
        return False
    if mode == "lig":
        quote, text = degrade(quote), degrade(text)
    if mode == "space":
        quote, text = degrade(quote), degrade(text)
        nq = normalize_for_match(quote).replace(" ", "")
        return bool(nq) and nq in normalize_for_match(text).replace(" ", "")
    nq = normalize_for_match(quote)
    return bool(nq) and nq in normalized_page(text)


def scoped(did, pg):
    if pg is not None and (did, int(pg)) in page_text:
        return page_text[(did, int(pg))]
    return doc_text.get(did, "")


populations = [
    ("entity_evidence (equipment)", f"""SELECT e.document_id::text, e.page_number, e.verbatim_quote
        FROM entity_evidence e JOIN raw.documents d ON d.id = e.document_id
        WHERE e.entity_type = 'equipment' AND {NOT_CUSTOMER}"""),
    ("entity_evidence (issue)", f"""SELECT e.document_id::text, e.page_number, e.verbatim_quote
        FROM entity_evidence e JOIN raw.documents d ON d.id = e.document_id
        WHERE e.entity_type = 'issue' AND {NOT_CUSTOMER}"""),
    ("home_issues", f"""SELECT i.source_document_id::text,
        (SELECT e.page_number FROM entity_evidence e WHERE e.entity_type = 'issue'
           AND e.entity_id = i.id AND e.document_id = i.source_document_id LIMIT 1),
        i.verbatim_quote
        FROM home_issues i JOIN raw.documents d ON d.id = i.source_document_id
        WHERE i.archived_at IS NULL AND {NOT_CUSTOMER}"""),
    ("coverage_items", f"""SELECT p.source_document_id::text, c.page_number, c.verbatim_quote
        FROM coverage_items c JOIN home_coverages p ON p.id = c.coverage_id
        JOIN raw.documents d ON d.id = p.source_document_id WHERE {NOT_CUSTOMER}"""),
    ("warranty_coverages", f"""SELECT p.source_document_id::text, c.page_number, c.verbatim_quote
        FROM warranty_coverages c JOIN home_warranties p ON p.id = c.warranty_id
        JOIN raw.documents d ON d.id = p.source_document_id WHERE {NOT_CUSTOMER}"""),
    ("home_utilities", f"""SELECT u.source_document_id::text, u.page_number, u.verbatim_quote
        FROM home_utilities u JOIN raw.documents d ON d.id = u.source_document_id
        WHERE {NOT_CUSTOMER}"""),
]
t6_rows, tot_n, tot = [], 0, {"raw": 0, "lig": 0, "space": 0}
per_doc = collections.defaultdict(lambda: {"n": 0, "raw": 0, "lig": 0, "space": 0})
res_len, fail_len = [], []
for name, sql in populations:
    rows_ = q(sql, (EXCLUDED,))
    hit = {"raw": 0, "lig": 0, "space": 0}
    for did, pg, quote in rows_:
        t = scoped(did, pg)
        for m in hit:
            if match(quote, t, m):
                hit[m] += 1
        if name == "entity_evidence (issue)":
            d = per_doc[did]; d["n"] += 1
            ok = match(quote, t, "raw")
            (res_len if ok else fail_len).append(len(quote or ""))
            for m in ("raw", "lig", "space"):
                d[m] += 1 if match(quote, t, m) else 0
    n_ = len(rows_); tot_n += n_
    for m in tot:
        tot[m] += hit[m]
    t6_rows.append((name, n_, hit["raw"], pct(hit["raw"], n_), hit["lig"], pct(hit["lig"], n_),
                    hit["space"], pct(hit["space"], n_)))
t6_rows.append(("total", tot_n, tot["raw"], pct(tot["raw"], tot_n), tot["lig"], pct(tot["lig"], tot_n),
                tot["space"], pct(tot["space"], tot_n)))
write_csv("criteria_t6_traceability.csv",
          ["population", "rows", "raw_matches", "raw_pct", "ligature_repaired", "ligature_pct",
           "space_repaired", "space_pct"], t6_rows)
docs_sorted = sorted(per_doc.values(), key=lambda d: (d["raw"] / d["n"] if d["n"] else 0, d["n"]))
write_csv("criteria_t6_by_document.csv",
          ["document", "issue_evidence_rows", "raw_pct", "ligature_pct", "space_pct"],
          [(f"D{i+1:02d}", d["n"], pct(d["raw"], d["n"]), pct(d["lig"], d["n"]), pct(d["space"], d["n"]))
           for i, d in enumerate(docs_sorted)] + [
              ("documents_with_20plus_rows_at_0pct", sum(1 for d in docs_sorted if d["n"] >= 20 and d["raw"] == 0)),
              ("documents_with_20plus_rows_at_100pct", sum(1 for d in docs_sorted if d["n"] >= 20 and d["raw"] == d["n"])),
              ("median_quote_chars_resolving", statistics.median(res_len) if res_len else ""),
              ("median_quote_chars_failing", statistics.median(fail_len) if fail_len else ""),
          ])

# ---- Cost ------------------------------------------------------------------
print("Measurement set: cost")
cost_filter = f"""FROM llm_cost_events e LEFT JOIN raw.documents d ON d.id::text = e.document_id::text
    WHERE (e.user_id IS NULL OR e.user_id::text NOT IN %s)
      AND (d.id IS NULL OR {NOT_CUSTOMER})"""
n_ev, n_docs, total_usd = q(f"SELECT COUNT(*), COUNT(DISTINCT e.document_id), ROUND(SUM(e.total_cost_usd)::numeric, 4) {cost_filter}",
                            (EXCLUDED, EXCLUDED))[0]
per_doc_cost = [float(r[0]) for r in q(f"SELECT SUM(e.total_cost_usd) {cost_filter} GROUP BY e.document_id",
                                         (EXCLUDED, EXCLUDED))]
day_ev, day_usd = q(f"SELECT COUNT(*), ROUND(SUM(e.total_cost_usd)::numeric, 4) {cost_filter} "
                    "AND e.created_at >= '2026-08-09' AND e.created_at < '2026-08-10'",
                    (EXCLUDED, EXCLUDED))[0]
optypes = q(f"SELECT e.operation_type, COUNT(*), ROUND(SUM(e.total_cost_usd)::numeric, 4) {cost_filter} "
            "GROUP BY 1 ORDER BY 2 DESC", (EXCLUDED, EXCLUDED))
doc_types = q(f"SELECT COALESCE(d.doc_type::text,'unclassified'), COUNT(DISTINCT e.document_id) {cost_filter} GROUP BY 1",
              (EXCLUDED, EXCLUDED))
tokens = q(f"SELECT e.model, SUM(e.input_tokens), SUM(e.output_tokens) {cost_filter} GROUP BY 1",
           (EXCLUDED, EXCLUDED))
# Reasoning-token correction, exactly as scripts/capstone_cost_model.py applies it:
# measured (answer, thinking) runs on the real extraction call, 24 August 2026.
PRICE = {"gemini-2.5-pro": (1.25, 10.00), "gemini-2.5-flash": (0.30, 2.50)}
THINK = {"gemini-2.5-flash": [(17_621, 8_190), (15_061, 8_188)],
         "gemini-2.5-pro": [(12_498, 12_432), (13_436, 11_495)]}


def ratio(model):
    rs = [(a + t) / a for a, t in THINK[model]]
    return sum(rs) / len(rs)


corrected = 0.0
for model, tin, tout in tokens:
    key = next((k for k in PRICE if k in (model or "")), None)
    if key is None:
        continue
    pin, pout = PRICE[key]
    corrected += (tin or 0) / 1e6 * pin + (tout or 0) * ratio(key) / 1e6 * pout
cost_rows = [
    ("events", n_ev), ("documents", n_docs), ("total_usd", total_usd),
    ("mean_per_document_usd", round(float(total_usd) / n_docs, 4) if n_docs else ""),
    ("median_per_document_usd", round(statistics.median(per_doc_cost), 4) if per_doc_cost else ""),
    ("anchor_day_2026_08_09_events", day_ev), ("anchor_day_2026_08_09_usd", day_usd),
    ("anchor_day_invoice_eur", 63.97),
    ("operation_types_recorded", len(optypes)),
    ("thinking_corrected_total_usd", round(corrected, 2)),
    ("thinking_corrected_mean_per_document_usd", round(corrected / n_docs, 2) if n_docs else ""),
]
cost_rows += [(f"optype_{o}_events", n) for o, n, _ in optypes]
cost_rows += [(f"optype_{o}_usd", u) for o, _, u in optypes]
cost_rows += [(f"documents_of_type_{t}", n) for t, n in doc_types]
cost_rows += [(f"tokens_{m}_input", i) for m, i, _ in tokens] + [(f"tokens_{m}_output", o) for m, _, o in tokens]
write_csv("cost_events.csv", ["metric", "value"], cost_rows)

# ---- Provenance -------------------------------------------------------------
write_csv("measurement.csv", ["key", "value"], [
    ("run_date", RUN_DATE),
    ("script", f"eda_homecommand_{SCRIPT_VERSION}.py"),
    ("basis", "production corpus at run time; real customers excluded via EDA_EXCLUDE_USER_IDS; "
              "de-duplicated on extracted text (SHA-256 over distinct section text)"),
    ("customer_accounts_excluded", len(EXCLUDED)),
    ("session", "read-only at the connection level; aggregates only; no property data written"),
])

CONN.close()
print(f"\nDone. {len(reps)} distinct documents. Figures + figures_data/ written to {OUT}")

#!/usr/bin/env python3
"""Redraw the report's figures from the published CSVs. No database needed.

    pip install matplotlib
    python replot_from_csv.py

eda_homecommand_v4.py needs production credentials and cannot be run by a reader.
This script needs nothing but the files in this repository. It reads
figures_data/*.csv, redraws the figures into replot/, and prints the headline
totals so they can be checked against the report.

The originals are left untouched, so the two sets can be compared side by side.

One partial: Figure 5's left panel is a histogram of individual equipment ages,
and per-item ages are not published here (figures_data/fig05_equipment.csv holds
the summary only). That panel is redrawn as the published counts instead, and the
figure is labelled accordingly.
"""
import csv
import sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    sys.exit("matplotlib is required:  pip install matplotlib")

HERE = Path(__file__).parent
DATA = HERE / "figures_data"
OUT = HERE / "replot"
OUT.mkdir(exist_ok=True)

INK = "#1f2430"
ACCENT = "#5142F0"
PALE = "#c7c9f5"
SEV_COLORS = {"Low": "#9aa4b2", "Medium": "#f0b429",
              "High": "#e8590c", "Critical": "#c92a2a"}
SEV_ORDER = ["Low", "Medium", "High", "Critical"]

plt.rcParams.update({
    "figure.dpi": 150, "savefig.dpi": 150, "font.size": 9,
    "axes.edgecolor": "#d0d5dd", "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": INK, "ytick.color": INK, "axes.grid": True,
    "grid.color": "#eceff3", "grid.linewidth": 0.8, "axes.axisbelow": True,
})


def rows(name):
    with open(DATA / name, newline="", encoding="utf-8-sig") as fh:
        return list(csv.reader(fh))


def save(fig, name):
    fig.tight_layout()
    fig.savefig(OUT / name, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("  wrote replot/" + name)


def label_bars(ax, bars, fmt="{:,.0f}"):
    for b in bars:
        h = b.get_height()
        if h:
            ax.text(b.get_x() + b.get_width() / 2, h, fmt.format(h),
                    ha="center", va="bottom", fontsize=8)


checks = {}

# --- Figure 1: corpus composition -------------------------------------------
r = rows("fig01_corpus.csv")[1:]
types = [x[0] for x in r]
distinct = [int(x[1]) for x in r]
records = [int(x[2]) for x in r]
x = range(len(types))
fig, ax = plt.subplots(figsize=(8, 4.2))
b1 = ax.bar([i - 0.2 for i in x], distinct, 0.4,
            label="distinct documents", color=ACCENT)
b2 = ax.bar([i + 0.2 for i in x], records, 0.4,
            label="document records (as uploaded)", color=PALE)
label_bars(ax, b1)
label_bars(ax, b2)
ax.set_xticks(list(x))
ax.set_xticklabels([t.replace("_", " ") for t in types], rotation=20, ha="right")
ax.set_ylabel("count")
ax.set_title("Corpus composition: %d distinct documents behind %d records"
             % (sum(distinct), sum(records)), loc="left", fontweight="bold")
ax.legend(frameon=False)
save(fig, "01_document_corpus.png")
checks["distinct documents"] = sum(distinct)
checks["document records"] = sum(records)

# --- Figure 2: extraction agreement -----------------------------------------
r = rows("fig02_agreement.csv")
labels = r[0][2:]
counts = [int(x[1]) for x in r[1:]]
mat = [[float(v) for v in x[2:]] for x in r[1:]]
n = len(labels)
fig, (axl, axr) = plt.subplots(1, 2, figsize=(11, 4.4),
                               gridspec_kw={"width_ratios": [1.15, 1]})
label_bars(axl, axl.bar(labels, counts, color=ACCENT))
axl.set_ylabel("distinct issues extracted")
axl.set_title("Same document, repeat ingestions", loc="left", fontweight="bold")
axl.tick_params(axis="x", rotation=30)
im = axr.imshow(mat, cmap="viridis", vmin=0, vmax=1)
axr.set_xticks(range(n))
axr.set_xticklabels(labels, rotation=90, fontsize=7)
axr.set_yticks(range(n))
axr.set_yticklabels(labels, fontsize=7)
for i in range(n):
    for j in range(n):
        axr.text(j, i, "%.2f" % mat[i][j], ha="center", va="center", fontsize=7,
                 color="white" if mat[i][j] < 0.6 else "black")
axr.grid(False)
axr.set_title("Pairwise agreement (Jaccard)", loc="left", fontweight="bold")
fig.colorbar(im, ax=axr, fraction=0.046)
save(fig, "02_extraction_agreement.png")
off_diag = [mat[i][j] for i in range(1, n) for j in range(1, n) if i != j]
checks["extractions compared"] = n
checks["Jaccard range (post-Feb runs)"] = "%.3f-%.3f" % (min(off_diag), max(off_diag))

# --- Figure 3: issues by category and severity -------------------------------
r = rows("fig03_issues.csv")[1:]
cats = sorted({x[0] for x in r})
grid = {(x[0], x[1]): int(x[2]) for x in r}
fig, ax = plt.subplots(figsize=(8, 4.2))
bottom = [0] * len(cats)
for s in SEV_ORDER:
    vals = [grid.get((c, s), 0) for c in cats]
    ax.bar(cats, vals, bottom=bottom, label=s, color=SEV_COLORS[s])
    bottom = [b + v for b, v in zip(bottom, vals)]
for i, tot in enumerate(bottom):
    ax.text(i, tot, "{:,}".format(tot), ha="center", va="bottom",
            fontsize=8, fontweight="bold")
ax.set_ylabel("issues")
ax.set_title("%s issues by category and severity" % "{:,}".format(sum(bottom)),
             loc="left", fontweight="bold")
ax.legend(frameon=False, title="severity")
save(fig, "03_issues_category_severity.png")
checks["issues (de-duplicated)"] = sum(bottom)

# --- Figure 4: cost exposure -------------------------------------------------
r = rows("fig04_cost.csv")[1:]
present = [x[0] for x in r]
issues = {x[0]: int(x[1]) for x in r}
vals = [float(x[2]) for x in r]
fig, ax = plt.subplots(figsize=(7.5, 4.2))
bars = ax.bar(present, vals, color=[SEV_COLORS[s] for s in present])
for b, s, v in zip(bars, present, vals):
    ax.text(b.get_x() + b.get_width() / 2, b.get_height(),
            "${:,.0f}\n({} issues)".format(v, issues[s]),
            ha="center", va="bottom", fontsize=8)
ax.set_ylabel("estimated repair exposure (midpoint, USD)")
ax.set_title("${:,.0f} identified repair exposure".format(sum(vals)),
             loc="left", fontweight="bold")
ax.text(0.0, -0.22, "Model-estimated ranges, not contractor quotes or completed "
        "repair costs.", transform=ax.transAxes, fontsize=8, color="#667085")
save(fig, "04_cost_exposure_severity.png")
checks["repair exposure (USD)"] = "{:,.0f}".format(sum(vals))
checks["issues carrying a cost"] = sum(issues.values())

# --- Figure 5: equipment (partial - see module docstring) --------------------
m = {x[0]: x[1] for x in rows("fig05_equipment.csv")[1:]}
total_eq = int(m["equipment_total"])
dated = int(m["with_manufacture_year"])
median_age = int(m["median_age_years"])
with_life = int(m["with_age_and_expected_life"])
near = int(m["at_or_over_75pct_life"])
fig, (axl, axr) = plt.subplots(1, 2, figsize=(11, 4.2))
b = axl.bar(["carries a\nmanufacture year", "no year"],
            [dated, total_eq - dated], color=[ACCENT, "#9aa4b2"])
label_bars(axl, b)
axl.set_ylabel("equipment items")
axl.set_title("Dated equipment: %d of %d (%.0f%%), median age %d yrs"
              % (dated, total_eq, 100.0 * dated / total_eq, median_age),
              loc="left", fontweight="bold", fontsize=9)
b = axr.bar([">=75% of life\nconsumed", "under 75%"],
            [near, max(with_life - near, 0)], color=["#e8590c", "#9aa4b2"])
label_bars(axr, b)
axr.set_ylabel("equipment items")
axr.set_title("Service life consumed (n=%d with age and expected life)" % with_life,
              loc="left", fontweight="bold", fontsize=9)
fig.text(0.01, -0.02, "Left panel differs from the original: that one is a histogram "
         "of individual equipment ages, and per-item ages are not published here.",
         fontsize=8, color="#667085")
save(fig, "05_equipment_age.png")
checks["equipment items"] = total_eq
checks["at or over 75% of service life"] = "%d of %d" % (near, with_life)

# --- Figure 6: bundle split-detection time -----------------------------------
r = rows("fig06_timing.csv")[1:]
buckets = [x[0] for x in r]
runs = [int(x[1]) for x in r]
means = [float(x[2]) for x in r]
mins = [float(x[3]) for x in r]
maxs = [float(x[4]) for x in r]
fig, ax = plt.subplots(figsize=(8, 4.2))
bars = ax.bar(buckets, means,
              yerr=[[m - lo for m, lo in zip(means, mins)],
                    [hi - m for m, hi in zip(means, maxs)]],
              capsize=4, color=ACCENT, error_kw={"ecolor": "#667085", "lw": 1})
for b, mn, nrun in zip(bars, means, runs):
    ax.text(b.get_x() + b.get_width() / 2, mn, "%.1fs\n(n=%d)" % (mn, nrun),
            ha="center", va="bottom", fontsize=8)
overall_mean = sum(m * n for m, n in zip(means, runs)) / sum(runs)
ax.set_ylabel("seconds")
ax.set_xlabel("upload size bucket")
ax.set_title("Bundle split-detection time - mean %.1fs, max %.1fs across %d bundle "
             "uploads" % (overall_mean, max(maxs), sum(runs)),
             loc="left", fontweight="bold")
save(fig, "06_processing_time.png")
checks["bundle uploads timed"] = sum(runs)
checks["split-detection mean (s)"] = round(overall_mean, 1)

# --- Figure 7: retrieval corpus ----------------------------------------------
r = rows("fig07_rag.csv")[1:]
ents = [x[0] for x in r]
nrows = [int(x[1]) for x in r]
ndist = [int(x[2]) for x in r]
xs = range(len(ents))
fig, ax = plt.subplots(figsize=(8.5, 4.2))
b1 = ax.bar([i - 0.2 for i in xs], nrows, 0.4, label="rows", color=PALE)
b2 = ax.bar([i + 0.2 for i in xs], ndist, 0.4, label="distinct content", color=ACCENT)
label_bars(ax, b1)
label_bars(ax, b2)
ax.set_xticks(list(xs))
ax.set_xticklabels([e.replace("_", " ") for e in ents], rotation=20, ha="right")
ax.set_ylabel("embedded chunks")
ax.set_title("Retrieval corpus: {:,} rows, {:,} distinct".format(
    sum(nrows), sum(ndist)), loc="left", fontweight="bold")
ax.legend(frameon=False)
save(fig, "07_rag_corpus.png")
checks["retrieval rows"] = "{:,}".format(sum(nrows))
checks["retrieval distinct content"] = "{:,}".format(sum(ndist))

# --- headline totals, for checking against the report ------------------------
print("\nHeadline figures, recomputed from the CSVs in this repository:")
width = max(len(k) for k in checks)
for k, v in checks.items():
    print("  %-*s  %s" % (width, k, v))

meta = {x[0]: x[1] for x in rows("measurement.csv")[1:]}
print("\nMeasurement set: %s, run %s" % (meta.get("script", "?"),
                                         meta.get("run_date", "?")))
print("Figures written to %s/ - compare them against the originals alongside." % OUT.name)

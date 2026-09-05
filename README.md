# HomeCommand capstone — the measurement set behind the report

This repository holds the measurement basis for the report: the aggregate tables
behind every figure it states, and the scripts that produced them. Each number the
report gives for the document corpus, the seven technical success criteria,
extraction traceability or the platform's metered AI spend is a row in one of the
tables below, and the report cites those tables. A reader holding this repository can
check the report without access to the database.

## What is here

| File | What it holds |
|---|---|
| `replot_from_csv.py` | Redraws the figures from the CSVs below, with no database and no credentials. This is the one a reader can run |
| `eda_homecommand_v4.py` | The script that produced everything here, in one read-only run against production. Needs credentials a reader does not have |
| `01_…07_*.png` | The seven figures in the Exploratory Data Analysis section |
| `figures_data/fig01…fig07_*.csv` | The aggregate table behind each figure |
| `figures_data/measurement.csv` | Run date, basis, script version |
| `figures_data/corpus_counts.csv` | Records, active records, records with text, distinct documents, hash coverage, status counts, upload routes, index sizes, homes |
| `figures_data/criteria_t1_completion.csv` … `criteria_t6_traceability.csv` | The technical success criteria T1–T6, each with its population, numerator and denominator |
| `figures_data/criteria_t6_by_document.csv` | Per-document quote-resolution rates for the traceability replay |
| `figures_data/cost_events.csv` | Metered model spend, the 9 August anchor day, the reasoning-token correction |
| `scripts/` | The four measurement scripts behind figures elsewhere in the report: the cost model, the section-label heterogeneity measurement, the citation checker and the run comparison. `scripts/capstone_cost_model.py` runs with no database and no credentials; `scripts/README.md` says what each of the others needs |
| `_superseded/2026-08-15-snapshot/` | The 15 August 2026 figures and CSVs, kept so the reproduction claim below can be checked |

## The basis

All figures describe the production corpus on the run date in `measurement.csv`, with
real customers excluded and documents de-duplicated on their extracted text (two
records are one document when a SHA-256 hash over their distinct section text matches).
The exclusion list is supplied to the script through an environment variable and is not
in this folder or in any file; the script refuses to run without it.

## What is not here

No document text, no address, no owner name, no account identifier. Documents appear
as `D01…` labels ordered by the value in the table, so a label does not identify the
same document across tables. The database credentials are read from the environment
and are not in the script.

## Reproducing it

### Without a database, which is how a reader checks this

```
pip install matplotlib
python replot_from_csv.py
```

This redraws the seven figures from `figures_data/*.csv` into `replot/` and prints the
headline totals it recomputed on the way: 30 distinct documents behind 52 records, 2,287
de-duplicated issues, $1,614,468 of identified repair exposure, 41 timed bundle uploads
averaging 16.3 seconds. Compare those against the report, and the redrawn figures against
the originals beside them. No credentials are involved.

One panel differs on purpose. Figure 5's left half is a histogram of individual equipment
ages in the original, and per-item ages are not published here, so the redrawn version
shows the published counts instead and says so on the figure.

### With the database, which only the authors can do

`eda_homecommand_v4.py` is what produced the CSVs in the first place. It cannot be run
from this repository: `POSTGRES_URI` is a production database credential and
`EDA_EXCLUDE_USER_IDS` is the account identifier the analysis excludes, and publishing
either would defeat the point of excluding it. The script is here so the method is
auditable, not so it can be re-run by a third party.

```
export POSTGRES_URI="postgresql://..."     # production credential, not published
export EDA_EXCLUDE_USER_IDS="<uuid>"       # excluded account, not published
python eda_homecommand_v4.py
```

The session is opened read-only at the connection level. The script writes only the
files listed above, and refuses to start if the exclusion list is missing.

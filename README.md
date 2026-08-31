# HomeCommand capstone — the measurement set behind the report

This folder is the single source of truth for every corpus, criteria and cost figure
in the report. Each number the report states about the document corpus, the seven
technical success criteria, extraction traceability or the platform's metered AI
spend is a row in one of the tables below, and the report cites those tables. A
reader holding this folder can check the report without access to the database.

## What is here

| File | What it holds |
|---|---|
| `eda_homecommand_v4.py` | The one script that produced everything here, in one read-only run |
| `01_…07_*.png` | The seven figures in the Exploratory Data Analysis section |
| `figures_data/fig01…fig07_*.csv` | The aggregate table behind each figure |
| `figures_data/measurement.csv` | Run date, basis, script version |
| `figures_data/corpus_counts.csv` | Records, active records, records with text, distinct documents, hash coverage, status counts, upload routes, index sizes, homes |
| `figures_data/criteria_t1_completion.csv` … `criteria_t6_traceability.csv` | The technical success criteria T1–T6, each with its population, numerator and denominator |
| `figures_data/criteria_t6_by_document.csv` | Per-document quote-resolution rates for the traceability replay |
| `figures_data/cost_events.csv` | Metered model spend, the 9 August anchor day, the reasoning-token correction |
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

```
export POSTGRES_URI="postgresql://..."
export EDA_EXCLUDE_USER_IDS="<uuid>[,<uuid>]"
python eda_homecommand_v4.py
```

The session is opened read-only at the connection level. The script writes only the
files listed above.

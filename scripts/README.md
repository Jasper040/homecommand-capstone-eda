# The measurement scripts

Four scripts stand behind figures the report states. Two of them run here, with no
database, no credentials and no configuration file. The other two are published so
the procedure can be read, and the table says plainly what each one would need to
execute end to end.

| Script | Runs with nothing installed but Python? | What it produces |
|---|---|---|
| `capstone_cost_model.py` | **Yes** | Every cost figure in the Cost Analysis section |
| `compare_findings_accuracy.py` | Yes, given two run snapshots | The comparison between two extraction runs |
| `capstone_heterogeneity.py` | Yes, over any folder of PDFs | Section-label overlap across inspection reports |
| `capstone_claims_check.py` | Reads here; needs the private repository to check anything | Re-runs the citation directives carried beside each claim |

## `capstone_cost_model.py` — the one to run first

```bash
python capstone_cost_model.py
```

No arguments, no environment, no network. It prints the fixed platform cost, the
build overhead held separate from it, the marginal cost per document, the breakeven
subscriber count and the sensitivity table, each line carrying where its input came
from: an invoice read directly, a read-only query, a direct experiment, or a figure
the operator supplied and which is labeled as such. Anything not yet known is `None`
and prints as a named gap instead of a plausible-looking number.

`--db` re-queries the production database instead of using the stored aggregates. It
opens a read-only session and writes nothing, and it refuses to start unless
`CAPSTONE_PRODUCTION_REF` names the database the published figures were measured
against, because a run against any other would produce different numbers under the
same labels.

## `capstone_heterogeneity.py` — the claim that rules cannot parse these documents

The report's central technical claim is that inspection reports have no shared
structure: across the twenty-four in the corpus, 1,443 distinct section labels
appear, 1,108 of them in exactly one report, and none in all of them. This script is
that measurement, re-implemented from the published procedure rather than lifted from
the original run, so agreement between the two is evidence that the procedure as
documented is the one that executed.

```bash
python capstone_heterogeneity.py --pdf-dir <folder of inspection reports>
```

Point it at any folder of inspection-report PDFs and it will report labels,
singletons, universal labels and pairwise Jaccard overlap for that set. The corpus
the report measured is not here: those are inspection reports on identifiable homes,
and publishing them would publish the addresses and the defects found at them. The
`--fetch` mode that retrieves them needs both database credentials and object-storage
access, and the seven-document validation case needs a manifest that maps labels to
filenames, which is withheld for the same reason.

## `capstone_claims_check.py` — how the report's factual claims were held in place

Each factual claim in the evidence documents carries a machine-checkable directive in
an HTML comment beside the sentence it guards: that a symbol is still defined in a
named file, that a quoted substring still appears in it byte for byte, that two
documents declaring the same figure declare the same value, or that a claim already
found false has not returned. The script re-runs them.

It is published so the mechanism can be read. Executing it against this project would
require the application source, which is not public: the checks resolve symbols and
quotations inside the extraction engine. `--figures` additionally re-queries the
production database.

The reason it exists is in the module docstring: an evidence pack once asserted
something a module docstring said, which the code had stopped doing two versions
earlier. Nothing failed, and the claim merely looked verified.

## `compare_findings_accuracy.py`

Takes two JSON snapshots of a document's extracted findings and reports counts,
severity and category distributions, per-page coverage and a paired diff. It compares
two runs against each other. Neither is a reference set, so what it measures is
change between runs, not correctness, and the report says so where it cites this.

## Relationship to the private copies

These are the scripts that produced the published figures. Three differences, all of
them removals:

1. The production database identifier is read from `CAPSTONE_PRODUCTION_REF` instead
   of being written into two files.
2. The seven-document validation manifest, which names private inspection reports by
   address, is loaded from a file that is not in this repository.
3. Two vendor invoice numbers are given as their ordinals.

No measurement code differs.

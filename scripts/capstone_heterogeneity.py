#!/usr/bin/env python
"""Re-run the inspection-report section-label heterogeneity measurement.

Why this exists
---------------
The Capstone report's central technical claim is that these documents defeat
rule-based parsing, and the evidence for it is one number: across the distinct
inspection reports in the corpus, **no section label appears in every report**.
That claim was first measured over seven documents and later over twenty-four,
and both times it was reported as a figure in a Markdown file. A figure in a
Markdown file is an assertion. This script is the measurement.

It is deliberately re-implemented from the *published procedure* rather than
lifted from the original run, so that agreement between the two is evidence
that the procedure as documented is what was actually executed.

Read-only by construction
-------------------------
The measurement opens PDFs and computes set overlaps. ``--fetch`` reads
``raw.documents`` over a read-only session and downloads objects from the
document bucket; it refuses to run unless the connection string resolves to the
expected project. Nothing here opens a writable session, and no code path
writes to a datastore.

The procedure (Data Understanding, deep dive D1)
------------------------------------------------
1. Open each PDF with PyMuPDF and take ``page.get_text()`` for every page.
2. A line is a candidate **section label** if it is 3-60 characters long, has at
   least three alphabetic characters, and at least 85% of its letters are
   uppercase.
3. Normalize: strip leading Roman, Arabic or letter numbering; lowercase; drop
   non-alphanumerics; delete the standalone word ``system``/``systems``;
   collapse whitespace.
4. Reduce each report to the *set* of its normalized labels.
5. Compute pairwise Jaccard overlap across every pair.

One note on the median. The published figures use the **upper** median for an
even number of pairs (the larger of the two middle values) rather than their
average. Both are printed below. It changes two seven-document subset figures
and no headline figure.

Usage
-----
    python scripts/capstone_heterogeneity.py --validate-seven
        Reproduce the seven-document case from files in 3-Source-documents/.

    python scripts/capstone_heterogeneity.py --fetch --cache-dir <dir>
        Download the distinct inspection reports read-only, then measure.

    python scripts/capstone_heterogeneity.py --pdf-dir <dir>
        Measure every PDF in a directory.

Exit code 0 = every published figure reproduced, 1 = at least one differs.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import statistics as st
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Set

REPO = Path(__file__).resolve().parents[1]
PRODUCTION_REF = os.environ.get("CAPSTONE_PRODUCTION_REF", "")

#: The seven documents of the original D1 measurement, as a label -> path
#: mapping loaded from a manifest beside this script. Four are published vendor
#: samples; three are inspection reports on private homes, so the filenames stay
#: out of this file and out of this repository. Without the manifest,
#: ``--validate-seven`` reports that it cannot run; every other mode is
#: unaffected.
SEVEN_MANIFEST = Path(__file__).with_name("seven_documents.json")


def load_seven() -> Dict[str, str]:
    """Label -> path-relative-to-source-dir for the seven validation documents."""
    if not SEVEN_MANIFEST.exists():
        return {}
    return json.loads(SEVEN_MANIFEST.read_text(encoding="utf-8"))

#: Published figures this script checks itself against.
PUBLISHED_SEVEN = {
    "labels": 198, "singletons": 166, "universal": 2, "ge4": 5, "pairs": 21,
    "mean": 0.114, "median_upper": 0.073, "min": 0.016, "max": 0.696,
}
PUBLISHED_24 = {
    "labels": 1443, "singletons": 1108, "universal": 0, "ge4": 70, "pairs": 276,
    "mean": 0.035, "median_upper": 0.004, "min": 0.000, "max": 0.696,
}

_LEADING_NUMBER = re.compile(r"^\s*(?:[IVXLC]+[.)]|\d+[.)]|[A-Za-z][.)])\s*")


def is_candidate_label(line: str) -> bool:
    """Step 2 of the procedure."""
    s = line.strip()
    if not (3 <= len(s) <= 60):
        return False
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 3:
        return False
    return sum(1 for c in letters if c.isupper()) / len(letters) >= 0.85


def normalize_label(s: str) -> str:
    """Step 3 of the procedure."""
    s = _LEADING_NUMBER.sub("", s.strip()).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(systems?)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def labels_of(path: Path) -> Set[str]:
    """Steps 1, 2, 3 and 4 for one document."""
    import fitz  # PyMuPDF

    out: Set[str] = set()
    with fitz.open(path) as doc:
        for page in doc:
            for line in page.get_text().splitlines():
                if is_candidate_label(line):
                    norm = normalize_label(line)
                    if norm:
                        out.add(norm)
    return out


def jaccard(a: Set[str], b: Set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def measure(sets: Dict[str, Set[str]]) -> dict:
    """Step 5, plus the label-frequency counts the report quotes."""
    keys = list(sets)
    every = set().union(*sets.values()) if sets else set()
    counts = {lab: sum(1 for k in keys if lab in sets[k]) for lab in every}
    pairs = sorted(jaccard(sets[a], sets[b]) for a, b in itertools.combinations(keys, 2))
    return {
        "n": len(keys),
        "labels": len(every),
        "singletons": sum(1 for c in counts.values() if c == 1),
        "ge4": sum(1 for c in counts.values() if c >= 4),
        "universal": sorted(lab for lab, c in counts.items() if c == len(keys)),
        "pairs": len(pairs),
        "mean": st.mean(pairs) if pairs else 0.0,
        "median": st.median(pairs) if pairs else 0.0,
        "median_upper": st.median_high(pairs) if pairs else 0.0,
        "min": min(pairs) if pairs else 0.0,
        "max": max(pairs) if pairs else 0.0,
        "counts": counts,
    }


def report(title: str, m: dict, published: dict | None = None) -> bool:
    print(f"\n=== {title}  (n = {m['n']})")
    rows = [
        ("distinct section labels", m["labels"], "labels", "{:d}"),
        ("appear in exactly one report", m["singletons"], "singletons", "{:d}"),
        ("appear in >= 4 reports", m["ge4"], "ge4", "{:d}"),
        ("appear in EVERY report", len(m["universal"]), "universal", "{:d}"),
        ("pairs compared", m["pairs"], "pairs", "{:d}"),
        ("mean pairwise Jaccard", m["mean"], "mean", "{:.3f}"),
        ("median, upper (as published)", m["median_upper"], "median_upper", "{:.3f}"),
        ("median, conventional", m["median"], None, "{:.4f}"),
        ("minimum pair", m["min"], "min", "{:.3f}"),
        ("maximum pair", m["max"], "max", "{:.3f}"),
    ]
    ok = True
    for label, value, key, fmt in rows:
        line = f"    {label:32s} {fmt.format(value):>8s}"
        if published and key and key in published:
            want = published[key]
            same = abs(value - want) < (0.0005 if isinstance(want, float) else 0.5)
            line += f"   published {fmt.format(want):>8s}   {'OK' if same else 'DIFFERS'}"
            ok = ok and same
        print(line)
    if m["universal"]:
        print(f"    universal labels: {', '.join(m['universal'])}")
    top = sorted(m["counts"].items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    print("    most-shared: " + ", ".join(f"{lab} {c}/{m['n']}" for lab, c in top))
    return ok


def fetch(cache: Path) -> List[Path]:
    """Download the distinct inspection reports read-only. Never writes."""
    import psycopg2

    sys.path.insert(0, str(REPO))
    from dotenv import load_dotenv

    load_dotenv(REPO / ".env")
    uri = os.environ["POSTGRES_URI"].replace("postgresql+psycopg2://", "postgresql://")
    user = re.search(r"//([^:]+):", uri).group(1)
    ref = user.split(".", 1)[1] if "." in user else None
    if ref != PRODUCTION_REF:
        raise SystemExit(f"refusing to fetch: connection resolves to {ref!r}, "
                         f"expected {PRODUCTION_REF!r}")
    print(f"connected as {user} -> {ref} (production), read-only")

    conn = psycopg2.connect(uri)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    cur.execute("""
        with r as (
          select d.id, d.storage_path, d.created_at,
                 (select string_agg(distinct s.content, '' order by s.content)
                    from document_sections s where s.document_id = d.id) as txt,
                 (select count(*) from home_issues i
                   where i.source_document_id = d.id and i.archived_at is null) as iss,
                 (select count(*) from home_equipment e
                   where e.source_document_id = d.id and e.archived_at is null) as eq
          from raw.documents d
          where d.archived_at is null and d.doc_type::text = 'inspection_report'),
        r2 as (select *, md5(txt) as h from r where txt is not null),
        rep as (select distinct on (h) * from r2 order by h, (iss + eq) desc, id)
        select storage_path, created_at from rep order by created_at
    """)
    rows = cur.fetchall()
    conn.close()
    print(f"{len(rows)} distinct inspection reports with extracted text")

    from backend.storage import get_document_storage

    storage = get_document_storage()
    cache.mkdir(parents=True, exist_ok=True)
    out = []
    for i, (path, _created) in enumerate(rows, 1):
        dest = cache / f"{i:02d}.pdf"
        if not dest.exists():
            blob = storage.download(path)
            if blob is None:
                print(f"  {i:2d}. MISSING {path}")
                continue
            dest.write_bytes(blob)
        out.append(dest)
    print(f"cached {len(out)} PDFs in {cache}")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pdf-dir", type=Path, help="measure every PDF in this directory")
    ap.add_argument("--fetch", action="store_true",
                    help="download the distinct inspection reports read-only, then measure")
    ap.add_argument("--cache-dir", type=Path, default=Path("./heterogeneity-pdfs"),
                    help="where --fetch caches downloads (default ./heterogeneity-pdfs)")
    ap.add_argument("--validate-seven", action="store_true",
                    help="reproduce the seven-document validation case")
    ap.add_argument("--source-dir", type=Path, default=Path("."),
                    help="folder holding the seven validation documents")
    args = ap.parse_args(argv)

    if not (args.pdf_dir or args.fetch or args.validate_seven):
        ap.error("choose one of --validate-seven, --fetch or --pdf-dir")

    all_ok = True

    if args.validate_seven:
        seven = load_seven()
        if not seven:
            print(f"cannot validate: no document manifest at {SEVEN_MANIFEST}. "
                  "Three of the seven are private property records and are not published; "
                  "see scripts/README.md.")
            return 1
        missing = [n for n, rel in seven.items() if not (args.source_dir / rel).exists()]
        if missing:
            print(f"cannot validate: missing {missing} under {args.source_dir}")
            return 1
        sets = {name: labels_of(args.source_dir / rel) for name, rel in seven.items()}
        all_ok &= report("Seven-document validation case (D1)", measure(sets), PUBLISHED_SEVEN)

    paths: List[Path] = []
    if args.fetch:
        paths = fetch(args.cache_dir)
    elif args.pdf_dir:
        paths = sorted(args.pdf_dir.glob("*.pdf"))
        if not paths:
            print(f"no PDFs in {args.pdf_dir}")
            return 1

    if paths:
        sets = {p.name: labels_of(p) for p in paths}
        # The published headline covers the 24 reports the corpus held on
        # 15 August 2026; a later corpus has more, so compare only when the
        # count matches.
        published = PUBLISHED_24 if len(sets) == 24 else None
        all_ok &= report(f"Corpus of {len(sets)} inspection reports", measure(sets), published)
        if len(sets) > 24:
            trimmed = dict(list(sets.items())[:24])
            all_ok &= report("The published 24 (oldest by ingest order)",
                             measure(trimmed), PUBLISHED_24)

    print()
    print("every published figure reproduced" if all_ok
          else "at least one figure DIFFERS from the published value")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

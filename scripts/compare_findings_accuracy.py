"""Compare findings from two timing-run snapshots for accuracy delta.

Reports:
  - counts (total findings, severity distribution, category distribution)
  - structure_label_raw distribution
  - % of findings with inventory_label_raw populated
  - per-page coverage (% of pages with at least one finding)
  - sample diff: pair findings by closest page_number + summary_label substring
    and report counts of (matched / unique-to-A / unique-to-B)

Usage:
    python scripts/compare_findings_accuracy.py findings_snapshot_a.json findings_snapshot_b.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List


def _load(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _summarize(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    findings: List[Dict[str, Any]] = snapshot.get("findings") or []
    severity = Counter((f.get("severity_raw") or "<null>") for f in findings)
    category = Counter((f.get("category_raw") or "<null>") for f in findings)
    structures = Counter((f.get("structure_label_raw") or "<null>") for f in findings)
    pages = Counter((f.get("page_number") or 0) for f in findings)
    with_inventory_label = sum(1 for f in findings if (f.get("inventory_label_raw") or "").strip())
    with_photo_refs = sum(
        1 for f in findings if (f.get("related_photo_labels") or [])
    )
    with_recommended = sum(
        1 for f in findings if (f.get("recommended_action_raw") or "").strip()
    )
    return {
        "count": len(findings),
        "severity": dict(severity),
        "category": dict(category),
        "structures": dict(structures),
        "distinct_pages": len(pages),
        "with_inventory_label_raw_pct": (
            round(100 * with_inventory_label / len(findings), 1) if findings else 0.0
        ),
        "with_photo_refs_pct": (
            round(100 * with_photo_refs / len(findings), 1) if findings else 0.0
        ),
        "with_recommended_action_pct": (
            round(100 * with_recommended / len(findings), 1) if findings else 0.0
        ),
    }


def _pairwise_match(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> Dict[str, int]:
    """Heuristic pairing: a finding in A matches one in B if same page AND
    summary_label first 30 chars overlap. Counts matched / a-only / b-only."""

    def _key(f: Dict[str, Any]) -> tuple:
        page = f.get("page_number") or 0
        label = (f.get("summary_label") or f.get("description_raw") or "").lower().strip()[:30]
        return (page, label)

    keys_a = {_key(f) for f in a}
    keys_b = {_key(f) for f in b}
    return {
        "matched": len(keys_a & keys_b),
        "a_only": len(keys_a - keys_b),
        "b_only": len(keys_b - keys_a),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshot_a")
    ap.add_argument("snapshot_b")
    args = ap.parse_args()

    a = _load(args.snapshot_a)
    b = _load(args.snapshot_b)

    sum_a = _summarize(a)
    sum_b = _summarize(b)
    pair = _pairwise_match(a.get("findings") or [], b.get("findings") or [])

    print(f"A: label={a.get('label')}  count={sum_a['count']}")
    print(f"B: label={b.get('label')}  count={sum_b['count']}")
    print()
    print("=== Per-snapshot summary ===")
    print(json.dumps({"A": sum_a, "B": sum_b}, indent=2))
    print()
    print("=== Pairwise heuristic match (same page + same 30-char label prefix) ===")
    print(json.dumps(pair, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

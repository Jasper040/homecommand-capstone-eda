#!/usr/bin/env python
"""Re-verify the Capstone evidence documents against this repository.

Why this exists
---------------
The 17 August data-preparation evidence pack asserted that PyMuPDF's text output fed
``document_sections``. It did not. The claim was wrong because it was copied verbatim
from a module docstring that was itself wrong, and which was corrected two days later.
Nothing failed; the claim simply looked verified and was not.

A docstring is prose that happens to live in a ``.py`` file, and it rots exactly like a
Markdown note. So the capstone documents do not get to *say* they were verified — they
carry machine-checkable directives next to each claim, and this script re-runs them.

Because the directive sits immediately beside the sentence it guards, in the same file,
a claim and its check cannot drift apart. Move the sentence and you move the directive.

Directives
----------
Each is an HTML comment, so it is invisible in rendered Markdown::

    <!-- verify:symbol <repo-relative-path>::<name> -->
        `name` is defined in that file (def / class / assignment / export).

    <!-- verify:quote <repo-relative-path> :: <exact substring> -->
        that substring still appears in that file, byte for byte.

    <!-- verify:shared <figure-id> = <value> -->
        every document that declares `figure-id` declares the same value.

    <!-- verify:figure <figure-id> = <value> :: <sql> -->
        re-run the query read-only and compare. Reported as drift, not failure,
        unless --strict: the corpus legitimately grows.

    <!-- verify:forbid <exact text> -->
        this string must not appear in any PROSE document: the ``--prose`` files
        plus any evidence file named ``*-PROSE.md`` (PROSE_DOC_RE) that does not
        declare ``verify:superseded``. Ordinary evidence documents are exempt --
        an evidence pack exists precisely to quote a false claim and correct it
        (see the NOTE in main()). Register a claim here once it has been found
        false, so it cannot quietly return to the report. Directive text itself
        is stripped before the scan, so declaring it is safe.

    <!-- verify:csv <path> :: <key_col>=<key_val> :: <value_col> = <expected> -->
        the published measurement set (EDA/figures_data/*.csv, relative to the
        evidence dir) says so. Never touches the database, so it cannot drift:
        a mismatch means the prose and the table the graders hold disagree.

    <!-- verify:superseded <reason> -->
        this document is historical. It is exempt from the automatic prose scan
        described below, because a superseded draft is expected to still contain the
        claims that superseded it. Its other directives are still checked.

Prose documents
---------------
``--prose <path>`` adds a report document (``.docx``, ``.md``, ``.txt``) to the scan.
Any evidence file named ``*-PROSE.md`` is scanned this way *automatically*, without
being named on the command line, unless it declares ``verify:superseded``. That rule
exists because a paste-ready draft is report text that happens to live in the evidence
folder: the 15 August ``SUCCESS-METHODS-PROSE.md`` carried the registered-false claim
"with OCR fallback" for a week, because ``forbid`` ran only against ``--prose``
documents and a draft was never one. A file that is one paste away from the report is
prose.

Prose carries no directives — it is report text — so two checks apply to it instead:

* every ``verify:forbid`` string is searched for;
* every snake_case identifier it names must exist somewhere in the repository,
  which catches a renamed function or a mistyped table prefix.

Semantic errors are NOT catchable this way. A sentence can name a real symbol and
still describe it wrongly. This tool narrows the surface; it does not close it.

Usage
-----
    python scripts/capstone_claims_check.py              # code + cross-doc checks
    python scripts/capstone_claims_check.py --figures    # also re-query the database
    python scripts/capstone_claims_check.py --strict     # figure drift is a failure

Exit code 0 = clean, 1 = at least one failure.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]

#: Where the capstone evidence documents live. They are working files that sit
#: outside this repository, so there is no default: set CAPSTONE_EVIDENCE_DIR or
#: pass --evidence-dir.
DEFAULT_EVIDENCE_DIR = Path(".")

DIRECTIVE_RE = re.compile(r"<!--\s*verify:(\w+)\s+(.*?)\s*-->", re.DOTALL)

#: Evidence files whose name marks them as paste-ready report text, not analysis.
#: These are scanned as prose (forbidden claims + identifier resolution) in addition
#: to their directives. Opt out with ``verify:superseded``.
PROSE_DOC_RE = re.compile(r"-PROSE[.]md$", re.IGNORECASE)

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if os.name == "nt" and not os.environ.get("WT_SESSION"):
    GREEN = RED = YELLOW = DIM = RESET = ""


class Result:
    def __init__(self) -> None:
        self.ok: List[str] = []
        self.failed: List[str] = []
        self.drifted: List[str] = []
        self.skipped: List[str] = []

    def passed(self, msg: str) -> None:
        self.ok.append(msg)

    def fail(self, msg: str) -> None:
        self.failed.append(msg)

    def drift(self, msg: str) -> None:
        self.drifted.append(msg)

    def skip(self, msg: str) -> None:
        self.skipped.append(msg)


def find_docs(evidence_dir: Path) -> List[Path]:
    if not evidence_dir.is_dir():
        return []
    return sorted(p for p in evidence_dir.rglob("*.md") if "_superseded" not in p.parts)


def read(path: Path) -> str:
    """Text of a document. Understands .docx as well as plain text."""
    if path.suffix.lower() == ".docx":
        import html as _html
        import zipfile
        try:
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return ""
        xml = re.sub(r"</w:p>", "\n", xml)
        return _html.unescape(re.sub(r"<[^>]+>", "", xml))
    return path.read_text(encoding="utf-8", errors="replace")


def strip_directives(text: str) -> str:
    """Remove HTML comments so a forbid directive never matches itself."""
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


# ------------------------------------------------------------------- identifiers
IDENT_RE = re.compile(r"\b_?[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")

#: Words that look like identifiers but are ordinary prose or well-known terms.
IDENT_ALLOW = {
    "read_only", "set_session", "e_g", "i_e", "de_duplicated", "end_to_end",
    "snake_case", "word_boundary", "delete_and_insert", "read_time",
    # Mathematical notation used in prose formulas, not code:
    #   life_consumed = age / expected_life_years
    #   RRF(d) = sum over i of 1 / (k + rank_i(d))
    "life_consumed", "rank_i",
    # LLM-invented JSON keys in home_equipment.specifications, quoted in the report
    # as evidence of an undeclared vocabulary. They are data, not code, and their
    # ABSENCE from the repo is the point of the sentence that names them.
    "energy_source", "heat_source",
    # A libpq/PgBouncer startup option, not a repo symbol.
    "default_transaction_read_only",
    # Capstone analysis artefacts: they live in the Capstone folder, not this repo.
    "eda_homecommand", "eda_homecommand_v2", "eda_homecommand_v3", "eda_homecommand_v4",
    "eda_homecommand_public",
    "figures_data", "fig01_corpus", "fig02_agreement", "fig03_issues",
    "fig04_cost", "fig05_equipment", "fig06_timing", "fig07_rag",
}


def repo_identifiers() -> set:
    """Every snake_case token appearing anywhere in the repo's source."""
    found = set()
    roots = [REPO / "backend", REPO / "frontend" / "client" / "src", REPO / "scripts"]
    exts = {".py", ".ts", ".tsx", ".js", ".sql", ".json"}
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in exts or not path.is_file():
                continue
            if "node_modules" in path.parts or "__pycache__" in path.parts:
                continue
            try:
                found.update(IDENT_RE.findall(path.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                continue
    return found


def check_prose(path: Path, forbidden: List[Tuple[str, str]], known: set,
                res: Result) -> None:
    text = strip_directives(read(path))
    if not text.strip():
        res.fail(f"{path.name}: could not read any text")
        return
    low = text.lower()
    for needle, declared_in in forbidden:
        if needle.lower() in low:
            res.fail(
                f"{path.name}: FORBIDDEN CLAIM present: {needle!r}\n"
                f"        (registered as false in {declared_in})"
            )
    def resolves(tok: str) -> bool:
        if tok in known or tok.lower() in IDENT_ALLOW:
            return True
        # A table-of-contents line glues the page number onto the entry
        # ("process_basic_extraction14"), and .docx text extraction cannot see the
        # tab between them. Retry without trailing digits before reporting.
        trimmed = tok.rstrip("0123456789")
        return trimmed != tok and (trimmed in known or trimmed.lower() in IDENT_ALLOW)

    unknown = sorted({tok for tok in IDENT_RE.findall(text) if not resolves(tok)})
    if unknown:
        res.fail(
            f"{path.name}: names {len(unknown)} identifier(s) not found anywhere in "
            f"the repo: {', '.join(unknown[:12])}"
        )
    else:
        res.passed(f"prose {path.name}: identifiers all resolve, no forbidden claims")


# --------------------------------------------------------------------------- symbols
def symbol_defined(source: str, name: str) -> bool:
    """True if `name` looks like it is defined in `source`.

    Deliberately language-loose: this repository is Python + TypeScript, and the
    point is to catch a *renamed or deleted* symbol, not to parse either language.
    """
    escaped = re.escape(name)
    patterns = (
        rf"^\s*(?:async\s+)?def\s+{escaped}\b",                       # python fn
        rf"^\s*class\s+{escaped}\b",                                  # python class
        rf"^\s*{escaped}\s*(?::[^=\n]+)?=",                           # assignment / const
        rf"^\s*export\s+(?:async\s+)?function\s+{escaped}\b",         # ts fn
        rf"^\s*export\s+(?:const|let|type|interface)\s+{escaped}\b",  # ts binding
        rf"^\s*(?:const|let|function)\s+{escaped}\b",                 # ts local
    )
    return any(re.search(p, source, re.MULTILINE) for p in patterns)


def check_symbol(arg: str, doc: Path, res: Result) -> None:
    if "::" not in arg:
        res.fail(f"{doc.name}: malformed symbol directive {arg!r}")
        return
    rel, name = (part.strip() for part in arg.split("::", 1))
    target = REPO / rel
    if not target.is_file():
        res.fail(f"{doc.name}: file not found for symbol {name} -> {rel}")
        return
    if symbol_defined(read(target), name):
        res.passed(f"symbol {rel}::{name}")
    else:
        res.fail(f"{doc.name}: symbol NOT FOUND {name} in {rel} (renamed or deleted?)")


# ---------------------------------------------------------------------------- quotes
def check_quote(arg: str, doc: Path, res: Result) -> None:
    if "::" not in arg:
        res.fail(f"{doc.name}: malformed quote directive {arg!r}")
        return
    rel, needle = (part.strip() for part in arg.split("::", 1))
    target = REPO / rel
    if not target.is_file():
        res.fail(f"{doc.name}: file not found for quote -> {rel}")
        return
    if needle in read(target):
        res.passed(f"quote {rel}: {needle[:48]}")
    else:
        res.fail(
            f"{doc.name}: QUOTE CHANGED in {rel}\n"
            f"        expected: {needle!r}\n"
            f"        -> the comment or docstring was edited. Re-read it before citing it."
        )


# --------------------------------------------------------------------------- figures
SHARED_RE = re.compile(r"^(\w[\w.-]*)\s*=\s*(.+)$", re.DOTALL)


def parse_shared(arg: str) -> Optional[Tuple[str, str]]:
    m = SHARED_RE.match(arg.strip())
    if not m:
        return None
    return m.group(1), m.group(2).strip()


def check_figure(arg: str, doc: Path, res: Result, run_sql: bool, strict: bool,
                 cursor) -> None:
    if "::" not in arg:
        res.fail(f"{doc.name}: malformed figure directive {arg!r}")
        return
    decl, sql = (part.strip() for part in arg.split("::", 1))
    parsed = parse_shared(decl)
    if not parsed:
        res.fail(f"{doc.name}: malformed figure declaration {decl!r}")
        return
    fid, expected = parsed
    if not run_sql:
        res.skip(f"figure {fid} (pass --figures to re-query)")
        return
    if cursor is None:
        res.skip(f"figure {fid} (no database connection)")
        return
    try:
        cursor.execute(sql)
        row = cursor.fetchone()
        actual = str(row[0]) if row else "<no rows>"
    except Exception as exc:  # noqa: BLE001 - report, never raise
        res.fail(f"{doc.name}: figure {fid} query failed: {str(exc)[:120]}")
        return
    if _norm_num(actual) == _norm_num(expected):
        res.passed(f"figure {fid} = {actual}")
    else:
        msg = f"{doc.name}: figure {fid} recorded {expected}, database says {actual}"
        (res.fail if strict else res.drift)(msg)


def _norm_num(value: str) -> str:
    return value.replace(",", "").replace("$", "").strip()


# ------------------------------------------------------------------------ csv
def check_csv(arg: str, doc: Path, evidence_dir: Path, res: Result) -> None:
    """``verify:csv <path> :: <key_col>=<key_val> :: <value_col> = <expected>``

    The published measurement set (``EDA/figures_data/*.csv``) is the single
    source of truth the report cites and the graders receive. Unlike
    ``verify:figure`` this never touches the database, so it cannot drift as
    the corpus grows: a mismatch means the prose and the published table
    disagree, which is always a failure.
    """
    parts = [p.strip() for p in arg.split("::")]
    if len(parts) != 3 or "=" not in parts[1] or "=" not in parts[2]:
        res.fail(f"{doc.name}: malformed csv directive {arg!r}")
        return
    rel, selector, decl = parts
    key_col, key_val = (s.strip() for s in selector.split("=", 1))
    value_col, expected = (s.strip() for s in decl.split("=", 1))
    target = evidence_dir / rel
    if not target.is_file():
        res.fail(f"{doc.name}: csv not found -> {rel}")
        return
    with target.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    matches = [r for r in rows if (r.get(key_col) or "").strip() == key_val]
    if not matches:
        res.fail(f"{doc.name}: csv {rel} has no row where {key_col}={key_val}")
        return
    if len(matches) > 1:
        # Silently taking matches[0] would make the answer depend on row order, which is
        # the one thing this directive promises it never does.
        res.fail(f"{doc.name}: csv {rel} has {len(matches)} rows where {key_col}={key_val} "
                 f"- ambiguous, the key must select exactly one row")
        return
    if value_col not in matches[0]:
        res.fail(f"{doc.name}: csv {rel} has no column {value_col!r}")
        return
    actual = (matches[0][value_col] or "").strip()
    if _norm_num(actual) == _norm_num(expected):
        res.passed(f"csv {rel}[{key_col}={key_val}].{value_col} = {actual}")
    else:
        res.fail(f"{doc.name}: csv {rel}[{key_col}={key_val}].{value_col} recorded "
                 f"{expected}, table says {actual}")


# --------------------------------------------------------------------------- runner
def connect():
    """Read-only production connection, or None. Never raises."""
    try:
        import psycopg2  # type: ignore
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        print(f"{YELLOW}psycopg2/dotenv unavailable - skipping figure checks{RESET}")
        return None, None
    load_dotenv(str(REPO / ".env"))
    uri = os.environ.get("POSTGRES_URI")
    if not uri:
        print(f"{YELLOW}POSTGRES_URI unset - skipping figure checks{RESET}")
        return None, None
    try:
        conn = psycopg2.connect(uri)
        conn.set_session(readonly=True, autocommit=True)
        return conn, conn.cursor()
    except Exception as exc:  # noqa: BLE001
        print(f"{YELLOW}database connect failed ({str(exc)[:80]}) - skipping figures{RESET}")
        return None, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--figures", action="store_true", help="re-query figures read-only")
    ap.add_argument("--strict", action="store_true", help="figure drift is a failure")
    ap.add_argument("--quiet", action="store_true", help="only print problems")
    ap.add_argument(
        "--evidence-dir",
        default=os.environ.get("CAPSTONE_EVIDENCE_DIR", str(DEFAULT_EVIDENCE_DIR)),
    )
    ap.add_argument(
        "--prose", action="append", default=[], metavar="PATH",
        help="report document (.docx/.md/.txt) to scan for forbidden claims and "
             "unresolvable identifiers. Repeatable.",
    )
    args = ap.parse_args()

    evidence_dir = Path(args.evidence_dir)
    explicit = args.evidence_dir != str(DEFAULT_EVIDENCE_DIR)
    docs = find_docs(evidence_dir)
    if not docs:
        msg = f"no evidence documents found in {evidence_dir}"
        if explicit:
            # The caller named this directory, so a miss is a real error.
            print(f"{RED}{msg}{RESET}")
            return 1
        # Default path: the capstone documents live outside the repo and are not
        # present on every machine. Skip rather than fail so this stays safe to
        # wire into a hook or CI job.
        print(f"{YELLOW}{msg} - skipping (set CAPSTONE_EVIDENCE_DIR to check them){RESET}")
        return 0

    res = Result()
    conn, cursor = connect() if args.figures else (None, None)
    # figure-id -> {value: [docs]}
    shared: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
    forbidden: List[Tuple[str, str]] = []
    superseded: set = set()
    total = 0

    try:
        for doc in docs:
            text = read(doc)
            for kind, arg in DIRECTIVE_RE.findall(text):
                total += 1
                if kind == "forbid":
                    forbidden.append((arg.strip(), doc.name))
                elif kind == "symbol":
                    check_symbol(arg, doc, res)
                elif kind == "quote":
                    check_quote(arg, doc, res)
                elif kind == "shared":
                    parsed = parse_shared(arg)
                    if not parsed:
                        res.fail(f"{doc.name}: malformed shared directive {arg!r}")
                        continue
                    fid, value = parsed
                    shared[fid][_norm_num(value)].append(doc.name)
                elif kind == "superseded":
                    superseded.add(doc.resolve())
                elif kind == "figure":
                    check_figure(arg, doc, res, args.figures, args.strict, cursor)
                    decl = arg.split("::", 1)[0]
                    parsed = parse_shared(decl)
                    if parsed:
                        shared[parsed[0]][_norm_num(parsed[1])].append(doc.name)
                elif kind == "csv":
                    check_csv(arg, doc, evidence_dir, res)
                else:
                    res.fail(f"{doc.name}: unknown directive verify:{kind}")
    finally:
        if conn is not None:
            conn.close()

    # NOTE: forbid is NOT checked against ordinary evidence documents. An evidence
    # pack exists precisely to quote a false claim and correct it, so a match there is
    # the file doing its job. The exception is a *-PROSE.md draft, which is report text
    # that happens to live in the evidence folder -- see PROSE_DOC_RE.

    prose_targets: List[Path] = []
    seen_prose: set = set()
    for raw in args.prose:
        path = Path(raw)
        if not path.is_file():
            res.fail(f"prose file not found: {raw}")
            continue
        if path.resolve() not in seen_prose:
            seen_prose.add(path.resolve())
            prose_targets.append(path)
    for doc in docs:
        if not PROSE_DOC_RE.search(doc.name):
            continue
        if doc.resolve() in superseded:
            res.skip(f"prose {doc.name} (declared superseded)")
            continue
        if doc.resolve() not in seen_prose:
            seen_prose.add(doc.resolve())
            prose_targets.append(doc)

    # prose documents: forbidden claims + identifier resolution
    if prose_targets:
        known = repo_identifiers()
        for path in prose_targets:
            check_prose(path, forbidden, known, res)

    # cross-document agreement
    for fid, by_value in sorted(shared.items()):
        if len(by_value) == 1:
            value, where = next(iter(by_value.items()))
            if len(where) > 1:
                res.passed(f"shared {fid} = {value} agrees across {len(where)} docs")
        else:
            detail = "; ".join(
                f"{v} in {', '.join(sorted(set(d)))}" for v, d in sorted(by_value.items())
            )
            res.fail(f"DOCUMENTS DISAGREE on {fid}: {detail}")

    # ------------------------------------------------------------------- report
    if not args.quiet:
        print(f"\n{DIM}{len(docs)} documents, {total} directives, "
              f"evidence dir: {evidence_dir}{RESET}")
        for msg in res.ok:
            print(f"  {GREEN}ok{RESET}   {msg}")
    for msg in res.skipped:
        if not args.quiet:
            print(f"  {DIM}skip {msg}{RESET}")
    for msg in res.drifted:
        print(f"  {YELLOW}DRIFT{RESET} {msg}")
    for msg in res.failed:
        print(f"  {RED}FAIL{RESET}  {msg}")

    print()
    if res.failed:
        print(f"{RED}{len(res.failed)} failure(s){RESET}, "
              f"{len(res.drifted)} drift, {len(res.ok)} ok")
        print("A failed quote means the code comment changed under the document. "
              "Re-read the code before editing the prose.")
        return 1
    if res.drifted:
        print(f"{YELLOW}{len(res.drifted)} figure(s) drifted{RESET}, {len(res.ok)} ok - "
              "expected as the corpus grows; date the figure or update it.")
        return 0
    print(f"{GREEN}all {len(res.ok)} checks passed{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Deterministic doc-drift check -- no model call.

A generated document's own front matter and prose carry a handful of
numbers/facts that already have an exact counterpart computed somewhere in
the fact store (`fetch_rule_candidate_rows` for rule counts, `graph.coverage`/
`graph.dialect_coverage` for coverage rates, the `gap`/`entity`/`member`
tables directly). After a `calibrate`/`derive` refresh, those two can drift
apart with nothing to notice it short of a human (or an agent) re-reading
the whole document side-by-side with a fresh brief -- exactly the
judgement-shaped-but-mechanical comparison this module replaces (issue
#161; extended in issue #199 to a dialect-scoped unparsed-line count/
line-recognition rate, since a document naming one dialect's own figures
can drift independently of the system-wide numbers `_coverage_rate_drift`
already checks).

Every check here follows structural.py's contract: pure extraction/
comparison over already-computed facts, no synthesis, no model call, and a
check that can't find the pattern it looks for in a given document skips
silently rather than guessing -- this is deliberately not full NLP-style
fact extraction from arbitrary prose (see the PR description for what that
means is out of scope for v1). Each check is independently additive: a
document with none of the checkable patterns present simply reports no
mismatches, it is never treated as a failure to find them.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import graph
from .brief import fetch_rule_candidate_rows
from .db import resolve_member_by_name
from .validate import BR_REF, split_frontmatter

# Doc types that carry a `module:` front-matter field naming the single
# member this document is about -- see reference/writing-rules.md's front
# matter example (`module: MMP0100`) and batch.py's module_index doc, plus
# executive_brief()'s own executive-summary template.
_MODULE_DOC_TYPES = {"module", "module_index", "executive_summary"}

# The gap-register's opening paragraph states its own totals in this exact
# shape -- see examples/outputs/docs/gap-register.md: "41 gaps total from
# the automated pass (`mfdoc coverage`: 20 high, 20 medium, 1 low* -- ...".
# Matched loosely (case-insensitive, tolerant of the intervening
# "from the automated pass (`mfdoc coverage`:" aside) rather than requiring
# that exact wording -- a real narrative pass may phrase the aside
# differently -- but the literal "N gap(s) total" / "N high, N medium, N
# low" numbers are the load-bearing part, and this only ever fires when
# that specific shape is actually present in the body.
_GAP_TOTAL_RE = re.compile(r"(\d+)\s+gaps?\s+total", re.I)
_GAP_SEVERITY_RE = re.compile(r"(\d+)\s+high,\s*(\d+)\s+medium,\s*(\d+)\s+low", re.I)

# system_brief()'s coverage dict is a fraction (e.g. 0.969); the narrative
# stage conventionally restates it as a percentage in prose -- see
# examples/outputs/docs/system-overview.md's "96.9% line recognition".
_LINE_RECOGNITION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s+line recognition", re.I)

# A dialect-scoped narrative claim (a language-guide's own prose, a
# calibration write-up) states these as an absolute count and a raw
# fraction rather than system-overview's rounded percentage -- see issue
# #199's "N unparsed lines"/"X.XXXX line-recognition rate" examples. Both
# are matched loosely (case-insensitive, "line-recognition"/"line
# recognition" either hyphenated or not) since a narrative pass phrases
# this by hand; the literal number is the load-bearing part.
_UNPARSED_COUNT_RE = re.compile(r"(\d+)\s+unparsed lines?", re.I)
_DIALECT_RATE_RE = re.compile(r"(\d+\.\d+)\s+line[- ]recognition rate", re.I)


def _rule_count_drift(conn, fm: dict, body: str) -> list[str]:
    """Compares the highest `MODULE:BR-nnn` id actually cited in `body`
    against `fetch_rule_candidate_rows`'s current count for that member --
    the same row set `module_brief`/`executive_brief` number `BR-nnn` IDs
    from, and the one `mfdoc calibrate`/`mfdoc derive` change when a
    dialect's rule recognition improves or source changes.

    `MEMBER:BR-nnn` (bare, not double-bracketed) is the one number in a
    narrative document that is contractually fixed text, not paraphrased
    prose -- reference/writing-rules.md requires the narrative pass to
    "copy it into the document" verbatim -- which is what makes this check
    reliable where a body-prose numeric claim in general is not.

    Only meaningful for a document naming a single member via a `module:`
    front-matter field (`doc_type: module`/`module_index`/
    `executive_summary`); returns `[]` for any other doc_type, an absent/
    non-string `module` field, or a document with no `MEMBER:BR-nnn`
    reference for that member at all (nothing to compare)."""
    if fm.get("doc_type") not in _MODULE_DOC_TYPES:
        return []
    module_name = fm.get("module")
    if not isinstance(module_name, str) or not module_name.strip():
        return []
    matches, ambiguous_libs = resolve_member_by_name(conn, module_name)
    if ambiguous_libs:
        libs = ", ".join(ambiguous_libs)
        return [
            f"module '{module_name}' is ambiguous across libraries ({libs}) in the "
            "current index -- cannot check its rule count without a library-qualified name"
        ]
    if not matches:
        return [
            f"module '{module_name}' no longer resolves to any member in the current "
            "index -- this document's citations cannot be checked against it at all"
        ]
    rows, _ = fetch_rule_candidate_rows(conn, module_name)
    current_count = len(rows)
    cited_ns = [
        int(m.group("n")) for m in BR_REF.finditer(body)
        if m.group("member").upper() == module_name.upper()
    ]
    if not cited_ns:
        return []
    max_cited = max(cited_ns)
    if max_cited == current_count:
        return []
    return [
        f"{module_name}: document's highest cited rule id is "
        f"{module_name}:BR-{max_cited:03d}, but the current fact store has "
        f"{current_count} rule candidate(s) for this member -- regenerate from a "
        f"fresh `mfdoc brief --module {module_name}`"
        + (f" / `--executive {module_name}`" if fm.get("doc_type") == "executive_summary" else "")
    ]


def _sources_drift(conn, fm: dict) -> list[str]:
    """Every name in front matter's `sources:` list should still resolve to
    a real member or entity in the current index -- a name that doesn't is
    concrete evidence this document was generated against a different
    ingest (a rename, a removed member, a re-scoped project.yml), even
    before reading a word of the body.

    Exempts `doc_type: language-guide` -- `templates/language-guide.md`
    populates `sources` with a descriptive placeholder (`["{DIALECT} source
    files"]`), not a member/entity name, per its own design (issue #91).
    Mirrors `validate.py`'s identical exclusion for the same doc type and
    the same reason -- without it, a real, freshly generated language guide
    could never validate clean under this check (issue #199 review)."""
    if fm.get("doc_type") == "language-guide":
        return []
    sources = fm.get("sources")
    if not isinstance(sources, list):
        return []
    problems: list[str] = []
    for name in sources:
        if not isinstance(name, str) or not name.strip():
            continue
        matches, ambiguous_libs = resolve_member_by_name(conn, name)
        if matches or ambiguous_libs:
            continue
        entity_row = conn.execute(
            "SELECT 1 FROM entity WHERE UPPER(name)=UPPER(?) LIMIT 1", (name,)
        ).fetchone()
        if entity_row:
            continue
        problems.append(
            f"source '{name}' listed in front matter no longer resolves to any member "
            "or entity in the current index"
        )
    return problems


def _gap_register_drift(conn, fm: dict, body: str) -> list[str]:
    """`doc_type: gap-register` only (see templates/gap-register.md) --
    compares the "N gap(s) total" / "N high, N medium, N low" figures
    convention (see this module's own `_GAP_TOTAL_RE`/`_GAP_SEVERITY_RE`
    docstrings) against a fresh `COUNT(*) FROM gap` / per-severity
    breakdown. Either pattern, or both, may be absent -- each is checked
    independently and silently skipped when its own pattern isn't found."""
    if fm.get("doc_type") != "gap-register":
        return []
    problems: list[str] = []
    total_match = _GAP_TOTAL_RE.search(body)
    if total_match:
        claimed_total = int(total_match.group(1))
        current_total = conn.execute("SELECT COUNT(*) FROM gap").fetchone()[0]
        if claimed_total != current_total:
            problems.append(
                f"document states {claimed_total} gap(s) total, but the current fact "
                f"store has {current_total} -- regenerate from a fresh `mfdoc coverage`"
            )
    sev_match = _GAP_SEVERITY_RE.search(body)
    if sev_match:
        claimed = {
            "high": int(sev_match.group(1)),
            "medium": int(sev_match.group(2)),
            "low": int(sev_match.group(3)),
        }
        current = dict(
            conn.execute("SELECT severity, COUNT(*) FROM gap GROUP BY severity").fetchall()
        )
        for severity, claimed_n in claimed.items():
            current_n = current.get(severity, 0)
            if claimed_n != current_n:
                problems.append(
                    f"document states {claimed_n} {severity}-severity gap(s), but the "
                    f"current fact store has {current_n} -- regenerate from a fresh "
                    "`mfdoc coverage`"
                )
    return problems


def _coverage_rate_drift(conn, fm: dict, body: str) -> list[str]:
    """`doc_type: system-overview` only -- compares a "NN.N% line
    recognition" claim (see this module's `_LINE_RECOGNITION_RE` docstring)
    against a freshly recomputed `graph.coverage()['line_recognition_rate']`.
    Tolerance of 0.05 percentage points absorbs rounding between this
    check's own rounding and whatever rounding the narrative pass applied
    when it wrote the percentage down."""
    if fm.get("doc_type") != "system-overview":
        return []
    match = _LINE_RECOGNITION_RE.search(body)
    if not match:
        return []
    claimed = float(match.group(1))
    current = round(graph.coverage(conn)["line_recognition_rate"] * 100, 1)
    if abs(claimed - current) > 0.05:
        return [
            f"document states {claimed}% line recognition, but the current fact store "
            f"computes {current}% -- regenerate from a fresh `mfdoc brief --system`"
        ]
    return []


def _decimal_places(text: str) -> int:
    """Number of digits after the decimal point in a `\\d+\\.\\d+`-shaped
    match's text, e.g. `2` for `"0.90"` -- used to round the fact store's
    current rate to the same precision a document actually claimed, rather
    than accepting anything within a fixed absolute tolerance (issue #199
    review: a fixed 0.0005 tolerance would accept a claim that is stale at
    the precision it was itself written to, e.g. 0.9004 read as "matching"
    a true 0.9000)."""
    return len(text.split(".", 1)[1])


def _dialect_stats_drift(conn, fm: dict, body: str) -> list[str]:
    """Compares a dialect-scoped "N unparsed lines" / "X.XXXX
    line-recognition rate" claim in `body` against `graph.dialect_coverage`
    for the dialect named in front matter's `dialect:` field -- see
    `templates/language-guide.md`, the one document type that names its
    single subject dialect this way (its `{DIALECT}` front-matter
    placeholder).

    Restricted to `doc_type: language-guide` specifically, unlike
    `_sources_drift`'s doc_type-agnostic gating: `dialect` is not a
    document-wide scope marker for every document type -- `templates/
    module.md` also carries one, but names the *member's own* dialect, not
    a claim about the whole dialect's totals. Comparing a module doc's
    member-scoped claim against `dialect_coverage`'s whole-dialect figures
    would misreport drift for a document that was never making a
    dialect-wide claim in the first place (issue #199 review).

    Both regexes are searched before `dialect_coverage` is ever called, so
    a tree containing many documents that don't state these figures at all
    (the common case) doesn't pay for two `COUNT(*)` scans per document
    just to find nothing to compare.

    Only meaningful when `dialect:` is a real string naming a dialect this
    fact store actually ingested anything for; a dialect with zero ingested
    source lines has nothing live to compare against (silently skipped,
    same "no basis for comparison" contract every other check here
    follows) rather than reported as 0-vs-N drift."""
    if fm.get("doc_type") != "language-guide":
        return []
    dialect = fm.get("dialect")
    if not isinstance(dialect, str) or not dialect.strip():
        return []
    dialect = dialect.strip()
    count_match = _UNPARSED_COUNT_RE.search(body)
    rate_match = _DIALECT_RATE_RE.search(body)
    if not count_match and not rate_match:
        return []
    stats = graph.dialect_coverage(conn, dialect)
    if not stats["source_lines"]:
        return []
    problems: list[str] = []
    if count_match:
        claimed = int(count_match.group(1))
        current = stats["unparsed_lines"]
        if claimed != current:
            problems.append(
                f"document states {claimed} unparsed line(s) for dialect '{dialect}', "
                f"but the current fact store has {current} -- regenerate from a fresh "
                f"`mfdoc lang-guide --config project.yml --dialect {dialect}`"
            )
    if rate_match:
        claimed_text = rate_match.group(1)
        claimed_rate = float(claimed_text)
        current_rate = round(stats["line_recognition_rate"], _decimal_places(claimed_text))
        if claimed_rate != current_rate:
            problems.append(
                f"document states a {claimed_rate} line-recognition rate for dialect "
                f"'{dialect}', but the current fact store computes {current_rate} -- "
                f"regenerate from a fresh `mfdoc lang-guide --config project.yml "
                f"--dialect {dialect}`"
            )
    return problems


def check_document(conn, path: Path) -> dict:
    """One document's drift report: `{"path", "ok", "skipped", "problems",
    "note"}`. `skipped` is True (with `problems` always `[]`) for anything
    this check has no basis to compare -- missing/malformed front matter,
    or `doc_type: register` (structural.py's own deterministic renderers
    already regenerate byte-identical on unchanged source; there is nothing
    to *diff*, only to regenerate -- see validate.py's
    `_artifact_consistency_problems` for that document family's own,
    narrower staleness check)."""
    text = path.read_text(encoding="utf-8")
    fm, body, err = split_frontmatter(text)
    if err or not isinstance(fm, dict):
        return {
            "path": str(path), "ok": True, "skipped": True, "problems": [],
            "note": err or "front matter is not a mapping",
        }
    if fm.get("doc_type") == "register":
        return {
            "path": str(path), "ok": True, "skipped": True, "problems": [],
            "note": "doc_type: register -- regenerate directly, nothing to diff",
        }
    problems: list[str] = [
        *_rule_count_drift(conn, fm, body),
        *_sources_drift(conn, fm),
        *_gap_register_drift(conn, fm, body),
        *_coverage_rate_drift(conn, fm, body),
        *_dialect_stats_drift(conn, fm, body),
    ]
    return {"path": str(path), "ok": not problems, "skipped": False, "problems": problems}


def check_tree(conn, root: Path) -> dict:
    """`root` a single file or a directory (recursively globbed for
    `*.md`, sorted for stable output). Aggregates `check_document` over
    every match."""
    paths = [root] if root.is_file() else sorted(root.rglob("*.md"))
    results = [check_document(conn, p) for p in paths]
    checked = [r for r in results if not r["skipped"]]
    return {
        "documents": len(results),
        "documents_checked": len(checked),
        "documents_drifted": sum(1 for r in checked if not r["ok"]),
        "total_mismatches": sum(len(r["problems"]) for r in checked),
        "results": results,
    }

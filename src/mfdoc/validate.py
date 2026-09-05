"""Stage 4 — validation.

The traceability promise is only worth something if it is checked by a machine.
This module re-reads the generated markdown, extracts every `[[MEMBER:LINE]]`
citation, and confirms the member exists and the line is within range. It also
enforces the front-matter contract so a document cannot silently omit its
confidence and review fields.

A citation that points at the wrong line is more damaging than no citation, because
a reviewer who spot-checks two citations and finds them correct will trust the
rest. Hence: check all of them, every build.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import yaml

from .brief import fetch_rule_candidate_rows
from .citations import _rule_id
from .conditions import (
    FAILURE_WORDS,
    OUTCOME_FIELD,
    SUCCESS_WORDS,
    comparisons_in,
    invert,
    prose_polarity,
)
from .db import insert
from .testlang import sidecar_path_for

CITATION = re.compile(r"\[\[(?P<member>[A-Z0-9#@$&\-_.]+)(?::(?P<from>\d+)(?:-(?P<to>\d+))?)?\]\]", re.I)

# A bare (not double-bracketed) `MEMBER:BR-nnn` reference, as generated test
# files carry per testreference/test-writing-rules.md's "leading comment
# carries the scenario's id" convention. Distinct from CITATION: this is a
# scenario id, checked against test_case.scenario_name, not a source-line
# citation checked against source_line.
#
# The leading boundary is a negative lookbehind against the member charset
# itself, not `\b` -- `\b` only anchors between a word char and a non-word
# char, and #/@/$/&/-/. (all valid leading characters in a Natural/Mantis
# member name, per this same character class) are non-word, so `\b` would
# silently swallow a leading one (e.g. matching "GS-WKAREA" instead of
# "#GS-WKAREA") and look the reference up under the wrong, truncated name.
#
# The digit count is deliberately `\d+`, not `\d{3,}`: `_rule_id` always
# zero-pads to 3+ digits, so a malformed id with fewer digits (e.g. a model
# writing "BR-4") is never a real scenario_name -- but it still needs to be
# *matched* here so the lookup below reports it as invalid, rather than
# the id being invisible to validation entirely.
BR_REF = re.compile(r"(?<![A-Z0-9#@$&.\-_])(?P<member>[A-Z0-9#@$&\-_.]+):BR-(?P<n>\d+)\b", re.I)

REQUIRED_TEST_FRONTMATTER = ["language", "framework"]

REQUIRED_FRONTMATTER = [
    "title", "doc_type", "system", "generated_by", "generated_at",
    "review_status", "confidence_summary", "sources",
]

# `doc_type: register` is the flat, deterministic index documents
# (`mfdoc rules-register`, `testplan.test_plan_register`,
# `testadvisor.testability_report`) -- there is no narrative judgement call
# in them, so the review/confidence workflow fields that apply to a
# model-authored doc don't mean anything here. Their whole front-matter
# contract is just enough to identify what they are; citations inside them
# are still fully checked below like any other document.
REQUIRED_REGISTER_FRONTMATTER = ["title", "doc_type"]

VALID_CONFIDENCE = {"verified", "inferred", "unresolved"}
VALID_REVIEW = {"draft", "in_review", "sme_approved", "signed_off"}

# Sentences that assert behaviour must carry a citation. These openers are the
# common shapes of an uncited functional claim.
ASSERTIVE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:The system|The program|The module|This module|The job|The transaction|"
    r"Users?|The user|On |When |If |The process|It )",
    re.I)

HEDGE = re.compile(r"\b(?:unresolved|not determined|could not be determined|needs confirmation|"
                   r"SME|to be confirmed|unknown|inferred)\b", re.I)


# Sentence boundary: a terminator followed by whitespace and something that starts a
# new sentence. Citations legitimately sit before the full stop, so `]].` must end a
# sentence rather than being treated as an abbreviation.
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"(*`\[])")

SKIP_BLOCK = re.compile(r"^\s*(\||>|#{1,6}\s|```|---\s*$)")


def _logical_units(body: str) -> list[str]:
    """Unwrap markdown into logical units: list items and paragraph sentences.

    Checking line by line produces false failures on any wrapped sentence whose
    citation happens to land on the following line, which trains the author to
    ignore the validator — the opposite of what it is for.
    """
    units: list[str] = []
    in_fence = False
    for para in re.split(r"\n\s*\n", body):
        buf: list[str] = []

        def flush():
            if buf:
                joined = " ".join(s.strip() for s in buf).strip()
                if joined:
                    units.extend(p for p in SENTENCE_SPLIT.split(joined) if p.strip())
                buf.clear()

        for line in para.split("\n"):
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence or SKIP_BLOCK.match(line):
                continue
            # A new list item or numbered item starts a new logical unit.
            if re.match(r"^\s*(?:[-*+]\s+|\d+\.\s+)", line):
                flush()
            buf.append(re.sub(r"^\s*(?:[-*+]\s+|\d+\.\s+)", "", line))
        flush()
    return units


def _uncited_assertions(body: str) -> list[str]:
    """Every logical unit (see `_logical_units`) that asserts a claim with no
    citation and no hedge -- except when the *next* unit opens with a
    citation.

    `SENTENCE_SPLIT` treats `[[MEMBER:LINE]]` as a valid sentence-starter (so
    a sentence that deliberately opens with a citation isn't itself
    mis-flagged), but that means a citation placed right after the period
    that ends the *previous* claim -- "...falls short. [[MMP0100:57]] If
    available stock..." -- gets split away from that claim and read as
    belonging to the sentence after it instead. Citation resolution itself
    reads `body` directly and is unaffected either way; this only stops that
    split from also making the claim it supports look uncited. Deliberately
    doesn't move the citation out of the next unit -- if that unit is itself
    an uncited assertion needing it, this check still credits it there too.
    """
    units = _logical_units(body)
    out = []
    for i, u in enumerate(units):
        if not (ASSERTIVE.match(u) and not CITATION.search(u) and not HEDGE.search(u)):
            continue
        nxt = units[i + 1] if i + 1 < len(units) else ""
        if nxt.lstrip().startswith("[["):
            continue
        out.append(u.strip()[:140])
    return out


def _split_frontmatter(text: str) -> tuple[dict | None, str, str | None]:
    if not text.startswith("---"):
        return None, text, "missing YAML front matter"
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text, "malformed YAML front matter"
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        return None, parts[2], f"unparseable YAML front matter: {exc}"
    return fm, parts[2], None


_LEADING_CITATION_RUN = re.compile(r"^(?:\[\[[^\]]+\]\]\s*)+")


def _containing_sentence(body: str, start: int, end: int) -> str:
    """The sentence in `body` that spans byte offset `start`..`end`.

    Reuses `SENTENCE_SPLIT` (the same boundary `_logical_units` splits on) so
    a citation's surrounding claim is read the same way whether checked for
    an uncited assertion or for a reversed comparison. Falls back to the
    whole enclosing paragraph if no sentence boundary is found, which is
    always at least as much text as the citation itself sits in.

    `SENTENCE_SPLIT` treats a citation as a valid sentence-starter, so a
    citation placed right after the period ending the claim it actually
    supports gets split into its own, mostly-empty unit -- unlike
    `_uncited_assertions` (which can afford to just credit the *next* unit's
    leading citation without moving anything, since both units keep their
    own text), a reversed-condition check needs the real preceding prose to
    read polarity from, not a fragment that's just the citation itself. When
    the located unit is nothing but a leading run of citations, merge in the
    unit before it instead of returning the citation alone.
    """
    para_start = body.rfind("\n\n", 0, start)
    para_start = 0 if para_start == -1 else para_start + 2
    para_end = body.find("\n\n", end)
    para_end = len(body) if para_end == -1 else para_end
    para = body[para_start:para_end]
    rel_start = start - para_start

    bounds = [0] + [m.start() for m in SENTENCE_SPLIT.finditer(para)] + [len(para)]
    for i, (lo, hi) in enumerate(zip(bounds, bounds[1:])):
        if lo <= rel_start < hi:
            if i > 0 and _LEADING_CITATION_RUN.match(para[lo:hi].lstrip()):
                lo = bounds[i - 1]
            return para[lo:hi]
    return para


# polarity -> narrative wording, for building the finding message. Covers
# every polarity `conditions.comparisons_in` can produce.
_POLARITY_WORDS = {
    "eq": "equals", "ne": "does not equal",
    "gt": "is greater than", "lt": "is less than",
    "ge": "is at least", "le": "is at most",
}


def _reversed_condition_problems(
    conn, member: str, member_id: int, lf: int, lt: int | None, body: str, cite_start: int, cite_end: int,
    outcome_field=OUTCOME_FIELD,
) -> list[str]:
    """Flag a citation whose surrounding sentence describes a comparison in
    the opposite direction from the `rule_candidate` condition(s) it cites.

    Only fires for "outcome" fields (`outcome_field`, defaulting to
    `conditions.OUTCOME_FIELD`) -- the failure mode this exists for is a
    reversed pass/fail interpretation, not general narration imprecision. A
    citation range spanning an `IF` and its paired `ELSE` is resolved against
    whichever branch the sentence's own wording (`SUCCESS_WORDS`/
    `FAILURE_WORDS`) describes; with no such hint, only the `IF`'s own
    condition is checked -- guessing which branch an ambiguous sentence means
    is worse than not checking it at all.

    Two comparison shapes reach this far (see `conditions.comparisons_in`):
    field-vs-literal (`c["literal"]` set) and field-vs-field
    (`c["other_field"]` set, `c["literal"]` `None`). For field-vs-field, the
    narrative claim is read off the *other field's own identifier* appearing
    in the sentence (stripped of its sigil) rather than a literal value --
    there is no concrete value to search prose for otherwise.
    """
    hi = lt or lf
    rows = conn.execute(
        "SELECT line_no, construct, condition, pair_line_no FROM rule_candidate "
        "WHERE member_id=? AND line_no BETWEEN ? AND ?",
        (member_id, lf, hi),
    ).fetchall()
    if not rows:
        return []

    if_comparisons: list[tuple[int, dict]] = []
    else_comparisons: list[tuple[int, dict]] = []
    for row in rows:
        if row["construct"] == "IF":
            if_comparisons.extend(
                (row["line_no"], c) for c in comparisons_in(row["condition"], outcome_field=outcome_field)
            )
        elif row["construct"] == "ELSE" and row["pair_line_no"] is not None:
            if_row = conn.execute(
                "SELECT condition FROM rule_candidate WHERE member_id=? AND line_no=? AND construct='IF'",
                (member_id, row["pair_line_no"]),
            ).fetchone()
            if if_row is not None:
                else_comparisons.extend(
                    (row["line_no"], {**c, "polarity": invert(c["polarity"])})
                    for c in comparisons_in(if_row["condition"], outcome_field=outcome_field)
                )

    if not if_comparisons and not else_comparisons:
        return []

    sentence = _containing_sentence(body, cite_start, cite_end)
    if len(CITATION.findall(sentence)) > 1:
        # A sentence citing more than one location is commonly contrasting
        # or cross-referencing them (an SME question comparing two checks
        # that read the same literal in opposite senses is exactly this
        # shape, and is itself a correct, valuable finding to leave alone,
        # not a reversed narration to flag) -- too ambiguous to anchor a
        # single-field polarity reading to just one of the citations in it.
        return []
    success_hint = bool(SUCCESS_WORDS.search(sentence)) and not FAILURE_WORDS.search(sentence)
    failure_hint = bool(FAILURE_WORDS.search(sentence)) and not SUCCESS_WORDS.search(sentence)

    if success_hint and else_comparisons:
        candidates = else_comparisons
    elif failure_hint or not else_comparisons:
        candidates = if_comparisons
    else:
        candidates = if_comparisons or else_comparisons

    problems = []
    seen = set()
    for line_no, c in candidates:
        key = (line_no, c["field"], c["literal"], c["other_field"])
        if key in seen:
            continue
        target = c["literal"] if c["literal"] is not None else c["other_field"].lstrip("#@$&")
        claimed = prose_polarity(sentence, target)
        if claimed is None or claimed == c["polarity"]:
            continue
        seen.add(key)
        actual = _POLARITY_WORDS[c["polarity"]]
        claimed_as = _POLARITY_WORDS[claimed]
        described = f"'{c['literal']}'" if c["literal"] is not None else c["other_field"]
        problems.append(
            f"comparison direction may be reversed near [[{member}:{line_no}]]: "
            f"text reads as though {c['field']} {claimed_as} {described}, "
            f"but the source condition means {c['field']} {actual} {described}"
        )
    return problems


def validate_doc(conn, path: Path, outcome_field=OUTCOME_FIELD) -> dict:
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []
    fm, body, fm_err = _split_frontmatter(text)
    if fm_err:
        problems.append(fm_err)
    if fm is not None and fm.get("doc_type") == "register":
        for key in REQUIRED_REGISTER_FRONTMATTER:
            if key not in fm:
                problems.append(f"front matter missing required key: {key}")
    elif fm is not None:
        for key in REQUIRED_FRONTMATTER:
            if key not in fm:
                problems.append(f"front matter missing required key: {key}")
        rs = fm.get("review_status")
        if rs and rs not in VALID_REVIEW:
            problems.append(f"review_status '{rs}' not one of {sorted(VALID_REVIEW)}")
        cs = fm.get("confidence_summary") or {}
        if isinstance(cs, dict):
            for k in cs:
                if k not in VALID_CONFIDENCE:
                    problems.append(f"confidence_summary key '{k}' not one of {sorted(VALID_CONFIDENCE)}")
        else:
            problems.append("confidence_summary should be a mapping of confidence level to count")

        if fm.get("doc_type") == "module":
            first_line = next((ln for ln in body.splitlines() if ln.strip()), "")
            if not first_line.lstrip().startswith("#"):
                problems.append(
                    "body does not open with a top-level '# ' heading -- looks like the "
                    "response narrated commentary (e.g. restating its own scope/instructions) "
                    "before the actual document content instead of starting with it"
                )

    # Scoped to narrative module docs only. A generated-test doc or a flat
    # register echoes source syntax and field-inventory phrasing verbatim,
    # sentence-per-YAML-field rather than sentence-per-claim -- the same
    # literal recurs across several adjacent lines with different framing
    # each time (a precondition, a Given, a When), which defeats the
    # single-nearest-occurrence assumption this check relies on and would
    # make it noise rather than signal outside the doc type it was built for.
    check_reversed_conditions = fm is not None and fm.get("doc_type") == "module"

    conn.execute("DELETE FROM doc_claim WHERE doc_path=?", (str(path),))

    cites = list(CITATION.finditer(body))
    good = bad = 0
    for m in cites:
        member = m.group("member").upper()
        lf = int(m.group("from")) if m.group("from") else None
        lt = int(m.group("to")) if m.group("to") else lf
        rows = conn.execute(
            "SELECT id, name, library, (SELECT MAX(line_no) FROM source_line WHERE member_id=member.id) AS maxline "
            "FROM member WHERE UPPER(name)=?", (member,)
        ).fetchall()
        valid, note = 1, None
        if not rows:
            valid, note = 0, f"member '{member}' is not in the index"
        elif len(rows) > 1:
            # The citation format [[MEMBER:LINE]] carries no library, but
            # `member` allows the same name in different libraries. Picking
            # one arbitrarily can validate a citation against the wrong
            # member's line range, so flag it instead of guessing -- unless
            # this is the one structural case where the ambiguity is already
            # resolved elsewhere in the fact store: a DDM and an FDT (or
            # other definition source) for the same physical entity are
            # ingested as two separate `member` rows sharing a name (neither
            # has a library, so there is no qualifier to disambiguate with),
            # but `derive` already picked one of them as that entity's
            # canonical `defined_in` -- entity_brief cites through that
            # member, so honour the same choice here rather than reporting
            # a false ambiguity for the one citation shape brief.py itself
            # produces for merged entities.
            entity_row = conn.execute(
                "SELECT defined_in FROM entity WHERE UPPER(name)=?", (member,)
            ).fetchone()
            preferred = next(
                (r for r in rows if entity_row and r["id"] == entity_row["defined_in"]), None
            ) if entity_row else None
            if preferred is not None:
                rows = [preferred]
            else:
                libs = ", ".join(sorted({r["library"] or "?" for r in rows}))
                valid, note = 0, f"member name '{member}' is ambiguous across libraries ({libs}); citation needs a library qualifier"

        if valid and lf is not None:
            row = rows[0]
            maxline = row["maxline"] or 0
            if lf < 1 or lf > maxline or (lt and lt > maxline):
                valid, note = 0, f"line {lf}{'-' + str(lt) if lt and lt != lf else ''} outside 1..{maxline}"
            elif check_reversed_conditions:
                problems.extend(
                    _reversed_condition_problems(
                        conn, member, row["id"], lf, lt, body, m.start(), m.end(),
                        outcome_field=outcome_field,
                    )
                )
        insert(conn, "doc_claim", doc_path=str(path), confidence="verified",
               citation=m.group(0), member_name=member, line_from=lf, line_to=lt,
               valid=valid, note=note)
        if valid:
            good += 1
        else:
            bad += 1
            problems.append(f"invalid citation {m.group(0)}: {note}")

    uncited = _uncited_assertions(body)
    if uncited:
        problems.append(f"{len(uncited)} assertive statement(s) carry no citation and no hedge")

    conn.commit()
    return {
        "path": str(path),
        "citations": len(cites),
        "valid_citations": good,
        "invalid_citations": bad,
        "uncited_assertions": uncited,
        "problems": problems,
        "ok": not problems,
        # Front matter/body this call already parsed, for a caller (e.g.
        # validate_test_doc) that needs the same document's checks on top
        # of these -- reusing this avoids a second disk read and YAML parse
        # of a file this function just read and parsed itself.
        "_fm": fm,
        "_body": body,
    }


def validate_test_doc(conn, path: Path) -> dict:
    """`validate_doc` plus the checks specific to a generated test file:
    `language`/`framework` front matter, and that every bare `MEMBER:BR-nnn`
    reference names a scenario that actually exists in test_case -- the
    generated-test equivalent of a citation pointing at a real source line.
    A model renumbering or inventing a BR-id would otherwise pass
    validate_doc's checks silently, since BR-nnn isn't a `[[...]]` citation
    validate_doc already resolves.

    `mfdoc test-gen`/`mfdoc test-batch` split a validated response's code
    fence out to a sibling source file (`testbatch.write_test_doc_with_sidecar`),
    replacing it in the `.md` with a `## Scenarios covered` manifest -- when
    that sidecar exists on disk next to `path`, the BR-nnn references are
    checked in the *sidecar's* actual content instead of `body` (which no
    longer has the code), and cross-checked against the manifest so a
    stale/hand-edited manifest can't silently drift from what the sidecar
    really contains. No sidecar on disk (older embedded-fence documents, or
    an unrecognised language) falls back to scanning `body` directly,
    exactly as before this feature existed.
    """
    result = validate_doc(conn, path)
    fm, body = result.pop("_fm"), result.pop("_body")
    problems = list(result["problems"])

    if fm is not None:
        for key in REQUIRED_TEST_FRONTMATTER:
            if key not in fm:
                problems.append(f"front matter missing required key: {key}")

    sidecar = sidecar_path_for(path, fm.get("language")) if fm is not None else None
    if sidecar is not None and sidecar.exists():
        manifest_ids = {
            f"{m.group('member').upper()}:BR-{m.group('n')}" for m in BR_REF.finditer(body)
        }
        code_ids = {
            f"{m.group('member').upper()}:BR-{m.group('n')}"
            for m in BR_REF.finditer(sidecar.read_text(encoding="utf-8"))
        }
        scan_ids = code_ids
        for sid in sorted(manifest_ids - code_ids):
            problems.append(f"'{sid}' is listed in {path.name}'s manifest but not found in "
                             f"{sidecar.name}'s actual content")
        for sid in sorted(code_ids - manifest_ids):
            problems.append(f"'{sid}' is referenced in {sidecar.name} but missing from "
                             f"{path.name}'s '## Scenarios covered' manifest")
    else:
        scan_ids = {f"{m.group('member').upper()}:BR-{m.group('n')}" for m in BR_REF.finditer(body)}

    bad_refs = 0
    for scenario in scan_ids:
        row = conn.execute(
            "SELECT 1 FROM test_case WHERE UPPER(scenario_name)=UPPER(?)", (scenario,)
        ).fetchone()
        if not row:
            bad_refs += 1
            problems.append(f"'{scenario}' is not a known test_case scenario -- run `mfdoc test-plan`, "
                             f"or this id was invented/renumbered")

    result["problems"] = problems
    result["invalid_scenario_refs"] = bad_refs
    result["ok"] = not problems
    return result


def _is_pipeline_doc(path: Path) -> bool:
    """`README.md` is project documentation, not pipeline output -- it has
    no front matter and was never meant to satisfy this contract, so a
    tree walk must skip it rather than reporting a false failure on the
    one file everyone browsing the directory expects to be different."""
    return path.name.upper() != "README.MD"


def validate_tests_tree(conn, root: Path) -> dict:
    results = [validate_test_doc(conn, p) for p in sorted(root.rglob("*.md")) if _is_pipeline_doc(p)]
    return {
        "documents": len(results),
        "documents_ok": sum(1 for r in results if r["ok"]),
        "total_citations": sum(r["citations"] for r in results),
        "invalid_citations": sum(r["invalid_citations"] for r in results),
        "invalid_scenario_refs": sum(r.get("invalid_scenario_refs", 0) for r in results),
        "results": results,
    }


def module_completeness_problems(conn, results: list[dict]) -> list[str]:
    """A member's `rule_candidate` that never shows up in any `doc_type:
    module` document's body -- across *all* of `results`, not one file at a
    time, since a chunked member's rules are only ever complete in
    aggregate across its several chunk documents.

    Chunking (batch.py) prevents one unbounded completion from silently
    dropping most of a large member's rules, but nothing before this
    checked that the *union* of what every chunk (or a single, unchunked
    doc) actually produced still covers the full set a member's brief
    handed the model -- a doc that trails off just short of its own
    assigned range, or a chunk whose model call plain never ran, still
    validates individually as long as what *is* there cites cleanly. This
    is the aggregate check that catches that: it reuses `fetch_rule_candidate_rows`
    (the same row set and ordering `_rule_id` numbers `BR-nnn` from) as the
    ground truth for what a member's full rule set actually is, so a gap
    reported here always names a real, stably-numbered missing rule.
    """
    cited: dict[str, set[int]] = defaultdict(set)
    members: set[str] = set()
    for r in results:
        fm = r.get("_fm")
        if not fm or fm.get("doc_type") != "module":
            continue
        for src in fm.get("sources") or []:
            members.add(src)
        for m in BR_REF.finditer(r.get("_body") or ""):
            cited[m.group("member").upper()].add(int(m.group("n")))

    problems = []
    for member in sorted(members):
        rows, ambiguous_libs = fetch_rule_candidate_rows(conn, member)
        if ambiguous_libs or not rows:
            continue
        total = len(rows)
        have = cited.get(member.upper(), set())
        missing = [n for n in range(1, total + 1) if n not in have]
        if not missing:
            continue
        span = _rule_id(member, missing[0])
        if len(missing) > 1:
            span += f"..{_rule_id(member, missing[-1])}"
        problems.append(
            f"{member}: {len(missing)}/{total} rule_candidate(s) never cited in any "
            f"generated module document (missing {span})"
        )
    return problems


# Row/line-count patterns used by `_artifact_consistency_problems` to
# re-derive a cheap invariant from a `structural.py`-rendered artifact's own
# markdown, without re-rendering (and diffing) the whole document.
_GAP_SUMMARY_ROW = re.compile(r"^\|\s*`[^`|]*`\s*\|[^|]*\|\s*(\d+)\s*\|\s*$", re.M)
_GLOSSARY_HEADING = re.compile(r"^### ", re.M)
# A call-graph node declaration line, e.g. `    n_ABC123["NAME"]` (resolved)
# or `    n_ABC123(["NAME (unresolved)"])` (unresolved) -- see
# structural.call_graph_diagram's render(). Deliberately distinct from an
# edge line (`    id --> id` / `    id -.->|unresolved| id`): after the node
# id (always plain alnum/underscore, per structural._mermaid_id) an edge
# line has a space next, never `[` or `(`, so this pattern never matches one.
_CALL_GRAPH_NODE = re.compile(r'^ {4}\S+[\[(]', re.M)


def _gap_summary_artifact_problems(conn, path: Path, body: str) -> list[str]:
    """gap-summary.md's table is one row per (gap_kind, severity), each
    carrying that group's `COUNT(*)`. Summing every row's count column must
    equal the live `gap` table's total row count -- a stale hand-edited copy
    (or one regenerated before a later `mfdoc derive`/`gap-summary` run) with
    a wrong count would otherwise sail through the citation checks above,
    since a `doc_type: register` doc like this carries no prose citations
    for them to check at all."""
    expected = conn.execute("SELECT COUNT(*) AS n FROM gap").fetchone()["n"]
    actual = sum(int(m) for m in _GAP_SUMMARY_ROW.findall(body))
    if actual != expected:
        return [
            f"{path.name}: table rows sum to {actual} gap(s), but the fact store "
            f"currently has {expected} -- looks stale, regenerate with `mfdoc gap-summary`"
        ]
    return []


def _glossary_artifact_problems(conn, path: Path, body: str) -> list[str]:
    """glossary.md renders one `### ` heading per distinct (name, kind) pair
    in `entity` (structural.glossary). A heading count that doesn't match
    the live table means the glossary was hand-edited or regenerated
    against an older fact store."""
    expected = conn.execute(
        "SELECT COUNT(*) AS n FROM (SELECT DISTINCT name, kind FROM entity)"
    ).fetchone()["n"]
    actual = len(_GLOSSARY_HEADING.findall(body))
    if actual != expected:
        return [
            f"{path.name}: {actual} entity heading(s) found, but the fact store "
            f"currently has {expected} distinct (name, kind) entit(y/ies) -- looks "
            f"stale, regenerate with `mfdoc glossary`"
        ]
    return []


def _call_graph_artifact_problems(conn, path: Path, body: str, fm: dict) -> list[str]:
    """call-graph.md's node count should match the distinct set of nodes
    `structural.build_call_graph` derives from `call_edge` (every caller,
    plus every callee -- keyed by member id where `call_edge.callee_id` is
    set, else by bare callee name, mirroring `call_graph_diagram`'s own
    node identity rule (Finding 1): a name alone is not a stable node key.

    Only checked when the diagram actually rendered every node inline --
    once the graph exceeds `max_nodes_inline`, `call_graph_diagram` collapses
    `call-graph.md` to one node per cluster instead (see its `title:
    "Call graph (collapsed)"` front matter), which this invariant does not
    apply to; the full per-cluster files it points at
    (`call-graph-<cluster>.md`) are not checked here since a cluster's own
    node set additionally depends on config (`cluster_by`), not just
    `call_edge` -- too fragile a heuristic for the exact node count."""
    if fm.get("title") == "Call graph (collapsed)":
        return []
    nodes: set[tuple[str, object]] = set()
    for r in conn.execute("SELECT caller_id, callee_id, callee_name FROM call_edge"):
        nodes.add(("id", r["caller_id"]))
        nodes.add(("id", r["callee_id"]) if r["callee_id"] is not None else ("name", r["callee_name"]))
    expected = len(nodes)
    actual = len(_CALL_GRAPH_NODE.findall(body))
    if actual != expected:
        return [
            f"{path.name}: {actual} node(s) drawn, but call_edge implies {expected} distinct "
            f"node(s) -- looks stale, regenerate with `mfdoc call-graph`"
        ]
    return []


def _artifact_consistency_problems(conn, results: list[dict]) -> list[str]:
    """A cheap, doc_type-aware consistency check for the deterministic
    structural artifacts (`structural.py`) that the citation/uncited-
    assertion checks above are nearly a no-op against: each one is a
    `doc_type: register` document with little to no prose, so a stale
    hand-edited copy (or one left over from before a later `mfdoc derive`
    run) can carry a wrong count and still validate clean by every check
    upstream of this one.

    There is no machine-readable tag naming *which* structural artifact a
    given file is -- only `doc_type: register` in front matter, shared by
    every one of them (and by other, unrelated register docs like
    rules-register.md). This falls back to the filename convention
    `structural.py`'s own CLI commands (`cmd_gap_summary`/`cmd_call_graph`/
    `cmd_glossary` in cli.py) write to by default -- `gap-summary.md`,
    `call-graph.md`, `glossary.md`. That is inherently a fragile match (a
    project could name its output file anything via `--out`); it only
    covers the default filenames, and silently skips a doc under any other
    name rather than guessing. Gated the same way by construction: a docs
    tree with none of these filenames contributes nothing here, so a project
    not using the structural-overview extension validates exactly as
    before."""
    problems: list[str] = []
    for r in results:
        fm = r.get("_fm")
        if not fm or fm.get("doc_type") != "register":
            continue
        path = Path(r["path"])
        body = r.get("_body") or ""
        if path.name == "gap-summary.md":
            problems.extend(_gap_summary_artifact_problems(conn, path, body))
        elif path.name == "glossary.md":
            problems.extend(_glossary_artifact_problems(conn, path, body))
        elif path.name == "call-graph.md":
            problems.extend(_call_graph_artifact_problems(conn, path, body, fm))
    return problems


def validate_tree(conn, root: Path, outcome_field=OUTCOME_FIELD) -> dict:
    results = [
        validate_doc(conn, p, outcome_field=outcome_field)
        for p in sorted(root.rglob("*.md")) if _is_pipeline_doc(p)
    ]
    return {
        "documents": len(results),
        "documents_ok": sum(1 for r in results if r["ok"]),
        "total_citations": sum(r["citations"] for r in results),
        "invalid_citations": sum(r["invalid_citations"] for r in results),
        "results": results,
        "completeness_problems": module_completeness_problems(conn, results),
        "artifact_problems": _artifact_consistency_problems(conn, results),
    }

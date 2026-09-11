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
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

import yaml

from .brief import fetch_routines, fetch_rule_candidate_rows
from .citations import _rule_id, numbered_rule_candidates
from .conditions import (
    FAILURE_WORDS,
    OUTCOME_FIELD,
    SUCCESS_WORDS,
    comparisons_in,
    invert,
    prose_polarity,
)
from .db import insert, resolve_member_by_name
from .testlang import sidecar_path_for
from .testplan import doc_rule_fingerprint

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


def _name_pattern(name: str) -> re.Pattern:
    """Compiled whole-token, case-insensitive match pattern for `name`.

    Reuses the same non-word-boundary trick `BR_REF` already uses instead of
    `\\b`: a Natural/Mantis member, program, map, or file name can contain
    `#@$&-_.`, all non-word characters that `\\b` would treat as a boundary
    even mid-name -- e.g. `\\bPGMX02\\b` would happily match inside
    `PGMX02-EXT`. `re.escape` is required since a target name may itself
    contain regex-special characters (`.`, `$`).

    A trailing `.` only counts as a same-name continuation (blocking the
    match, as in `PGMX02.EXT`) when another name-charset character follows
    it -- a bare `.` immediately after the name is ordinary sentence-ending
    punctuation (`"...calls PGMX02. It then..."`), not part of the name, and
    must not hide a real mention. The lookbehind mirrors this symmetrically
    on the leading side (a bare `.` immediately before the name doesn't
    block the match; `.` preceded by another name-charset character does,
    as in `EXT.PGMX02`) even though, unlike the trailing case, this side has
    no realistic motivating example in prose -- a sentence never opens
    mid-name the way it closes one -- so it's kept consistent with the
    trailing side on principle rather than because a real case forced it.

    Factored out of `_name_mentioned` so `forward_reference_problems` can
    reuse the exact same match rule to find *where* a routine name occurs
    (not just whether it occurs at all). `lru_cache`d -- both callers use
    this inside loops over many citations/routines, often re-checking the
    same handful of names repeatedly, so recompiling on every call would
    add needless overhead to `mfdoc validate` runs."""
    return _compile_name_pattern(name)


_NAME_PATTERN_CACHE_SIZE = 2048


@lru_cache(maxsize=_NAME_PATTERN_CACHE_SIZE)
def _compile_name_pattern(name: str) -> re.Pattern:
    return re.compile(
        rf"(?<![A-Z0-9#@$&\-_])(?<![A-Z0-9#@$&\-_]\.)"
        rf"{re.escape(name)}"
        rf"(?![A-Z0-9#@$&\-_]|\.[A-Z0-9#@$&\-_])",
        re.I,
    )


def _name_mentioned(text: str, name: str) -> bool:
    """Whether `name` appears in `text` as a whole token, case-insensitive.
    See `_name_pattern` for why this isn't just `re.search(r'\\bname\\b', ...)`."""
    return bool(_name_pattern(name).search(text))


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

# A unit that is itself phrased as a question (a genuine open SME/gap-register
# question, e.g. "When X occurs, is Y the correct outcome?") is not an
# assertion of behaviour and doesn't need a citation or hedge the way a
# declarative claim does -- even though it can open with the same words
# ASSERTIVE matches ("When ", "If ", ...). Anchored on the *whole* unit
# ending in `?` (only trailing whitespace/closing punctuation allowed after
# it) so a declarative sentence that merely contains an embedded `?`
# elsewhere (e.g. quoting a literal screen prompt) is not exempted.
QUESTION_UNIT = re.compile(r"\?[\"'”’)]*\s*$")


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
    citation, or the unit is itself phrased as a genuine question (see
    `QUESTION_UNIT`) rather than a declarative claim.

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
        if QUESTION_UNIT.search(u):
            continue
        nxt = units[i + 1] if i + 1 < len(units) else ""
        if nxt.lstrip().startswith("[["):
            continue
        out.append(u.strip()[:140])
    return out


def split_frontmatter(text: str) -> tuple[dict | None, str, str | None]:
    if not text.startswith("---"):
        return None, text, "missing YAML front matter"
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text, "malformed YAML front matter"
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        return None, parts[2], f"unparseable YAML front matter: {exc}"
    # `yaml.safe_load` happily parses syntactically valid YAML between the
    # `---` markers into a truthy non-mapping (a bare scalar or a list, e.g.
    # "- a\n- b") -- `or {}` above only substitutes for a *falsy* parse
    # (`None`/`""`/`[]`), not a truthy non-dict one. Every caller of this
    # function treats a non-`None` `fm` as a mapping (`fm.get(...)`,
    # `fm["sources"]`, `"key" in fm`) with no shape check of its own, so
    # left unguarded this reaches `validate_doc` (and, through it, every
    # `mfdoc test-validate`/`mfdoc validate` call) as an `AttributeError`/
    # `TypeError` crash instead of the malformed-front-matter problem this
    # function exists to report (Copilot review on issue #195's fix).
    if not isinstance(fm, dict):
        return None, parts[2], f"front matter is not a mapping, got {fm!r}"
    return fm, parts[2], None


_LEADING_CITATION_RUN = re.compile(r"^(?:\[\[[^\]]+\]\]\s*)+")


def _containing_paragraph(body: str, start: int, end: int) -> tuple[str, int]:
    """The paragraph in `body` that spans character offset `start`..`end`, and
    `start`'s offset relative to that paragraph's own start.

    Factored out of `_containing_sentence` so a caller that needs the whole
    paragraph (e.g. `_statement_completeness_problems`, which tolerates a
    target named anywhere in the paragraph, not just the citing sentence)
    doesn't duplicate this boundary-finding.
    """
    para_start = body.rfind("\n\n", 0, start)
    para_start = 0 if para_start == -1 else para_start + 2
    para_end = body.find("\n\n", end)
    para_end = len(body) if para_end == -1 else para_end
    return body[para_start:para_end], start - para_start


def _containing_sentence(body: str, start: int, end: int) -> str:
    """The sentence in `body` that spans character offset `start`..`end`.

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
    para, rel_start = _containing_paragraph(body, start, end)
    bounds = [0] + [m.start() for m in SENTENCE_SPLIT.finditer(para)] + [len(para)]
    for i, (lo, hi) in enumerate(zip(bounds, bounds[1:])):
        if lo <= rel_start < hi:
            if i > 0 and _LEADING_CITATION_RUN.match(para[lo:hi].lstrip()):
                lo = bounds[i - 1]
            return para[lo:hi]
    return para


_STATEMENT_SOURCES = [
    ("call_edge", "callee_name", "call_kind",
     "SELECT line_no, call_kind, callee_name FROM call_edge "
     "WHERE caller_id=? AND dynamic=0 AND callee_name IS NOT NULL AND callee_name != ''"),
    ("interaction", "target", "kind",
     "SELECT line_no, kind, target FROM interaction "
     "WHERE member_id=? AND dynamic=0 AND target IS NOT NULL AND target != ''"),
    ("data_access", "entity_name", "verb",
     "SELECT line_no, verb, entity_name FROM data_access "
     "WHERE member_id=? AND entity_name IS NOT NULL AND entity_name != ''"),
]


def _fetch_statement_rows(conn, member_id: int) -> list[tuple[str, str, object]]:
    """Every `call_edge`/`interaction`/`data_access` row for `member_id`,
    across all three tables, as `(target_col, kind_col, row)` triples.

    Fetched once per member rather than once per citation:
    `_statement_completeness_problems` used to re-run all three queries,
    each re-filtered by line range, for every citation of a member -- a doc
    citing the same member several times (the common case) paid for the
    same three queries again each time. The caller (`validate_doc`) caches
    this per member_id across a document's whole citation loop; the
    per-citation line-range filter now happens in memory in
    `_statement_completeness_problems` instead of in SQL.
    """
    rows = []
    for _table, target_col, kind_col, sql in _STATEMENT_SOURCES:
        for row in conn.execute(sql, (member_id,)).fetchall():
            rows.append((target_col, kind_col, row))
    return rows


# `generated_by` front matter is always written as "legacy-functional-docs
# <version>" (see batch.py's _render_module_chunk_index and every other doc
# writer) -- capturing the version lets a stale-regeneration check compare it
# against what's actually installed now.
_GENERATED_BY_VERSION = re.compile(r"^legacy-functional-docs\s+(\S+)\s*$")

# A bare dotted-numeric version ("0.2.0", "12.3"), the only shape this
# module's own __version__ ever takes -- deliberately not a general semver
# parser (no pre-release/build-metadata handling): this only needs to order
# two versions of *this* project, not validate arbitrary version strings,
# and pulling in a dependency for that would violate this repo's
# stdlib-only-core policy for one narrow comparison.
_DOTTED_VERSION = re.compile(r"^\d+(\.\d+)*$")


def _parse_version(v: str) -> tuple[int, ...] | None:
    """`v` as a tuple of ints, with trailing zero components stripped (so
    "0.2" and "0.2.0" -- semantically the same version, just written with a
    different number of components -- parse to the identical tuple and
    compare equal, rather than Python's own tuple ordering rule for
    differing lengths silently ranking the shorter one as "older": `(0, 2)
    < (0, 2, 0)` is true in plain Python, which would misreport "0.2" as
    older than an installed "0.2.0" even though nothing actually changed).
    Always at least one component long, even if every component was zero
    (`"0"` -> `(0,)`, not `()`)."""
    if not _DOTTED_VERSION.match(v):
        return None
    parts = [int(p) for p in v.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _staleness_problem(fm: dict | None) -> str | None:
    """A document's `generated_by` version differs from the mfdoc version
    installed right now, or `None` when it matches (or there's nothing to
    compare -- no/unrecognised `generated_by`, e.g. a `doc_type: register`
    document, which doesn't require the field at all).

    This only catches a version bump -- exactly the same limitation
    `batch.py`'s `_corpus_signature` docstring already documents for its own,
    narrower purpose: a pipeline fix landed and released without bumping
    `__version__` (common mid-development, before a tagged release) produces
    no mismatch here either, since there is nothing else in a document's own
    front matter that would reveal it. Advisory, not a hard failure: a
    version mismatch alone doesn't mean the document's *content* is wrong,
    only that it predates whatever changed -- worth surfacing so a stale
    regeneration doesn't sit unnoticed indefinitely, not worth blocking a
    build over.

    The message direction (older/newer/differs) is only ever asserted when
    both versions actually parse as ordinary dotted-numeric versions and
    compare unequal as tuples -- a non-numeric or otherwise unparseable
    version string (someone's local dev build, a version scheme this
    project doesn't use) gets a direction-neutral "differs from" instead of
    a guessed "older", which would be actively wrong exactly when it's a
    newer/different build rather than an old one.
    """
    from . import __version__ as installed_version

    generated_by = fm.get("generated_by") if isinstance(fm, dict) else None
    if not isinstance(generated_by, str):
        return None
    m = _GENERATED_BY_VERSION.match(generated_by.strip())
    if not m or m.group(1) == installed_version:
        return None
    doc_version = m.group(1)

    doc_parsed = _parse_version(doc_version)
    installed_parsed = _parse_version(installed_version)
    if doc_parsed is not None and installed_parsed is not None:
        if doc_parsed < installed_parsed:
            return (
                f"generated by legacy-functional-docs {doc_version}, older than the "
                f"installed version {installed_version} -- this document may predate a "
                f"pipeline fix; consider regenerating it"
            )
        if doc_parsed > installed_parsed:
            return (
                f"generated by legacy-functional-docs {doc_version}, newer than the "
                f"installed version {installed_version} -- the installed mfdoc is older "
                f"than what produced this document; upgrading before comparing further "
                f"documents may avoid confusing version-direction mismatches"
            )
        # Equal as parsed tuples (e.g. "0.2" vs "0.2.0") despite differing as
        # strings -- not actually a mismatch worth reporting.
        return None
    return (
        f"generated by legacy-functional-docs {doc_version}, which differs from the "
        f"installed version {installed_version} -- direction (older/newer) could not "
        f"be determined from these version strings; confirm which is current before "
        f"treating this document as stale"
    )


def _statement_completeness_problems(
    rows: list[tuple[str, str, object]], member: str, lf: int, lt: int | None,
    body: str, cite_start: int, cite_end: int,
) -> list[str]:
    """Flag a `call_edge`/`interaction`/`data_access` row (from `rows`,
    `_fetch_statement_rows`'s output for this citation's member) inside the
    `lf`..`lt` range whose target name never appears anywhere in the
    paragraph citing that range.

    Deliberately paragraph-scoped, not sentence-scoped (unlike
    `_reversed_condition_problems`): a branch's narration legitimately spans
    several sentences in one paragraph (a setup sentence, then one sentence
    per statement), and a target named two sentences after the citation is
    still a real mention. `dynamic=1` call_edge/interaction rows are
    excluded before `rows` is even built (see `_STATEMENT_SOURCES`) --
    their target is a variable, not a literal name, so there is nothing
    meaningful to search prose for.

    Advisory only: the caller must not add these to `problems`. This is a
    deliberately different shape of check from the ones already built (see
    issue #59) and its false-positive rate against real generated docs is
    not yet known.
    """
    hi = lt or lf
    para, _ = _containing_paragraph(body, cite_start, cite_end)

    range_str = f"{lf}{'-' + str(lt) if lt and lt != lf else ''}"
    problems = []
    for target_col, kind_col, row in rows:
        if not (lf <= row["line_no"] <= hi):
            continue
        target = row[target_col]
        if _name_mentioned(para, target):
            continue
        problems.append(
            f"statement inside [[{member}:{range_str}]] targets '{target}' "
            f"({row[kind_col]} at line {row['line_no']}) but '{target}' is not "
            f"named anywhere in the citing paragraph"
        )
    return problems


# A narrative sentence deferring explanation of something to another part of
# a chunked document set, without saying which -- the shape of prose
# `_generate_module_doc_chunked`'s per-chunk narration used to produce for a
# routine whose own rules fall in a *different* chunk (see `brief.py`'s
# `chunk_map` parameter, which now hands every chunk the routine -> chunk-
# number mapping precisely so this has something concrete to say instead).
# Deliberately narrow to chunk-crossing language, not "covered elsewhere" or
# "described in more detail below" generally -- those routinely and validly
# refer to a later section of the *same* document, which is not a gap.
DEFERRED_REFERENCE = re.compile(
    r"\b(?:cover(?:ed|s)|document(?:ed|s)|explain(?:ed|s)|address(?:ed|es))\s+"
    r"(?:by|in)\s+(?:a|another|a\s+later|a\s+separate|the\s+next)\s+chunk\b"
    r"|"
    r"\bnot\s+(?:captured|covered|documented)\s+(?:within|in)\s+this\s+chunk(?:'s\s+rule\s+range)?\b",
    re.I,
)

# A concrete pointer that resolves a DEFERRED_REFERENCE match: either a
# specific chunk number ("chunk 15", "chunk15.md", "chunk 3 of 16") or a
# citation/markdown link naming where the deferred content actually lives.
_CONCRETE_CHUNK_POINTER = re.compile(r"\bchunk\s*[-.]?\s*\d+\b|\.chunk\d+\.md", re.I)


def _deferred_reference_problems(body: str) -> list[str]:
    """Flag a `DEFERRED_REFERENCE` match whose containing paragraph names no
    concrete chunk to look in.

    Advisory only (mirrors `_statement_completeness_problems`): this is a
    prose-quality signal, not a citation-integrity one, and its false-
    positive rate against phrasing this pattern hasn't seen is unknown. Its
    job is to catch a *regression* back to the vague, unresolved forward
    reference this exists to prevent -- the actual fix is giving the model
    the routine -> chunk-number map in the first place (see brief.py's
    `chunk_map`), not retrying against this check.
    """
    problems = []
    for m in DEFERRED_REFERENCE.finditer(body):
        para, _ = _containing_paragraph(body, m.start(), m.end())
        if _CONCRETE_CHUNK_POINTER.search(para):
            continue
        snippet = body[max(0, m.start() - 40): m.end() + 40].replace("\n", " ").strip()
        problems.append(
            f"deferred reference with no concrete chunk named near: …{snippet}…"
        )
    return problems


# The *concrete* counterpart to DEFERRED_REFERENCE above: the phrasing
# module_brief's `chunk_map` guidance actually instructs the model to use --
# "documented in chunk N" naming a specific chunk index -- rather than the
# vague "documented in a later chunk" DEFERRED_REFERENCE exists to catch.
# The two patterns are deliberately disjoint (DEFERRED_REFERENCE requires
# "a"/"another"/"a later"/"a separate"/"the next" between by/in and "chunk";
# this requires a literal number there instead), so a well-formed match for
# one never also matches the other -- a chunk's prose using one of these two
# verb/phrase shapes lands in exactly one of the two checks below, never
# both. That's narrower than covering every way a model might phrase a
# chunk-crossing reference, though: a phrasing neither pattern recognises
# (e.g. "continues in chunk 2") matches neither check and is silently
# missed by both, not caught as a false positive by either.
_CONCRETE_FORWARD_REFERENCE = re.compile(
    r"\b(?:cover(?:ed|s)|document(?:ed|s)|explain(?:ed|s)|address(?:ed|es))\s+"
    r"(?:by|in)\s+chunk\s*[-.]?\s*(?P<n>\d+)\b",
    re.I,
)

# A chunk document's filename stem, per batch.py's `_generate_module_doc_
# chunked` naming convention (see its `expected_chunk_names`/
# `_prune_stale_chunk_files`): "<member-stem>.chunkNN" with NN zero-padded to
# that run's own chunk-count width. The width isn't recoverable from one
# filename alone (it depends on the member's total chunk count), so this
# only recovers the chunk index as an int -- comparisons below are always
# int-to-int, never against the padded string.
_CHUNK_FILENAME = re.compile(r"\.chunk0*(?P<n>\d+)$", re.I)


def forward_reference_problems(conn, results: list[dict]) -> list[str]:
    """A chunk document's "documented in chunk N" forward reference
    (`module_brief`'s `chunk_map` parameter -- see its docstring, and
    `brief.py`'s "Internal routines" section for the exact phrasing this
    instructs the model to use) whose named chunk either doesn't exist in
    this member's own generated chunk-file set, or exists but never
    mentions the routine the deferral names.

    Complements `_deferred_reference_problems` (per-document; only checks
    that *some* concrete chunk number is named at all, not that it's the
    right one) with the cross-document check issue #128 describes: each
    chunk is generated, validated, and retried independently of every other
    chunk in its set (`_generate_module_doc_chunked`'s per-chunk loop), so
    nothing before this ever went back and confirmed a chunk's own forward
    reference was actually fulfilled by the chunk it named -- a wrong,
    stale, or hallucinated chunk number happily validates as long as the
    chunk making the claim is itself well-formed. Cross-checking a forward
    reference at all needs to see every chunk file generated for a member so
    far, not just the one being narrated -- so, like `module_completeness_problems`
    and `statement_citation_coverage_problems`, it runs at the tree level,
    not inside `validate_doc`. That still works when only one chunk exists
    on disk (a partial or single-chunk member): the "named chunk doesn't
    exist" half of the check needs no sibling to fire against, so it still
    catches an unambiguous reference to a chunk that was never generated.

    Advisory only (mirrors `_deferred_reference_problems`): identifying
    *which* routine a deferral is about is a prose-proximity heuristic (the
    nearest of this member's known routine names -- from the fact store's
    `routine` table, the same ground truth `chunk_map` itself is built from
    -- mentioned before the "documented in chunk N" phrase, in the same
    paragraph) rather than a citation with a fixed, parseable shape. A
    chunk's own phrasing naming the routine in some way this doesn't
    recognise produces a false negative (a real mismatch this misses), not
    a false positive, so on its own that would argue for hard-failure. But
    the "chunk N doesn't exist" half of this check *is* unambiguous, and
    both halves share one scan over the same matches -- keeping the whole
    check advisory (like #59's `_statement_completeness_problems` already
    reasons for a narrower heuristic than this) accepts a slightly softer
    guarantee on the "exists but doesn't mention it" half in exchange for
    not needing a second pass to split the two apart.
    """
    # member (upper) -> {chunk index -> body}, built only from doc_type:
    # module documents whose filename matches the chunk-file convention.
    # A chunked member's own module_index overview (doc_type: module_index)
    # and an unchunked member's plain module doc (no ".chunkN" suffix in its
    # filename) both fall outside this map, as intended: neither is a member
    # of the chunk set a forward reference needs to resolve against.
    chunk_bodies: dict[str, dict[int, str]] = defaultdict(dict)
    chunk_docs: list[tuple[str, int, str]] = []  # (member, chunk index, body)
    for r in results:
        fm = r.get("_fm")
        if not fm or fm.get("doc_type") != "module":
            continue
        sources = fm.get("sources") or []
        if len(sources) != 1:
            continue
        m = _CHUNK_FILENAME.search(Path(r["path"]).stem)
        if not m:
            continue
        member = sources[0].upper()
        idx = int(m.group("n"))
        body = r.get("_body") or ""
        chunk_bodies[member][idx] = body
        chunk_docs.append((sources[0], idx, body))

    routines_cache: dict[str, list[str]] = {}
    problems: list[str] = []
    for member_name, this_chunk, body in chunk_docs:
        member = member_name.upper()
        # No `len(siblings) < 2` gate here: `siblings` always contains at
        # least `this_chunk` itself (this same loop populated `chunk_bodies`
        # from `chunk_docs`, which is exactly the set this iterates), so a
        # single-chunk member (only "<member>.chunk01.md" on disk, e.g. a
        # `mfdoc batch` run not yet resumed to completion) still reaches the
        # checks below with `siblings == {this_chunk: body}`. That's enough
        # to catch an unambiguous forward reference to a chunk that doesn't
        # exist yet -- `target_chunk not in siblings` -- which is exactly
        # what a partial chunk set should still be checked for; only the
        # second half ("chunk N exists but never mentions the routine")
        # needs a real sibling to inspect, and it naturally can't fire until
        # `target_chunk in siblings` holds, which requires 2+ chunks anyway.
        siblings = chunk_bodies.get(member) or {}

        if member not in routines_cache:
            rows, ambiguous_libs = resolve_member_by_name(conn, member_name)
            routines_cache[member] = (
                [] if ambiguous_libs or not rows
                else [rt["name"] for rt in fetch_routines(conn, rows[0]["id"])]
            )
        routine_names = routines_cache[member]
        if not routine_names:
            continue

        for dm in _CONCRETE_FORWARD_REFERENCE.finditer(body):
            para, rel_start = _containing_paragraph(body, dm.start(), dm.end())
            target_chunk = int(dm.group("n"))
            if target_chunk == this_chunk:
                continue  # a chunk naming its own number isn't deferring anywhere

            # Nearest of this member's known routine names mentioned before
            # the deferral phrase, in the same paragraph -- the routine the
            # deferral is presumably about. Skip entirely when none is
            # found: with nothing concrete to check the claim against,
            # flagging anything here would just be re-deriving
            # _deferred_reference_problems's own "vague deferral" finding.
            named_routine = None
            best_pos = -1
            preceding = para[:rel_start]
            for name in routine_names:
                last_start = -1
                for m in _name_pattern(name).finditer(preceding):
                    last_start = m.start()
                if last_start > best_pos:
                    best_pos = last_start
                    named_routine = name
            if named_routine is None:
                continue

            if target_chunk not in siblings:
                problems.append(
                    f"{member_name} chunk {this_chunk}: forward reference near routine "
                    f"'{named_routine}' names chunk {target_chunk}, but this member's "
                    f"generated chunk set has no such chunk (chunks present: "
                    f"{', '.join(str(n) for n in sorted(siblings))})"
                )
                continue
            if not _name_mentioned(siblings[target_chunk], named_routine):
                problems.append(
                    f"{member_name} chunk {this_chunk}: forward reference says routine "
                    f"'{named_routine}' is documented in chunk {target_chunk}, but "
                    f"'{named_routine}' is never mentioned anywhere in chunk "
                    f"{target_chunk}'s generated document"
                )
    return problems


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


def validate_doc(conn, path: Path, outcome_field=OUTCOME_FIELD, _text: str | None = None) -> dict:
    """`_text`, when given, is used instead of reading `path` again -- a
    tree walk (`validate_tree`) that already read every file once to decide
    scope (`_partition_pipeline_docs`, issue #130) passes its cached
    content through here rather than re-reading the same file from disk a
    second time. Callers validating a single known path (the common case
    outside a tree walk) simply omit it."""
    text = _text if _text is not None else path.read_text(encoding="utf-8")
    problems: list[str] = []
    omitted_targets: list[str] = []
    deferred_references: list[str] = []
    staleness_problems: list[str] = []
    # Cache of _fetch_statement_rows(conn, member_id) results, keyed by
    # member_id -- a document commonly cites the same member several times
    # (different line ranges each time), and this avoids re-running all
    # three _STATEMENT_SOURCES queries for every one of those citations.
    statement_rows_cache: dict[int, list] = {}
    fm, body, fm_err = split_frontmatter(text)
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
        # `_out_of_scope_sources` (issue #130) deliberately does *not* treat
        # a malformed `sources` value as a cross-project skip signal -- it
        # relies on this check to actually report the contract violation
        # instead, rather than the value silently passing through as
        # "valid" (or distorting a tree-level aggregate check that iterates
        # `sources` expecting a list of member-name strings).
        if "sources" in fm:
            sources = fm["sources"]
            if not isinstance(sources, list):
                problems.append(f"sources must be a list, got {sources!r}")
            elif not all(isinstance(s, str) for s in sources):
                problems.append(f"sources must be a list of strings, got {sources!r}")
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

        if fm.get("doc_type") in ("module", "module_index"):
            first_line = next((ln for ln in body.splitlines() if ln.strip()), "")
            if not first_line.lstrip().startswith("#"):
                problems.append(
                    "body does not open with a top-level '# ' heading -- looks like the "
                    "response narrated commentary (e.g. restating its own scope/instructions) "
                    "before the actual document content instead of starting with it"
                )

    # Scoped to narrative module docs only -- both a member's own chunk
    # documents (doc_type: module) and a chunked member's whole-module
    # overview (doc_type: module_index, batch.py's _render_module_index_doc),
    # since the latter's five reconciled sections carry citations copied
    # forward from already-validated chunks and are exactly as meaningful to
    # check here. A generated-test doc or a flat register echoes source
    # syntax and field-inventory phrasing verbatim,
    # sentence-per-YAML-field rather than sentence-per-claim -- the same
    # literal recurs across several adjacent lines with different framing
    # each time (a precondition, a Given, a When), which defeats the
    # single-nearest-occurrence assumption this check relies on and would
    # make it noise rather than signal outside the doc type it was built for.
    # (Gates `_statement_completeness_problems` below too, for the same reason.)
    module_doc_checks = fm is not None and fm.get("doc_type") in ("module", "module_index")

    if module_doc_checks:
        deferred_references.extend(_deferred_reference_problems(body))
        staleness = _staleness_problem(fm)
        if staleness:
            staleness_problems.append(staleness)

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
            elif module_doc_checks:
                problems.extend(
                    _reversed_condition_problems(
                        conn, member, row["id"], lf, lt, body, m.start(), m.end(),
                        outcome_field=outcome_field,
                    )
                )
                if row["id"] not in statement_rows_cache:
                    statement_rows_cache[row["id"]] = _fetch_statement_rows(conn, row["id"])
                omitted_targets.extend(
                    _statement_completeness_problems(
                        statement_rows_cache[row["id"]], member, lf, lt, body, m.start(), m.end()
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
        "omitted_statement_targets": omitted_targets,
        "deferred_references": deferred_references,
        "staleness_problems": staleness_problems,
        "problems": problems,
        "ok": not problems,
        # Front matter/body this call already parsed, for a caller (e.g.
        # validate_test_doc) that needs the same document's checks on top
        # of these -- reusing this avoids a second disk read and YAML parse
        # of a file this function just read and parsed itself.
        "_fm": fm,
        "_body": body,
    }


def validate_test_doc(conn, path: Path, _text: str | None = None,
                       _prior_fingerprint: str | None = None,
                       _render_time: bool = False,
                       _valid_scenarios: Callable[[], set[str]] | None = None,
                       _fingerprint_cache: dict[tuple[str, ...], str | None] | None = None) -> dict:
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

    The sidecar is only rewritten after a *successful* validation
    (`testbatch.write_test_doc_with_sidecar`), so it can itself go stale: if
    something upstream of `test-plan` renumbers `rule_candidate` rows after
    the sidecar was last written (a `classify-rules` re-run, a `derive`
    rebuild), every BR-id it contains shifts positionally and stops
    matching any current `test_case` row (issue #195). Comparing that
    numbering against a freshly generated manifest would then report every
    id in the sidecar as missing, even though nothing about the current run
    is wrong.

    Detected two ways, tried in this order:

    1. **Fingerprint comparison (authoritative).** `write_test_doc_with_
       sidecar` stamps a `test_case_fingerprint` field into the document's
       front matter -- `testplan.member_rule_fingerprint`'s hash of the
       exact `rule_candidate` `(id, line_no)` ordering that determined this
       render's `BR-nnn` numbering, at write time. If that field is
       present, this recomputes the same fingerprint from `sources`'
       member(s) right now (`testplan.doc_rule_fingerprint`) and compares:
       any mismatch means the corpus has genuinely moved on since the
       sidecar was written, full stop -- this is a direct, exact signal,
       not an inference from which ids happen to still resolve. It is what
       correctly catches the case an ID-overlap check alone cannot: a rule
       inserted (or removed) *after* the sidecar's own BR-range still
       shifts every later id project-wide, but leaves the sidecar's own
       (unshifted) ids a literal subset of the current valid set -- e.g.
       old `{BR-001, BR-002, BR-003}` with a rule now inserted afterward,
       current valid set `{BR-001, BR-002, BR-003, BR-004}`. An
       ID-membership check reads that as "still current" (every old id
       still resolves) and wrongly cross-checks the stale sidecar against
       the fresh manifest anyway; the fingerprint, computed from the whole
       member's `rule_candidate` ordering rather than just the ids this one
       sidecar happens to mention, does not.
    2. **ID-overlap fallback (legacy documents only).** No stored
       `test_case_fingerprint` at all (an older document written before
       this field existed, or a hand-written/test fixture) falls back to
       one of two things, depending on `_render_time`:
       - **`_render_time=False`** (a standalone check -- `mfdoc
         test-validate`, a dry-run reuse check): checks whether *every*
         one of the sidecar's own BR-ids resolves against `test_case`; if
         it has BR-ids and *any* of them don't, the sidecar is treated as
         though it weren't there. Deliberately `all(...)`, not `any(...)`:
         a partial positional shift (old `{BR-001, BR-002, BR-003}`
         renumbered to `{BR-002, BR-003, BR-004}`) still has two
         overlapping ids by coincidence, which `any(...)` would wrongly
         call "still current". This still can't catch the
         insertion-after-range case (1) handles for a document with no
         fingerprint context at all.
       - **`_render_time=True`** (`_generate_test_doc_from_brief`/
         `run_test_batch`'s retry loops, validating a freshly-generated
         candidate that's about to replace this sidecar's pairing if it
         validates clean): the sidecar is treated as though it weren't
         there outright, with no id-overlap check at all. A legacy
         document's sidecar predating this fingerprint entirely can
         otherwise **deadlock**: on the very next `test-plan` re-run that
         adds a new scenario after the old sidecar's range, the fresh
         candidate's manifest legitimately names that new id, the old
         sidecar's ids remain a coincidental subset of the current valid
         set (`all(...)` reads that as "still current"), the cross-check
         reports the new id as "missing from sidecar", validation fails
         on *every* retry (the corpus hasn't changed between them), and
         because `write_test_doc_with_sidecar` only runs after a
         *successful* validation, the sidecar is never refreshed and
         never gets its own fingerprint either -- reproducing indefinitely
         on every future invocation, not self-resolving the way a
         `_render_time=True` bypass instead makes it. Safe specifically
         because this validation's own outcome determines whether the
         sidecar is about to be rewritten anyway: a candidate this bypass
         lets through still has to actually resolve against `test_case`
         (the ordinary `bad_refs` check below still runs against `body`,
         unaffected by this), so nothing invented slips through -- this
         bypass only ever removes a *stale-or-legacy* sidecar's veto
         power over an otherwise-correct fresh candidate, never weakens
         what "correct" means.

    A document with a real fingerprint of its own (case 1 above) is
    unaffected by `_render_time` either way -- the exact check is always
    preferred when it's available, at any call site.

    Either way, `result["sidecar_stale"]` reports the outcome without it
    counting toward `problems`/`ok` -- a stale sidecar isn't a defect in
    *this* document, and gets overwritten with fresh content the next time
    this validation actually succeeds. When the ID-overlap fallback is what
    triggered it, a sidecar with one genuinely invented/malformed id mixed
    in among otherwise-current ones is also treated as stale rather than
    flagged directly in `problems` -- not silently lost, though:
    `result["sidecar_unresolved_ids"]` lists exactly which of the
    sidecar's ids didn't resolve, for a caller or a human reading a `mfdoc
    test-validate` report who wants to tell "genuine renumbering" apart
    from "one bad id" in that fallback case.

    `_prior_fingerprint`, from `testbatch._prior_fingerprint_for`: the
    fingerprint a *previous* successful render already stamped at `path`,
    for a caller validating a freshly-generated candidate that has just
    overwritten that same path -- the candidate's own front matter never
    carries `test_case_fingerprint` itself (only `write_test_doc_with_
    sidecar` adds it, after validation succeeds), so without this the
    fingerprint check above would have nothing to compare against on
    exactly the validation this issue is about (the render/retry loop's
    own first pass, not a later re-check of an already-fully-written
    document) and would silently fall through to the weaker ID-overlap
    fallback every time. Used only when the document being validated
    itself carries no `test_case_fingerprint` of its own.

    A malformed `sources` front-matter value (not a list of plain strings
    -- `validate_doc`'s own `_out_of_scope_sources`/`REQUIRED_FRONTMATTER`
    checks already flag this in `problems`) must never make the
    fingerprint lookup itself raise: `doc_rule_fingerprint` is only ever
    called after confirming every element is a string, and any other
    shape leaves `fp` as `None` -- caught here, not treated as "no
    fingerprint available" as a matter of course, so this can't turn a
    front-matter contract violation into an unhandled crash in `mfdoc
    test-validate`.
    """
    result = validate_doc(conn, path, _text=_text)
    fm, body = result.pop("_fm"), result.pop("_body")
    problems = list(result["problems"])

    if fm is not None:
        for key in REQUIRED_TEST_FRONTMATTER:
            if key not in fm:
                problems.append(f"front matter missing required key: {key}")

    # Fetched at most once per call, lazily, and reused for both the
    # sidecar staleness decision and the final `bad_refs` check -- a per-id
    # `SELECT ... WHERE UPPER(scenario_name)=UPPER(?)` query (no index on
    # that expression) would otherwise scan `test_case` twice per id: once
    # for staleness, once for validity (Copilot PR review on issue #195's
    # fix). Lazy rather than unconditional: a document with no sidecar and
    # no `MEMBER:BR-nnn` references at all (an edge case `validate_tests_
    # tree` can still walk into) has no need for this and shouldn't pay a
    # full `test_case` scan on every single document it validates.
    #
    # `_valid_scenarios`, from `validate_tests_tree`: a zero-arg callable
    # that computes and memoizes the full-corpus scan *once*, shared across
    # every document in the tree, instead of every sidecar-bearing document
    # re-running its own `SELECT scenario_name FROM test_case` (Copilot
    # review -- otherwise O(document_count * corpus_size) for a tree
    # validation). Deliberately a callable, not a precomputed set: a tree
    # walk with no sidecar-bearing/BR-referencing documents at all must
    # still never run that scan (the same "don't pay for what nothing needs"
    # contract this whole cache already had, just now shared across
    # documents instead of scoped to one call -- a second review round
    # after the first version of this fix computed the set unconditionally,
    # before any document's own need for it was known). A caller validating
    # one document in isolation (`mfdoc test-gen`'s single-file path, the
    # render/retry loops, every test in this suite) has no tree-wide scan
    # to share and leaves this unset, falling back to the same lazy
    # per-call query as before.
    _valid_scenarios_cache: set[str] | None = None

    def valid_scenarios() -> set[str]:
        nonlocal _valid_scenarios_cache
        if _valid_scenarios_cache is None:
            _valid_scenarios_cache = _valid_scenarios() if _valid_scenarios is not None else {
                row["scenario_name"].upper() for row in conn.execute("SELECT scenario_name FROM test_case")
            }
        return _valid_scenarios_cache

    # The document's own stamped fingerprint, if any -- falling back to
    # `_prior_fingerprint` (the previous successful render's, captured by
    # the caller before overwriting `path` with a fresh candidate) only
    # when this document carries none of its own. A freshly-generated
    # candidate never has one yet (only `write_test_doc_with_sidecar`
    # stamps it, after validation succeeds), so without this fallback the
    # fingerprint check below would have nothing to compare against on
    # exactly the render/retry loop's own first validation of a chunk.
    stored_fingerprint = (fm.get("test_case_fingerprint") if fm is not None else None) or _prior_fingerprint
    _current_fingerprint_cache: list = []  # 0 or 1 element -- memoized None is valid too

    def current_fingerprint() -> str | None:
        if not _current_fingerprint_cache:
            sources = fm.get("sources") if fm is not None else None
            # A malformed `sources` (not a list of plain strings --
            # already flagged separately in `problems` by validate_doc's
            # own front-matter checks) must never make this raise:
            # `doc_rule_fingerprint`'s `sorted(..., key=str.upper)` would
            # otherwise crash on a non-string element (e.g. `sources:
            # [123]`), turning a front-matter contract violation into an
            # unhandled `mfdoc test-validate` crash instead of just
            # leaving the fingerprint unavailable and falling through to
            # the ID-overlap fallback below.
            fp = None
            if isinstance(sources, list) and sources and all(isinstance(s, str) for s in sources):
                # Stripped: `resolve_member_by_name`'s lookup is an exact
                # `UPPER(name)=UPPER(?)` match, so `sources: ["FAKEMOD "]`
                # (stray whitespace, however it got there) would otherwise
                # fail to resolve and silently fall back to the weaker
                # id-overlap check instead of the exact fingerprint one.
                #
                # `_fingerprint_cache`, from a caller re-validating several
                # documents that share the same `sources` -- most commonly
                # every chunk of one member in `_generate_member_test_doc_
                # chunked`, or every retry attempt of one member's document
                # in `run_test_batch` (Copilot review): each of those calls
                # `doc_rule_fingerprint`, which re-queries and re-hashes
                # that member's *entire* `rule_candidate` set from
                # scratch, even though it's the same member and therefore
                # the same fingerprint every time. Keyed by the sorted,
                # stripped `sources` tuple so it's correct regardless of
                # input ordering/whitespace; a caller with nothing to
                # share (every other call site, including this suite)
                # leaves it unset and pays the same per-call cost as
                # before.
                key = tuple(sorted((s.strip() for s in sources), key=str.upper))
                if _fingerprint_cache is not None and key in _fingerprint_cache:
                    fp = _fingerprint_cache[key]
                else:
                    fp = doc_rule_fingerprint(conn, list(key))
                    if _fingerprint_cache is not None:
                        _fingerprint_cache[key] = fp
            _current_fingerprint_cache.append(fp)
        return _current_fingerprint_cache[0]

    sidecar = sidecar_path_for(path, fm.get("language")) if fm is not None else None
    sidecar_usable = False
    sidecar_unresolved_ids: list[str] = []
    sidecar_had_ids = False
    # Populated only by the `_render_time` legacy-sidecar bypass below, with
    # exactly the old sidecar's ids that are *still* real `test_case`
    # scenarios today (Copilot review): that bypass drops the stale
    # sidecar's veto power over a fresh candidate entirely, which is right
    # for the *new*-id deadlock it exists to fix (see this function's
    # docstring) but was also silently dropping the other direction --
    # `bad_refs` below only ever checks that a candidate's *own* ids are
    # valid, never that it didn't just quietly stop mentioning a scenario
    # the old sidecar did. Restricted to still-valid ids on purpose: an old
    # id the corpus has since retired is exactly what the bypass exists to
    # stop vetoing, and demanding a candidate still reference it would
    # reintroduce the same deadlock this bypass closes.
    legacy_bypass_still_valid_ids: set[str] = set()
    if sidecar is not None and sidecar.exists():
        code_ids = {
            f"{m.group('member').upper()}:BR-{m.group('n')}"
            for m in BR_REF.finditer(sidecar.read_text(encoding="utf-8"))
        }
        sidecar_had_ids = bool(code_ids)
        # Staleness guard (issue #195): the sidecar is only rewritten after a
        # *successful* validation, so if something upstream of `test-plan`
        # renumbers `rule_candidate` rows after the sidecar was last written
        # (a `classify-rules` re-run, a `derive` rebuild), every BR-id in it
        # shifts positionally and stops matching any current `test_case`
        # row. Comparing that stale numbering against a freshly generated
        # manifest then produces a "not found" for every id in the sidecar,
        # not because anything about this run is actually wrong -- just
        # because the sidecar predates the renumbering.
        #
        # Fingerprint check first (exact, see docstring): a stored
        # `test_case_fingerprint` that no longer matches the current
        # `rule_candidate` ordering for this document's `sources` means the
        # corpus has genuinely moved on, regardless of whether the
        # sidecar's own ids happen to still resolve -- this is what catches
        # an insertion *after* the sidecar's own BR-range, which leaves its
        # ids a literal (and therefore ID-overlap-invisible) subset of the
        # current valid set.
        fp = current_fingerprint() if stored_fingerprint else None
        if stored_fingerprint and fp is not None:
            sidecar_usable = fp == stored_fingerprint
        elif _render_time:
            # No fingerprint context at all (a legacy sidecar predating
            # this field), validating a freshly-generated candidate about
            # to replace it if this succeeds: treat the sidecar as absent
            # rather than falling to the id-overlap heuristic below. That
            # heuristic can otherwise deadlock a legacy document
            # indefinitely -- see this function's own docstring -- because
            # `write_test_doc_with_sidecar` (the only thing that would
            # ever stamp a real fingerprint) only runs after a successful
            # validation, and the id-overlap check is exactly what would
            # keep failing it. Safe here specifically because this is a
            # render/retry-loop call: the ordinary `bad_refs` check below
            # still runs against `body` regardless, so an invented id in
            # the candidate is still caught -- this bypass only removes a
            # stale sidecar's veto power, not the underlying correctness
            # check itself.
            sidecar_usable = False
            legacy_bypass_still_valid_ids = code_ids & valid_scenarios()
        else:
            # Fallback (a standalone check -- mfdoc test-validate, a
            # dry-run reuse check -- on a document with no fingerprint
            # context): the same id-overlap heuristic this guard
            # originally shipped with. Deliberately `all(...)`, not
            # `any(...)`: a partial positional shift (old {BR-001,
            # BR-002, BR-003} renumbered to {BR-002, BR-003, BR-004})
            # still leaves some ids coincidentally overlapping with
            # `test_case`'s current numbering, which `any(...)` would
            # wrongly read as "still current". This fallback still can't
            # see an insertion after the sidecar's own range the way the
            # fingerprint check above can -- accepted only because it's
            # limited to documents predating that field, and only reached
            # outside the render loop (where `_render_time` above already
            # closes the gap that matters most).
            sidecar_usable = not code_ids or code_ids <= valid_scenarios()
        if not sidecar_usable:
            # Diagnostic only, deliberately not appended to `problems`/`ok`:
            # a stale sidecar isn't a defect in *this* document -- it's
            # leftover state from before an upstream renumbering, and
            # `write_test_doc_with_sidecar` will overwrite it with fresh
            # content the next time this validation actually succeeds.
            # Recorded via `result["sidecar_unresolved_ids"]` so a caller
            # (or a human reading a `mfdoc test-validate` report) can still
            # see exactly which sidecar ids don't resolve against current
            # `test_case` rows (even when the fingerprint mismatch, not an
            # unresolved id, is what actually triggered `sidecar_stale` --
            # this can come back empty in that case, which is expected).
            sidecar_unresolved_ids = sorted(code_ids - valid_scenarios())
    if sidecar is not None and sidecar.exists() and sidecar_usable:
        manifest_ids = {
            f"{m.group('member').upper()}:BR-{m.group('n')}" for m in BR_REF.finditer(body)
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
        if sidecar_had_ids and not sidecar_usable and not scan_ids:
            # A stale sidecar that actually had BR-nnn content is being
            # ignored (see the staleness guard above), and the document
            # body has nothing to fall back on scanning either (an empty
            # "## Scenarios covered" manifest, or a code fence with no
            # references) -- this document is untraceable, not clean.
            # Without this, `bad_refs` would stay 0 purely because there is
            # nothing left to check, and `ok=True` would silently report a
            # document that can no longer be verified against anything as
            # though it had passed genuine verification.
            problems.append(
                f"{sidecar.name} is stale and {path.name}'s body has no MEMBER:BR-nnn "
                f"references to fall back on -- this document cannot be verified at all"
            )
        # Completeness check preserved across the `_render_time` legacy-
        # sidecar bypass (Copilot review): that bypass only ever removes
        # the *stale* sidecar's veto power over ids the candidate newly
        # introduces (the deadlock case its docstring describes) -- it was
        # never meant to also waive whether the candidate still covers
        # every scenario the old sidecar did. Scoped to ids that are still
        # genuinely valid `test_case` scenarios today
        # (`legacy_bypass_still_valid_ids`), so a scenario the corpus has
        # since retired can't reintroduce the exact deadlock this bypass
        # exists to close. The fully-empty-`scan_ids` case above already
        # reports the more severe "untraceable" problem; this covers the
        # narrower, easier-to-miss partial-omission case that check alone
        # doesn't catch.
        for sid in sorted(legacy_bypass_still_valid_ids - scan_ids):
            problems.append(
                f"'{sid}' was referenced in {sidecar.name if sidecar is not None else 'the previous sidecar'} "
                f"and is still a valid test_case scenario, but is no longer referenced anywhere in "
                f"{path.name} -- confirm this scenario was intentionally dropped, not silently omitted"
            )

    bad_refs = 0
    for scenario in scan_ids:
        if scenario.upper() not in valid_scenarios():
            bad_refs += 1
            problems.append(f"'{scenario}' is not a known test_case scenario -- run `mfdoc test-plan`, "
                             f"or this id was invented/renumbered")

    result["problems"] = problems
    result["invalid_scenario_refs"] = bad_refs
    result["sidecar_stale"] = sidecar is not None and sidecar.exists() and not sidecar_usable
    result["sidecar_unresolved_ids"] = sidecar_unresolved_ids
    result["ok"] = not problems
    return result


_NON_PIPELINE_DOC_NAMES = frozenset({"README.MD", "SME-NOTES.MD"})


def _is_pipeline_doc(path: Path) -> bool:
    """`README.md` is project documentation, not pipeline output -- it has
    no front matter and was never meant to satisfy this contract, so a
    tree walk must skip it rather than reporting a false failure on the
    one file everyone browsing the directory expects to be different.
    `sme-notes.md` (see `sme_notes.py`, `options.sme_notes`) is the same
    kind of exception: an SME-authored input file, not pipeline output, so
    it has no front matter either -- `examples/sme-notes.md`'s worked
    example would otherwise fail a `--docs examples` tree walk that isn't
    aware of it."""
    return path.name.upper() not in _NON_PIPELINE_DOC_NAMES


def _out_of_scope_sources(fm: dict | None, known_members: set[str]) -> list[str] | None:
    """`fm`'s `sources` front matter, if this document looks like it belongs
    to a *different* project's fact store rather than the one currently
    loaded (see `_partition_pipeline_docs`) -- otherwise `None`.

    A multi-project workspace commonly runs several `mfdoc` configs against
    one shared parent output directory (each project's own `--out`/`--docs`
    subtree living side by side under it, per `--config`'s namespacing
    convention -- see `cli._project_namespace`). Pointing `--docs` at that
    shared parent instead of one project's own subtree makes a tree walk
    visit every project's files, not just the one implied by `--config` --
    with nothing in the directory structure itself to say which file
    belongs to which project. `sources` front matter (required on every
    narrative/generated-test document, see `REQUIRED_FRONTMATTER`) already
    names the real member(s) that document was generated from, so cross-
    checking it against the currently loaded fact store's own `member`
    table is a reliable, already-available signal: a document whose
    `sources` names at least one member, but none of them exist here, was
    generated against a *different* project's fact store, and validating
    it against this one would silently misreport it (module: "member is not
    in the index" citation failures for module docs; "not a known test_case
    scenario" for generated tests) instead of flagging it as out of scope.

    Returns `None` (don't skip) whenever there isn't a real signal either
    way: no front matter, no `sources` key, or an empty `sources` list --
    `doc_type: register` documents (gap-summary.md, glossary.md, ...) and
    `interface-matrix.md` legitimately carry no (or an empty) `sources`
    list despite belonging to this project, and must validate exactly as
    before rather than being silently skipped for lack of a signal.

    Also returns `None` (don't skip) when `sources` isn't a list at all, or
    is a list containing a non-string item -- a bare string, a mapping, or
    any other YAML shape someone wrote by mistake instead of a list of
    member-name strings. That's a genuine front-matter contract violation
    (`sources` is a `REQUIRED_FRONTMATTER` key), not a cross-project signal,
    and this out-of-scope partition has no business swallowing it:
    iterating a malformed value character-by-character (a string) or
    key-by-key (a mapping) can easily produce zero matches against
    `known_members` and get misclassified as belonging to a different
    project, silently skipping the document instead of letting it fall
    through to `validate_doc`'s own `sources` shape check (added alongside
    this exclusion specifically so that check is real, not just assumed --
    see the `REQUIRED_FRONTMATTER` loop there) flag the real problem.

    And returns `None` (don't skip) for `doc_type: language-guide`
    specifically: `templates/language-guide.md` populates `sources` with a
    descriptive placeholder (`["{DIALECT} source files"]`), not a member
    name, per its own design (issue #91) -- the only doc type in this
    codebase where `sources` isn't member provenance. Every other doc type
    that carries a non-empty `sources` list (`module`, `module_index`,
    `generated_test`, and also `data-entity`/`process`, whose templates
    default to `sources: []` but whose real generated instances list actual
    member names, per `docs/guides/architecture.md`) does use real member
    names there, so this exclusion is deliberately narrow to the one type
    that doesn't, rather than an allowlist that would wrongly re-enable
    cross-project contamination for those. Compared stripped and
    case-folded, the same as the general `doc_type` presence check just
    above, so incidental whitespace/casing in front matter can't
    accidentally fall through to the member-matching path below and get
    misclassified as cross-project.

    Also returns `None` (don't skip) whenever `doc_type` itself is missing
    or isn't a non-empty string. `doc_type` is a `REQUIRED_FRONTMATTER` key
    (and, for a register doc, a `REQUIRED_REGISTER_FRONTMATTER` one) --
    `validate_doc` already enforces its presence the same way for every
    other required key (`for key in REQUIRED_FRONTMATTER: if key not in
    fm`), so a document with missing/malformed `doc_type` already has a
    reported front-matter violation independent of `sources`. Treating
    `sources` as a cross-project skip signal on top of that would let this
    out-of-scope partition paper over that violation by skipping the file
    entirely instead of letting it fall through to the check that already
    exists for it -- and there is deliberately no separate enumerated list
    of "valid" doc_type strings to check against here (there isn't one
    anywhere else in this codebase either -- doc_type values are recognised
    ad hoc, per call site, e.g. `in ("module", "module_index")` above and in
    `validate_doc`), so this only guards against the shapes that indicate a
    genuinely broken/missing value (absent, `None`, empty/whitespace, or a
    non-string like a list or number), not against some closed vocabulary.

    Callers must never invoke this with an empty `known_members` -- an empty
    fact store isn't "no signal" the way an empty/absent `sources` is, it's
    the strongest possible signal that something is badly wrong (`mfdoc
    ingest` was never run, or `--config` points at an empty or wrong
    project), and it's a signal about the fact store, not about any one
    document. Under the matching rule below, an empty `known_members` would
    make every document with a non-empty `sources` list look "out of scope"
    simultaneously, since nothing could ever match -- silently skipping the
    entire tree and letting `mfdoc validate` report a false green run
    instead of the real "member is not in the index" failures a broken
    setup should surface. `_partition_pipeline_docs` guards this before
    calling here, once per tree walk, rather than this function re-checking
    it per file."""
    if not fm:
        return None
    doc_type = fm.get("doc_type")
    if not isinstance(doc_type, str) or not doc_type.strip():
        return None
    if doc_type.strip().casefold() == "language-guide":
        return None
    sources = fm.get("sources")
    if not sources:
        return None
    if not isinstance(sources, list):
        return None
    if not all(isinstance(s, str) for s in sources):
        # A `sources` list containing a non-string item (a number, a nested
        # list/dict someone wrote by mistake) is the same kind of
        # front-matter contract violation as `sources` not being a list at
        # all, above -- `str(s).strip()` on a non-string item would coerce
        # it into something that (almost) never matches `known_members`,
        # turning what should be a reported malformed-`sources` failure into
        # a silent out-of-scope skip instead.
        return None
    # Strip whitespace before the membership check -- a front-matter value
    # like "MMP0100 " (easy to introduce hand-editing YAML) must still match
    # `known_members` the way the unpadded name would, or a same-project doc
    # gets misclassified as out of scope over pure formatting. An
    # all-whitespace/empty entry filters out rather than comparing as "".
    normalized = [s.strip() for s in sources]
    normalized = [s for s in normalized if s]
    if not normalized:
        return None
    if any(s.upper() in known_members for s in normalized):
        return None
    return normalized


def _partition_pipeline_docs(conn, root: Path) -> tuple[list[Path], list[str], dict[Path, str]]:
    """Every pipeline-output markdown file under `root`, split into paths to
    actually validate and human-readable notes for ones skipped as
    belonging to a different project (see `_out_of_scope_sources`). Shared
    by `validate_tree` and `validate_tests_tree` so both commands stop
    cross-checking an unrelated project's generated docs against this
    project's fact store when `--docs` points at a directory shared by more
    than one project (issue #130) -- a malformed/unparseable front matter
    is deliberately still handed to the real validator rather than silently
    dropped here, so that failure is reported exactly as before.

    If `known_members` comes back empty -- no `mfdoc ingest` has ever been
    run against the loaded config, or `--config` points at the wrong/an
    empty project -- every file is handed through untouched (nothing goes
    into `skipped`) rather than partitioned by `_out_of_scope_sources` at
    all. An empty fact store can't provide the cross-project signal that
    function relies on: every document with a non-empty `sources` list
    would spuriously "match" the out-of-scope shape (nothing to compare
    against), which would skip the *entire* tree and let `mfdoc validate`
    exit 0 over a broken/misconfigured setup instead of surfacing the real
    "member is not in the index" failures normal validation exists to
    catch -- exactly the case this whole partition must never hide.

    Also returns a `{path: text}` cache of every file's content already read
    here to make the out-of-scope decision -- `validate_tree`/
    `validate_tests_tree` hand it to `validate_doc`/`validate_test_doc` so
    an in-scope document's content is read from disk exactly once per run,
    not once here (for its front matter) and again there (for the rest of
    validation)."""
    known_members = {
        (row[0] or "").upper() for row in conn.execute("SELECT name FROM member").fetchall()
    }
    in_scope: list[Path] = []
    skipped: list[str] = []
    text_cache: dict[Path, str] = {}
    for path in sorted(root.rglob("*.md")):
        if not _is_pipeline_doc(path):
            continue
        text = path.read_text(encoding="utf-8")
        text_cache[path] = text
        if not known_members:
            in_scope.append(path)
            continue
        fm, _, fm_err = split_frontmatter(text)
        bad_sources = None if fm_err else _out_of_scope_sources(fm, known_members)
        if bad_sources is None:
            in_scope.append(path)
        else:
            skipped.append(
                f"{path}: sources {bad_sources} not found in the loaded fact store -- "
                f"looks like it belongs to a different project; skipped rather than "
                f"validated against the wrong one"
            )
    return in_scope, skipped, text_cache


def validate_tests_tree(conn, root: Path) -> dict:
    paths, out_of_scope, text_cache = _partition_pipeline_docs(conn, root)
    # Computed at most once, lazily, and shared across every document below
    # instead of each sidecar-bearing document re-scanning `test_case` on
    # its own (Copilot review on issue #195's fix -- see
    # `validate_test_doc`'s `_valid_scenarios` docstring). A `list`, not a
    # plain variable, purely so the closure below can rebind it without a
    # `nonlocal` declaration.
    _valid_scenarios_cache: list[set[str]] = []

    def shared_valid_scenarios() -> set[str]:
        if not _valid_scenarios_cache:
            _valid_scenarios_cache.append(
                {row["scenario_name"].upper() for row in conn.execute("SELECT scenario_name FROM test_case")}
            )
        return _valid_scenarios_cache[0]

    # Shared the same way, across every chunk of one member's several chunk
    # documents in a tree (`MEMBER.chunk1.md`, `MEMBER.chunk2.md`, ...) --
    # see `validate_test_doc`'s `_fingerprint_cache` docstring.
    fingerprint_cache: dict[tuple[str, ...], str | None] = {}
    results = [
        validate_test_doc(
            conn, p, _text=text_cache.get(p),
            _valid_scenarios=shared_valid_scenarios, _fingerprint_cache=fingerprint_cache,
        )
        for p in paths
    ]
    return {
        "documents": len(results),
        "documents_ok": sum(1 for r in results if r["ok"]),
        "total_citations": sum(r["citations"] for r in results),
        "invalid_citations": sum(r["invalid_citations"] for r in results),
        "invalid_scenario_refs": sum(r.get("invalid_scenario_refs", 0) for r in results),
        "results": results,
        # Advisory only -- never subtracted from documents_ok and never
        # affects a caller's exit code; see _out_of_scope_sources.
        "out_of_scope_documents": out_of_scope,
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
        missing = [n for n, _ in numbered_rule_candidates(rows) if n not in have]
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


def _coalesce_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort and merge overlapping/adjacent `[lo, hi]` ranges into a minimal
    equivalent set, so the per-statement coverage check (`any(lo <= ln <= hi
    for lo, hi in ranges)`) that follows has as few ranges as possible to
    scan -- a member's citations across a whole module document set can
    otherwise number in the hundreds, with lots of overlap between adjacent
    or re-cited ranges, checked once per `call_edge`/`interaction` row.
    Preserves membership exactly: a line covered by any input range is
    covered by exactly one output range, and vice versa."""
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged = [ordered[0]]
    for lo, hi in ordered[1:]:
        last_lo, last_hi = merged[-1]
        if lo <= last_hi + 1:
            if hi > last_hi:
                merged[-1] = (last_lo, hi)
        else:
            merged.append((lo, hi))
    return merged


def statement_citation_coverage_problems(conn, results: list[dict]) -> list[str]:
    """A member's non-dynamic `call_edge` row, or `interaction` row, whose
    source line is never covered by *any* `[[MEMBER:LINE]]` citation across
    that member's whole `doc_type: module` document set.

    Extends `module_completeness_problems` (#50) to a class of fact that
    check has nothing to cross-reference at all: `call_edge`/`interaction`
    rows (a `DO`/`PERFORM` subroutine call, a `PROGRAM`+`DO` external call, a
    `RELEASE`, a `PROMPT`, a `CHAIN`/`TRANSFER` -- see `mantis.py`'s
    `RE_DO_ENTRY`/`RE_CALL`/`RE_RELEASE`/`RE_PROMPT` handlers) never create a
    `rule_candidate` row, so they never get a `BR-nnn` id a narrator could be
    asked to carry forward the way `module_completeness_problems` checks for.
    Citation-range coverage is the closest dialect-neutral equivalent
    available for them.

    Distinct from, and stricter than, `_statement_completeness_problems`
    (#59): that check only inspects the *paragraph* of a citation whose own
    range already happens to cover the row's line -- a line the model drops
    from every citation in a chunk entirely, rather than citing without
    naming its target, has no citation range there for it to inspect at all.
    This checks coverage first, at the citation-range level, before any
    question of whether the citing prose names the target -- and unlike
    #59 (advisory only), a gap here is aggregated separately, under its own
    `statement_coverage_problems` hard-failure list (which `cmd_validate`
    checks alongside `completeness_problems`), so `mfdoc validate`'s exit
    code actually reflects it.

    Scoped to `doc_type: module` documents only, deliberately mirroring
    `module_completeness_problems`'s own scoping (see
    `test_module_completeness_ignores_module_index_docs`): a chunked
    member's `module_index` overview carries citations copied forward from
    its already-checked chunks, so counting it too would let an index page
    launder a chunk's real gap.

    `data_access` rows are deliberately excluded -- CRUD/field-level access
    already has other machinery (`unused_entity_fields`) checking it, and
    this issue is scoped to control-transfer/resource statements only.
    """
    cited_ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    members: set[str] = set()
    for r in results:
        fm = r.get("_fm")
        if not fm or fm.get("doc_type") != "module":
            continue
        doc_sources = {src.upper() for src in (fm.get("sources") or [])}
        members.update(fm.get("sources") or [])
        for m in CITATION.finditer(r.get("_body") or ""):
            lf = m.group("from")
            if lf is None:
                continue
            cited_member = m.group("member").upper()
            # Only a citation to a member this document actually declares as
            # one of its own `sources` counts toward that member's coverage
            # -- a module doc can legitimately cite a *different* member in
            # passing (e.g. a caller referencing a callee's line), and that
            # must not let an unrelated document's citation satisfy this
            # gate for a member it isn't actually documenting.
            if cited_member not in doc_sources:
                continue
            lf = int(lf)
            lt = int(m.group("to")) if m.group("to") else lf
            cited_ranges[cited_member].append((lf, lt))

    problems = []
    for member in sorted(members):
        rows, ambiguous_libs = resolve_member_by_name(conn, member)
        if ambiguous_libs or not rows:
            continue
        member_id = rows[0]["id"]
        member_ranges = _coalesce_ranges(cited_ranges.get(member.upper(), []))
        missing = []
        for table, target_col, kind_col, sql in _STATEMENT_SOURCES:
            if table == "data_access":
                continue
            for stmt in conn.execute(sql, (member_id,)).fetchall():
                ln = stmt["line_no"]
                if not any(lo <= ln <= hi for lo, hi in member_ranges):
                    missing.append((ln, stmt[kind_col], stmt[target_col]))
        if not missing:
            continue
        missing.sort()
        examples = "; ".join(f"{kind} '{target}' @{ln}" for ln, kind, target in missing[:5])
        more = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
        problems.append(
            f"{member}: {len(missing)} call/interaction statement(s) never covered by any "
            f"citation across its generated module document(s) ({examples}{more})"
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
    `call_edge` -- too fragile a heuristic for the exact node count. Same
    exemption for "Call graph (index)": when the graph has 2+ disconnected
    components, `call_graph_diagram` renders one node per *component*
    instead, which is legitimately far fewer than call_edge's real node
    count and checked the same fragile-heuristic way per-component files
    already aren't."""
    if fm.get("title") in ("Call graph (collapsed)", "Call graph (index)"):
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
    paths, out_of_scope, text_cache = _partition_pipeline_docs(conn, root)
    results = [validate_doc(conn, p, outcome_field=outcome_field, _text=text_cache.get(p)) for p in paths]
    return {
        "documents": len(results),
        "documents_ok": sum(1 for r in results if r["ok"]),
        "total_citations": sum(r["citations"] for r in results),
        "invalid_citations": sum(r["invalid_citations"] for r in results),
        "results": results,
        "completeness_problems": module_completeness_problems(conn, results),
        "statement_coverage_problems": statement_citation_coverage_problems(conn, results),
        "artifact_problems": _artifact_consistency_problems(conn, results),
        # Advisory only (see _statement_completeness_problems) -- never
        # subtracted from documents_ok and never affects a caller's exit code.
        #
        # Deduplicated across the whole tree: `_statement_completeness_problems`
        # runs once per citation with no dedup of its own, so the same
        # statement is reported once per citation whose range covers its
        # line (e.g. two overlapping-range citations of the same member both
        # covering one omitted FETCH target produce the same message twice).
        # The message string already encodes member/line/target uniquely, so
        # deduping on it is sufficient; `dict.fromkeys` preserves first-seen
        # order.
        "omitted_statement_targets": list(dict.fromkeys(
            p for r in results for p in r.get("omitted_statement_targets", [])
        )),
        # Advisory only, same as omitted_statement_targets above -- never
        # subtracted from documents_ok and never affects a caller's exit code.
        "deferred_references": list(dict.fromkeys(
            p for r in results for p in r.get("deferred_references", [])
        )),
        # Advisory only, same reasoning as deferred_references above (see
        # forward_reference_problems's own docstring for why) -- never
        # subtracted from documents_ok and never affects a caller's exit code.
        "forward_reference_problems": forward_reference_problems(conn, results),
        # Advisory only, same reasoning as the two lists above -- a version
        # mismatch alone doesn't mean a document's content is wrong.
        "stale_documents": [
            f"{r['path']}: {p}" for r in results for p in r.get("staleness_problems", [])
        ],
        # Advisory only -- never subtracted from documents_ok and never
        # affects a caller's exit code; see _out_of_scope_sources.
        "out_of_scope_documents": out_of_scope,
    }

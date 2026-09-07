"""Phase 3 -- batch narrative harness for formulaic module docs (option C).

Generates one module document per Natural/Mantis program-level member: brief
-> model call (writing rules + template as context) -> write -> validate ->
retry once on validation failure with the failure text appended. System
overview, process flows and the gap register are judgement-heavy and stay in
the interactive CLI/Claude Code path -- this harness only ever touches the
high-volume, formulaic module docs.

Concurrency is limited to the model call itself. All SQLite access
(module_brief, validate_doc) stays on the calling thread: sqlite3
connections are not safe to share across threads, and the actual bottleneck
in this workload is model latency, not local DB reads.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import __version__
from .brief import (
    fetch_routines, fetch_rule_candidate_rows, module_brief, routine_aware_chunk_ranges,
    routine_for_line,
)
from .citations import _cite, _rule_id
from .db import GAP_SEVERITY_ORDER_SQL
from .redact import NULL_REDACTOR, Redactor
from .validate import CITATION, _split_frontmatter, validate_doc

# The example `generated_by` value in reference/writing-rules.md's worked
# example and templates/module.md's front matter block is a literal,
# unchanging string ("legacy-functional-docs 0.1.0") -- neither document
# tells the model what the real, currently-installed version is, so a model
# has no way to write it correctly and, worse, tends to just echo the
# worked example's literal value verbatim run after run. Rather than fix
# this by telling the model the real version (one more fact that could be
# stated wrong or go stale in the prompt itself), the version is corrected
# deterministically here, the same way `_render_module_chunk_index` already
# builds it directly rather than asking a model for it.
_GENERATED_BY_LINE = re.compile(r"(?m)^generated_by:\s*legacy-functional-docs\s+\S+\s*$")


def _fix_generated_by_version(text: str) -> str:
    """`text` with its `generated_by:` line's version corrected to the
    actually-installed `__version__`, regardless of what the model wrote.
    A no-op if the line isn't present in the expected `legacy-functional-docs
    <version>` shape (e.g. missing front matter entirely) -- validate_doc's
    own front-matter check reports that case, not this function's job to."""
    return _GENERATED_BY_LINE.sub(f"generated_by: legacy-functional-docs {__version__}", text, count=1)

# Object types that get the batch treatment: one module, one program's worth
# of judgement-light narrative. Data stores, system overview, process flows
# and the gap register stay in the CLI path.
BATCHABLE_OBJECT_TYPES = {"program", "subprogram", "subroutine", "copycode"}
BATCHABLE_DIALECTS = {"natural", "mantis"}

# A member whose own rule_candidate count exceeds this gets rendered as
# several independent chunk documents instead of one (see
# generate_module_doc/_generate_module_doc_chunked below) -- calibratable
# per-project via options.narrative.max_rules_per_call, same pattern as
# testbatch.py's DEFAULT_MAX_SCENARIOS_PER_CALL/max_scenarios_per_call.
# Asking a single non-streaming completion to narrate a large module's
# entire rule set in one pass risks the response running out of room partway
# through and silently dropping the remaining subroutines/rules -- a
# response that still validates (every rule it did write is cited) but is
# nowhere near complete, and nothing before this caught that. Chunk
# boundaries are chosen by routine_aware_chunk_ranges (brief.py), which
# packs whole routines into each chunk rather than cutting at a flat rule
# count -- see that function's docstring for why.
DEFAULT_MAX_RULES_PER_CALL = 40


def _resolve_max_rules_per_call(max_rules_per_call: int | None) -> int:
    """`None` means "not configured" -- fall back to the default. Anything
    else must be a positive int: `or DEFAULT_MAX_RULES_PER_CALL` would treat
    an explicit `0` the same as "not configured" (0 is falsy) and silently
    substitute the default instead of respecting it or rejecting it, masking
    a real misconfiguration either way."""
    if max_rules_per_call is None:
        return DEFAULT_MAX_RULES_PER_CALL
    if max_rules_per_call <= 0:
        raise ValueError(
            f"options.narrative.max_rules_per_call must be a positive integer, "
            f"got {max_rules_per_call!r}"
        )
    return max_rules_per_call


@dataclass
class ModelResponse:
    text: str
    input_tokens: int
    output_tokens: int


# A caller takes a prompt and returns a ModelResponse. Swap in a fake for
# tests; the CLI wires up an Anthropic-backed one.
ModelCaller = Callable[[str], ModelResponse]


def model_response_from_message(message) -> ModelResponse:
    """Build a `ModelResponse` from an Anthropic SDK `Message` -- shared by
    every ModelCaller backed by that SDK's `messages.create` response shape
    (anthropic_caller.py's direct-API client and vertex_caller.py's
    Claude-via-Vertex client both return this same shape), so a future change
    to how text/usage is extracted only needs to land in one place."""
    text = "".join(block.text for block in message.content if block.type == "text")
    return ModelResponse(
        text=text,
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
    )


def select_batch_members(conn) -> list[str]:
    placeholders_d = ",".join("?" * len(BATCHABLE_DIALECTS))
    placeholders_t = ",".join("?" * len(BATCHABLE_OBJECT_TYPES))
    rows = conn.execute(
        f"""
        SELECT name FROM member
         WHERE dialect IN ({placeholders_d}) AND object_type IN ({placeholders_t})
         ORDER BY name
        """,
        (*BATCHABLE_DIALECTS, *BATCHABLE_OBJECT_TYPES),
    ).fetchall()
    return [r["name"] for r in rows]


def _output_subdir(conn, name: str) -> Path:
    """Where this member's output should nest, mirroring the only two
    source-grouping facts actually stored on `member` -- dialect (always
    present) and library (present for Natural/Mantis, null for e.g.
    DDM/FDT/JCL) -- rather than inventing a directory from anything not in
    the fact store. `resolve_member_by_name` already refuses to guess when
    a bare name collides across libraries; this mirrors that refusal into
    a distinct, clearly-labelled bucket instead of crashing or picking one
    arbitrarily, and does the same for a name that doesn't resolve at all
    (e.g. a typo in --members) so a single bad name can't abort the run."""
    from .db import resolve_member_by_name

    rows, ambiguous_libs = resolve_member_by_name(conn, name, columns="dialect, library")
    if ambiguous_libs:
        return Path("_ambiguous")
    if not rows:
        return Path("_unknown")
    row = rows[0]
    parts = [row["dialect"]]
    if row["library"]:
        parts.append(row["library"])
    return Path(*parts)


def build_prompt(brief: str, writing_rules: str, template: str, retry_note: str | None = None) -> str:
    parts = [
        "You are writing first-draft functional documentation for one legacy "
        "mainframe module. Follow the writing rules and template exactly. "
        "Never assert behaviour that cannot be traced to a specific source "
        "line in the brief below -- drop or mark `unresolved` anything that "
        "isn't. Output only the completed document (front matter + body), "
        "nothing else.",
        "# Writing rules\n\n" + writing_rules,
        "# Template\n\n" + template,
        "# Fact brief\n\n" + brief,
    ]
    if retry_note:
        parts.append(
            "# Previous attempt failed validation\n\n" + retry_note
            + "\n\nFix these problems and resend the complete document."
        )
    return "\n\n---\n\n".join(parts)


# Corrective hints keyed by a substring of a validate_doc problem -- appended
# to the generic bullet list a retry prompt already carries, for failure
# shapes seen recurring in real chunked-generation runs where the plain
# problem text alone wasn't directive enough to get a clean second attempt.
# Matched with `in`, not equality: validate_doc's exact wording for these
# varies by which specific check produced it (missing vs malformed front
# matter, for instance), but the shared substring is stable.
_RETRY_HINTS: tuple[tuple[str, str], ...] = (
    ("front matter", (
        "Your response must begin with the literal `---` front-matter delimiter "
        "as its very first characters -- no preamble, no restating these "
        "instructions, no explanation before it."
    )),
    ("does not open with a top-level", (
        "Do not narrate what you are about to do. Output only the finished "
        "document, starting with its front matter and then its top-level "
        "`# ` heading -- nothing else before either."
    )),
)


def _retry_note(problems: list[str]) -> str:
    """The generic per-problem bullet list `validate_doc` produced, plus any
    hint from `_RETRY_HINTS` whose failure shape actually occurred -- so a
    retry gets both the specific facts (what resolved wrong) and, for shapes
    a plain problem description hasn't reliably fixed on retry before, an
    explicit corrective instruction."""
    bullets = "\n".join(f"- {p}" for p in problems)
    hints = [hint for substr, hint in _RETRY_HINTS if any(substr in p for p in problems)]
    if not hints:
        return bullets
    return bullets + "\n\n" + "\n".join(hints)


@dataclass
class DocResult:
    member: str
    path: str
    ok: bool
    attempts: int
    input_tokens: int
    output_tokens: int
    problems: list[str] = field(default_factory=list)
    skipped: bool = False
    # True when `attempts` counts chunks rendered (_generate_module_doc_chunked),
    # not retry attempts on a single call -- keeps BatchSummary.retried from
    # mischarging a 3-chunk member with zero actual retries as "retried".
    chunked: bool = False
    # Populated only by _generate_module_doc_chunked: {"<chunk index>":
    # {"ok": bool, "brief_sha256": str}} for every chunk this run touched
    # (skipped or freshly rendered) -- run_batch persists this under the
    # member's own state entry so a later run can skip a chunk whose own
    # rule-range brief is unchanged, instead of the member-level resume
    # check's all-or-nothing choice between reusing every chunk or
    # re-rendering all of them.
    chunk_state: dict | None = None


def _generate_module_doc_from_brief(conn, member_name: str, brief: str, out_path: Path,
                                     caller: ModelCaller, writing_rules: str, template: str,
                                     max_attempts: int = 2) -> DocResult:
    """Call -> validate -> retry-once loop, given an already-built brief --
    the part of generate_module_doc that doesn't care whether `brief` covers
    a member's whole rule set or just one chunk of it, shared by the plain
    single-call path and _generate_module_doc_chunked's per-chunk calls
    below (mirrors testbatch.py's _generate_test_doc_from_brief)."""
    retry_note = None
    input_tokens = output_tokens = 0
    problems: list[str] = []
    attempt = 0
    for attempt in range(1, max_attempts + 1):
        prompt = build_prompt(brief, writing_rules, template, retry_note)
        response = caller(prompt)
        input_tokens += response.input_tokens
        output_tokens += response.output_tokens
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_fix_generated_by_version(response.text), encoding="utf-8")
        result = validate_doc(conn, out_path)
        if result["ok"]:
            return DocResult(member_name, str(out_path), True, attempt, input_tokens, output_tokens, [])
        problems = result["problems"]
        retry_note = _retry_note(problems)
    return DocResult(member_name, str(out_path), False, attempt, input_tokens, output_tokens, problems)


def _aggregate_chunk_confidence(chunk_paths: list[Path]) -> dict[str, int]:
    """Sum each given chunk's own (model-produced, already validated)
    confidence_summary -- never re-guessed here. Callers must pass only ok
    chunks' paths: a failed chunk's file still exists on disk (written on
    every attempt, even the last failed one) and can carry a perfectly
    parseable confidence_summary despite failing validation for an
    unrelated reason -- including it here would over-report confidence for
    rules the index doesn't actually claim as covered. A path that doesn't
    exist or doesn't parse contributes nothing either way. (Mirrors
    testbatch.py's helper of the same name -- duplicated rather than
    imported, since testbatch already depends on batch and not the other
    way around.)"""
    totals = {"verified": 0, "inferred": 0, "unresolved": 0}
    for path in chunk_paths:
        if not path.exists():
            continue
        fm, _, err = _split_frontmatter(path.read_text(encoding="utf-8"))
        if err or not isinstance(fm, dict):
            continue
        cs = fm.get("confidence_summary")
        if not isinstance(cs, dict):
            continue
        for key in totals:
            value = cs.get(key)
            if isinstance(value, int):
                totals[key] += value
    return totals


def _prune_stale_chunk_files(out_path: Path, expected_names: set[str]) -> None:
    """Delete any `{stem}.chunk<N>{suffix}` file already on disk next to
    `out_path` that isn't one of this run's expected chunk filenames --
    otherwise a rerun whose chunk width changed (crossing a power of ten in
    either direction, or the unpadded->padded migration itself) leaves
    stale files behind indefinitely (e.g. a legacy unpadded `chunk1.md`
    alongside the new `chunk01.md`), which confuses doc-site nav and wastes
    space. Scoped to the chunk-file naming pattern specifically so this
    never touches an unrelated file that happens to share the member's
    stem (mirrored verbatim in testbatch.py's chunked path)."""
    if not out_path.parent.is_dir():
        return
    pattern = re.compile(rf"^{re.escape(out_path.stem)}\.chunk\d+{re.escape(out_path.suffix)}$")
    for candidate in out_path.parent.iterdir():
        if not candidate.is_file() or candidate.name in expected_names:
            continue
        if not pattern.match(candidate.name):
            continue
        try:
            candidate.unlink()
        except OSError:
            # Best-effort: a stale file some other process is holding open
            # (or that vanished between the iterdir() listing and this
            # unlink) must not abort the whole batch run over cosmetic
            # cleanup -- the new, correctly-named chunk still gets written
            # either way.
            pass


# The five module.md sections that genuinely benefit from a single
# whole-module statement rather than N chunks each describing their own
# slice -- see _generate_module_index_narrative. Order matters: it's both
# the order these are rendered in the assembled index document and the
# order a reconciliation response is expected to answer in.
NARRATIVE_SECTIONS: tuple[str, ...] = (
    "Purpose", "How it is invoked", "Inputs", "Data used", "Outputs and effects",
)

_SECTION_HEADING_RE_CACHE: dict[str, re.Pattern] = {}


def _extract_section(body: str, heading: str) -> str | None:
    """The text of the first `## {heading}` section in `body` (stripped),
    or None if that heading is absent or its section is blank. Reused both
    to pull an already-validated chunk's own narrative sections (feeding
    _generate_module_index_narrative's prompt) and to parse a reconciliation
    response back into per-heading text (_split_reconciled_sections) --
    the same "find a named `## ` section" operation either way.

    Fence-aware: scans line by line, tracking whether each line is inside a
    ``` fenced code block, so a line starting with `##` *inside* a fence
    (e.g. a Mermaid diagram's own comment syntax, or an example markdown
    snippet quoted in a section's own prose) is never mistaken for the next
    section's heading -- the same failure mode validate.py's _logical_units
    already guards its own line scan against. A regex-only approach (a
    single `re.search` over the whole body) can't make that distinction and
    would silently truncate a section at the first such line."""
    heading_re = _SECTION_HEADING_RE_CACHE.get(heading)
    if heading_re is None:
        heading_re = re.compile(rf"^##\s+{re.escape(heading)}\s*$")
        _SECTION_HEADING_RE_CACHE[heading] = heading_re
    in_fence = False
    collecting = False
    collected: list[str] = []
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            if collecting:
                collected.append(line)
            continue
        if not in_fence and re.match(r"^##\s", line):
            if collecting:
                break  # a real (fence-external) heading ends this section
            collecting = heading_re.match(line) is not None
            continue
        if collecting:
            collected.append(line)
    if not collecting:
        return None
    text = "\n".join(collected).strip("\n").strip()
    return text or None


def _chunk_processing_labels(rule_rows: list, routines: list[dict],
                              ranges: list[tuple[int, int]]) -> list[str]:
    """One human-scannable label per entry in `ranges` (same order), naming
    the routine(s) (first-seen order, backtick-quoted) whose rules fall in
    that range -- purely a lookup against facts brief.py already recorded
    (fetch_routines/routine_for_line), never a paraphrase or guess at what a
    routine "does". Falls back to a plain "no internal routine" note for a
    range whose rules are all in the member's main body -- the same case
    routine_for_line itself represents as None."""
    labels = []
    for start, end in ranges:
        names: list[str] = []
        for row in rule_rows[start - 1:end]:
            r = routine_for_line(routines, row["line_no"])
            name = r["name"] if r else None
            if name is not None and name not in names:
                names.append(name)
        if names:
            labels.append(", ".join(f"`{n}`" for n in names))
        else:
            labels.append("member main body (no internal routine)")
    return labels


def _consolidated_gap_lines(conn, member_name: str, member_id: int,
                             ok_chunk_paths: list[Path]) -> list[str]:
    """Every `gap` table row recorded for this member (same query and
    ordering module_brief's own "Known gaps for this module" section
    already runs -- both order by `db.GAP_SEVERITY_ORDER_SQL`, a real
    high/medium/low priority rather than `severity`'s own TEXT lexicographic
    order, and share that one constant so the two can't drift apart again --
    including that section's own citation, so a gap's free-text `detail`
    that happens to read as an assertive claim doesn't trip validate_doc's
    uncited-assertion check here any more than it does there), plus every ok
    chunk's own `sme_questions` front-matter entries -- both are
    already-recorded facts (a gap row from `derive`, an sme_questions string
    a chunk's own generation already validated), just scattered one-per-chunk
    today. Gap rows come first, in real severity-priority order, since
    they're the ground-truth record; sme_questions only add text not already
    covered by a gap row's own detail.

    Two different dedup keys, deliberately not "exact text" for both: gap
    *rows* dedupe against each other on `(gap_kind, line_no, first
    sentence)` (see `seen_gap_identity` below -- several distinct gap rows
    commonly share identical `detail` text, so text alone would silently
    collapse them); `sme_questions` dedupe on exact text, against both other
    questions and *every* sentence of each gap row's own detail
    (`seen_content` -- not just the first sentence, so an sme_question that
    duplicates a gap's second-or-later sentence, or its full multi-sentence
    detail, still gets recognised as already covered)."""
    # Two dedup keys, deliberately different: `seen_gap_identity` dedupes
    # gap *rows* against each other -- (gap_kind, line_no, first_sentence),
    # not first_sentence alone, since several distinct gap rows commonly
    # share identical detail text (e.g. every unparsed_line gap for a member
    # reads "Statement not recognised by the Natural scanner in {member}."
    # regardless of which line it's on -- dedup on text alone would collapse
    # every one of them down to a single line and silently drop the rest).
    # `seen_content` dedupes only the substantive text -- a gap row's own
    # first sentence, or an sme_question's raw text -- so a chunk's
    # sme_question that just restates a gap row's own finding (or another
    # chunk's identical question) is recognised as the same content even
    # though a gap row and an sme_question render differently; it is never
    # used to drop a *gap row* itself.
    seen_gap_identity: set[tuple] = set()
    seen_content: set[str] = set()
    lines: list[str] = []
    for r in conn.execute(
        f"SELECT gap_kind, detail, severity, line_no FROM gap WHERE member_id=? "
        f"ORDER BY {GAP_SEVERITY_ORDER_SQL}, line_no",
        (member_id,),
    ).fetchall():
        # A gap's `detail` is free text meant for a fact brief (module_brief's
        # own "Known gaps" section pastes it verbatim the same way) -- fine
        # there, since a brief is never itself validated. Pasted whole into a
        # document validate_doc *does* check, and a `detail` with more than
        # one sentence risks its second sentence (typically elaboration/an
        # instruction to confirm, not the citable fact itself) reading as an
        # uncited assertive claim on its own, since validate_doc checks each
        # sentence independently. Rather than dropping every sentence after
        # the first (losing real context a reviewer may need), repeat the
        # same citation before every sentence -- a citation immediately
        # preceding a claim satisfies validate_doc's check for that claim
        # (see CITATION.search(u) in _uncited_assertions), so this keeps the
        # full detail while citing each sentence in it individually.
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", r["detail"].strip()) if s.strip()]
        first_sentence = sentences[0] if sentences else r["detail"].strip()
        identity = (r["gap_kind"], r["line_no"], first_sentence)
        if identity in seen_gap_identity:
            continue
        seen_gap_identity.add(identity)
        seen_content.update(sentences or [first_sentence])
        loc = _cite(member_name, r["line_no"])
        detail_text = " ".join(f"{loc} {s}" for s in sentences) if sentences else f"{loc} {r['detail'].strip()}"
        lines.append(f"[{r['severity']}] {r['gap_kind']}: {detail_text}")
    for path in ok_chunk_paths:
        if not path.exists():
            continue
        fm, _, err = _split_frontmatter(path.read_text(encoding="utf-8"))
        if err or not isinstance(fm, dict):
            continue
        for q in fm.get("sme_questions") or []:
            if not isinstance(q, str):
                continue
            q = q.strip()
            if q and q not in seen_content:
                seen_content.add(q)
                lines.append(q)
    return lines


def build_reconciliation_prompt(member_name: str, chunk_sources: list[str], writing_rules: str,
                                 index_template: str | None, retry_note: str | None = None) -> str:
    """Prompt for the one bounded model call `_generate_module_index_narrative`
    makes per chunked member: reconcile N already-validated, already-cited
    excerpts (one per ok chunk, each already-cited text from that chunk's
    own Purpose/How-invoked/Inputs/Data-used/Outputs-and-effects sections --
    see _extract_section/NARRATIVE_SECTIONS) into one whole-module
    statement per section. Deliberately never includes source lines or a
    module_brief() -- reconciling, not re-deriving, is the entire point;
    see the design spec (docs/superpowers/specs/2026-09-06-chunked-module-
    index-overview-design.md) for why that keeps this safe from the same
    silent-truncation risk chunking itself exists to guard against."""
    parts = [
        f"You are reconciling several already-validated, already-cited excerpts of "
        f"the SAME legacy mainframe module, `{member_name}` -- one excerpt per chunk "
        "this module's business-rule set was split into for documentation purposes -- "
        "into one coherent whole-module statement. Do not invent any claim, fact, or "
        "citation that is not already present, in substance, in the excerpts "
        "below; every sentence you write must carry a citation copied from one "
        "of them. Where excerpts genuinely conflict, prefer the more specific or "
        "more heavily-cited statement and note the discrepancy as an "
        "`(unresolved)` item rather than silently picking one.\n\n"
        "Output exactly five sections, in this exact order, headed exactly as "
        "shown, and nothing else -- no preamble, no restating these "
        "instructions:\n\n" + "\n".join(f"## {h}" for h in NARRATIVE_SECTIONS),
        "# Writing rules\n\n" + writing_rules,
    ]
    if index_template:
        parts.append("# Module-index template (for section-content expectations)\n\n" + index_template)
    parts.append("# Per-chunk excerpts to reconcile\n\n" + "\n\n---\n\n".join(chunk_sources))
    if retry_note:
        parts.append(
            "# Previous attempt failed validation\n\n" + retry_note
            + "\n\nFix these problems and resend all five sections, in order."
        )
    return "\n\n---\n\n".join(parts)


def _reconciliation_source(chunk_index: int, chunk_body: str) -> str:
    """One chunk's contribution to the reconciliation prompt: its own
    already-validated NARRATIVE_SECTIONS text, labelled by chunk number so
    the model can attribute/compare across chunks. A chunk with none of
    the five sections present (shouldn't happen for a document that passed
    validate_doc against templates/module.md's contract, but nothing here
    depends on that) contributes an empty-but-labelled block rather than
    being silently skipped."""
    parts = [f"### Chunk {chunk_index}"]
    for heading in NARRATIVE_SECTIONS:
        section = _extract_section(chunk_body, heading)
        if section:
            parts.append(f"**{heading}**\n\n{section}")
    return "\n\n".join(parts)


def _split_reconciled_sections(text: str) -> tuple[dict[str, str], list[str]]:
    """A reconciliation response, split into {heading: text} for whichever
    of NARRATIVE_SECTIONS it actually included, plus the list of headings
    it omitted -- a model dropping a required section outright still
    produces a document with nothing said for that heading, which
    validate_doc's citation/uncited-assertion checks alone wouldn't
    reliably catch (an empty section asserts nothing), so this is checked
    explicitly by _generate_module_index_narrative before deciding whether
    to accept the response."""
    sections: dict[str, str] = {}
    missing: list[str] = []
    for heading in NARRATIVE_SECTIONS:
        value = _extract_section(text, heading)
        if value is None:
            missing.append(heading)
        else:
            sections[heading] = value
    return sections, missing


_NARRATIVE_SKIPPED_NOTE = (
    "Narrative synthesis was skipped for this section because not every chunk "
    "of this member validated ok -- see the Chunk files section below and this "
    "member's gap register."
)
_NARRATIVE_FAILED_NOTE = (
    "Narrative synthesis did not produce a section that passed validation here "
    "after retrying -- see the gap register and this document's own generation "
    "problems for detail."
)


def _citations_in(text: str) -> set[str]:
    """Every `[[MEMBER:LINE]]`-shaped citation literally present in `text`,
    upper-cased whole so the same citation written with different member-name
    casing by the model still compares equal to how it appeared in a chunk
    excerpt (line numbers are untouched by .upper())."""
    return {m.group(0).upper() for m in CITATION.finditer(text)}


def _uncited_provenance_problems(sections: dict[str, str], allowed_citations: set[str]) -> list[str]:
    """A citation appearing in a reconciled section but never present in any
    of the chunk excerpts the reconciliation call was given is exactly the
    failure mode the design spec's safety argument depends on not
    happening: `validate_doc` alone only proves a citation *resolves* to a
    real source line, not that it was actually copied forward rather than
    invented fresh by the model for a broadened whole-module claim. Checked
    deterministically here, against `allowed_citations` (every citation
    already present, verbatim, in the excerpts given -- see
    _generate_module_index_narrative), so this can never pass by construction
    the way relying on validate_doc's citation-resolution check alone
    would."""
    problems = []
    for heading, text in sections.items():
        extra = _citations_in(text) - allowed_citations
        for cite in sorted(extra):
            problems.append(
                f"narrative synthesis introduced citation {cite} in '## {heading}' that "
                "was not present in any given chunk excerpt -- reconciliation must only "
                "reuse citations already given, never invent or copy in a new one"
            )
    return problems


def _generate_module_index_narrative(conn, member_name: str, chunk_bodies: list[tuple[int, str]],
                                      caller: ModelCaller, writing_rules: str,
                                      index_template: str | None, out_path: Path,
                                      assemble, max_attempts: int = 2,
                                      ) -> tuple[bool, int, int, int, list[str], dict[str, str] | None]:
    """The one bounded model call per chunked member: reconcile every ok
    chunk's own Purpose/How-invoked/Inputs/Data-used/Outputs-and-effects
    sections into a single whole-module statement per section, then splice
    the result into the full index document via `assemble` (a callable
    taking {heading: text} for NARRATIVE_SECTIONS and returning the full
    candidate document text) and validate the *whole* assembled document --
    same call -> write -> validate -> retry-once shape as
    _generate_module_doc_from_brief, so a bad reconciliation retries and
    reports exactly like a bad chunk does, rather than silently degrading
    the index.

    Only ever called after every chunk is ok (see _generate_module_doc_chunked)
    and only ever given already-validated chunk text -- never raw source,
    never module_brief() -- so this cannot reintroduce the silent-truncation
    risk chunking itself exists to guard against. That alone isn't a
    guarantee the model actually reused what it was given rather than
    inventing a new (but still resolvable) citation for a broadened claim --
    `_uncited_provenance_problems` checks that deterministically, on top of
    `validate_doc`'s own checks, rather than resting on the prompt's own
    instructions. See the design spec for the full reasoning.

    Returns `(ok, attempts, input_tokens, output_tokens, problems, sections)`
    -- `sections` is the accepted {heading: text} mapping when ok, or None
    on failure (the last, rejected attempt is not a fact worth caching)."""
    sources = [_reconciliation_source(i, body) for i, body in chunk_bodies]
    allowed_citations: set[str] = set()
    for body in sources:
        allowed_citations |= _citations_in(body)
    retry_note: str | None = None
    input_tokens = output_tokens = 0
    problems: list[str] = []
    attempt = 0
    for attempt in range(1, max_attempts + 1):
        prompt = build_reconciliation_prompt(member_name, sources, writing_rules, index_template, retry_note)
        response = caller(prompt)
        input_tokens += response.input_tokens
        output_tokens += response.output_tokens

        found, missing = _split_reconciled_sections(response.text)
        sections = {
            h: found.get(h, "*(narrative synthesis did not return this section)*")
            for h in NARRATIVE_SECTIONS
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(assemble(sections), encoding="utf-8")
        result = validate_doc(conn, out_path)
        problems = list(result["problems"]) + _uncited_provenance_problems(found, allowed_citations)
        if missing:
            problems = [
                f"narrative synthesis response missing required section(s): {', '.join(missing)}"
            ] + problems
        if not problems:
            return True, attempt, input_tokens, output_tokens, [], sections

        # `problems` (not just result["problems"]) -- a rejected attempt
        # whose only failure is a provenance violation (an invented-but-
        # resolvable citation `validate_doc` alone can't see) must still
        # tell the model what to fix, or an empty retry_note repeats the
        # exact same invalid citation next attempt.
        note = _retry_note(problems) if problems else ""
        if missing:
            note = (
                "Your response must include all five required sections, each headed "
                "exactly `## <heading>` (e.g. `## Purpose`), in this order: "
                + ", ".join(NARRATIVE_SECTIONS)
                + f". Missing: {', '.join(missing)}.\n\n" + note
            ).strip()
        retry_note = note

    # Every attempt failed -- leave a plainly-worded, clearly-labelled note
    # on disk instead of the last rejected attempt's raw (possibly
    # malformed, possibly citation-invented) prose. DocResult.problems
    # already carries the detail; this is what a reader sees in the
    # document itself.
    failed_sections = {h: _NARRATIVE_FAILED_NOTE for h in NARRATIVE_SECTIONS}
    out_path.write_text(assemble(failed_sections), encoding="utf-8")
    return False, attempt, input_tokens, output_tokens, problems, None


def _render_module_index_doc(member_name: str, system: str | None,
                              chunk_entries: list[tuple[int, tuple[int, int], Path, DocResult]],
                              confidence: dict[str, int], routine_labels: list[str],
                              gap_lines: list[str], sections: dict[str, str]) -> str:
    """The whole-module overview document at a chunked member's normal
    out_path -- doc_type: module_index (see templates/module-index.md),
    distinct from a chunk's own doc_type: module. Deterministic parts
    (Business rules ranges, Processing-sequence skeleton, Chunk files,
    consolidated Gaps) are built here, the same way _render_module_chunk_index
    always has, from already-recorded/already-validated facts only -- see
    that function's former docstring (still true) for why: chunking guards
    against a model response silently losing content, so the summary of what
    each chunk covered must never itself be asked of a model that could get
    it wrong the same way.

    `sections` supplies the five NARRATIVE_SECTIONS -- either a genuinely
    reconciled whole-module statement per section (_generate_module_index_narrative)
    or a plain skipped/failed placeholder (_generate_module_doc_chunked, when
    not every chunk is ok, or when reconciliation itself didn't validate) --
    this function only ever splices whatever it's given; it never decides
    which case applies."""
    today = datetime.date.today().isoformat()

    lines_chunks = []
    br_lines = []
    seq_lines = []
    for (index, (start, end), path, result), label in zip(chunk_entries, routine_labels, strict=True):
        status = "OK" if result.ok else "FAILED: " + "; ".join(result.problems)[:200]
        lines_chunks.append(
            f"- [{path.name}](./{path.name}) -- rules {start}-{end} -- {status}"
        )
        # _rule_id keeps this in lockstep with how every other BR-nnn id in
        # the tool is formatted/numbered (citations.py) -- `start`/`end` are
        # already the same 1-based rule ordinals module_brief numbers a
        # chunk's own BR ids from (routine_aware_chunk_ranges), so these
        # range endpoints are real ids, not just look-alike text.
        br_lines.append(f"- `{_rule_id(member_name, start)}`..`{_rule_id(member_name, end)}` -- {label}")
        seq_lines.append(
            f"- chunk {index} ([{path.name}](./{path.name})): "
            f"`BR-{start:03d}`..`BR-{end:03d}` -- {label}"
        )

    fm = "\n".join([
        "---",
        f'title: "{member_name} — module documentation, chunked"',
        "doc_type: module_index",
        f'system: "{system or "unknown"}"',
        f'module: "{member_name}"',
        f"generated_by: legacy-functional-docs {__version__}",
        f'generated_at: "{today}"',
        "review_status: draft",
        "reviewers: []",
        "confidence_summary:",
        f"  verified: {confidence['verified']}",
        f"  inferred: {confidence['inferred']}",
        f"  unresolved: {confidence['unresolved']}",
        f'sources: ["{member_name}"]',
        "sme_questions: []",
        "---",
    ])
    body_parts = [
        "",
        f"# {member_name} — module documentation, chunked",
        "",
        f"This member's business-rule set was rendered as {len(chunk_entries)} separate "
        "documents rather than one -- a single completion this large risks silently "
        "truncating before it covers every rule. This document is a whole-module "
        "overview for a reader who wants a single starting point; per-rule detail "
        "still lives in the chunk files below.",
        "",
    ]
    for heading in NARRATIVE_SECTIONS[:4]:  # Purpose, How it is invoked, Inputs, Data used
        body_parts += [f"## {heading}", "", sections[heading], ""]
    body_parts += [
        "## Business rules",
        "",
        "Ranges over this member's full `BR-nnn` set, grouped by chunk/routine -- the "
        "per-rule detail for each range lives in the chunk file named in Chunk files below.",
        "",
        *br_lines,
        "",
        "## Processing sequence",
        "",
        "One entry per chunk, in execution order -- each chunk's own document narrates "
        "the detailed sequence for its range.",
        "",
        *seq_lines,
        "",
        f"## {NARRATIVE_SECTIONS[4]}",  # Outputs and effects
        "",
        sections[NARRATIVE_SECTIONS[4]],
        "",
        "## Gaps and questions for review",
        "",
        *([f"- {g}" for g in gap_lines] if gap_lines else ["- No gaps recorded for this module."]),
        "",
        "## Chunk files",
        "",
        *lines_chunks,
        "",
    ]
    return fm + "\n".join(body_parts)


def _generate_module_doc_chunked(conn, member_name: str, system: str | None, rule_rows: list,
                                  out_path: Path, caller: ModelCaller, writing_rules: str,
                                  template: str, redact: Redactor, lexicon: dict[str, str] | None,
                                  max_attempts: int, chunk_size: int,
                                  prior_chunks: dict | None = None,
                                  index_template: str | None = None) -> DocResult:
    """Render one member as several independent chunk documents plus a
    deterministic index doc at `out_path`, instead of asking one completion
    to cover the member's whole rule set. Each chunk goes through the exact
    same call/validate/retry path (_generate_module_doc_from_brief) a
    normal single-call member does, scoped to a routine-aware slice via
    module_brief's `rule_range` (see routine_aware_chunk_ranges) -- so one
    bad chunk retries and reports on its own, rather than forcing a
    full-member re-generation, and (short of a single oversized routine)
    no chunk's prompt asks for much more than a normal small member's.

    `prior_chunks` (from a previous run's state, keyed by chunk index as a
    string) lets a chunk whose own rule-range brief is unchanged from that
    prior run -- and whose file on disk still validates -- skip the model
    call entirely and reuse the existing file, rather than the member-level
    resume check's only choice: reuse every chunk (if the whole member's
    combined brief hash is unchanged) or re-render all of them. This is
    what makes a fix affecting only one routine's worth of source cheap to
    pick up: only the chunk(s) whose own brief actually changed re-render."""
    routines = fetch_routines(conn, rule_rows[0]["member_id"])
    ranges = routine_aware_chunk_ranges(
        [r["line_no"] for r in rule_rows], routines, chunk_size,
    )
    chunk_count = len(ranges)
    # Routine name (upper) -> 1-based chunk index whose rule range contains
    # that routine's own rules -- known in full before any chunk is
    # narrated, since `ranges` is already fixed above. Handed to every
    # chunk's own brief (see module_brief's `chunk_map` param) so a chunk
    # whose own rules dispatch to a routine documented elsewhere can name
    # the specific chunk instead of leaving a dangling "covered elsewhere".
    #
    # `ranges` is in rule-ordinal space (1-based position within `rule_rows`,
    # not raw source line numbers -- see routine_aware_chunk_ranges), so a
    # routine's chunk is found by scanning `rule_rows` directly (not via a
    # line_no-keyed dict: `rule_candidate.line_no` is not guaranteed unique
    # per member, and a dict built that way silently collapses same-line
    # rows to whichever is last, discarding the rest) for the first row
    # whose line falls inside the routine's own span, using that row's
    # position (its index in `rule_rows`, 1-based) as the ordinal.
    # routine_aware_chunk_ranges already keeps a routine's rules as one
    # contiguous, unsplit run, so any one of its rows' ordinals lands in the
    # same chunk as every other rule belonging to that routine. A routine
    # with no rule_candidate rows of its own (nothing to key off) is simply
    # left out of the map.
    chunk_map: dict[str, int] = {}
    for routine in routines:
        end_line = routine["end_line"] if routine["end_line"] is not None else routine["start_line"]
        ordinal = next(
            (pos for pos, r in enumerate(rule_rows, start=1)
             if routine["start_line"] <= r["line_no"] <= end_line),
            None,
        )
        if ordinal is None:
            continue
        for idx, (start, end) in enumerate(ranges, start=1):
            if start <= ordinal <= end:
                chunk_map[routine["name"].upper()] = idx
                break
    input_tokens = output_tokens = 0
    chunk_entries: list[tuple[int, tuple[int, int], Path, DocResult]] = []
    problems: list[str] = []
    chunk_state: dict[str, dict] = {}

    chunk_width = len(str(chunk_count))
    expected_chunk_names = {
        f"{out_path.stem}.chunk{n:0{chunk_width}d}{out_path.suffix}" for n in range(1, chunk_count + 1)
    }
    _prune_stale_chunk_files(out_path, expected_chunk_names)
    for i, (start, end) in enumerate(ranges, start=1):
        chunk_path = out_path.with_name(f"{out_path.stem}.chunk{i:0{chunk_width}d}{out_path.suffix}")
        brief = module_brief(
            conn, member_name, redact=redact, lexicon=lexicon,
            rule_range=(start, end), chunk_info=(i, chunk_count), chunk_map=chunk_map,
        )
        brief_hash = hashlib.sha256(brief.encode("utf-8")).hexdigest()
        prior_chunk = (prior_chunks or {}).get(str(i))
        reusable = (
            isinstance(prior_chunk, dict) and prior_chunk.get("brief_sha256") == brief_hash
            and chunk_path.exists()
        )
        if reusable:
            # Re-validate rather than trust the stored "ok" flag verbatim --
            # the *content* is cached, but validate_doc's own logic can have
            # changed since it was last checked, and this costs no model call.
            revalidated = validate_doc(conn, chunk_path)
            result = DocResult(
                member_name, str(chunk_path), revalidated["ok"], 0, 0, 0,
                revalidated["problems"],
            )
        else:
            # A caller exception here (transient network error, rate limit,
            # timeout, ...) must isolate to this one chunk, not propagate out
            # of this whole function and discard every already-completed
            # chunk_state entry built up in this same pass -- see issue #78's
            # review discussion. Recorded as a failed DocResult (ok=False,
            # attempts=1 -- one call was actually attempted and failed,
            # matching run_batch's own accounting for a failed initial call)
            # so the member overall reports not-ok and this chunk re-renders
            # on the next run (chunk_path was never written, so the
            # `reusable` check above can't mistake it for done), while every
            # other chunk's real work from this pass is preserved.
            try:
                result = _generate_module_doc_from_brief(
                    conn, member_name, brief, chunk_path, caller, writing_rules, template,
                    max_attempts=max_attempts,
                )
            except Exception as exc:
                result = DocResult(
                    member_name, str(chunk_path), False, 1, 0, 0,
                    [f"model call failed: {exc!r}"],
                )
        input_tokens += result.input_tokens
        output_tokens += result.output_tokens
        chunk_entries.append((i, (start, end), chunk_path, result))
        chunk_state[str(i)] = {"ok": result.ok, "brief_sha256": brief_hash}
        if not result.ok:
            problems.append(f"chunk {i}/{chunk_count} ({chunk_path.name}) failed: " + "; ".join(result.problems))

    confidence = _aggregate_chunk_confidence([p for _, _, p, r in chunk_entries if r.ok])
    routine_labels = _chunk_processing_labels(rule_rows, routines, ranges)
    ok_chunk_paths = [p for _, _, p, r in chunk_entries if r.ok]
    gap_lines = _consolidated_gap_lines(conn, member_name, rule_rows[0]["member_id"], ok_chunk_paths)

    def assemble(sections: dict[str, str]) -> str:
        return _render_module_index_doc(
            member_name, system, chunk_entries, confidence, routine_labels, gap_lines, sections,
        )

    all_chunks_ok = all(r.ok for _, _, _, r in chunk_entries)
    if all_chunks_ok:
        # The reconciliation call's only input is every ok chunk's own
        # already-validated narrative sections -- never source, never
        # module_brief() -- see _generate_module_index_narrative's docstring
        # and the design spec for why that keeps this from reintroducing the
        # silent-truncation risk chunking exists to guard against.
        chunk_bodies = [
            (index, path.read_text(encoding="utf-8"))
            for index, _, path, result in chunk_entries if result.ok
        ]
        # Same resumability idea as per-chunk reuse above, one level up: a
        # prior run's reconciled sections are reused (no model call) when
        # every ok chunk's own body -- and the writing-rules/index-template
        # text the reconciliation prompt is built from -- is byte-identical
        # to what produced them last time. Stored under chunk_state's
        # "_narrative" key (a chunk index is never this string, so it can't
        # collide with a real chunk's own entry) alongside the per-chunk
        # entries, so it rides along in run_batch's existing state file with
        # no separate plumbing. The accepted `sections` themselves are cached
        # verbatim (not re-derived from the rendered document): re-parsing
        # `## ` headings back out of the assembled index risks truncating a
        # section early if its own reconciled prose happens to contain a
        # fenced code block with a `##`-prefixed line inside it.
        narrative_input_hash = hashlib.sha256(
            "\x00".join(f"{index}:{body}" for index, body in chunk_bodies).encode("utf-8")
            + b"\x00" + writing_rules.encode("utf-8")
            + b"\x00" + (index_template or "").encode("utf-8")
        ).hexdigest()
        prior_narrative = (prior_chunks or {}).get("_narrative") if isinstance(prior_chunks, dict) else None
        reused = False
        if (
            isinstance(prior_narrative, dict) and prior_narrative.get("ok")
            and prior_narrative.get("input_sha256") == narrative_input_hash
            and isinstance(prior_narrative.get("sections"), dict)
            and set(prior_narrative["sections"]) == set(NARRATIVE_SECTIONS)
        ):
            prior_sections = prior_narrative["sections"]
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(assemble(prior_sections), encoding="utf-8")
            revalidated = validate_doc(conn, out_path)
            if revalidated["ok"]:
                narrative_ok, narrative_problems, n_in, n_out, sections = True, [], 0, 0, prior_sections
                reused = True
        if not reused:
            narrative_ok, _, n_in, n_out, narrative_problems, sections = _generate_module_index_narrative(
                conn, member_name, chunk_bodies, caller, writing_rules, index_template, out_path,
                assemble, max_attempts=max_attempts,
            )
        input_tokens += n_in
        output_tokens += n_out
        chunk_state["_narrative"] = {
            "ok": narrative_ok, "input_sha256": narrative_input_hash,
            "sections": sections if narrative_ok else None,
        }
        if not narrative_ok:
            problems.append("narrative synthesis: " + "; ".join(narrative_problems))
    else:
        sections = {h: _NARRATIVE_SKIPPED_NOTE for h in NARRATIVE_SECTIONS}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(assemble(sections), encoding="utf-8")
        problems.append(
            "narrative synthesis: skipped -- not every chunk validated ok"
        )

    # The index is built deterministically (short of the narrative call
    # handled above, which validates itself before being accepted), but
    # that's no reason to skip a final check here too -- validate_doc is the
    # same ground truth every chunk (and every other generated doc in this
    # tool) is judged against, and a bug in the deterministic assembly
    # deserves the same loud, reported failure a bad model response gets,
    # not a silent ok=True because no chunk (or the narrative call) happened
    # to fail.
    index_validation = validate_doc(conn, out_path)
    if not index_validation["ok"]:
        problems = problems + [f"index document: {p}" for p in index_validation["problems"]]

    return DocResult(
        member_name, str(out_path), not problems, chunk_count, input_tokens, output_tokens,
        problems, chunked=True, chunk_state=chunk_state,
    )


def generate_module_doc(conn, member_name: str, out_path: Path, caller: ModelCaller,
                         writing_rules: str, template: str, redact: Redactor = NULL_REDACTOR,
                         max_attempts: int = 2, lexicon: dict[str, str] | None = None,
                         max_rules_per_call: int | None = None,
                         prior_chunks: dict | None = None,
                         index_template: str | None = None) -> DocResult:
    """Single-member version of the harness: brief -> call -> validate ->
    retry once. Used directly for one-off generation and by run_batch's
    per-item work (with the model call itself dispatched to a thread pool
    by the caller).

    A member whose own rule_candidate count exceeds `max_rules_per_call`
    (default DEFAULT_MAX_RULES_PER_CALL) renders as several independent
    chunk documents instead -- see _generate_module_doc_chunked. The
    ambiguous-name and no-rule-candidate cases fall through to the
    original single-call path unchanged (module_brief already reports both
    as prose in the brief itself)."""
    rows, ambiguous_libs = fetch_rule_candidate_rows(conn, member_name)
    threshold = _resolve_max_rules_per_call(max_rules_per_call)
    if not ambiguous_libs and rows and len(rows) > threshold:
        system = conn.execute(
            "SELECT system FROM member WHERE UPPER(name)=UPPER(?)", (member_name,)
        ).fetchone()
        return _generate_module_doc_chunked(
            conn, member_name, system["system"] if system else None, rows, out_path, caller,
            writing_rules, template, redact, lexicon, max_attempts, threshold,
            prior_chunks=prior_chunks, index_template=index_template,
        )

    brief = module_brief(conn, member_name, redact=redact, lexicon=lexicon)
    return _generate_module_doc_from_brief(
        conn, member_name, brief, out_path, caller, writing_rules, template, max_attempts=max_attempts,
    )


@dataclass
class BatchSummary:
    results: list[DocResult]
    total_input_tokens: int
    total_output_tokens: int
    cost_usd: float | None
    retried: int
    ok: int
    failed: int
    skipped: int


def _corpus_signature(conn, redact: Redactor = NULL_REDACTOR,
                       lexicon: dict[str, str] | None = None,
                       extra: list[str] | None = None) -> str:
    """Fingerprint of every input to module_brief() that isn't the derive
    code itself: every source_file's (path, sha256) (order-independent),
    the installed mfdoc version, and the effective redact/lexicon policy.

    A per-source_file-only check isn't safe on its own even for the source
    dimension: module_brief() also pulls in facts owned by other members
    (inbound callers, copycode-inherited rules), so a member's brief can
    change even when its own file didn't -- hashing the whole corpus at
    once is what makes it safe. redact/lexicon matter too: both come from
    project.yml, not from anything a source_file hash can see, so a policy
    change with no source edits must still be able to invalidate this.

    Folding in `__version__` catches a code upgrade (most commonly a
    dialect-scanner or derive bugfix) picked up via a fresh `pip install`.
    It does NOT catch derive/extraction code edited in place without a
    version bump (e.g. mid-development, before a release) -- that residual
    case still needs `--state` (or the affected member's entry in it)
    cleared by hand after re-deriving.

    `extra` lets a caller with additional non-source inputs to its own
    brief (testbatch.py's language/framework/test_case status) fold them
    into the same signature rather than reimplementing this function --
    order matters and is the caller's to keep stable across runs.
    """
    rows = conn.execute("SELECT path, sha256 FROM source_file ORDER BY path").fetchall()
    digest = hashlib.sha256()
    digest.update(__version__.encode("utf-8"))
    digest.update(b"\x00")
    for r in rows:
        digest.update(r["path"].encode("utf-8"))
        digest.update(b"\x00")
        digest.update(r["sha256"].encode("utf-8"))
        digest.update(b"\x00")
    digest.update(redact.signature().encode("utf-8"))
    digest.update(b"\x00")
    for key in sorted(lexicon or {}):
        digest.update(key.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(lexicon[key].encode("utf-8"))
        digest.update(b"\x00")
    for term in extra or []:
        digest.update(term.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _load_state(state_path: Path) -> dict:
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {}


def _save_state(state_path: Path, state: dict) -> None:
    """Write `state` to `state_path` atomically: a direct write_text leaves
    a window where a process killed mid-write drops a truncated/invalid
    JSON file in place of the last good checkpoint -- a real risk now that
    this is called after every member/chunk, not just once at the end of a
    run (see issue #78 review). Writing to a temp file in the same
    directory first and os.replace()-ing it over the real path means every
    on-disk state file is either the previous checkpoint or the new one in
    full, never a partial write -- os.replace is atomic on both POSIX and
    Windows, unlike a plain os.rename on Windows when the destination
    exists."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=state_path.parent, prefix=f".{state_path.name}.", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(state, indent=2))
        os.replace(tmp_name, state_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def estimate_cost(
    input_tokens: int, output_tokens: int,
    cost_per_mtok_in: float | None, cost_per_mtok_out: float | None,
) -> float | None:
    """Dollar cost for `input_tokens`/`output_tokens` at the given
    per-million-token rates, or None if pricing isn't configured (either
    rate missing) -- the shared "unknown" sentinel every caller printing
    a cost line checks for (see run_batch below and cli.py's
    cmd_classify_rules/cmd_batch), so the formula and its unknown-pricing
    behavior live in exactly one place instead of being retyped at each
    call site."""
    if cost_per_mtok_in is None or cost_per_mtok_out is None:
        return None
    return (input_tokens / 1_000_000) * cost_per_mtok_in + (output_tokens / 1_000_000) * cost_per_mtok_out


def _skip_result(name: str, out_path: Path, prior: dict) -> DocResult:
    """A prior successful run's result, reused as-is without regenerating anything."""
    return DocResult(name, str(out_path), True, prior.get("attempts", 1), 0, 0, [], skipped=True)


def run_batch(conn, members: list[str], out_dir: Path, caller: ModelCaller,
              writing_rules: str, template: str, redact: Redactor = NULL_REDACTOR,
              concurrency: int = 4, state_path: Path | None = None,
              cost_per_mtok_in: float | None = None, cost_per_mtok_out: float | None = None,
              lexicon: dict[str, str] | None = None,
              max_rules_per_call: int | None = None,
              index_template: str | None = None,
              ) -> BatchSummary:
    """Run the harness over `members`, resumable via `state_path`.

    Output nests as `out_dir/<dialect>/<library>/<member>.md` (the library
    segment omitted when the member has none, e.g. DDM/FDT-only dialects) --
    mirroring the only two source-grouping facts actually stored on
    `member`, via `_output_subdir`, rather than a flat `out_dir/<member>.md`.

    A member whose own rule_candidate count exceeds `max_rules_per_call`
    renders as several independent chunk documents plus a deterministic
    index doc at its normal `out_path` -- see generate_module_doc /
    _generate_module_doc_chunked. Chunked members are rendered serially,
    on this thread, after the pool below closes: generate_module_doc
    touches `conn` throughout (validate_doc between/after each chunk's
    model call), and sqlite3 connections can't cross threads (the pool
    only ever calls `caller` off-thread, never `conn`, for exactly this
    reason). A member large enough to need chunking is already the rare,
    expensive case; trading its concurrency with the other members for
    correctness here is the right call (mirrors testbatch.py's
    run_test_batch, which makes the identical trade-off).

    Two tiers of skip, cheapest first:

    1. Corpus-level: if nothing `_corpus_signature` covers changed since the
       last successful run (source files, mfdoc version, redact/lexicon
       policy -- see that function's docstring for what it does and doesn't
       catch), every member's facts and brief inputs are identical to last
       time -- module_brief() itself is skipped entirely for any member with
       a prior successful run and an existing output file, not just the
       model call.
    2. Per-member: otherwise (corpus signature changed, or no prior state),
       brief is computed and hashed as before; a member is skipped (not
       re-generated) only when its own brief hash is unchanged from the
       last successful run and the output file still exists -- a member
       whose brief changed, or whose prior attempt failed, is always
       re-run. The effective `max_rules_per_call` threshold is folded into
       the hash too: an unchanged brief but a changed threshold can still
       flip a member between the single-doc and chunked output shapes, and
       the per-member skip must not treat that as "nothing changed".

    This is what makes a run over thousands of members interruptible and
    restartable without burning tokens -- or needless DB queries -- on
    work that's already done.

    `_corpus_signature` itself is only computed when `state_path` is given
    -- with no state file there is nothing to compare it against, and the
    per-member tier below runs unconditionally anyway.
    """
    threshold = _resolve_max_rules_per_call(max_rules_per_call)
    state = _load_state(state_path) if state_path else {}
    corpus_sig = (
        _corpus_signature(conn, redact, lexicon, extra=[str(threshold)]) if state_path else None
    )
    corpus_unchanged = bool(state_path) and state.get("_corpus_sha256") == corpus_sig
    if state_path:
        # Written into `state` up front, before any per-member checkpoint,
        # so a process killed mid-run still leaves a resumed run able to
        # take the corpus-level `corpus_unchanged` fast-path above -- not
        # just a run that reached the very end. See issue #78.
        state["_corpus_sha256"] = corpus_sig
    results: list[DocResult] = []
    # Keyed by the subdir-qualified state_key computed below, not bare
    # member name: two batchable members can share a name across
    # libraries/dialects (member.name is only unique together with
    # library+dialect), and `members` can legitimately contain that bare
    # name more than once (once per colliding member). A bare-name-keyed
    # dict here would let the second one's brief/state silently clobber the
    # first's between the loop below and the two loops that consume these
    # -- keying by state_key throughout (not just in `state` itself) is
    # what actually prevents that, not merely computing a qualified key and
    # then discarding it.
    briefs: dict[str, str] = {}
    to_run: list[tuple[str, str, Path, str]] = []
    to_run_chunked: list[tuple[str, str, Path, str]] = []

    for name in members:
        subdir = _output_subdir(conn, name)
        out_path = out_dir / subdir / f"{name}.md"
        state_key = f"{subdir.as_posix()}/{name}"
        prior = state.get(state_key)
        # `prior` is only ever meaningful as this member's own state entry;
        # guard against the (currently reserved but unenforced) "_corpus_sha256"
        # key ever being looked up as if it were one -- see cli.py's --members
        # normalisation, which keeps ordinary member names from colliding with it.
        prior_ok = isinstance(prior, dict) and prior.get("ok") and out_path.exists()

        if corpus_unchanged and prior_ok:
            results.append(_skip_result(name, out_path, prior))
            continue

        # Always the member's *full* brief, even for a member that ends up
        # chunked below -- it's only ever used as a content fingerprint for
        # resume/skip, never sent to the model as-is (the chunked path
        # builds its own per-chunk briefs from scratch).
        brief = module_brief(conn, name, redact=redact, lexicon=lexicon)
        brief_hash = hashlib.sha256(f"{brief}\x00{threshold}".encode("utf-8")).hexdigest()
        if prior_ok and prior.get("brief_sha256") == brief_hash:
            results.append(_skip_result(name, out_path, prior))
            continue

        rows, ambiguous_libs = fetch_rule_candidate_rows(conn, name)
        if not ambiguous_libs and rows and len(rows) > threshold:
            to_run_chunked.append((name, brief_hash, out_path, state_key))
        else:
            briefs[state_key] = brief
            to_run.append((name, brief_hash, out_path, state_key))

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {
            pool.submit(caller, build_prompt(briefs[state_key], writing_rules, template)):
                (name, brief_hash, out_path, state_key)
            for name, brief_hash, out_path, state_key in to_run
        }
        for fut in as_completed(futures):
            name, brief_hash, out_path, state_key = futures[fut]
            # A caller exception here (transient network error, rate limit,
            # timeout, ...) must isolate to this one member, not propagate
            # and kill every other in-flight/queued future in the pool --
            # see issue #78. Recorded as a failed DocResult (ok=False) so a
            # re-run's resume check (prior_ok above) re-does exactly this
            # member and nothing else that already succeeded.
            try:
                response = fut.result()
            except Exception as exc:
                result = DocResult(
                    name, str(out_path), False, 1, 0, 0,
                    [f"model call failed: {exc!r}"],
                )
                results.append(result)
                state[state_key] = {"ok": False, "attempts": 1, "brief_sha256": brief_hash}
                if state_path:
                    _save_state(state_path, state)
                continue

            input_tokens, output_tokens = response.input_tokens, response.output_tokens

            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(_fix_generated_by_version(response.text), encoding="utf-8")
            validation = validate_doc(conn, out_path)
            attempts = 1
            if not validation["ok"]:
                retry_note = _retry_note(validation["problems"])
                retry_prompt = build_prompt(briefs[state_key], writing_rules, template, retry_note)
                try:
                    retry_response = caller(retry_prompt)
                except Exception as exc:
                    # attempts=2, not 1: the first call already completed
                    # (it just failed validation), and this retry call was
                    # itself attempted -- matching the normal
                    # retry-on-validation-failure path's attempts=2 below,
                    # so BatchSummary/resume state isn't misreported as a
                    # single-attempt failure. See issue #78 review.
                    result = DocResult(
                        name, str(out_path), False, 2, input_tokens, output_tokens,
                        validation["problems"] + [f"retry model call failed: {exc!r}"],
                    )
                    results.append(result)
                    state[state_key] = {"ok": False, "attempts": 2, "brief_sha256": brief_hash}
                    if state_path:
                        _save_state(state_path, state)
                    continue
                input_tokens += retry_response.input_tokens
                output_tokens += retry_response.output_tokens
                out_path.write_text(_fix_generated_by_version(retry_response.text), encoding="utf-8")
                validation = validate_doc(conn, out_path)
                attempts = 2

            result = DocResult(
                name, str(out_path), validation["ok"], attempts, input_tokens, output_tokens,
                validation.get("problems", []),
            )
            results.append(result)
            state[state_key] = {"ok": result.ok, "attempts": attempts, "brief_sha256": brief_hash}
            # Checkpoint after every completed/failed member, not only once
            # at the very end -- otherwise a later member's failure (or the
            # process being killed mid-run) loses every already-completed
            # member's state from this same pass too, forcing a full re-run
            # instead of just re-doing what actually failed. See issue #78.
            if state_path:
                _save_state(state_path, state)

    for name, brief_hash, out_path, state_key in to_run_chunked:
        prior = state.get(state_key)
        prior_chunks = prior.get("chunks") if isinstance(prior, dict) else None
        # Same isolation as the single-call pool above, one member wide: a
        # per-chunk caller exception is now handled inside
        # _generate_module_doc_chunked itself (each chunk's own chunk_state
        # entry survives a sibling chunk's failure), so this try/except is
        # a second line of defense for anything unexpected *outside* that
        # per-chunk loop (chunk-range computation, narrative reconciliation,
        # ...) -- in that rarer case there's no partial chunk_state from
        # this pass to report, so prior_chunks (last run's state) is the
        # best available fallback, same as before. See issue #78.
        try:
            result = generate_module_doc(
                conn, name, out_path, caller, writing_rules, template, redact=redact,
                lexicon=lexicon, max_rules_per_call=threshold, prior_chunks=prior_chunks,
                index_template=index_template,
            )
        except Exception as exc:
            result = DocResult(
                name, str(out_path), False, 0, 0, 0,
                [f"model call failed: {exc!r}"], chunked=True, chunk_state=prior_chunks,
            )
        results.append(result)
        state[state_key] = {
            "ok": result.ok, "attempts": result.attempts, "brief_sha256": brief_hash,
            "chunks": result.chunk_state,
        }
        # Checkpoint after every chunked member too -- these are rendered
        # serially and can each involve several model calls of their own, so
        # this is exactly the same "don't lose already-completed work" case
        # the pool above guards against, just one member wide instead of
        # scoped to a single call.
        if state_path:
            _save_state(state_path, state)

    if state_path:
        # `_corpus_sha256` was already written into `state` up front (see
        # above) so every incremental checkpoint above already carries it --
        # this final save just persists whatever the last member/chunk loop
        # iteration didn't already flush (there always is at least one,
        # from the corpus-signature write itself).
        _save_state(state_path, state)

    total_in = sum(r.input_tokens for r in results)
    total_out = sum(r.output_tokens for r in results)
    cost = estimate_cost(total_in, total_out, cost_per_mtok_in, cost_per_mtok_out)

    return BatchSummary(
        results=sorted(results, key=lambda r: r.member),
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        cost_usd=cost,
        retried=sum(1 for r in results if r.attempts > 1 and not r.skipped and not r.chunked),
        ok=sum(1 for r in results if r.ok),
        failed=sum(1 for r in results if not r.ok),
        skipped=sum(1 for r in results if r.skipped),
    )

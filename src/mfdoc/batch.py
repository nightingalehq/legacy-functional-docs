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
import logging
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import __version__
from .brief import (
    MemberFacts, _unescape_cell, build_member_facts, chunk_density_metrics, fetch_routines,
    fetch_rule_candidate_rows, flag_density_outliers, format_density_note, member_shared_prefix,
    module_brief, routine_aware_chunk_ranges, routine_for_line,
)
from .citations import _cite, _rule_id, numbered_rule_candidates
from .db import GAP_SEVERITY_ORDER_SQL
from .redact import NULL_REDACTOR, Redactor
from .validate import CITATION, _logical_units, split_frontmatter, validate_doc

# Progress/diagnostic output for a long (potentially thousands-of-members,
# hours-long) `mfdoc batch` run -- issue #83. This is deliberately *not*
# stdout: cli.py's cmd_batch still prints the final per-member OK/FAIL/SKIP
# table and summary line itself (that's real, scriptable CLI output), this
# logger only carries the in-flight, per-event noise (a member skipped on
# resume, a retry, a failed model call) a person watching a long run wants
# visibility into as it happens, at whatever level --verbose/--log-file asks
# for.
logger = logging.getLogger("mfdoc.batch")

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
    """`text` with `_strip_response_preamble` applied, then its
    `generated_by:` line's version corrected to the actually-installed
    `__version__`, regardless of what the model wrote.

    The version-correction half is a no-op if the line isn't present in
    the expected `legacy-functional-docs <version>` shape (e.g. missing
    front matter entirely) -- validate_doc's own front-matter check
    reports that case, not this function's job to. The function as a
    whole is *not* a no-op in that case, though: `_strip_response_preamble`
    still runs regardless, so a response with a stray fence/preamble but
    no `generated_by:` line at all still comes back changed.

    Also runs `_strip_response_preamble` first -- this is already the one
    function every response.text write site (batch.py and testbatch.py)
    calls right before write_text, so it's the natural single place for
    both pieces of caller-agnostic response post-processing to live,
    rather than threading a second call through every one of those sites."""
    return _GENERATED_BY_LINE.sub(
        f"generated_by: legacy-functional-docs {__version__}",
        _strip_response_preamble(text),
        count=1,
    )


# AnthropicCaller/VertexCaller are bare completions -- nothing but the
# prompt's own "start with the YAML front matter, no preamble" instruction
# shapes their output, and in practice they follow it. ClaudeCLICaller
# (--provider claude-code) instead runs Claude Code itself in headless mode
# (`claude -p`): still following that same prompt, but under Claude Code's
# own system prompt/agent framing, which can add a wrapping code fence or a
# line or two of lead-in commentary ("Here's the requested document:")
# before the actual document (issue #150). split_frontmatter's own check is
# strict -- text must literally start with "---" -- so this has to be fixed
# before the response reaches it, not by loosening that check.
#
# Deliberately caller-agnostic (applied at every response.text write site
# regardless of which ModelCaller produced it, not gated on
# --provider claude-code) rather than special-cased to one caller: it's a
# no-op for a response that already starts with "---", and protects the
# bare-completion callers too if they ever exhibit the same shape.
# Deliberately doesn't consume the newline before the closing "```" --
# that newline is indistinguishable from the document body's own trailing
# newline (both are the same character), so only the delimiter itself is
# removed, leaving whatever whitespace the body already had intact.
_TRAILING_FENCE = re.compile(r"```[ \t]*$")
_FRONTMATTER_START = re.compile(r"(?m)^---[ \t]*$")


def _looks_like_real_frontmatter(candidate: str) -> bool:
    """True if `candidate` (which starts with a `---` line) actually opens
    a non-empty YAML mapping, per `split_frontmatter` itself -- not just
    a bare `---` line that happens to appear in the text for an unrelated
    reason. build_prompt's own section separator is literally the string
    `"\\n\\n---\\n\\n"`; naively treating any `---` line as a frontmatter
    start would misidentify that separator (e.g. in a fake-echo caller's
    response, which is just its whole prompt echoed back) as real front
    matter and truncate/misreport an unrelated failure. The YAML block
    between this `---` and the next one has to actually parse as a
    mapping with at least one key -- a prompt section's prose (a markdown
    heading, a paragraph) never does."""
    fm, _, err = split_frontmatter(candidate)
    return err is None and isinstance(fm, dict) and bool(fm)


def _strip_trailing_fence(candidate: str) -> str:
    """`candidate` (already sliced to start at a verified-real `---` front
    matter line) with a trailing wrapping code-fence delimiter removed, if
    present. Slicing to the frontmatter start already discards any leading
    fence-open marker or preamble text before it, whatever form either
    took -- this is the other half, for a fence that wraps the *whole*
    response and so still has its closing ``` after the document. Doesn't
    consume the newline immediately before the closing "```" -- see
    `_TRAILING_FENCE`'s own comment."""
    stripped = candidate.rstrip("\n")
    if _TRAILING_FENCE.search(stripped):
        return _TRAILING_FENCE.sub("", stripped, count=1)
    return candidate


# Only look for a rescuable "---" within this many leading characters.
# A genuine preamble ("Here's the requested document:") is usually a line
# or two, but under Claude Code's own agent framing it can run to several
# sentences of stated intent ("I'll review the fact brief and existing
# rule candidates ... then write the corrected document ...") before the
# actual lead-in line -- issue #197 found a live leak this size (~400
# chars) sailing straight through the original 300-char window untouched,
# because the real "---" simply wasn't in the searched slice at all.
# Bounding the search at all still matters: two different things live out
# there and both need excluding: build_prompt's own "\n\n---\n\n" section
# separators (excluded by `_looks_like_real_frontmatter`'s YAML-mapping
# check, regardless of position) and a worked front-matter *example*
# quoted verbatim inside the prompt's own writing-rules/template text
# (which -- being a fully formed example -- *does* parse as a real YAML
# mapping, so only the window keeps that one from being mistaken for the
# actual response). A fake-echo caller's response is its whole prompt
# echoed back, so both can appear in the same text this function has to
# handle correctly. 900 chars comfortably covers a multi-sentence stated-
# intent preamble while still landing well short of where a quoted
# front-matter example would appear in a real prompt (brief + rules +
# template, each many lines long).
_PREAMBLE_SEARCH_WINDOW = 900


def _strip_response_preamble(text: str) -> str:
    """Strip a wrapping code fence and any pre-`---` preamble text from a
    raw model response, so `split_frontmatter`'s leading-`---` check finds
    the real front matter even when the caller added lead-in text or
    fenced the whole output. A no-op when `text` already starts with `---`,
    and a no-op (original `text` returned untouched) when no `---` line
    that actually opens a real YAML mapping is found near the start --
    nothing to rescue, so `split_frontmatter`'s own "missing YAML front
    matter" error still reports the real raw output.

    Every "---" line within `_PREAMBLE_SEARCH_WINDOW` characters of the
    start is a candidate, checked in order via `_looks_like_real_
    frontmatter` -- the first one that actually opens a real YAML mapping
    wins, so an early build_prompt section separator (never a real
    mapping) is skipped in favour of a later one within the window,
    rather than accepted just for appearing first."""
    if text.startswith("---"):
        return text
    for match in _FRONTMATTER_START.finditer(text[:_PREAMBLE_SEARCH_WINDOW]):
        candidate = text[match.start():]
        if _looks_like_real_frontmatter(candidate):
            return _strip_trailing_fence(candidate)
    return text


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
    # How many transient-error retries (issue #79's call_with_retry, inside
    # AnthropicCaller/VertexCaller) this one response cost -- 0 for a caller
    # that doesn't retry at all (the `fake-echo` test caller, ClaudeCLICaller)
    # or that succeeded on its first attempt. Defaults to 0 so every existing
    # ModelResponse(...) call site (real or in tests) that doesn't know or
    # care about retries keeps working unchanged; a retrying caller sets this
    # on the response it returns (see AnthropicCaller.__call__).
    retries: int = 0
    # Issue #159: tokens the Anthropic SDK's `Usage` object reports spent
    # writing this call's prompt into the ephemeral cache, and tokens read
    # back out of an existing cache entry instead of being billed at full
    # input-token price. Both default 0 -- every existing ModelResponse(...)
    # call site (real or in tests) that doesn't know or care about caching
    # keeps working unchanged, and a real call that didn't create or hit a
    # cache entry (no cache_control sent, or a genuine cache miss) reports
    # 0 for the one that didn't apply, same as the SDK's own `Usage` object
    # (whose cache fields are `None`, not present, when caching wasn't used).
    # Issue #169: `ClaudeCLICaller` populates these too, read off `claude -p
    # --output-format json`'s own `usage` object -- `input_tokens` alone only
    # reports a turn's uncached/cache-miss portion once caching is in play.
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


# A caller takes a prompt and returns a ModelResponse. Swap in a fake for
# tests; the CLI wires up an Anthropic-backed one.
ModelCaller = Callable[[str], ModelResponse]


def model_response_from_message(message) -> ModelResponse:
    """Build a `ModelResponse` from an Anthropic SDK `Message` -- shared by
    every ModelCaller backed by that SDK's `messages.create` response shape
    (anthropic_caller.py's direct-API client and vertex_caller.py's
    Claude-via-Vertex client both return this same shape), so a future change
    to how text/usage is extracted only needs to land in one place.

    `cache_creation_input_tokens`/`cache_read_input_tokens` are read via
    `getattr` with a `None`-coalescing fallback to 0: both are declared
    `Optional[int] = None` on the SDK's `Usage` model (confirmed against
    the installed `anthropic` package's `types/usage.py`), present with a
    real count only when the request actually sent `cache_control` and
    either created or hit a cache entry -- never absent as an attribute,
    but frequently `None`."""
    text = "".join(block.text for block in message.content if block.type == "text")
    usage = message.usage
    return ModelResponse(
        text=text,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
    )


def _timed_call(caller: ModelCaller, prompt: str) -> tuple[ModelResponse, float]:
    """`caller(prompt)`, plus the wall-clock seconds it took -- issue #84's
    per-call latency tracking. Timed here, once, rather than at each call
    site, so every model call in this module (the plain single/pooled path,
    a chunk's own call, and the whole-module narrative-reconciliation call)
    is measured the same way. Deliberately wraps the call itself, not
    anything before or after it (prompt assembly, file writes, validate_doc)
    -- those are cheap, local, and not what "is this run stuck or just
    slow" is asking about; the model call (including whatever retries
    AnthropicCaller/VertexCaller made internally per issue #79) is the part
    with real, externally-caused latency.

    `time.perf_counter` (a monotonic clock, unaffected by wall-clock
    adjustments) is used rather than `time.time` for the same reason
    `call_with_retry`'s own timing-sensitive tests avoid `time.time` --
    duration here is a difference between two points, never a timestamp
    that needs to mean anything on its own."""
    start = time.perf_counter()
    response = caller(prompt)
    return response, time.perf_counter() - start


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


_BATCH_INSTRUCTIONS = (
    "You are writing first-draft functional documentation for one legacy "
    "mainframe module. Follow the writing rules and template exactly. "
    "Never assert behaviour that cannot be traced to a specific source "
    "line in the brief below -- drop or mark `unresolved` anything that "
    "isn't. Output only the completed document (front matter + body), "
    "nothing else."
)


def build_prompt_parts(brief: str, writing_rules: str, template: str,
                        retry_note: str | None = None) -> list[str]:
    """The ordered sections `build_prompt` joins into one flat string,
    returned unjoined -- issue #159's single source of truth for the split
    between the stable prefix (instructions + writing rules + template,
    byte-identical across every chunk/member/retry in one project run) and
    the per-call variable suffix (fact brief, and on a retry, the retry
    note). `build_prompt` itself is just `"\\n\\n---\\n\\n".join(...)` of
    this; `build_prompt_cache_prefix` derives the same stable prefix from
    the first three sections here so AnthropicCaller/VertexCaller can mark
    it as an ephemeral cache breakpoint without re-parsing a joined
    string."""
    parts = [
        _BATCH_INSTRUCTIONS,
        "# Writing rules\n\n" + writing_rules,
        "# Template\n\n" + template,
        "# Fact brief\n\n" + brief,
    ]
    if retry_note:
        parts.append(
            "# Previous attempt failed validation\n\n" + retry_note
            + "\n\nFix these problems and resend the complete document."
        )
    return parts


def build_prompt(brief: str, writing_rules: str, template: str, retry_note: str | None = None) -> str:
    return "\n\n---\n\n".join(build_prompt_parts(brief, writing_rules, template, retry_note))


def build_prompt_cache_prefix(writing_rules: str, template: str) -> str:
    """The exact leading substring of every `build_prompt(...)` call sharing
    this `writing_rules`/`template` (i.e. every call in one project's batch
    run) -- instructions + writing rules + template, with the trailing
    section separator included so it lines up with where `# Fact brief`
    starts. AnthropicCaller/VertexCaller mark this whole prefix with
    `cache_control: {"type": "ephemeral"}` (issue #159) so it's billed once
    per project run instead of once per call; a caller with no cache-prefix
    support (ClaudeCLICaller, the fake-echo test caller) never sees this at
    all -- run_batch only hands it to callers that expose
    `set_cache_prefixes`."""
    stable = build_prompt_parts("", writing_rules, template)[:3]
    return "\n\n---\n\n".join(stable) + "\n\n---\n\n"


def build_member_prompt_cache_prefix(shared_prefix: str) -> str:
    """The exact leading substring of a chunked member's own per-chunk
    `brief` text (issue #214, following up on #207/#183) that stays byte-
    identical across every one of that member's chunks: the `"# Fact
    brief\\n\\n"` heading `build_prompt_parts` always puts ahead of a brief,
    plus `shared_prefix` itself (`member_shared_prefix(member_facts,
    redact)` -- see that function's docstring for exactly what it does and
    doesn't include), plus the same `"\\n\\n---\\n\\n"` section separator
    `_generate_module_doc_chunked` puts between `shared_prefix` and that
    chunk's own `module_brief()` text.

    Registered via `AnthropicCaller`/`VertexCaller`'s `set_member_cache_
    prefixes` -- *not* against the whole prompt like `build_prompt_cache_
    prefix` above, since `shared_prefix` alone (unlike that function's
    output) is never a literal prefix of the full request: the existing
    project-level prefix always precedes it. `_content` matches this
    against the text left over after the project-level prefix is already
    stripped -- see its own docstring."""
    return "# Fact brief\n\n" + shared_prefix + "\n\n---\n\n"


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


# Issue #131 (generalized by #170): across two real regenerations, the large
# majority of `mfdoc batch`'s token cost came from full-chunk regenerations
# chasing down a handful of sentences flagged by validate_doc, in chunks
# that were otherwise already valid -- fixing 2 sentences out of a 40-rule
# chunk cost the same (a whole fresh model call, re-narrating everything) as
# regenerating from scratch. `NEAR_MISS_MAX_LOCALIZED` bounds how small "a
# handful" has to be (matching #131's original "1-5 per attempt" report)
# before `_is_near_miss` calls a validation failure a near-miss worth a
# cheap targeted patch (see `build_localized_patch_prompt`) instead of going
# straight to a full chunk retry. This only applies to failure classes that
# are *sentence-localized* -- they name a specific citation/snippet a patch
# prompt can point the model back at. Two are recognized so far: uncited-
# and-unhedged assertive statements (#131's original case) and reversed-
# condition flags (`validate._reversed_condition_problems`, #170). A chunk
# failing for any other reason -- a real citation-format error, missing/
# malformed front matter, a broken forward-reference, a structurally broken
# response, or simply too many localized findings to call "almost right" --
# still falls through to the existing full-retry path unchanged, since
# those failure classes aren't tied to one sentence a small patch could fix.
NEAR_MISS_MAX_LOCALIZED = 3

# The exact prefix `validate._reversed_condition_problems` uses for every
# problem string it produces -- each one already names the flagged
# `[[MEMBER:LINE]]` citation and what's wrong with it, so `_localized_
# findings` only needs to recognize the shape, not re-derive anything from
# the fact store.
_REVERSED_CONDITION_PREFIX = "comparison direction may be reversed near "


def _localized_findings(result: dict) -> tuple[list[str], list[str]] | None:
    """Split `result["problems"]` (a `validate_doc` return value) into the
    two sentence-localized failure classes `_is_near_miss` recognizes --
    uncited-and-unhedged assertive statements and reversed-condition flags
    -- returning `None` the moment any problem in the list doesn't fit
    either shape. That `None` is what tells `_is_near_miss` a failure isn't
    (purely) localized: a structural problem (missing front matter, a
    broken forward-reference, an invalid citation, anything not tied to one
    specific sentence) means the whole chunk still needs a full retry, even
    if it also happens to carry a localized finding alongside it."""
    uncited = result.get("uncited_assertions") or []
    # `validate_doc` appends exactly one summary problem for every uncited
    # assertion found together (`"{n} assertive statement(s) carry no
    # citation and no hedge"`) -- this deliberately reconstructs that exact
    # string to match against, rather than pattern-matching on a substring,
    # since `uncited_assertions` (the actual flagged sentences) is the
    # authoritative signal `validate_doc` already computed it from.
    uncited_problem = (
        f"{len(uncited)} assertive statement(s) carry no citation and no hedge"
        if uncited else None
    )
    reversed_findings: list[str] = []
    for p in result["problems"]:
        if p == uncited_problem:
            continue
        if p.startswith(_REVERSED_CONDITION_PREFIX):
            reversed_findings.append(p)
            continue
        return None
    return uncited, reversed_findings


def _is_near_miss(result: dict) -> bool:
    """True when `result` (a `validate_doc` return value) failed validation
    for reasons that are *entirely* sentence-localized (see
    `_localized_findings`) and small enough in total to count as "almost
    right" (`NEAR_MISS_MAX_LOCALIZED`) rather than a response that needs a
    full rewrite."""
    if result["ok"]:
        return False
    split = _localized_findings(result)
    if split is None:
        return False
    uncited, reversed_findings = split
    total = len(uncited) + len(reversed_findings)
    return 0 < total <= NEAR_MISS_MAX_LOCALIZED


# Issue #171: even a near-miss uncited assertion (see `_is_near_miss` above)
# always paid for a full model call to fix -- even though a common shape is
# that the model's prose is a correct paraphrase of a fact the *same brief*
# already states with a citation elsewhere, and it simply dropped or
# misplaced the `[[MEMBER:LINE]]` tag. `_auto_cite_uncited_assertions`
# handles that shape entirely in code: no model call, just a text splice --
# and only for the uncited-assertion class. Reversed-condition findings have
# no equivalent shape (the fix is a polarity correction to existing prose,
# not "attach a citation that already exists elsewhere for this exact
# claim"), so this pass is never invoked for them; the reversed-condition
# near-miss path is unchanged by this issue.

# A backtick- or quote-delimited token -- a field/rule name like
# `ORDER-STATUS` or a literal like `'CONF'` -- is the only thing
# `_find_confident_citation` treats as evidence two lines describe the same
# fact. brief.py already wraps every field name, literal, and condition
# fragment it renders in backticks (see e.g. module_brief's per-row
# `` f"...`{...}`..." `` calls); reusing that convention means the token set
# is already exactly "the specific things this line asserts", not just any
# shared word. A bare-word overlap ("the", "system", "before posting") is
# exactly the false-positive risk issue #171 asks this pass to avoid, so
# plain words are never considered tokens at all.
_KEY_TOKEN = re.compile(r"`([^`\n]+)`|'([^'\n]+)'|\"([^\"\n]+)\"")


def _key_tokens(text: str) -> set[str]:
    """Every backtick- or quote-delimited token in `text`, kept verbatim
    including its surrounding quote characters where present -- so a
    quoted literal `'CONF'` and the bare word `CONF` are never treated as
    the same token. An assertion that names the field but not the literal
    value it's compared against (or vice versa) is deliberately not a match
    on that token alone.

    Deliberately does *not* know about `brief.py`'s pipe-escaping (issue
    #185) -- `text` here is compared as both a narrated sentence's tokens
    and a brief line's tokens in `_find_confident_citation`, and a
    narrated sentence never carries that rendering artifact in the first
    place. Decoding it belongs solely to `_maybe_unescape_table_row`,
    applied only to a brief line before it's tokenized, and only when that
    line is actually one of `brief._tbl`'s rows (see that function's own
    docstring for why even that scoping matters) -- applying decoding here
    unconditionally would also "decode" a sentence that happens to contain
    its own literal `\\|`, silently turning it into a different string
    than the brief's own (correctly decoded) token and producing exactly
    the false mismatch this was meant to fix (Copilot review on PR
    #213)."""
    tokens: set[str] = set()
    for m in _KEY_TOKEN.finditer(text):
        tok = next(g for g in m.groups() if g is not None).strip()
        if tok:
            tokens.add(tok)
    return tokens


def _maybe_unescape_table_row(line: str) -> str:
    """Undo `brief._esc_cell`'s pipe-table escaping (`brief._unescape_cell`)
    -- but only when `line` is actually one of `brief._tbl`'s compact rows,
    identified structurally by *not* starting with the `"- "` bullet prefix
    every other cited section in `module_brief`'s output still uses
    (header comments, data access, inbound callers, interactions, messages,
    gaps, guard-chain summaries, ...  -- see `brief.py`'s own render calls).
    A `_tbl` row is pipe-delimited with no leading bullet at all.

    Decoding a bullet line that was never `_esc_cell`-encoded is a bug, not
    just unnecessary: real cited prose (a header comment, a data-access
    key expression, ...) can legitimately contain its own literal `\\` or
    `\\|` that has nothing to do with this rendering's table-escaping, and
    blindly "decoding" it would alter that line's key tokens, either
    missing a real auto-citation match or attaching one to a sentence
    whose actual source value differs (Copilot review round 4 on PR
    #213)."""
    if line.lstrip().startswith("- "):
        return line
    return _unescape_cell(line)


def _brief_cited_lines(brief: str) -> list[tuple[str, str]]:
    """(citation, line) for every line in `brief` carrying at least one
    `[[MEMBER:LINE]]` citation -- the candidate set `_find_confident_
    citation` searches for a matching already-cited fact."""
    out = []
    for line in brief.splitlines():
        m = CITATION.search(line)
        if m:
            out.append((m.group(0), line))
    return out


def _find_confident_citation(sentence: str, brief_lines: list[tuple[str, str]]) -> str | None:
    """The citation from the single brief-cited line whose key tokens (see
    `_key_tokens`) are a superset of `sentence`'s, or `None` when there is
    no confident match.

    Deliberately conservative, per issue #171's explicit tradeoff: a false
    positive here (the wrong citation attached to a sentence) is worse than
    the one model call this whole pass exists to save, so this returns
    `None` -- meaning "fall back to `build_localized_patch_prompt`" -- in
    every case except a clean, unambiguous match:

    - `sentence` names no key tokens at all (nothing specific to match on --
      most commonly a genuinely uncited claim with no fixable citation
      anywhere, which is exactly what still needs a model to write a real
      hedge or new sentence, not a spliced-in citation).
    - No brief line's tokens are a superset of `sentence`'s (a *partial*
      overlap is not enough -- every specific literal/field-name token the
      sentence names must be present on that one line).
    - More than one brief line qualifies (ambiguous: attaching either
      citation could be wrong, so neither is attached).

    This catches real paraphrases (same field name and literal, reworded
    prose) but will not catch a paraphrase that drops the literal/field name
    entirely, or a fact spread across multiple brief lines -- those still
    cost the one model call this issue is optimizing away for the common
    case, which is the intended tradeoff."""
    sentence_tokens = _key_tokens(sentence)
    if not sentence_tokens:
        return None
    matches = {
        cite for cite, line in brief_lines
        if sentence_tokens <= _key_tokens(_maybe_unescape_table_row(line))
    }
    return matches.pop() if len(matches) == 1 else None


# A sentence ends at its own terminating punctuation, optionally followed by
# a closing quote/paren -- the citation is spliced in just before that
# punctuation, matching where a model-written citation for the same claim
# would normally sit ("...equals `'CONF'` [[MMP0100:5]].").
_SENTENCE_END = re.compile(r"[.!?]+[\"'”’)]*\s*$")


def _splice_citation(current_text: str, sentence: str, citation: str) -> str | None:
    """`current_text` with `citation` spliced into the one occurrence of
    `sentence`, immediately before its terminating punctuation -- or `None`
    when `sentence` doesn't appear in `current_text` exactly once (e.g. it
    was wrapped across source lines, so the single-line form
    `_logical_units` produces doesn't match verbatim, or it happens to
    recur). Refusing to guess which occurrence in that case is the same
    conservative choice as `_find_confident_citation`'s: fall back to the
    model patch rather than splice in the wrong place."""
    if current_text.count(sentence) != 1:
        return None
    m = _SENTENCE_END.search(sentence)
    if m:
        patched = sentence[:m.start()] + " " + citation + sentence[m.start():]
    else:
        patched = sentence + " " + citation
    return current_text.replace(sentence, patched, 1)


def _auto_cite_uncited_assertions(
    brief: str, current_text: str, uncited: list[str],
) -> tuple[str, list[str]] | None:
    """Issue #171's deterministic auto-citation pass: for each snippet in
    `uncited` (truncated flagged-sentence text from a near-miss
    `validate_doc` result -- see `_uncited_assertions`), locate its full,
    untruncated sentence in `current_text` and try `_find_confident_
    citation` against `brief`'s own already-cited lines.

    Returns `(patched_text, remaining_uncited)` where `remaining_uncited`
    holds the original snippets (unchanged) for whichever sentences did
    *not* get a confident match -- the caller still owes those to
    `build_localized_patch_prompt` -- or `None` when nothing in `uncited`
    got a confident match at all (nothing to re-validate; caller should
    proceed exactly as before this pass existed).

    Scoped to uncited assertions only (never called for reversed-condition
    findings): see the module-level comment above `_KEY_TOKEN`."""
    _, body, err = split_frontmatter(current_text)
    if err:
        return None
    units = [u.strip() for u in _logical_units(body)]
    brief_lines = _brief_cited_lines(brief)
    patched_text = current_text
    remaining: list[str] = []
    patched_any = False
    for snippet in uncited:
        full = next((u for u in units if u[:140] == snippet), None)
        if full is None:
            remaining.append(snippet)
            continue
        citation = _find_confident_citation(full, brief_lines)
        if citation is None:
            remaining.append(snippet)
            continue
        spliced = _splice_citation(patched_text, full, citation)
        if spliced is None:
            remaining.append(snippet)
            continue
        patched_text = spliced
        patched_any = True
    if not patched_any:
        return None
    return patched_text, remaining


def build_localized_patch_prompt(
    brief: str, current_text: str, uncited: list[str], reversed_findings: list[str],
) -> str:
    """A far smaller, targeted follow-up prompt for the near-miss case
    `_is_near_miss` detects (issue #131, generalized by #170). Unlike
    `build_prompt`'s full-retry prompt, this never resends the writing
    rules or template: the model already demonstrated it can follow them
    (the rest of `current_text` is proof), so the only thing worth asking
    for again is a fix to the specific flagged findings -- using a citation
    already present in `brief` (the same fact brief the original response
    was written from), an explicit hedge, or a corrected comparison
    direction, depending on the finding. Everything else in the document is
    asked to come back unchanged -- far cheaper, and far less likely to
    perturb an otherwise-valid chunk, than a full from-scratch chunk
    regeneration paying to fix one or two findings out of dozens of correct
    sentences."""
    sections = []
    if uncited:
        bullets = "\n".join(f"- {s}" for s in uncited)
        sections.append(
            "## Uncited assertive statements\n\n"
            "The snippets below may be truncated to 140 characters; use "
            "them to locate the full sentence in the current document. For "
            "each, either add a `[[MEMBER:LINE]]` citation to a fact "
            "already present in the brief below that supports it, or -- "
            "only if no such fact exists -- rewrite it as an explicit "
            "hedge instead of an assertion.\n\n" + bullets
        )
    if reversed_findings:
        bullets = "\n".join(f"- {f}" for f in reversed_findings)
        sections.append(
            "## Comparison direction may be reversed\n\n"
            "Each finding below names the exact `[[MEMBER:LINE]]` citation "
            "whose surrounding sentence describes a comparison in the "
            "opposite direction from what the cited source condition "
            "means. Locate that sentence and correct which outcome it "
            "describes so it matches the source condition's actual "
            "polarity, without changing the citation itself.\n\n" + bullets
        )
    return (
        "The document below is almost entirely valid first-draft functional "
        "documentation. A small number of specific, locatable sentences "
        "have a flagged problem -- everything else in it already validated "
        "clean.\n\n"
        "# Flagged findings (locate the matching sentence; fix only these)\n\n"
        + "\n\n".join(sections) + "\n\n"
        "Do not change anything else: no other sentence, heading, citation, "
        "or front-matter field. Output the complete corrected document, "
        "nothing else.\n\n"
        "# Fact brief\n\n" + brief + "\n\n"
        "# Current document\n\n" + current_text
    )


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
    # Wall-clock seconds spent in model calls for this member -- issue #84.
    # Sum of every _timed_call() this member's generation made: the
    # validation-retry-on-failure call's own time is included (attempts=2
    # means two model calls, both timed), and a chunked member's duration
    # covers every chunk call plus the one whole-module narrative-
    # reconciliation call. 0.0 for a skipped (resumed, no model call) or
    # caller-exception-failed (no response, nothing to time) member.
    duration_s: float = 0.0
    # Total transient-error retries (ModelResponse.retries, issue #79) across
    # every model call this member's generation made -- distinct from
    # `attempts`/BatchSummary.retried, which count validation-failure retries,
    # a different (and non-transient) kind of "tried again". A member can be
    # high in one and zero in the other: a call that needed 3 internal
    # connection-reset retries but validated clean on the first attempt has
    # retries=3, attempts=1; a call that validated clean first try but failed
    # validation once (attempts=2) with no transient errors has retries=0.
    retries: int = 0


def _generate_module_doc_from_brief(conn, member_name: str, brief: str, out_path: Path,
                                     caller: ModelCaller, writing_rules: str, template: str,
                                     max_attempts: int = 2) -> DocResult:
    """Call -> validate -> retry-once loop, given an already-built brief --
    the part of generate_module_doc that doesn't care whether `brief` covers
    a member's whole rule set or just one chunk of it, shared by the plain
    single-call path and _generate_module_doc_chunked's per-chunk calls
    below (mirrors testbatch.py's _generate_test_doc_from_brief).

    A validation failure that `_is_near_miss` calls a near-miss (issue #131,
    generalized by #170: only a handful of sentence-localized findings --
    uncited-and-unhedged assertive statements and/or reversed-condition
    flags -- nothing structural wrong) gets one cheap targeted-patch attempt
    (`build_localized_patch_prompt`) before counting against `max_attempts`
    -- it doesn't consume one of the full-regeneration attempts, since it
    asks for something far smaller than one. A chunk failing for any other
    reason, or where the patch attempt itself doesn't resolve everything,
    falls straight through to the existing full-chunk retry loop unchanged."""
    retry_note = None
    input_tokens = output_tokens = 0
    duration_s = 0.0
    retries = 0
    problems: list[str] = []
    uncited_assertions: list[str] = []
    attempt = 0
    for attempt in range(1, max_attempts + 1):
        prompt = build_prompt(brief, writing_rules, template, retry_note)
        response, elapsed = _timed_call(caller, prompt)
        input_tokens += response.input_tokens
        output_tokens += response.output_tokens
        duration_s += elapsed
        retries += response.retries
        out_path.parent.mkdir(parents=True, exist_ok=True)
        text = _fix_generated_by_version(response.text)
        out_path.write_text(text, encoding="utf-8")
        result = validate_doc(conn, out_path, _text=text)
        if result["ok"]:
            return DocResult(
                member_name, str(out_path), True, attempt, input_tokens, output_tokens, [],
                duration_s=duration_s, retries=retries,
            )

        if _is_near_miss(result):
            uncited, reversed_findings = _localized_findings(result)

            if uncited:
                # Issue #171: try a deterministic, no-model-call fix first --
                # only for the uncited-assertion findings (never reversed-
                # condition ones, see _auto_cite_uncited_assertions).
                auto = _auto_cite_uncited_assertions(brief, text, uncited)
                if auto is not None:
                    candidate_text, remaining_uncited = auto
                    candidate_result = validate_doc(conn, out_path, _text=candidate_text)
                    if candidate_result["ok"]:
                        logger.info(
                            "%s: %d near-miss uncited assertion(s) auto-cited from the "
                            "brief with no model call; chunk now validates clean",
                            member_name, len(uncited) - len(remaining_uncited),
                        )
                        out_path.write_text(candidate_text, encoding="utf-8")
                        return DocResult(
                            member_name, str(out_path), True, attempt, input_tokens,
                            output_tokens, [], duration_s=duration_s, retries=retries,
                        )
                    if _is_near_miss(candidate_result):
                        # Auto-citation resolved some (not all) of the
                        # findings and didn't introduce anything non-
                        # localized -- keep the patched text and the
                        # narrowed finding lists, so the fallback patch
                        # prompt below only asks about what's still wrong.
                        logger.info(
                            "%s: %d/%d near-miss uncited assertion(s) auto-cited from "
                            "the brief with no model call; %d still need a targeted patch",
                            member_name, len(uncited) - len(remaining_uncited), len(uncited),
                            len(remaining_uncited),
                        )
                        text = candidate_text
                        out_path.write_text(text, encoding="utf-8")
                        result = candidate_result
                        uncited, reversed_findings = _localized_findings(candidate_result)
                    # else: the candidate is no longer a near-miss (an
                    # auto-cited splice landed somewhere that broke a
                    # different check) -- discard it and fall through to
                    # the patch prompt using the original, unpatched
                    # `text`/`uncited`/`reversed_findings` instead.

            logger.warning(
                "%s: validation failed on attempt %d/%d with %d near-miss "
                "localized finding(s) only (%d uncited, %d reversed-condition) "
                "-- trying a targeted patch before a full chunk retry",
                member_name, attempt, max_attempts,
                len(uncited) + len(reversed_findings), len(uncited), len(reversed_findings),
            )
            patch_prompt = build_localized_patch_prompt(brief, text, uncited, reversed_findings)
            patch_response, patch_elapsed = _timed_call(caller, patch_prompt)
            input_tokens += patch_response.input_tokens
            output_tokens += patch_response.output_tokens
            duration_s += patch_elapsed
            retries += patch_response.retries
            text = _fix_generated_by_version(patch_response.text)
            out_path.write_text(text, encoding="utf-8")
            result = validate_doc(conn, out_path, _text=text)
            if result["ok"]:
                return DocResult(
                    member_name, str(out_path), True, attempt, input_tokens, output_tokens, [],
                    duration_s=duration_s, retries=retries,
                )
            logger.warning(
                "%s: targeted patch attempt did not resolve validation (%d problem(s)); "
                "falling back to a full chunk retry",
                member_name, len(result["problems"]),
            )

        logger.warning(
            "%s: validation failed on attempt %d/%d (%d problem(s))",
            member_name, attempt, max_attempts, len(result["problems"]),
        )
        problems = result["problems"]
        uncited_assertions = result.get("uncited_assertions") or []
        retry_note = _retry_note(problems)
    if uncited_assertions:
        # Issue #131's fallback ask: surface the flagged sentences
        # themselves directly and prominently, not just the "N assertive
        # statement(s)" count already in `problems` -- so a human can
        # hand-patch immediately from this result instead of re-deriving
        # which sentences they were from the document text. Each entry is a
        # snippet, not the full sentence: validate_doc truncates
        # uncited_assertions to 140 characters.
        problems = problems + [f"uncited (snippet): {s}" for s in uncited_assertions]
    return DocResult(
        member_name, str(out_path), False, attempt, input_tokens, output_tokens, problems,
        duration_s=duration_s, retries=retries,
    )


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
        fm, _, err = split_frontmatter(path.read_text(encoding="utf-8"))
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
        fm, _, err = split_frontmatter(path.read_text(encoding="utf-8"))
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


_RECONCILIATION_INSTRUCTIONS = (
    "You are reconciling several already-validated, already-cited excerpts of "
    "one legacy mainframe module -- one excerpt per chunk that module's "
    "business-rule set was split into for documentation purposes -- into one "
    "coherent whole-module statement. Do not invent any claim, fact, or "
    "citation that is not already present, in substance, in the excerpts "
    "below; every sentence you write must carry a citation copied from one "
    "of them. Where excerpts genuinely conflict, prefer the more specific or "
    "more heavily-cited statement and note the discrepancy as an "
    "`(unresolved)` item rather than silently picking one.\n\n"
    "Output exactly five sections, in this exact order, headed exactly as "
    "shown, and nothing else -- no preamble, no restating these "
    "instructions:\n\n" + "\n".join(f"## {h}" for h in NARRATIVE_SECTIONS)
)


def build_reconciliation_prompt_parts(member_name: str, chunk_sources: list[str], writing_rules: str,
                                       index_template: str | None,
                                       retry_note: str | None = None) -> list[str]:
    """The ordered sections `build_reconciliation_prompt` joins into one flat
    string, returned unjoined -- same purpose as `build_prompt_parts` (issue
    #159): the generic reconciliation instructions and writing rules (plus,
    when configured, the module-index template) are byte-identical across
    every chunked member and every retry in one project run; `member_name`
    and the chunk excerpts being reconciled are the only things that vary,
    so they're deliberately placed last (not first, as the original prompt
    text had it) -- a variable section anywhere before the stable prefix
    would break the exact-prefix match `build_reconciliation_prompt_cache_
    prefix` relies on. `build_reconciliation_prompt` itself is just
    `"\\n\\n---\\n\\n".join(...)` of this."""
    parts = [
        _RECONCILIATION_INSTRUCTIONS,
        "# Writing rules\n\n" + writing_rules,
    ]
    if index_template:
        parts.append("# Module-index template (for section-content expectations)\n\n" + index_template)
    parts.append(
        f"# Module being reconciled: {member_name}\n\n"
        "# Per-chunk excerpts to reconcile\n\n" + "\n\n---\n\n".join(chunk_sources)
    )
    if retry_note:
        parts.append(
            "# Previous attempt failed validation\n\n" + retry_note
            + "\n\nFix these problems and resend all five sections, in order."
        )
    return parts


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
    return "\n\n---\n\n".join(
        build_reconciliation_prompt_parts(member_name, chunk_sources, writing_rules, index_template, retry_note)
    )


def build_reconciliation_prompt_cache_prefix(writing_rules: str, index_template: str | None) -> str:
    """The exact leading substring of every `build_reconciliation_prompt(...)`
    call sharing this `writing_rules`/`index_template` -- reconciliation
    instructions + writing rules (+ module-index template, if configured) --
    with the trailing section separator included. Unlike `build_prompt`'s
    prefix, this one is stable across every chunked member and retry in a
    project run (member_name and the chunk excerpts always come after it --
    see `build_reconciliation_prompt_parts`), so AnthropicCaller/VertexCaller
    can mark it as its own `cache_control` breakpoint (issue #159)."""
    stable = build_reconciliation_prompt_parts("", [], writing_rules, index_template)[:-1]
    return "\n\n---\n\n".join(stable) + "\n\n---\n\n"


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
                                      ) -> tuple[bool, int, int, int, list[str], dict[str, str] | None, float, int]:
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

    Returns `(ok, attempts, input_tokens, output_tokens, problems, sections,
    duration_s, retries)` -- `sections` is the accepted {heading: text}
    mapping when ok, or None on failure (the last, rejected attempt is not a
    fact worth caching); `duration_s`/`retries` are this call's own wall-clock
    time and transient-retry count (issue #84), folded by the caller into
    the chunked member's overall DocResult totals alongside every chunk's."""
    sources = [_reconciliation_source(i, body) for i, body in chunk_bodies]
    allowed_citations: set[str] = set()
    for body in sources:
        allowed_citations |= _citations_in(body)
    retry_note: str | None = None
    input_tokens = output_tokens = 0
    duration_s = 0.0
    retries = 0
    problems: list[str] = []
    attempt = 0
    for attempt in range(1, max_attempts + 1):
        prompt = build_reconciliation_prompt(member_name, sources, writing_rules, index_template, retry_note)
        response, elapsed = _timed_call(caller, prompt)
        input_tokens += response.input_tokens
        output_tokens += response.output_tokens
        duration_s += elapsed
        retries += response.retries

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
            return True, attempt, input_tokens, output_tokens, [], sections, duration_s, retries

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
    return False, attempt, input_tokens, output_tokens, problems, None, duration_s, retries


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


def _chunk_reuse_ok(conn, prior_chunks: dict | None, i: int, brief_hash: str,
                     chunk_path: Path) -> bool:
    """Whether chunk `i` can be reused verbatim -- no model call -- given a
    prior run's chunk_state and this chunk's freshly-computed brief hash:
    the prior run must have recorded this exact chunk as clean (`ok` True)
    with the same `brief_sha256`, its output file must still exist on disk,
    and it must still validate today (validate_doc's own logic can have
    changed since it was last checked, even though the content hasn't).

    A failed chunk is never a reuse candidate: it leaves its last (invalid)
    attempt on disk, so treating it as reusable would re-validate the same
    bad file forever and the chunk would never re-render.

    Shared by _generate_module_doc_chunked's real reuse path and
    plan_batch's dry-run estimate (issue #160) so both apply exactly the
    same reuse rule -- a preview that used a second, slightly different
    copy of this logic could drift from what a real run actually does."""
    prior_chunk = (prior_chunks or {}).get(str(i))
    reusable = (
        isinstance(prior_chunk, dict) and prior_chunk.get("ok") is True
        and prior_chunk.get("brief_sha256") == brief_hash
        and chunk_path.exists()
    )
    if not reusable:
        return False
    return validate_doc(conn, chunk_path)["ok"]


def _routine_chunk_map(routines: list, rule_rows: list, ranges: list[tuple[int, int]]) -> dict[str, int]:
    """Routine name (upper) -> 1-based chunk index whose rule range contains
    that routine's own rules -- the mapping `module_brief`'s `chunk_map`
    param needs to annotate "Internal routines" with `[documented in
    chunk N]` (see that function's docstring). Factored out of
    `_generate_module_doc_chunked` (issue #214 review) so `plan_batch`'s
    dry-run preview computes the identical mapping before hashing a
    preview chunk brief, instead of silently omitting it and hashing a
    brief `module_brief` would never actually render that way for a member
    with internal routines -- exactly the class of resume-preview drift
    this module's docstrings already warn `_chunk_reuse_ok`/`plan_batch`
    must not have.

    `ranges` is in rule-ordinal space (1-based position within `rule_rows`,
    not raw source line numbers -- see `routine_aware_chunk_ranges`), so a
    routine's chunk is found by scanning `rule_rows` directly (not via a
    line_no-keyed dict: `rule_candidate.line_no` is not guaranteed unique
    per member, and a dict built that way silently collapses same-line rows
    to whichever is last, discarding the rest) for the first row whose line
    falls inside the routine's own span, using that row's position (its
    index in `rule_rows`, 1-based) as the ordinal. A routine with no
    rule_candidate rows of its own (nothing to key off) is simply left out
    of the map."""
    chunk_map: dict[str, int] = {}
    for routine in routines:
        end_line = routine["end_line"] if routine["end_line"] is not None else routine["start_line"]
        ordinal = next(
            (pos for pos, r in numbered_rule_candidates(rule_rows)
             if routine["start_line"] <= r["line_no"] <= end_line),
            None,
        )
        if ordinal is None:
            continue
        for idx, (start, end) in enumerate(ranges, start=1):
            if start <= ordinal <= end:
                chunk_map[routine["name"].upper()] = idx
                break
    return chunk_map


def _generate_module_doc_chunked(conn, member_name: str, system: str | None, rule_rows: list,
                                  out_path: Path, caller: ModelCaller, writing_rules: str,
                                  template: str, redact: Redactor, lexicon: dict[str, str] | None,
                                  max_attempts: int, chunk_size: int,
                                  prior_chunks: dict | None = None,
                                  index_template: str | None = None,
                                  sme_notes: dict | None = None,
                                  member_facts: MemberFacts | None = None) -> DocResult:
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
    pick up: only the chunk(s) whose own brief actually changed re-render.

    `member_facts`, when given, is a `MemberFacts` (built via
    `build_member_facts`) that some earlier step (`run_batch`'s own
    routing/hashing pass, which already computes a full, unchunked
    `module_brief` for every member before deciding chunked vs. not) has
    already built for this exact member -- passed through so this
    function's chunk loop doesn't gather the same whole-member facts a
    second time (issue #183 review feedback). When omitted (the default,
    e.g. `generate_module_doc` called directly, not via `run_batch`), this
    function builds its own, exactly as before this parameter existed."""
    # Resolved up front (not just before the chunk loop below) so `routines`
    # -- needed immediately after, for chunk-range computation and
    # chunk_map -- can come from `member_facts.routines` too, rather than
    # this function's own `fetch_routines()` call always running even when
    # a caller-supplied `member_facts` already has the same rows (issue
    # #183 review feedback).
    if member_facts is None:
        member_facts = build_member_facts(conn, member_name)
    routines = (
        member_facts.routines if isinstance(member_facts, MemberFacts)
        else fetch_routines(conn, rule_rows[0]["member_id"])
    )
    ranges = routine_aware_chunk_ranges(
        [r["line_no"] for r in rule_rows], routines, chunk_size,
    )
    chunk_count = len(ranges)
    # Source-density estimate per chunk (issue #105): routine-aware chunking
    # packs by rule count within routine boundaries, but that count says
    # nothing about how content-dense a chunk's *source* actually is -- two
    # chunks can carry the same rule
    # count while one's source sprawls across far more lines with far
    # deeper nesting, and that's exactly the kind of chunk that tends to
    # burn through its retries without anyone realising *why* until the
    # pattern's noticed across a whole run. Computed once, up front, from
    # facts already in `rule_rows` (line_no, depth) -- cheap, no extra
    # fact-store query -- and only rendered into a chunk's own diagnostics
    # if that chunk actually fails (see the `problems.append` below), never
    # into the brief itself.
    density_metrics = flag_density_outliers(chunk_density_metrics(
        [r["line_no"] for r in rule_rows], ranges, [r["depth"] for r in rule_rows],
    ))
    # Handed to every chunk's own brief (see module_brief's `chunk_map`
    # param) so a chunk whose own rules dispatch to a routine documented
    # elsewhere can name the specific chunk instead of leaving a dangling
    # "covered elsewhere" -- see _routine_chunk_map's own docstring for the
    # mapping's exact shape and why plan_batch's preview shares this same
    # helper (issue #214 review) instead of computing it separately.
    chunk_map = _routine_chunk_map(routines, rule_rows, ranges)
    input_tokens = output_tokens = 0
    duration_s = 0.0
    retries = 0
    chunk_entries: list[tuple[int, tuple[int, int], Path, DocResult]] = []
    problems: list[str] = []
    chunk_state: dict[str, dict] = {}

    chunk_width = len(str(chunk_count))
    expected_chunk_names = {
        f"{out_path.stem}.chunk{n:0{chunk_width}d}{out_path.suffix}" for n in range(1, chunk_count + 1)
    }
    _prune_stale_chunk_files(out_path, expected_chunk_names)
    # Issue #214: this member's own chunk-invariant shared prefix,
    # registered as a second, member-level cache_control breakpoint
    # (see build_member_prompt_cache_prefix/AnthropicCaller.set_member_
    # cache_prefixes) *before* the chunk loop below runs -- every chunk's
    # own build_prompt() call shares this exact leading text, so it's
    # computed once per member here, not once per chunk.
    #
    # Gated on `caller` actually exposing `set_member_cache_prefixes`
    # (AnthropicCaller, VertexCaller) -- checked *before* `shared_prefix` is
    # even computed, not just before registering it, so a caller without
    # this hook (ClaudeCLICaller, the fake-echo test caller) never gets
    # `shared_prefix` prepended into its own brief below either (Copilot
    # review on PR #215): with no cache breakpoint to make it a saving, that
    # prepend would be a pure duplicate-context/token-cost regression for
    # exactly those callers, and for ClaudeCLICaller specifically, a real
    # (uncached) change to what gets sent to the model on every chunk --
    # not the caching-only change this issue is scoped to.
    set_member_cache_prefixes = getattr(caller, "set_member_cache_prefixes", None)
    shared_prefix = (
        member_shared_prefix(member_facts, redact)
        if isinstance(member_facts, MemberFacts) and set_member_cache_prefixes is not None
        else None
    )
    if shared_prefix is not None:
        set_member_cache_prefixes([build_member_prompt_cache_prefix(shared_prefix)])
    # `member_facts` was already resolved above (before `routines`) --
    # every whole-member section module_brief() renders (interface, data
    # access, calls, inbound callers, gaps, ...) is identical across this
    # member's chunks, since none of it depends on rule_range, so sharing
    # one fact-gather here instead of letting each chunk's own
    # module_brief() call re-run those ~15 fact-store queries is the whole
    # point of this loop no longer calling module_brief() with facts=None.
    for i, (start, end) in enumerate(ranges, start=1):
        chunk_path = out_path.with_name(f"{out_path.stem}.chunk{i:0{chunk_width}d}{out_path.suffix}")
        chunk_brief = module_brief(
            conn, member_name, redact=redact, lexicon=lexicon,
            rule_range=(start, end), chunk_info=(i, chunk_count), chunk_map=chunk_map,
            sme_notes=sme_notes, facts=member_facts,
        )
        # `shared_prefix` (when this member resolved to a real MemberFacts)
        # is prepended ahead of every chunk's own module_brief() text, with
        # the same separator build_member_prompt_cache_prefix expects to
        # find -- this is what makes the registered member-level cache
        # prefix above an actual literal prefix of this brief, on every
        # chunk, not just a string that happens to be byte-identical
        # somewhere inside it. Deliberately redundant with what
        # module_brief() itself still renders in full below (issue #214's
        # "out of scope" note): the caching win comes from the cache *hit*
        # on this leading copy across chunks, not from removing these facts
        # from module_brief()'s own per-chunk text.
        brief = (
            shared_prefix + "\n\n---\n\n" + chunk_brief if shared_prefix is not None
            else chunk_brief
        )
        brief_hash = hashlib.sha256(brief.encode("utf-8")).hexdigest()
        result = None
        if _chunk_reuse_ok(conn, prior_chunks, i, brief_hash, chunk_path):
            result = DocResult(member_name, str(chunk_path), True, 0, 0, 0, [])
            logger.debug("%s: chunk %d/%d reused (unchanged)", member_name, i, chunk_count)
        if result is None:
            logger.info("%s: chunk %d/%d generating", member_name, i, chunk_count)
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
                logger.error(
                    "%s: chunk %d/%d model call failed: %s: %s",
                    member_name, i, chunk_count, exc.__class__.__name__, exc,
                    exc_info=True,
                )
                result = DocResult(
                    member_name, str(chunk_path), False, 1, 0, 0,
                    [f"model call failed: {exc.__class__.__name__}: {exc}"],
                )
        input_tokens += result.input_tokens
        output_tokens += result.output_tokens
        duration_s += result.duration_s
        retries += result.retries
        chunk_entries.append((i, (start, end), chunk_path, result))
        chunk_state[str(i)] = {"ok": result.ok, "brief_sha256": brief_hash}
        if not result.ok:
            density_note = format_density_note(density_metrics[i - 1])
            logger.warning(
                "%s: chunk %d/%d failed: %s -- %s", member_name, i, chunk_count,
                "; ".join(result.problems), density_note,
            )
            problems.append(
                f"chunk {i}/{chunk_count} ({chunk_path.name}) failed: "
                + "; ".join(result.problems) + f" -- {density_note}"
            )
        else:
            logger.debug("%s: chunk %d/%d complete", member_name, i, chunk_count)

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
                n_duration, n_retries = 0.0, 0
                reused = True
        if not reused:
            (narrative_ok, _, n_in, n_out, narrative_problems, sections,
             n_duration, n_retries) = _generate_module_index_narrative(
                conn, member_name, chunk_bodies, caller, writing_rules, index_template, out_path,
                assemble, max_attempts=max_attempts,
            )
        input_tokens += n_in
        output_tokens += n_out
        duration_s += n_duration
        retries += n_retries
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
        problems, chunked=True, chunk_state=chunk_state, duration_s=duration_s, retries=retries,
    )


def generate_module_doc(conn, member_name: str, out_path: Path, caller: ModelCaller,
                         writing_rules: str, template: str, redact: Redactor = NULL_REDACTOR,
                         max_attempts: int = 2, lexicon: dict[str, str] | None = None,
                         max_rules_per_call: int | None = None,
                         prior_chunks: dict | None = None,
                         index_template: str | None = None,
                         sme_notes: dict | None = None,
                         facts: MemberFacts | None = None) -> DocResult:
    """Single-member version of the harness: brief -> call -> validate ->
    retry once. Used directly for one-off generation and by run_batch's
    per-item work (with the model call itself dispatched to a thread pool
    by the caller).

    A member whose own rule_candidate count exceeds `max_rules_per_call`
    (default DEFAULT_MAX_RULES_PER_CALL) renders as several independent
    chunk documents instead -- see _generate_module_doc_chunked. The
    ambiguous-name and no-rule-candidate cases fall through to the
    original single-call path unchanged (module_brief already reports both
    as prose in the brief itself).

    `facts`, when given, is a `MemberFacts` (`build_member_facts`) already
    built for this exact member by an earlier step -- run_batch's own
    routing/hashing pass builds one for every member (to fingerprint its
    brief and decide chunked vs. not) before ever reaching this function,
    so passing it through here means a member that turns out to be chunked
    doesn't have its whole-member facts gathered a second time (issue #183
    review feedback). Reused on both branches below: the chunked path
    passes it straight through to `_generate_module_doc_chunked` as
    `member_facts`, and the non-chunked path passes it to `module_brief`
    itself (`facts=facts`) -- either way, this function never gathers
    whole-member facts itself when a caller already built them."""
    rows, ambiguous_libs = fetch_rule_candidate_rows(conn, member_name)
    threshold = _resolve_max_rules_per_call(max_rules_per_call)
    if not ambiguous_libs and rows and len(rows) > threshold:
        system = conn.execute(
            "SELECT system FROM member WHERE UPPER(name)=UPPER(?)", (member_name,)
        ).fetchone()
        return _generate_module_doc_chunked(
            conn, member_name, system["system"] if system else None, rows, out_path, caller,
            writing_rules, template, redact, lexicon, max_attempts, threshold,
            prior_chunks=prior_chunks, index_template=index_template, sme_notes=sme_notes,
            member_facts=facts,
        )

    brief = module_brief(conn, member_name, redact=redact, lexicon=lexicon, sme_notes=sme_notes, facts=facts)
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
    # Sum of every result's DocResult.duration_s -- total wall-clock time
    # spent across every model call this run made. Because to_run members
    # are dispatched concurrently (ThreadPoolExecutor), this is *not* the
    # run's own elapsed time -- it's the sum of each member's own call
    # time, which can exceed real elapsed time whenever concurrency > 1.
    # What it answers is "how much model-call time did this run actually
    # spend", the input a per-member average (total_duration_s / len(results))
    # or a "which members were slow" scan (sorting `results` by duration_s)
    # both build on -- issue #84.
    total_duration_s: float = 0.0
    # Sum of every result's DocResult.retries -- total transient-error
    # retries (issue #79) across every model call this run made. Distinct
    # from `retried` above, which counts members that needed a
    # validation-failure retry, a different and non-transient kind of
    # "tried again" (see DocResult.retries' own docstring note).
    total_retries: int = 0


def _corpus_signature(conn, redact: Redactor = NULL_REDACTOR,
                       lexicon: dict[str, str] | None = None,
                       sme_notes: dict | None = None,
                       extra: list[str] | None = None) -> str:
    """Fingerprint of every input to module_brief() that isn't the derive
    code itself: every source_file's (path, sha256, dialect_hash)
    (order-independent), the installed mfdoc version, and the effective
    redact/lexicon/sme_notes policy.

    `dialect_hash` (issue #194's per-source-file dialect-parser-code
    fingerprint, set by `cli.cmd_ingest`) is included alongside `sha256`
    for the same reason `sha256` is: a dialect parser fix applied in place,
    with no `__version__` bump, changes what `mfdoc ingest` puts in the
    fact store for that file without changing its path or content hash.
    Without folding this in too, a resumed `mfdoc batch`/`test-batch` could
    read `_corpus_sha256` as unchanged and skip re-deriving a brief whose
    underlying facts have, in fact, changed.

    A per-source_file-only check isn't safe on its own even for the source
    dimension: module_brief() also pulls in facts owned by other members
    (inbound callers, copycode-inherited rules), so a member's brief can
    change even when its own file didn't -- hashing the whole corpus at
    once is what makes it safe. redact/lexicon/sme_notes matter too: all
    three come from project.yml (sme_notes indirectly, via the file it
    points at), not from anything a source_file hash can see, so a policy
    or sme-notes.md edit with no source changes must still be able to
    invalidate this -- otherwise the corpus-level fast path in run_batch
    would treat an sme-notes.md edit as "nothing changed" and never even
    reach the per-member brief-hash check that would otherwise catch it.

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
    rows = conn.execute("SELECT path, sha256, dialect_hash FROM source_file ORDER BY path").fetchall()
    digest = hashlib.sha256()
    digest.update(__version__.encode("utf-8"))
    digest.update(b"\x00")
    for r in rows:
        digest.update(r["path"].encode("utf-8"))
        digest.update(b"\x00")
        digest.update(r["sha256"].encode("utf-8"))
        digest.update(b"\x00")
        digest.update((r["dialect_hash"] or "").encode("utf-8"))
        digest.update(b"\x00")
    digest.update(redact.signature().encode("utf-8"))
    digest.update(b"\x00")
    for key in sorted(lexicon or {}):
        digest.update(key.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(lexicon[key].encode("utf-8"))
        digest.update(b"\x00")
    # Notes is keyed by lowercased member/entity name, with the general
    # section keyed by `None` -- sorted with `None` first (via the `(key
    # is not None, key)` tuple) so the digest is deterministic regardless
    # of dict insertion order without needing every key to be comparable
    # to every other (str vs. None can't otherwise be sorted directly).
    for key in sorted((sme_notes or {}), key=lambda k: (k is not None, k)):
        digest.update((key or "").encode("utf-8"))
        digest.update(b"\x00")
        digest.update(sme_notes[key].encode("utf-8"))
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
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=state_path.parent,
        prefix=f".{state_path.name}.", suffix=".tmp", delete=False,
    )
    tmp_name = tmp.name
    try:
        with tmp:
            tmp.write(json.dumps(state, indent=2))
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


def _apply_cache_prefixes(caller: ModelCaller, writing_rules: str, template: str,
                           index_template: str | None) -> None:
    """Hand `caller` this run's stable prompt prefixes (issue #159), once,
    up front -- every build_prompt/build_reconciliation_prompt call in one
    project run shares the same writing_rules/template/index_template text,
    so there's no reason to recompute or re-send this per call. Only a
    caller that opts in by exposing `set_cache_prefixes` (AnthropicCaller,
    VertexCaller) is touched at all -- `getattr(..., None)` leaves
    ClaudeCLICaller and the fake-echo test caller (neither has any such
    method, nor any equivalent to `cache_control`) completely untouched."""
    set_cache_prefixes = getattr(caller, "set_cache_prefixes", None)
    if set_cache_prefixes is None:
        return
    set_cache_prefixes([
        build_prompt_cache_prefix(writing_rules, template),
        build_reconciliation_prompt_cache_prefix(writing_rules, index_template),
    ])


def run_batch(conn, members: list[str], out_dir: Path, caller: ModelCaller,
              writing_rules: str, template: str, redact: Redactor = NULL_REDACTOR,
              concurrency: int = 4, state_path: Path | None = None,
              cost_per_mtok_in: float | None = None, cost_per_mtok_out: float | None = None,
              lexicon: dict[str, str] | None = None,
              max_rules_per_call: int | None = None,
              index_template: str | None = None,
              sme_notes: dict | None = None,
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
    _apply_cache_prefixes(caller, writing_rules, template, index_template)
    state = _load_state(state_path) if state_path else {}
    corpus_sig = (
        _corpus_signature(conn, redact, lexicon, sme_notes, extra=[str(threshold)]) if state_path else None
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
    # Each entry's MemberFacts (issue #183) is kept alive from this routing
    # pass until the sequential to_run_chunked loop near the end of this
    # function consumes it -- i.e. for as long as the to_run thread pool
    # below is running, for every member that turns out to need chunking.
    # This trades some peak memory (one MemberFacts per pending chunked
    # member, held simultaneously) for not re-gathering those same facts a
    # second time once that member's chunk loop actually starts (Copilot
    # review on PR #206) -- the same "hold what you'll need until you use
    # it" shape `briefs` above already has for non-chunked members' full
    # rendered brief text. Accepted as-is for now (a chunked member's
    # MemberFacts is one member's worth of raw rows, not multiplied by its
    # chunk count); revisit only if a real run's chunked-member count made
    # this measurably worse than `briefs`' existing footprint.
    to_run_chunked: list[tuple[str, str, Path, str, MemberFacts | str]] = []

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
            logger.debug("skip %s: unchanged (corpus signature match, resumed)", name)
            results.append(_skip_result(name, out_path, prior))
            continue

        # Always the member's *full* brief, even for a member that ends up
        # chunked below -- it's only ever used as a content fingerprint for
        # resume/skip, never sent to the model as-is (the chunked path
        # builds its own per-chunk briefs from scratch). Built via
        # build_member_facts() + module_brief(facts=...) rather than a bare
        # module_brief(...) call so the same whole-member facts can be
        # threaded through to the chunked path below instead of that path
        # gathering them a second time (issue #183 review feedback).
        member_facts = build_member_facts(conn, name)
        brief = module_brief(conn, name, redact=redact, lexicon=lexicon, sme_notes=sme_notes, facts=member_facts)
        brief_hash = hashlib.sha256(f"{brief}\x00{threshold}".encode("utf-8")).hexdigest()
        if prior_ok and prior.get("brief_sha256") == brief_hash:
            logger.debug("skip %s: unchanged (brief hash match, resumed)", name)
            results.append(_skip_result(name, out_path, prior))
            continue

        rows, ambiguous_libs = fetch_rule_candidate_rows(conn, name)
        if not ambiguous_libs and rows and len(rows) > threshold:
            to_run_chunked.append((name, brief_hash, out_path, state_key, member_facts))
        else:
            briefs[state_key] = brief
            to_run.append((name, brief_hash, out_path, state_key))

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {
            pool.submit(_timed_call, caller, build_prompt(briefs[state_key], writing_rules, template)):
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
                response, elapsed = fut.result()
            except Exception as exc:
                logger.error(
                    "%s: model call failed: %s: %s", name, exc.__class__.__name__, exc,
                    exc_info=True,
                )
                result = DocResult(
                    name, str(out_path), False, 1, 0, 0,
                    [f"model call failed: {exc.__class__.__name__}: {exc}"],
                )
                results.append(result)
                state[state_key] = {"ok": False, "attempts": 1, "brief_sha256": brief_hash}
                if state_path:
                    _save_state(state_path, state)
                continue

            input_tokens, output_tokens = response.input_tokens, response.output_tokens
            duration_s = elapsed
            retries = response.retries

            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(_fix_generated_by_version(response.text), encoding="utf-8")
            validation = validate_doc(conn, out_path)
            attempts = 1
            if not validation["ok"]:
                logger.warning(
                    "%s: validation failed (%d problem(s)), retrying once",
                    name, len(validation["problems"]),
                )
                retry_note = _retry_note(validation["problems"])
                retry_prompt = build_prompt(briefs[state_key], writing_rules, template, retry_note)
                try:
                    retry_response, retry_elapsed = _timed_call(caller, retry_prompt)
                except Exception as exc:
                    # attempts=2, not 1: the first call already completed
                    # (it just failed validation), and this retry call was
                    # itself attempted -- matching the normal
                    # retry-on-validation-failure path's attempts=2 below,
                    # so BatchSummary/resume state isn't misreported as a
                    # single-attempt failure. See issue #78 review.
                    logger.error(
                        "%s: retry model call failed: %s: %s",
                        name, exc.__class__.__name__, exc,
                        exc_info=True,
                    )
                    result = DocResult(
                        name, str(out_path), False, 2, input_tokens, output_tokens,
                        validation["problems"] + [f"retry model call failed: {exc.__class__.__name__}: {exc}"],
                        duration_s=duration_s, retries=retries,
                    )
                    results.append(result)
                    state[state_key] = {"ok": False, "attempts": 2, "brief_sha256": brief_hash}
                    if state_path:
                        _save_state(state_path, state)
                    continue
                input_tokens += retry_response.input_tokens
                output_tokens += retry_response.output_tokens
                duration_s += retry_elapsed
                retries += retry_response.retries
                out_path.write_text(_fix_generated_by_version(retry_response.text), encoding="utf-8")
                validation = validate_doc(conn, out_path)
                attempts = 2

            result = DocResult(
                name, str(out_path), validation["ok"], attempts, input_tokens, output_tokens,
                validation.get("problems", []), duration_s=duration_s, retries=retries,
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

    for name, brief_hash, out_path, state_key, member_facts in to_run_chunked:
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
                index_template=index_template, sme_notes=sme_notes,
                # Reuse the MemberFacts the routing/hashing pass above
                # already built for this member instead of gathering the
                # same whole-member facts a second time (issue #183 review
                # feedback) -- unless that pass hit an ambiguous/not-found
                # lookup (a str, not a MemberFacts), in which case there's
                # nothing valid to reuse and generate_module_doc must build
                # its own.
                facts=member_facts if isinstance(member_facts, MemberFacts) else None,
            )
        except Exception as exc:
            logger.error(
                "%s: chunked generation failed: %s: %s", name, exc.__class__.__name__, exc,
                exc_info=True,
            )
            result = DocResult(
                name, str(out_path), False, 0, 0, 0,
                [f"model call failed: {exc.__class__.__name__}: {exc}"], chunked=True, chunk_state=prior_chunks,
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
        total_duration_s=sum(r.duration_s for r in results),
        total_retries=sum(r.retries for r in results),
    )


@dataclass
class MemberPlan:
    """One member's resume estimate from plan_batch -- what run_batch would
    actually do for this member, computed the same way but with no model
    call and no write. `status` is one of:

    - "skip": corpus- or member-level resume hit -- run_batch would call
      `module_brief()` at most once (member-level check) and no model.
    - "render": a normal (non-chunked) member that will make exactly one
      model call (plus a possible validation retry).
    - "chunked": an over-threshold member rendered as several chunks --
      `chunk_count`/`chunks_reusable` describe how many of those chunks
      would actually need a model call versus be reused from prior state.
    """
    member: str
    status: str
    chunk_count: int | None = None
    chunks_reusable: int | None = None
    narrative_reusable: bool | None = None

    @property
    def chunks_to_render(self) -> int | None:
        if self.chunk_count is None or self.chunks_reusable is None:
            return None
        return self.chunk_count - self.chunks_reusable


@dataclass
class BatchPlan:
    """Whole-run resume estimate from plan_batch -- see that function's
    docstring. Aggregates MemberPlan entries into the totals cli.py prints
    before a real `mfdoc batch` run spends any model calls (issue #160)."""
    corpus_unchanged: bool
    members: list[MemberPlan]

    @property
    def members_total(self) -> int:
        return len(self.members)

    @property
    def members_skip(self) -> int:
        return sum(1 for m in self.members if m.status == "skip")

    @property
    def members_render(self) -> int:
        return sum(1 for m in self.members if m.status == "render")

    @property
    def members_chunked(self) -> int:
        return sum(1 for m in self.members if m.status == "chunked")

    @property
    def chunks_total(self) -> int:
        return sum(m.chunk_count or 0 for m in self.members if m.status == "chunked")

    @property
    def chunks_reusable(self) -> int:
        return sum(m.chunks_reusable or 0 for m in self.members if m.status == "chunked")

    @property
    def chunks_to_render(self) -> int:
        return self.chunks_total - self.chunks_reusable


def plan_batch(conn, members: list[str], out_dir: Path,
               redact: Redactor = NULL_REDACTOR,
               state_path: Path | None = None,
               lexicon: dict[str, str] | None = None,
               max_rules_per_call: int | None = None,
               sme_notes: dict | None = None,
               writing_rules: str | None = None,
               index_template: str | None = None,
               member_cache_capable: bool = False) -> BatchPlan:
    """Cheap, local, no-model-call preview of what `run_batch` over these
    same arguments would actually do -- every brief a real run would render
    is computed and hashed exactly the same way (corpus-level check, then
    per-member brief hash, then -- for an over-threshold member -- each
    chunk's own brief hash via the same `_chunk_reuse_ok` a real chunked
    render uses), but no model is ever called and nothing is written to
    disk. Meant to be read before committing to a real `mfdoc batch` run,
    the same way `mfdoc coverage`/`mfdoc gate` are read before generating
    docs at all.

    This exists because a "resume" can silently be a full regeneration in
    disguise: `_rule_id`'s `BR-nnn` numbering (see citations.py) -- and
    hence a chunk's own rendered brief text -- is a position in that
    member's whole rule list, so a single rule added or removed anywhere
    earlier in the same member renumbers every later rule and changes every
    later chunk's brief hash, even when that chunk's own routine's facts
    are otherwise unchanged (issue #160's motivating case). There is no way
    to tell from the CLI invocation alone whether a given resume will
    actually hit the per-chunk cache or fully re-render every chunk --
    this function does the same (cheap, deterministic) hashing work a real
    run would, up front, so that can be seen before it costs anything.

    `writing_rules`/`index_template` are optional: when given, a chunked
    member whose every chunk is reusable also gets a `narrative_reusable`
    verdict (whether the whole-module reconciliation call would also be
    skipped) -- omitted (left None) when not given, since computing it
    needs the same inputs a real reconciliation call would build its prompt
    from.

    `member_cache_capable` (issue #214): whether the caller a real run
    would actually use exposes `set_member_cache_prefixes` (AnthropicCaller,
    VertexCaller -- not ClaudeCLICaller or the fake-echo test caller). This
    function never builds a caller itself (that's the whole point -- no
    `--model`/`--provider` credentials needed for a preview), so it can't
    detect this on its own; a caller of `plan_batch` that already knows
    which provider a real run would use (cli.py passes `args.provider in
    ("anthropic", "vertex")`) should pass it through, so a chunked member's
    preview hash matches whether a real run would actually prepend/register
    a member-level shared prefix -- see `_generate_module_doc_chunked`'s
    matching gate. Defaults to False (assume no member-level tier), the
    safe direction: understating `chunks_reusable` costs an unnecessary
    re-render; overstating it would wrongly report a stale chunk as
    reusable."""
    threshold = _resolve_max_rules_per_call(max_rules_per_call)
    state = _load_state(state_path) if state_path else {}
    corpus_sig = (
        _corpus_signature(conn, redact, lexicon, sme_notes, extra=[str(threshold)]) if state_path else None
    )
    corpus_unchanged = bool(state_path) and state.get("_corpus_sha256") == corpus_sig

    plans: list[MemberPlan] = []
    for name in members:
        subdir = _output_subdir(conn, name)
        out_path = out_dir / subdir / f"{name}.md"
        state_key = f"{subdir.as_posix()}/{name}"
        prior = state.get(state_key)
        prior_ok = isinstance(prior, dict) and prior.get("ok") and out_path.exists()

        if corpus_unchanged and prior_ok:
            plans.append(MemberPlan(name, "skip"))
            continue

        # Built once via build_member_facts()/module_brief(facts=...), not a
        # bare module_brief(...) call, so the same whole-member facts can be
        # reused by the chunk loop below instead of it gathering them a
        # second time (issue #183 review feedback) -- this initial call
        # already builds them internally when facts=None, so the only
        # change here is capturing that same MemberFacts for reuse.
        member_facts = build_member_facts(conn, name)
        brief = module_brief(conn, name, redact=redact, lexicon=lexicon, sme_notes=sme_notes, facts=member_facts)
        brief_hash = hashlib.sha256(f"{brief}\x00{threshold}".encode("utf-8")).hexdigest()
        if prior_ok and prior.get("brief_sha256") == brief_hash:
            plans.append(MemberPlan(name, "skip"))
            continue

        rows, ambiguous_libs = fetch_rule_candidate_rows(conn, name)
        if ambiguous_libs or not rows or len(rows) <= threshold:
            plans.append(MemberPlan(name, "render"))
            continue

        # Reuse member_facts.routines (already fetched above) rather than a
        # second fetch_routines() call for the same member (issue #183
        # review feedback) -- falls back to a fresh fetch only in the
        # (here, already-ruled-out-in-practice) case where member_facts
        # turned out to be the "ambiguous"/"not found" str, not a
        # MemberFacts, since that carries no routines to reuse.
        routines = (
            member_facts.routines if isinstance(member_facts, MemberFacts)
            else fetch_routines(conn, rows[0]["member_id"])
        )
        ranges = routine_aware_chunk_ranges([r["line_no"] for r in rows], routines, threshold)
        chunk_count = len(ranges)
        chunk_width = len(str(chunk_count))
        # Issue #214 review: a real chunked render passes chunk_map to every
        # chunk's own module_brief() call (see _generate_module_doc_chunked)
        # -- this preview must too, or a member with internal routines gets
        # a chunk_brief missing the chunk_map paragraph/`[documented in
        # chunk N]` annotations _generate_module_doc_chunked's own brief
        # actually carries, hashing something a real render would never
        # produce. Same `_routine_chunk_map` helper both use, so neither can
        # drift from the other.
        chunk_map = _routine_chunk_map(routines, rows, ranges)
        prior_chunks = prior.get("chunks") if isinstance(prior, dict) else None
        chunks_reusable = 0
        chunk_bodies: list[tuple[int, str]] = []
        # A real chunked render's own brief_hash (see
        # _generate_module_doc_chunked) is computed over `shared_prefix +
        # "\n\n---\n\n" + chunk_brief`, not `chunk_brief` alone -- this
        # preview must hash the identical string, or its chunks_reusable
        # count silently drifts from what a real run would actually do
        # (exactly the class of bug this function's own docstring exists to
        # prevent). Computed once per member, same as the real render, and
        # only when the run this previews would actually register/prepend
        # it -- see _generate_module_doc_chunked's matching
        # caller_supports_member_cache gate; a caller with no
        # set_member_cache_prefixes hook never gets this prepended in a
        # real run, so this preview must not hash as if it would.
        shared_prefix = (
            member_shared_prefix(member_facts, redact)
            if isinstance(member_facts, MemberFacts) and member_cache_capable
            else None
        )
        for i, (start, end) in enumerate(ranges, start=1):
            chunk_path = out_path.with_name(f"{out_path.stem}.chunk{i:0{chunk_width}d}{out_path.suffix}")
            chunk_brief = module_brief(
                conn, name, redact=redact, lexicon=lexicon,
                rule_range=(start, end), chunk_info=(i, chunk_count), chunk_map=chunk_map,
                sme_notes=sme_notes, facts=member_facts,
            )
            preview_brief = (
                shared_prefix + "\n\n---\n\n" + chunk_brief if shared_prefix is not None else chunk_brief
            )
            chunk_hash = hashlib.sha256(preview_brief.encode("utf-8")).hexdigest()
            if _chunk_reuse_ok(conn, prior_chunks, i, chunk_hash, chunk_path):
                chunks_reusable += 1
                if writing_rules is not None and chunk_path.exists():
                    chunk_bodies.append((i, chunk_path.read_text(encoding="utf-8")))

        narrative_reusable = None
        if writing_rules is not None and chunks_reusable == chunk_count and len(chunk_bodies) == chunk_count:
            narrative_input_hash = hashlib.sha256(
                "\x00".join(f"{index}:{body}" for index, body in chunk_bodies).encode("utf-8")
                + b"\x00" + writing_rules.encode("utf-8")
                + b"\x00" + (index_template or "").encode("utf-8")
            ).hexdigest()
            prior_narrative = (prior_chunks or {}).get("_narrative") if isinstance(prior_chunks, dict) else None
            narrative_reusable = bool(
                isinstance(prior_narrative, dict) and prior_narrative.get("ok")
                and prior_narrative.get("input_sha256") == narrative_input_hash
                and isinstance(prior_narrative.get("sections"), dict)
                and set(prior_narrative["sections"]) == set(NARRATIVE_SECTIONS)
            )

        plans.append(MemberPlan(
            name, "chunked", chunk_count=chunk_count, chunks_reusable=chunks_reusable,
            narrative_reusable=narrative_reusable,
        ))

    return BatchPlan(corpus_unchanged=corpus_unchanged, members=plans)

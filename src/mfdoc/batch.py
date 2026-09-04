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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import __version__
from .brief import fetch_rule_candidate_rows, module_brief
from .redact import NULL_REDACTOR, Redactor
from .validate import BR_REF, _split_frontmatter, validate_doc

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
# nowhere near complete, and nothing before this caught that.
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


def _chunk_ranges(total: int, size: int) -> list[tuple[int, int]]:
    """1-based, inclusive `(start, end)` ranges of `size` over `total`
    items -- e.g. `_chunk_ranges(5, 2) == [(1, 2), (3, 4), (5, 5)]`."""
    return [(i + 1, min(i + size, total)) for i in range(0, total, size)]


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
        out_path.write_text(response.text, encoding="utf-8")
        result = validate_doc(conn, out_path)
        if result["ok"]:
            return DocResult(member_name, str(out_path), True, attempt, input_tokens, output_tokens, [])
        problems = result["problems"]
        retry_note = "\n".join(f"- {p}" for p in problems)
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


def _render_module_chunk_index(member_name: str, system: str | None,
                                chunk_entries: list[tuple[int, tuple[int, int], Path, DocResult]],
                                confidence: dict[str, int]) -> str:
    """The index document at a chunked member's normal out_path -- built
    here, deterministically, never model-generated, for the same reason
    testbatch.py's _render_chunk_index is: the thing chunking guards
    against is a model response silently losing content partway through,
    so the summary of what each chunk covered must not be asked of a model
    itself. Every field is either a static convention (doc_type,
    generated_by) or aggregated from already-validated chunk documents
    (confidence_summary, the rule IDs each chunk's body actually cites) --
    nothing here is invented."""
    today = datetime.date.today().isoformat()
    covered_ids: list[str] = []
    lines_chunks = []
    for index, (start, end), path, result in chunk_entries:
        status = "OK" if result.ok else "FAILED: " + "; ".join(result.problems)[:200]
        lines_chunks.append(
            f"- [{path.name}](./{path.name}) -- rules {start}-{end} -- {status}"
        )
        if result.ok:
            body = path.read_text(encoding="utf-8")
            covered_ids.extend(sorted({
                f"{m.group('member').upper()}:BR-{m.group('n')}" for m in BR_REF.finditer(body)
            }))

    fm = "\n".join([
        "---",
        f'title: "{member_name} — module documentation, chunked"',
        "doc_type: module",
        f'system: "{system or "unknown"}"',
        f'module: "{member_name}"',
        "generated_by: legacy-functional-docs 0.1.0",
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
    body = "\n".join([
        "",
        f"# {member_name} — module documentation, chunked",
        "",
        f"This member's business-rule set was rendered as {len(chunk_entries)} separate "
        "documents rather than one -- a single completion this large risks silently "
        "truncating before it covers every rule.",
        "",
        "## Chunks",
        "",
        *lines_chunks,
        "",
        "## Business rules covered",
        "",
        *[f"- {sid}" for sid in sorted(set(covered_ids))],
        "",
    ])
    return fm + body


def _generate_module_doc_chunked(conn, member_name: str, system: str | None, rule_count: int,
                                  out_path: Path, caller: ModelCaller, writing_rules: str,
                                  template: str, redact: Redactor, lexicon: dict[str, str] | None,
                                  max_attempts: int, chunk_size: int) -> DocResult:
    """Render one member as several independent chunk documents plus a
    deterministic index doc at `out_path`, instead of asking one completion
    to cover the member's whole rule set. Each chunk goes through the exact
    same call/validate/retry path (_generate_module_doc_from_brief) a
    normal single-call member does, scoped to `chunk_size` rules via
    module_brief's `rule_range` -- so one bad chunk retries and reports on
    its own, rather than forcing a full-member re-generation, and no
    chunk's prompt asks for any more rules than a normal small member's."""
    ranges = _chunk_ranges(rule_count, chunk_size)
    chunk_count = len(ranges)
    input_tokens = output_tokens = 0
    chunk_entries: list[tuple[int, tuple[int, int], Path, DocResult]] = []
    problems: list[str] = []

    for i, (start, end) in enumerate(ranges, start=1):
        chunk_path = out_path.with_name(f"{out_path.stem}.chunk{i}{out_path.suffix}")
        brief = module_brief(
            conn, member_name, redact=redact, lexicon=lexicon,
            rule_range=(start, end), chunk_info=(i, chunk_count),
        )
        result = _generate_module_doc_from_brief(
            conn, member_name, brief, chunk_path, caller, writing_rules, template,
            max_attempts=max_attempts,
        )
        input_tokens += result.input_tokens
        output_tokens += result.output_tokens
        chunk_entries.append((i, (start, end), chunk_path, result))
        if not result.ok:
            problems.append(f"chunk {i}/{chunk_count} ({chunk_path.name}) failed: " + "; ".join(result.problems))

    confidence = _aggregate_chunk_confidence([p for _, _, p, r in chunk_entries if r.ok])
    index_text = _render_module_chunk_index(member_name, system, chunk_entries, confidence)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(index_text, encoding="utf-8")

    # The index is built deterministically, not model-generated, but that's
    # not a reason to skip checking it -- validate_doc is the same ground
    # truth every chunk (and every other generated doc in this tool) is
    # judged against, and a bug in _render_module_chunk_index deserves the
    # same loud, reported failure a bad model response gets, not a silent
    # ok=True because no chunk happened to fail.
    index_validation = validate_doc(conn, out_path)
    if not index_validation["ok"]:
        problems = problems + [f"index document: {p}" for p in index_validation["problems"]]

    return DocResult(
        member_name, str(out_path), not problems, chunk_count, input_tokens, output_tokens,
        problems, chunked=True,
    )


def generate_module_doc(conn, member_name: str, out_path: Path, caller: ModelCaller,
                         writing_rules: str, template: str, redact: Redactor = NULL_REDACTOR,
                         max_attempts: int = 2, lexicon: dict[str, str] | None = None,
                         max_rules_per_call: int | None = None) -> DocResult:
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
            conn, member_name, system["system"] if system else None, len(rows), out_path, caller,
            writing_rules, template, redact, lexicon, max_attempts, threshold,
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
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _skip_result(name: str, out_path: Path, prior: dict) -> DocResult:
    """A prior successful run's result, reused as-is without regenerating anything."""
    return DocResult(name, str(out_path), True, prior.get("attempts", 1), 0, 0, [], skipped=True)


def run_batch(conn, members: list[str], out_dir: Path, caller: ModelCaller,
              writing_rules: str, template: str, redact: Redactor = NULL_REDACTOR,
              concurrency: int = 4, state_path: Path | None = None,
              cost_per_mtok_in: float | None = None, cost_per_mtok_out: float | None = None,
              lexicon: dict[str, str] | None = None,
              max_rules_per_call: int | None = None,
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
    results: list[DocResult] = []
    briefs: dict[str, str] = {}
    to_run: list[tuple[str, str, Path]] = []
    to_run_chunked: list[tuple[str, str, Path]] = []

    state_keys: dict[str, str] = {}
    for name in members:
        subdir = _output_subdir(conn, name)
        out_path = out_dir / subdir / f"{name}.md"
        # Keyed by subdir+name, not bare name: two batchable members can
        # share a name across libraries/dialects (member.name is only
        # unique together with library+dialect), and each now gets its own
        # output path -- state must track them separately too, or one's
        # resume state would silently overwrite the other's.
        state_key = f"{subdir.as_posix()}/{name}"
        state_keys[name] = state_key
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
            to_run_chunked.append((name, brief_hash, out_path))
        else:
            briefs[name] = brief
            to_run.append((name, brief_hash, out_path))

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {
            pool.submit(caller, build_prompt(briefs[name], writing_rules, template)): (name, brief_hash, out_path)
            for name, brief_hash, out_path in to_run
        }
        for fut in as_completed(futures):
            name, brief_hash, out_path = futures[fut]
            response = fut.result()
            input_tokens, output_tokens = response.input_tokens, response.output_tokens

            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(response.text, encoding="utf-8")
            validation = validate_doc(conn, out_path)
            attempts = 1
            if not validation["ok"]:
                retry_note = "\n".join(f"- {p}" for p in validation["problems"])
                retry_prompt = build_prompt(briefs[name], writing_rules, template, retry_note)
                retry_response = caller(retry_prompt)
                input_tokens += retry_response.input_tokens
                output_tokens += retry_response.output_tokens
                out_path.write_text(retry_response.text, encoding="utf-8")
                validation = validate_doc(conn, out_path)
                attempts = 2

            result = DocResult(
                name, str(out_path), validation["ok"], attempts, input_tokens, output_tokens,
                validation.get("problems", []),
            )
            results.append(result)
            state[state_keys[name]] = {"ok": result.ok, "attempts": attempts, "brief_sha256": brief_hash}

    for name, brief_hash, out_path in to_run_chunked:
        result = generate_module_doc(
            conn, name, out_path, caller, writing_rules, template, redact=redact,
            lexicon=lexicon, max_rules_per_call=threshold,
        )
        results.append(result)
        state[state_keys[name]] = {"ok": result.ok, "attempts": result.attempts, "brief_sha256": brief_hash}

    if state_path:
        state["_corpus_sha256"] = corpus_sig
        _save_state(state_path, state)

    total_in = sum(r.input_tokens for r in results)
    total_out = sum(r.output_tokens for r in results)
    cost = None
    if cost_per_mtok_in is not None and cost_per_mtok_out is not None:
        cost = (total_in / 1_000_000) * cost_per_mtok_in + (total_out / 1_000_000) * cost_per_mtok_out

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

"""Test-render — the narrate stage for generated tests (Option C: batch,
formulaic, one member's worth of judgement-light rendering per call).

Mirrors batch.py's harness deliberately: test_case_brief() takes the place
of module_brief() as the only input the model sees, and the output is still
a Markdown document (front matter + a fenced code block). Validated with
`validate_test_doc`, not the plain `validate_doc` module docs use -- a
generated test file carries two things a module doc doesn't (`language`/
`framework` front matter, bare `MEMBER:BR-nnn` scenario references) that
`validate_doc` alone doesn't check, and this harness's retry-on-failure loop
and resumable "ok" state need to see those problems the same run they
happen, not only on a later, separate `mfdoc test-validate`.
"""

from __future__ import annotations

import datetime
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .batch import ModelCaller, DocResult
from .batch import _corpus_signature as _base_corpus_signature
from .batch import _load_state, _output_subdir, _save_state, _skip_result
from .redact import NULL_REDACTOR, Redactor
from .testlang import sidecar_path_for
from .testplan import fetch_test_case_rows, test_case_brief, test_case_brief_chunk
from .validate import BR_REF, _split_frontmatter, validate_test_doc

# A member whose test_case set exceeds this gets rendered as several
# independent chunk documents instead of one (see
# generate_member_test_doc/_generate_member_test_doc_chunked below) --
# calibratable per-project via options.testgen.max_scenarios_per_call, same
# pattern as --claude-code-timeout's DEFAULT_TIMEOUT_S. Motivated by a real
# client member (hundreds of test_case rows): a single non-streaming
# completion asked for that much structured output returned a "successful"
# (exit 0, no error) response whose visible text was silently truncated
# well before the closing fence.
DEFAULT_MAX_SCENARIOS_PER_CALL = 150


def _resolve_max_scenarios_per_call(max_scenarios_per_call: int | None) -> int:
    """`None` means "not configured" -- fall back to the default. Anything
    else must be a positive int: `or DEFAULT_MAX_SCENARIOS_PER_CALL` would
    treat an explicit `0` the same as "not configured" (0 is falsy) and
    silently substitute the default instead of respecting it or rejecting
    it, masking a real misconfiguration either way."""
    if max_scenarios_per_call is None:
        return DEFAULT_MAX_SCENARIOS_PER_CALL
    if max_scenarios_per_call <= 0:
        raise ValueError(
            f"options.testgen.max_scenarios_per_call must be a positive integer, "
            f"got {max_scenarios_per_call!r}"
        )
    return max_scenarios_per_call


def extract_code_fence(body: str, language: str) -> str | None:
    """The contents of the single ```<language> ... ``` fence in `body`, or
    None if the count isn't exactly one. Deliberately conservative: more
    than one fence means the document doesn't match
    reference/test-writing-rules.md's single-fence contract, and this must
    not guess which one is "the" test file. This is a genuinely different
    job from validate.py's `_logical_units`/`SKIP_BLOCK` (which discard
    fence content while checking prose outside it), not a refactor of it --
    this one has to capture the content, not skip past it."""
    pattern = re.compile(r"```" + re.escape(language) + r"\n(.*?)```", re.S)
    matches = pattern.findall(body)
    if len(matches) != 1:
        return None
    return matches[0]


def write_test_doc_with_sidecar(out_path: Path, doc_text: str, language: str) -> Path | None:
    """Given a response that has already validated ok, split its one code
    fence out to a sibling source file (`{member}.py`/`{member}.java`, per
    `language`) and rewrite `out_path` to reference it plus a `## Scenarios
    covered` manifest instead of embedding the fence -- the manifest is
    what lets `validate_test_doc` keep checking every MEMBER:BR-nnn
    reference once the actual code has moved somewhere its body-only scan
    would no longer see.

    Returns the sidecar path written, or None if no split was performed
    (unrecognised language, front matter missing, fence not exactly one,
    or no scenario references found in it) -- `out_path` is left completely
    untouched in every None case, so a doc that doesn't fit this shape just
    keeps today's embedded-fence behaviour."""
    sidecar_path = sidecar_path_for(out_path, language)
    if sidecar_path is None or not doc_text.startswith("---"):
        return None
    parts = doc_text.split("---", 2)
    if len(parts) < 3:
        return None
    front_matter_block, body = parts[1], parts[2]

    code = extract_code_fence(body, language)
    if code is None:
        return None
    scenario_ids = sorted({
        f"{m.group('member').upper()}:BR-{m.group('n')}" for m in BR_REF.finditer(code)
    })
    if not scenario_ids:
        return None

    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(code, encoding="utf-8")

    fence_pattern = re.compile(r"```" + re.escape(language) + r"\n.*?```", re.S)
    prose = fence_pattern.sub(
        f"See [`{sidecar_path.name}`](./{sidecar_path.name}) for the generated test source.",
        body, count=1,
    ).rstrip()
    manifest = "\n\n## Scenarios covered\n\n" + "\n".join(f"- {sid}" for sid in scenario_ids) + "\n"
    out_path.write_text(f"---{front_matter_block}---{prose}{manifest}", encoding="utf-8")
    return sidecar_path


def select_test_batch_members(conn) -> list[str]:
    """Members with at least one derived test_case row -- run `mfdoc
    test-plan` first; this never derives facts itself."""
    rows = conn.execute(
        """
        SELECT DISTINCT m.name FROM test_case tc JOIN member m ON m.id = tc.member_id
         ORDER BY m.name
        """
    ).fetchall()
    return [r["name"] for r in rows]


def build_test_prompt(brief: str, writing_rules: str, template: str, language: str,
                       framework: str, retry_note: str | None = None) -> str:
    parts = [
        f"You are writing first-draft {language}/{framework} tests for one legacy "
        "mainframe module, from a fact brief that already cites every scenario back "
        "to source. Follow the writing rules and template exactly. Never assert a "
        "consequence that isn't in the brief's cited source excerpt -- write up to "
        "the branch decision and mark it `unresolved` instead of inventing one. "
        "Output only the completed document (front matter + one fenced code block), "
        "nothing else.",
        "# Writing rules\n\n" + writing_rules,
        "# Template\n\n" + template,
        "# Test brief\n\n" + brief,
    ]
    if retry_note:
        parts.append(
            "# Previous attempt failed validation\n\n" + retry_note
            + "\n\nFix these problems and resend the complete document."
        )
    return "\n\n---\n\n".join(parts)


def _generate_test_doc_from_brief(conn, member_name: str, brief: str, language: str, framework: str,
                                   out_path: Path, caller: ModelCaller, writing_rules: str,
                                   template: str, max_attempts: int = 2) -> DocResult:
    """Call -> validate -> retry-once loop, given an already-built brief --
    the part of generate_member_test_doc that doesn't care whether `brief`
    covers a member's whole test_case set or just one chunk of it, shared
    by the plain single-call path and _generate_member_test_doc_chunked's
    per-chunk calls below."""
    retry_note = None
    input_tokens = output_tokens = 0
    problems: list[str] = []
    attempt = 0
    for attempt in range(1, max_attempts + 1):
        prompt = build_test_prompt(brief, writing_rules, template, language, framework, retry_note)
        response = caller(prompt)
        input_tokens += response.input_tokens
        output_tokens += response.output_tokens
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(response.text, encoding="utf-8")
        result = validate_test_doc(conn, out_path)
        if result["ok"]:
            write_test_doc_with_sidecar(out_path, response.text, language)
            return DocResult(member_name, str(out_path), True, attempt, input_tokens, output_tokens, [])
        problems = result["problems"]
        retry_note = "\n".join(f"- {p}" for p in problems)
    return DocResult(member_name, str(out_path), False, attempt, input_tokens, output_tokens, problems)


def _chunk_rows(rows: list, size: int) -> list[list]:
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def _aggregate_chunk_confidence(chunk_paths: list[Path]) -> dict[str, int]:
    """Sum each given chunk's own (model-produced, already validated)
    confidence_summary -- never re-guessed here. Callers must pass only ok
    chunks' paths: a failed chunk's file still exists on disk (written on
    every attempt, even the last failed one) and can carry a perfectly
    parseable confidence_summary despite failing validation for an
    unrelated reason (e.g. a bad citation) -- including it here would
    over-report confidence for scenarios the index doesn't actually claim
    as covered. A path that doesn't exist or doesn't parse contributes
    nothing either way."""
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


def _render_chunk_index(member_name: str, system: str | None, language: str, framework: str,
                         chunk_entries: list[tuple[int, Path, DocResult]],
                         confidence: dict[str, int]) -> str:
    """The index document at a chunked member's normal out_path -- built
    here, deterministically, never model-generated, precisely because the
    thing that broke for the client member that motivated this was a
    model-authored front matter block getting silently dropped from an
    oversized response. Every field here
    is either a static convention (doc_type, generated_by) or aggregated
    from already-validated chunk documents (confidence_summary, the
    '## Scenarios covered' manifest) -- nothing here is invented."""
    today = datetime.date.today().isoformat()
    covered_ids: list[str] = []
    lines_chunks = []
    for index, path, result in chunk_entries:
        status = "OK" if result.ok else "FAILED: " + "; ".join(result.problems)[:200]
        lines_chunks.append(f"- [{path.name}](./{path.name}) -- {status}")
        if result.ok:
            body = path.read_text(encoding="utf-8")
            covered_ids.extend(sorted({
                f"{m.group('member').upper()}:BR-{m.group('n')}" for m in BR_REF.finditer(body)
            }))

    fm = "\n".join([
        "---",
        f'title: "{member_name} — generated tests ({language}), chunked"',
        "doc_type: generated_test",
        f'system: "{system or "unknown"}"',
        f'module: "{member_name}"',
        f"language: {language}",
        f"framework: {framework}",
        "generated_by: legacy-functional-docs 0.1.0",
        f'generated_at: "{today}"',
        "review_status: draft",
        "reviewers: []",
        "confidence_summary:",
        f"  verified: {confidence['verified']}",
        f"  inferred: {confidence['inferred']}",
        f"  unresolved: {confidence['unresolved']}",
        f'sources: ["{member_name}"]',
        "---",
    ])
    body = "\n".join([
        "",
        f"# {member_name} — generated tests ({language}/{framework}), chunked",
        "",
        f"This member's test_case set was rendered as {len(chunk_entries)} separate "
        "documents rather than one -- a single completion this large risks silently "
        f"truncating before its closing fence. See [[{member_name}]] for the module as "
        "a whole.",
        "",
        "## Chunks",
        "",
        *lines_chunks,
        "",
        "## Scenarios covered",
        "",
        *[f"- {sid}" for sid in sorted(set(covered_ids))],
        "",
    ])
    return fm + body


def _generate_member_test_doc_chunked(conn, member_name: str, system: str | None, rows: list,
                                       language: str, framework: str, out_path: Path,
                                       caller: ModelCaller, writing_rules: str, template: str,
                                       redact: Redactor, max_attempts: int, chunk_size: int) -> DocResult:
    """Render one member as several independent chunk documents plus a
    deterministic index doc at `out_path`, instead of asking one completion
    to cover every scenario. Each chunk goes through the exact same
    call/validate/retry path (_generate_test_doc_from_brief) a normal
    single-call member does, scoped to `chunk_size` scenarios via
    test_case_brief_chunk -- so one bad chunk retries and reports on its
    own, rather than forcing a full-member re-generation, and no chunk's
    prompt is any larger than a normal small member's."""
    chunks = _chunk_rows(rows, chunk_size)
    chunk_count = len(chunks)
    input_tokens = output_tokens = 0
    chunk_entries: list[tuple[int, Path, DocResult]] = []
    problems: list[str] = []

    for i, chunk_rows in enumerate(chunks, start=1):
        chunk_path = out_path.with_name(f"{out_path.stem}.chunk{i}{out_path.suffix}")
        brief = test_case_brief_chunk(member_name, system, chunk_rows, i, chunk_count, redact=redact)
        result = _generate_test_doc_from_brief(
            conn, member_name, brief, language, framework, chunk_path, caller,
            writing_rules, template, max_attempts=max_attempts,
        )
        input_tokens += result.input_tokens
        output_tokens += result.output_tokens
        chunk_entries.append((i, chunk_path, result))
        if not result.ok:
            problems.append(f"chunk {i}/{chunk_count} ({chunk_path.name}) failed: " + "; ".join(result.problems))

    confidence = _aggregate_chunk_confidence([p for _, p, r in chunk_entries if r.ok])
    index_text = _render_chunk_index(member_name, system, language, framework, chunk_entries, confidence)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(index_text, encoding="utf-8")

    # The index is built deterministically, not model-generated, but that's
    # not a reason to skip checking it -- validate_test_doc is the same
    # ground truth every chunk (and every other generated doc in this
    # tool) is judged against, and a bug in _render_chunk_index deserves
    # the same loud, reported failure a bad model response gets, not a
    # silent `ok=True` because no chunk happened to fail.
    index_validation = validate_test_doc(conn, out_path)
    if not index_validation["ok"]:
        problems = problems + [f"index document: {p}" for p in index_validation["problems"]]

    return DocResult(member_name, str(out_path), not problems, chunk_count, input_tokens, output_tokens, problems)


def generate_member_test_doc(conn, member_name: str, language: str, framework: str,
                              out_path: Path, caller: ModelCaller, writing_rules: str,
                              template: str, redact: Redactor = NULL_REDACTOR,
                              max_attempts: int = 2,
                              max_scenarios_per_call: int | None = None) -> DocResult:
    """Single-member version: brief -> call -> validate -> retry once.
    Used directly by `mfdoc test-gen` and by run_test_batch's per-item work.

    A member whose test_case set exceeds `max_scenarios_per_call` (default
    DEFAULT_MAX_SCENARIOS_PER_CALL) renders as several independent chunk
    documents instead -- see _generate_member_test_doc_chunked. The
    ambiguous-name and no-test_case-rows cases fall through to the
    original single-call path unchanged (test_case_brief already reports
    both as prose in the brief itself, which the model then fails to turn
    into a valid document -- existing, unchanged behaviour, not something
    this change alters)."""
    system, rows, ambiguous_libs = fetch_test_case_rows(conn, member_name)
    threshold = _resolve_max_scenarios_per_call(max_scenarios_per_call)
    if not ambiguous_libs and rows and len(rows) > threshold:
        return _generate_member_test_doc_chunked(
            conn, member_name, system, rows, language, framework, out_path, caller,
            writing_rules, template, redact, max_attempts, threshold,
        )

    brief = test_case_brief(conn, member_name, redact=redact)
    return _generate_test_doc_from_brief(
        conn, member_name, brief, language, framework, out_path, caller, writing_rules,
        template, max_attempts=max_attempts,
    )


@dataclass
class TestBatchSummary:
    results: list[DocResult]
    total_input_tokens: int
    total_output_tokens: int
    ok: int
    failed: int
    skipped: int


def _corpus_signature(conn, language: str, framework: str, threshold: int,
                       redact: Redactor = NULL_REDACTOR) -> str:
    """Fingerprint of every input to test_case_brief() that isn't the derive
    code itself, via batch._corpus_signature's `extra` hook, plus:

    - language/framework this run targets, since the same test_case rows
      render to a different file per target;
    - every test_case's (scenario_name, status), since a human promoting a
      test-overlay.yml entry past `draft` (which testplan.py folds into
      test_case.status on the next `mfdoc test-plan`) changes what
      test_case_brief() renders for that scenario without touching any
      source_file -- the source-only signature above can't see that on its
      own, and this run's corpus-level skip must not treat it as unchanged;
    - the effective max_scenarios_per_call threshold, since raising or
      lowering it can flip a member between the single-doc and chunked
      output shapes without any test_case row or status changing at all --
      the two checks above wouldn't see that either, and a stale "nothing
      changed" skip would leave the previous run's now-wrong-shaped output
      (or count of chunk files) in place.
    """
    status_rows = conn.execute(
        "SELECT scenario_name, status FROM test_case ORDER BY scenario_name"
    ).fetchall()
    extra = [language, framework, str(threshold)]
    for r in status_rows:
        extra.append(r["scenario_name"])
        extra.append(r["status"])
    return _base_corpus_signature(conn, redact=redact, extra=extra)


def run_test_batch(conn, members: list[str], language: str, framework: str, out_dir: Path,
                    caller: ModelCaller, writing_rules: str, template: str,
                    redact: Redactor = NULL_REDACTOR, concurrency: int = 4,
                    state_path: Path | None = None,
                    max_scenarios_per_call: int | None = None) -> TestBatchSummary:
    """Resumable render over `members` for one language/framework target --
    NOTE on `--matrix` + a shared `--state` file: per-member state keys
    (`f"{subdir}::{member}::{language}::{framework}"`, see below) already
    include language/framework, so per-member resume/skip is correct across
    every target in a matrix invocation. `state["_corpus_sha256"]`, however,
    is a single *global* key -- each target's `run_test_batch` call computes
    its own per-target signature but overwrites the same shared key with it,
    so on a resumed run only the last target run in a given matrix
    invocation gets the fast corpus-level "nothing changed, skip everything"
    check; earlier targets fall back to the slower (still correct)
    per-member `brief_sha256` check instead. This is not a correctness bug --
    no wrong output, no wrong skip -- only a redundant, cheap, local (no
    model call) brief rebuild per non-last target on resume. Left as-is:
    fixing it would touch this function's state-key shape, which the
    2026-08-11 test-generation-matrix design spec's Non-goals section
    explicitly puts out of scope for that feature.
    see batch.run_batch's docstring for the two-tier skip logic this
    mirrors. Output nests as `out_dir/<dialect>/<library>/<language>/<framework>/<member>.md`
    (library segment omitted when the member has none), via the same
    `_output_subdir` batch.py uses for module docs, with language/framework
    beneath it so the same state file/out_dir can track multiple
    destination languages *and* frameworks for one project without one
    target's state or file clobbering another's (e.g. pytest vs unittest
    output for the same member/language). State is keyed by
    `f"{subdir}::{member}::{language}::{framework}"` for the same reason
    batch.py's state key includes the subdir -- two batchable members can
    share a bare name across libraries/dialects."""
    threshold = _resolve_max_scenarios_per_call(max_scenarios_per_call)
    state = _load_state(state_path) if state_path else {}
    corpus_sig = _corpus_signature(conn, language, framework, threshold, redact) if state_path else None
    corpus_unchanged = bool(state_path) and state.get("_corpus_sha256") == corpus_sig
    results: list[DocResult] = []
    briefs: dict[str, str] = {}
    to_run: list[tuple[str, str, Path]] = []
    to_run_chunked: list[tuple[str, str, Path]] = []

    state_keys: dict[str, str] = {}
    for name in members:
        subdir = _output_subdir(conn, name)
        key = f"{subdir.as_posix()}::{name}::{language}::{framework}"
        state_keys[name] = key
        out_path = out_dir / subdir / language / framework / f"{name}.md"
        prior = state.get(key)
        prior_ok = isinstance(prior, dict) and prior.get("ok") and out_path.exists()

        if corpus_unchanged and prior_ok:
            results.append(_skip_result(name, out_path, prior))
            continue

        # Always the member's *full* brief, even for a member that ends up
        # chunked below -- it's only ever used as a content fingerprint for
        # resume/skip, never sent to the model as-is. `threshold` is folded
        # into the hash too: unchanged brief content but a changed
        # max_scenarios_per_call can still flip this member between the
        # single-doc and chunked output shapes, and the per-member skip
        # must not treat that as "nothing changed" (see _corpus_signature's
        # docstring for the same reasoning at the corpus level).
        brief = test_case_brief(conn, name, redact=redact)
        brief_hash = hashlib.sha256(f"{brief}\x00{threshold}".encode("utf-8")).hexdigest()
        if prior_ok and prior.get("brief_sha256") == brief_hash:
            results.append(_skip_result(name, out_path, prior))
            continue

        _, rows, ambiguous_libs = fetch_test_case_rows(conn, name)
        if not ambiguous_libs and rows and len(rows) > threshold:
            to_run_chunked.append((name, brief_hash, out_path))
        else:
            briefs[name] = brief
            to_run.append((name, brief_hash, out_path))

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {
            pool.submit(
                caller, build_test_prompt(briefs[name], writing_rules, template, language, framework)
            ): (name, brief_hash, out_path)
            for name, brief_hash, out_path in to_run
        }
        for fut in as_completed(futures):
            name, brief_hash, out_path = futures[fut]
            response = fut.result()
            input_tokens, output_tokens = response.input_tokens, response.output_tokens

            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(response.text, encoding="utf-8")
            final_text = response.text
            validation = validate_test_doc(conn, out_path)
            attempts = 1
            if not validation["ok"]:
                retry_note = "\n".join(f"- {p}" for p in validation["problems"])
                retry_prompt = build_test_prompt(
                    briefs[name], writing_rules, template, language, framework, retry_note
                )
                retry_response = caller(retry_prompt)
                input_tokens += retry_response.input_tokens
                output_tokens += retry_response.output_tokens
                out_path.write_text(retry_response.text, encoding="utf-8")
                final_text = retry_response.text
                validation = validate_test_doc(conn, out_path)
                attempts = 2

            if validation["ok"]:
                write_test_doc_with_sidecar(out_path, final_text, language)

            result = DocResult(
                name, str(out_path), validation["ok"], attempts, input_tokens, output_tokens,
                validation.get("problems", []),
            )
            results.append(result)
            state[state_keys[name]] = {
                "ok": result.ok, "attempts": attempts, "brief_sha256": brief_hash,
            }

    # Large members (chunked) render serially, on this thread, after the
    # pool above closes -- generate_member_test_doc touches `conn`
    # throughout (validate_test_doc between/after each chunk's model call),
    # and sqlite3 connections can't cross threads (the pool above only ever
    # calls `caller` off-thread, never `conn`, for exactly this reason). A
    # member large enough to need chunking is already the rare, expensive
    # case; trading its concurrency with the other members for correctness
    # here is the right call, not a regression worth chasing.
    for name, brief_hash, out_path in to_run_chunked:
        result = generate_member_test_doc(
            conn, name, language, framework, out_path, caller, writing_rules, template,
            redact=redact, max_scenarios_per_call=threshold,
        )
        results.append(result)
        state[state_keys[name]] = {
            "ok": result.ok, "attempts": result.attempts, "brief_sha256": brief_hash,
        }

    if state_path:
        state["_corpus_sha256"] = corpus_sig
        _save_state(state_path, state)

    return TestBatchSummary(
        results=sorted(results, key=lambda r: r.member),
        total_input_tokens=sum(r.input_tokens for r in results),
        total_output_tokens=sum(r.output_tokens for r in results),
        ok=sum(1 for r in results if r.ok),
        failed=sum(1 for r in results if not r.ok),
        skipped=sum(1 for r in results if r.skipped),
    )

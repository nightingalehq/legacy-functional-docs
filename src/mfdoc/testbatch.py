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

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .batch import ModelCaller, DocResult
from .batch import _corpus_signature as _base_corpus_signature
from .batch import _load_state, _output_subdir, _save_state, _skip_result
from .redact import NULL_REDACTOR, Redactor
from .testplan import test_case_brief
from .validate import validate_test_doc


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


def generate_member_test_doc(conn, member_name: str, language: str, framework: str,
                              out_path: Path, caller: ModelCaller, writing_rules: str,
                              template: str, redact: Redactor = NULL_REDACTOR,
                              max_attempts: int = 2) -> DocResult:
    """Single-member version: brief -> call -> validate -> retry once.
    Used directly by `mfdoc test-gen` and by run_test_batch's per-item work."""
    brief = test_case_brief(conn, member_name, redact=redact)
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
            return DocResult(member_name, str(out_path), True, attempt, input_tokens, output_tokens, [])
        problems = result["problems"]
        retry_note = "\n".join(f"- {p}" for p in problems)
    return DocResult(member_name, str(out_path), False, attempt, input_tokens, output_tokens, problems)


@dataclass
class TestBatchSummary:
    results: list[DocResult]
    total_input_tokens: int
    total_output_tokens: int
    ok: int
    failed: int
    skipped: int


def _corpus_signature(conn, language: str, framework: str, redact: Redactor = NULL_REDACTOR) -> str:
    """Fingerprint of every input to test_case_brief() that isn't the derive
    code itself, via batch._corpus_signature's `extra` hook, plus:

    - language/framework this run targets, since the same test_case rows
      render to a different file per target;
    - every test_case's (scenario_name, status), since a human promoting a
      test-overlay.yml entry past `draft` (which testplan.py folds into
      test_case.status on the next `mfdoc test-plan`) changes what
      test_case_brief() renders for that scenario without touching any
      source_file -- the source-only signature above can't see that on its
      own, and this run's corpus-level skip must not treat it as unchanged.
    """
    status_rows = conn.execute(
        "SELECT scenario_name, status FROM test_case ORDER BY scenario_name"
    ).fetchall()
    extra = [language, framework]
    for r in status_rows:
        extra.append(r["scenario_name"])
        extra.append(r["status"])
    return _base_corpus_signature(conn, redact=redact, extra=extra)


def run_test_batch(conn, members: list[str], language: str, framework: str, out_dir: Path,
                    caller: ModelCaller, writing_rules: str, template: str,
                    redact: Redactor = NULL_REDACTOR, concurrency: int = 4,
                    state_path: Path | None = None) -> TestBatchSummary:
    """Resumable render over `members` for one language/framework target --
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
    state = _load_state(state_path) if state_path else {}
    corpus_sig = _corpus_signature(conn, language, framework, redact) if state_path else None
    corpus_unchanged = bool(state_path) and state.get("_corpus_sha256") == corpus_sig
    results: list[DocResult] = []
    briefs: dict[str, str] = {}
    to_run: list[tuple[str, str, Path]] = []

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

        brief = test_case_brief(conn, name, redact=redact)
        briefs[name] = brief
        brief_hash = hashlib.sha256(brief.encode("utf-8")).hexdigest()
        if prior_ok and prior.get("brief_sha256") == brief_hash:
            results.append(_skip_result(name, out_path, prior))
            continue
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
                validation = validate_test_doc(conn, out_path)
                attempts = 2

            result = DocResult(
                name, str(out_path), validation["ok"], attempts, input_tokens, output_tokens,
                validation.get("problems", []),
            )
            results.append(result)
            state[state_keys[name]] = {
                "ok": result.ok, "attempts": attempts, "brief_sha256": brief_hash,
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

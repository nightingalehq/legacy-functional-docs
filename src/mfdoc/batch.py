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

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import __version__
from .brief import module_brief
from .redact import NULL_REDACTOR, Redactor
from .validate import validate_doc

# Object types that get the batch treatment: one module, one program's worth
# of judgement-light narrative. Data stores, system overview, process flows
# and the gap register stay in the CLI path.
BATCHABLE_OBJECT_TYPES = {"program", "subprogram", "subroutine", "copycode"}
BATCHABLE_DIALECTS = {"natural", "mantis"}


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


def generate_module_doc(conn, member_name: str, out_path: Path, caller: ModelCaller,
                         writing_rules: str, template: str, redact: Redactor = NULL_REDACTOR,
                         max_attempts: int = 2, lexicon: dict[str, str] | None = None) -> DocResult:
    """Single-member version of the harness: brief -> call -> validate ->
    retry once. Used directly for one-off generation and by run_batch's
    per-item work (with the model call itself dispatched to a thread pool
    by the caller)."""
    brief = module_brief(conn, member_name, redact=redact, lexicon=lexicon)
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
              ) -> BatchSummary:
    """Run the harness over `members`, resumable via `state_path`.

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
       re-run.

    This is what makes a run over thousands of members interruptible and
    restartable without burning tokens -- or needless DB queries -- on
    work that's already done.

    `_corpus_signature` itself is only computed when `state_path` is given
    -- with no state file there is nothing to compare it against, and the
    per-member tier below runs unconditionally anyway.
    """
    state = _load_state(state_path) if state_path else {}
    corpus_sig = _corpus_signature(conn, redact, lexicon) if state_path else None
    corpus_unchanged = bool(state_path) and state.get("_corpus_sha256") == corpus_sig
    results: list[DocResult] = []
    briefs: dict[str, str] = {}
    to_run: list[tuple[str, str, Path]] = []

    for name in members:
        out_path = out_dir / f"{name}.md"
        prior = state.get(name)
        # `prior` is only ever meaningful as this member's own state entry;
        # guard against the (currently reserved but unenforced) "_corpus_sha256"
        # key ever being looked up as if it were one -- see cli.py's --members
        # normalisation, which keeps ordinary member names from colliding with it.
        prior_ok = isinstance(prior, dict) and prior.get("ok") and out_path.exists()

        if corpus_unchanged and prior_ok:
            results.append(_skip_result(name, out_path, prior))
            continue

        brief = module_brief(conn, name, redact=redact, lexicon=lexicon)
        briefs[name] = brief
        brief_hash = hashlib.sha256(brief.encode("utf-8")).hexdigest()
        if prior_ok and prior.get("brief_sha256") == brief_hash:
            results.append(_skip_result(name, out_path, prior))
            continue
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
            state[name] = {"ok": result.ok, "attempts": attempts, "brief_sha256": brief_hash}

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
        retried=sum(1 for r in results if r.attempts > 1 and not r.skipped),
        ok=sum(1 for r in results if r.ok),
        failed=sum(1 for r in results if not r.ok),
        skipped=sum(1 for r in results if r.skipped),
    )

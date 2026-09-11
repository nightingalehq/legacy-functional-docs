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
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .batch import ModelCaller, DocResult
from .batch import _corpus_signature as _base_corpus_signature
from .batch import (
    _auto_cite_uncited_assertions,
    _fix_generated_by_version,
    _is_near_miss,
    _load_state,
    _localized_findings,
    _output_subdir,
    _save_state,
    _skip_result,
)
from .brief import (
    chunk_density_metrics, fetch_routines, flag_density_outliers, format_density_note,
    routine_aware_chunk_ranges,
)
from .redact import NULL_REDACTOR, Redactor
from .testlang import sidecar_path_for
from .testplan import (
    doc_rule_fingerprint, fetch_test_case_rows, member_rule_fingerprint, test_case_brief,
    test_case_brief_chunk,
)
from .validate import BR_REF, split_frontmatter, validate_test_doc

# Same progress/diagnostic logger idea as batch.py -- see that module's
# `logger` docstring; mirrored here for `mfdoc test-batch`'s own resumable,
# potentially long-running render loop.
logger = logging.getLogger("mfdoc.testbatch")

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


def write_test_doc_with_sidecar(conn, member_name: str, out_path: Path, doc_text: str,
                                 language: str) -> Path | None:
    """Given a response that has already validated ok, split its one code
    fence out to a sibling source file (`{member}.py`/`{member}.java`, per
    `language`) and rewrite `out_path` to reference it plus a `## Scenarios
    covered` manifest instead of embedding the fence -- the manifest is
    what lets `validate_test_doc` keep checking every MEMBER:BR-nnn
    reference once the actual code has moved somewhere its body-only scan
    would no longer see.

    Also stamps a `test_case_fingerprint` field into the rewritten front
    matter, from `testplan.doc_rule_fingerprint(conn, sources)` (`sources`
    parsed from this document's own front matter, normally just
    `[member_name]`) -- the exact `rule_candidate` ordering that
    determined this render's `BR-nnn` numbering, at the moment the sidecar
    is written. This is what
    lets `validate_test_doc` later detect a genuine positional renumbering
    directly (issue #195), rather than only inferring staleness from
    whether the sidecar's own ids happen to still resolve against current
    `test_case` rows -- a check an inserted-but-not-yet-shifted-past rule
    can slip past (see `member_rule_fingerprint`'s docstring). `conn` is
    only ever used for this fingerprint lookup; omitted (left out of
    front matter entirely) whenever the document's own `sources` can't be
    parsed as a non-empty list of strings -- missing/malformed front
    matter, or a syntactically valid but empty `sources: []` (a shape
    `validate_doc`'s own front-matter check doesn't flag as malformed, so
    it can't be assumed away). Deliberately *not* substituted with
    `[member_name]` in that case (an earlier version of this fix did):
    `validate_test_doc`'s own recomputation always reads the document's
    *own* `sources` value verbatim, with no such substitution, so a
    fingerprint derived from a fabricated `[member_name]` here could never
    be reproduced by that recomputation -- permanently pushing such a
    document onto the weaker id-overlap fallback despite carrying what
    looks like a valid stamped value. `member_name` itself is otherwise
    unused inside this function now (kept in the signature purely for
    caller-side clarity/API consistency across its several call sites,
    and because a future use -- e.g. logging which member a write
    concerns -- shouldn't need a signature change to add).

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

    fence_pattern = re.compile(r"```" + re.escape(language) + r"\n.*?```", re.S)
    prose = fence_pattern.sub(
        f"See [`{sidecar_path.name}`](./{sidecar_path.name}) for the generated test source.",
        body, count=1,
    ).rstrip()
    manifest = "\n\n## Scenarios covered\n\n" + "\n".join(f"- {sid}" for sid in scenario_ids) + "\n"
    # Fingerprinted from the document's *own* parsed `sources` list, not
    # bare `[member_name]`: `validate_test_doc` recomputes via
    # `doc_rule_fingerprint(conn, fm["sources"])` at validation time, and a
    # document whose front matter legitimately names more than one source
    # member (`doc_rule_fingerprint`/the validator's own front-matter
    # contract both allow this) would otherwise be stamped from a
    # single-member hash here but compared against a multi-member one
    # there -- mismatching by construction on every single validation,
    # not because anything about the corpus ever changed. Falls back to
    # `[member_name]` only when this document's own `sources` can't be
    # parsed as a non-empty list of strings (front matter missing/
    # malformed -- the same "leave it out" trade-off as an unresolvable
    # fingerprint below, not a case worth failing this write over).
    #
    # `isinstance(doc_fm, dict)`, not just `is not None` (Copilot review):
    # `split_frontmatter` can return a truthy scalar or list for
    # syntactically-valid-but-non-mapping YAML between the `---` markers
    # (e.g. a bare `sources` string with no `key:` at all) -- `yaml.
    # safe_load`'s `or {}` only substitutes for a *falsy* parse (`None`/
    # `""`/`[]`), not a truthy non-dict one, so `.get` on it would raise
    # instead of just leaving `fingerprint` unset. This is computed --
    # and can raise, absent this guard -- before either on-disk file
    # below is written, so a malformed-YAML crash here can never leave a
    # freshly split sidecar paired with an `out_path` that was never
    # rewritten to reference it (see this function's write order below).
    doc_fm, _doc_body, _doc_err = split_frontmatter(doc_text)
    doc_sources = doc_fm.get("sources") if isinstance(doc_fm, dict) else None
    fingerprint = None
    if isinstance(doc_sources, list) and doc_sources and all(isinstance(s, str) for s in doc_sources):
        # Stripped, matching validate.py's read-side handling of the same
        # field exactly -- both sides must normalize identically, or a
        # `sources` entry with incidental whitespace resolves on one side
        # and not the other, silently skipping the fingerprint stamp.
        #
        # Deliberately *not* substituted with `[member_name]` when
        # `sources` is missing, malformed, or (a valid but unusable shape)
        # an empty list (Copilot review): `validate_test_doc`'s own
        # recomputation always reads the document's *own* `sources` value
        # verbatim, with no such substitution -- a fingerprint stamped
        # from a fabricated `[member_name]` here could never be
        # reproduced by that recomputation for a document whose `sources`
        # is genuinely `[]` (a syntactically valid list, so it isn't
        # caught by `validate_doc`'s own malformed-shape check either),
        # permanently pushing that document onto the weaker fallback
        # despite carrying what looks like a valid stamped value. Leaving
        # `fingerprint` as `None` here instead keeps write and read
        # consistent by construction: neither side can compute one for a
        # document with no real `sources` to work from.
        doc_sources = [s.strip() for s in doc_sources]
        fingerprint = doc_rule_fingerprint(conn, doc_sources)
    if fingerprint is not None:
        front_matter_block = front_matter_block.rstrip("\n") + f'\ntest_case_fingerprint: "{fingerprint}"\n'

    # Both on-disk writes deferred to here, after every step above that
    # can still bail out (None) or -- pre-Copilot-review -- raise: writing
    # the sidecar before this point risked leaving a freshly-split sidecar
    # on disk while `out_path` itself was never rewritten to reference it
    # (e.g. because a later step raised), a mismatched pair no different
    # in effect from the staleness this whole mechanism exists to prevent.
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(code, encoding="utf-8")
    out_path.write_text(f"---{front_matter_block}---{prose}{manifest}", encoding="utf-8")
    return sidecar_path


def _prior_fingerprint_for(out_path: Path) -> str | None:
    """The `test_case_fingerprint` a *previous* successful render already
    stamped at `out_path`, read before this run's own first write to that
    path -- `None` if the path doesn't exist yet (nothing to compare
    against, a genuinely fresh render) or carries no such field (an older
    document, or a fresh render's own front matter never gets one stamped
    into it by the model -- only `write_test_doc_with_sidecar` adds it,
    after validation succeeds).

    Why this matters (issue #195 review): `_generate_test_doc_from_brief`/
    `run_test_batch`'s retry loops write the model's freshly-generated
    candidate text straight over `out_path` *before* calling
    `validate_test_doc` on it -- so by the time that validation runs, any
    fingerprint the *previous* render had stamped is already gone from
    disk, even though the (still on-disk, not-yet-overwritten) sidecar
    file right next to it is exactly what that old fingerprint was
    recorded against. Without capturing it here, first, `validate_test_doc`
    would see a candidate document with no `test_case_fingerprint` of its
    own and fall back to the weaker id-overlap heuristic for precisely the
    validation this issue is actually about -- the fingerprint fix would
    then only ever apply to a document's *second* validation onward (e.g.
    a later `mfdoc test-validate`/dry-run reuse check), never the render
    loop's own first pass. Callers thread the result through every
    `validate_test_doc(..., _prior_fingerprint=...)` call in one render
    attempt (the prior document doesn't change mid-retry -- only the
    candidate text does).

    Known, accepted residual gap: if a *previous invocation* (not just a
    prior attempt within the current one) exhausted every retry and left
    an invalid candidate on disk -- `out_path`'s last write on that run --
    that candidate never validated, so `write_test_doc_with_sidecar` never
    ran and no fingerprint was ever stamped. A fresh invocation's call to
    this function then genuinely has nothing to recover, and (if the
    corpus has *also* shifted in the meantime) is exposed to the same
    stale-old-sidecar risk this whole mechanism exists to close. Accepted
    rather than fixed here: closing it would mean persisting the
    fingerprint separately from the document itself (e.g. in `--state`
    resume metadata) purely to survive a validation failure that already
    needs investigating on its own -- a permanently-failing member is
    already an anomaly a human needs to look at, not a case this
    mechanism should add complexity trying to paper over silently.

    A second known, accepted limitation (Copilot review), specific to a
    *chunked* member: the fingerprint this recovers (and the one
    `write_test_doc_with_sidecar` stamps) describes the whole member's
    `rule_candidate` ordering, not one chunk's own slice of it. If that
    ordering is unchanged but chunk *boundaries* move on their own
    (`options.testgen.max_scenarios_per_call` changing, or a `routine`
    boundary shifting) -- so `chunk1` at this same path now covers a
    different range of scenarios than it did before -- the member-wide
    fingerprint still matches, and this chunk's stale content can be read
    as current even though it no longer describes what "chunk1" now
    means. A properly *chunk*-scoped fingerprint would need to describe
    which rule range each chunk index actually covers, which depends on
    the very chunk-planning logic (`brief.routine_aware_chunk_ranges`)
    being evaluated at the point this function is called -- a real design
    change (in the same category as #207/#214's caching redesign), not an
    incremental fix, and out of scope for this one. The insertion/
    positional-shift case this whole mechanism exists to fix (issue #195
    itself, and every regression added for it) doesn't hit this: that
    case shifts the member-wide ordering itself, which the fingerprint
    already catches correctly."""
    if not out_path.exists():
        return None
    fm, _body, _err = split_frontmatter(out_path.read_text(encoding="utf-8"))
    # A previous failed render can leave arbitrary YAML on disk between
    # the `---` markers -- a bare scalar or list is valid YAML but not a
    # mapping (`yaml.safe_load`'s `or {}` in `split_frontmatter` only
    # substitutes for a *falsy* result, e.g. `None`/`""`/`[]`, not a
    # truthy non-dict one like a non-empty string or list) -- and `.get`
    # on anything but a dict raises. This is exactly the "invalid prior
    # candidate on disk" case this function's own docstring already
    # expects to see; must return `None` (nothing usable to recover), not
    # crash the render this function is trying to help succeed.
    if not isinstance(fm, dict):
        return None
    return fm.get("test_case_fingerprint")


def _prune_stale_test_chunk_files(out_path: Path, expected_names: set[str], language: str) -> None:
    """`batch._prune_stale_chunk_files`, extended for test-batch's own
    chunk sidecars: a stale `{stem}.chunk<N>{suffix}` file gets its
    matching `.chunk<N>.py`/`.nsp`/... sidecar removed alongside it,
    since that shared helper only knows about the `.md`-shaped chunk
    index files module docs use and has no concept of a sidecar at all.

    Used two ways (Copilot review, issue #195): the normal chunked path
    (`expected_names` names this run's real chunk files, same as before)
    *and* the single-document path a member falls back to once its
    `test_case` count drops back under the chunking threshold
    (`expected_names=set()`, since a single-document render has no
    chunks of its own at all) -- without the second call, a member that
    shrinks below the threshold between runs would leave every one of its
    old `.chunk<N>.md`/sidecar pairs on disk indefinitely: nothing in the
    single-document path ever revisits them, but a full tree walk
    (`mfdoc test-validate`, `validate_tests_tree`) still finds and
    validates them independently, where their now-orphaned manifests/
    sidecars can still produce the exact false staleness failures this
    whole mechanism exists to prevent -- just for files nothing renders
    into any more, rather than ones actively being resumed."""
    if not out_path.parent.is_dir():
        return
    pattern = re.compile(rf"^{re.escape(out_path.stem)}\.chunk\d+{re.escape(out_path.suffix)}$")
    for candidate in list(out_path.parent.iterdir()):
        if not candidate.is_file() or candidate.name in expected_names:
            continue
        if not pattern.match(candidate.name):
            continue
        sidecar = sidecar_path_for(candidate, language)
        try:
            candidate.unlink()
        except OSError:
            # Best-effort, same as batch._prune_stale_chunk_files -- a
            # file some other process is holding open must not abort an
            # otherwise-successful run over cosmetic cleanup.
            continue
        if sidecar is not None and sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                pass


def _invalidate_sidecar_if_range_changed(chunk_path: Path, language: str, expected_ids: set[str]) -> None:
    """Remove `chunk_path`'s existing sidecar if its own `MEMBER:BR-nnn`
    content no longer matches `expected_ids` -- this chunk index's current
    row range, from this run's freshly recomputed `routine_aware_chunk_
    ranges` -- before this chunk is (re)rendered.

    Closes a narrower gap than the full chunk-scoped-fingerprint redesign
    `_prior_fingerprint_for`'s docstring already declines to do (Copilot
    review, issue #195): `write_test_doc_with_sidecar` stamps a
    *member*-wide `test_case_fingerprint` (the whole member's
    `rule_candidate` `(id, line_no)` ordering), not one scoped to which
    scenario range a given chunk *index* covers. If that member-wide
    ordering is unchanged but chunk boundaries move on their own (a
    `max_scenarios_per_call` change, or a `routine` boundary shifting) --
    so `chunk1` at this same path now covers a different range of
    scenarios than it did before -- the stamped fingerprint still matches
    a freshly computed one, and `validate_test_doc`'s authoritative
    fingerprint check (case 1: an exact match short-circuits everything
    else) would otherwise read the *old*, now-differently-scoped sidecar
    as still current -- cross-checking a freshly-generated candidate's
    ids against a sidecar for the wrong range, which can fail validation
    on every retry (the corpus hasn't actually changed) since
    `write_test_doc_with_sidecar` only ever refreshes the sidecar after a
    *successful* validation.

    Doesn't require re-deriving what chunk boundaries *should* be at
    validation time the way a real chunk-scoped fingerprint would (the
    "real design change...out of scope" `_prior_fingerprint_for` already
    flags): this call site already has this run's authoritative range for
    chunk `i` in hand (`chunk_rows`, from the very `ranges` this render
    loop just computed), so it can compare that directly against what's
    already on disk and drop the sidecar outright when they disagree --
    the same effect `validate_test_doc`'s own bypass has for a *legacy*
    sidecar with no fingerprint context at all (see its docstring), just
    triggered here for a *range-shifted* sidecar instead of a fingerprint-
    less one. A dropped sidecar is unconditionally correct to discard: it
    is about to be re-rendered as a cache miss regardless (a caller only
    reaches here when `_test_chunk_reuse_ok` already said no), and
    `write_test_doc_with_sidecar` recreates a fresh, correctly-scoped one
    the moment that render validates.

    No-op if this chunk has no sidecar yet (a fresh render, nothing to
    invalidate) or if its content already matches `expected_ids` (nothing
    changed for this index -- the common case). Best-effort on removal,
    same as `_prune_stale_test_chunk_files`: a transient filesystem lock
    here must not abort an otherwise-successful chunked render; leaving a
    wrong-range sidecar in place in that rare case reproduces the same
    (already-documented, already-accepted) residual risk as this
    function's sibling cleanup helpers, not a new one."""
    sidecar = sidecar_path_for(chunk_path, language)
    if sidecar is None or not sidecar.exists():
        return
    on_disk_ids = {
        f"{m.group('member').upper()}:BR-{m.group('n')}"
        for m in BR_REF.finditer(sidecar.read_text(encoding="utf-8"))
    }
    if on_disk_ids == expected_ids:
        return
    try:
        sidecar.unlink()
    except OSError:
        pass


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


_TEST_BATCH_INSTRUCTIONS_TEMPLATE = (
    "You are writing first-draft {language}/{framework} tests for one legacy "
    "mainframe module, from a fact brief that already cites every scenario back "
    "to source. Follow the writing rules and template exactly. Never assert a "
    "consequence that isn't in the brief's cited source excerpt -- write up to "
    "the branch decision and mark it `unresolved` instead of inventing one. "
    "Output only the completed document (front matter + one fenced code block), "
    "nothing else."
)


def build_test_prompt_parts(brief: str, writing_rules: str, template: str, language: str,
                             framework: str, retry_note: str | None = None) -> list[str]:
    """The ordered sections `build_test_prompt` joins into one flat string,
    returned unjoined -- same split as batch.py's `build_prompt_parts`
    (issue #159, extended here by #168): a stable prefix (instructions +
    writing rules + template, byte-identical across every chunk/member/retry
    for one language/framework target in a project's test-generation run)
    followed by the per-call variable suffix (test brief, and on a retry,
    the retry note). `build_test_prompt` itself is just
    `"\\n\\n---\\n\\n".join(...)` of this; `build_test_prompt_cache_prefix`
    derives the same stable prefix from the first three sections here so
    AnthropicCaller/VertexCaller can mark it as an ephemeral cache
    breakpoint without re-parsing a joined string. Unlike build_prompt_parts,
    the instructions section itself varies per language/framework -- so the
    cache prefix (and set_cache_prefixes registration) is necessarily
    per-target too, not shared across a `--matrix` run's different targets."""
    parts = [
        _TEST_BATCH_INSTRUCTIONS_TEMPLATE.format(language=language, framework=framework),
        "# Writing rules\n\n" + writing_rules,
        "# Template\n\n" + template,
        "# Test brief\n\n" + brief,
    ]
    if retry_note:
        parts.append(
            "# Previous attempt failed validation\n\n" + retry_note
            + "\n\nFix these problems and resend the complete document."
        )
    return parts


def build_test_prompt(brief: str, writing_rules: str, template: str, language: str,
                       framework: str, retry_note: str | None = None) -> str:
    return "\n\n---\n\n".join(
        build_test_prompt_parts(brief, writing_rules, template, language, framework, retry_note)
    )


def build_test_prompt_cache_prefix(writing_rules: str, template: str, language: str, framework: str) -> str:
    """The exact leading substring of every `build_test_prompt(...)` call
    sharing this `writing_rules`/`template`/`language`/`framework` (i.e.
    every call for one target in one project's test-batch run) --
    instructions + writing rules + template, with the trailing section
    separator included so it lines up with where `# Test brief` starts.
    AnthropicCaller/VertexCaller mark this whole prefix with
    `cache_control: {"type": "ephemeral"}` (issue #159, extended to
    test-batch by #168) so it's billed once per project/target run instead
    of once per call; a caller with no cache-prefix support (ClaudeCLICaller,
    the fake-echo test caller) never sees this at all -- run_test_batch only
    hands it to callers that expose `set_cache_prefixes`."""
    stable = build_test_prompt_parts("", writing_rules, template, language, framework)[:3]
    return "\n\n---\n\n".join(stable) + "\n\n---\n\n"


def build_localized_test_patch_prompt(
    brief: str, current_text: str, uncited: list[str], reversed_findings: list[str],
) -> str:
    """Test-generation equivalent of batch.py's `build_localized_patch_prompt`
    (issue #131, generalized by #170; ported here by #188): the same small,
    targeted follow-up prompt for a near-miss `validate_test_doc` failure
    (see `_is_near_miss`/`_localized_findings`, reused unchanged from
    batch.py -- `validate_test_doc`'s only sentence-localized failure class
    is the uncited-assertion one; a reversed-condition finding can never
    occur here, since `validate_doc`'s `_reversed_condition_problems` call
    is gated on `doc_type in ("module", "module_index")`, never
    `doc_type: generated_test`. `reversed_findings` stays in the signature
    only so this is a drop-in match for `_localized_findings`'s return
    shape; it is always empty in practice for a test doc). Never resends
    the writing rules/template/instructions: the model already demonstrated
    it can follow them (the rest of `current_text`, including its one
    fenced code block, is proof), so the only thing worth asking for again
    is a citation or hedge fix to the specific flagged prose sentence(s)."""
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
        "The document below is almost entirely valid first-draft generated "
        "test documentation. A small number of specific, locatable "
        "sentences have a flagged problem -- everything else in it, "
        "including its fenced test code, already validated clean.\n\n"
        "# Flagged findings (locate the matching sentence; fix only these)\n\n"
        + "\n\n".join(sections) + "\n\n"
        "Do not change anything else: no other sentence, heading, citation, "
        "front-matter field, or any part of the fenced code block. Output "
        "the complete corrected document, nothing else.\n\n"
        "# Test brief\n\n" + brief + "\n\n"
        "# Current document\n\n" + current_text
    )


def _generate_test_doc_from_brief(conn, member_name: str, brief: str, language: str, framework: str,
                                   out_path: Path, caller: ModelCaller, writing_rules: str,
                                   template: str, max_attempts: int = 2) -> DocResult:
    """Call -> validate -> retry-once loop, given an already-built brief --
    the part of generate_member_test_doc that doesn't care whether `brief`
    covers a member's whole test_case set or just one chunk of it, shared
    by the plain single-call path and _generate_member_test_doc_chunked's
    per-chunk calls below.

    `caller(prompt)` is allowed to raise (a `claude -p` timeout is a real
    `subprocess.TimeoutExpired`/`RuntimeError`, not a hypothetical -- see
    claude_cli_caller.py's `DEFAULT_TIMEOUT_S`): an uncaught exception here
    would otherwise propagate out of run_test_batch/generate_member_test_doc
    and abort the whole batch, discarding every already-completed chunk's
    result with no record that they finished. Treated exactly like a failed
    validation instead -- the exception text becomes this attempt's
    `retry_note`/`problems`, so a retry is attempted the same as any other
    failure, and a final failure is reported as an ordinary ok=False
    DocResult rather than an unhandled crash. Every attempt's problems (an
    exception's message, or a failed validation's problem list) accumulate
    onto `problems` rather than replacing it, in either direction: a prior
    validation failure's problems survive a later attempt raising, and a
    prior exception's message survives a later attempt's response still
    failing validation -- neither an unguarded `problems = [...]` on the
    exception branch nor on the validation branch would keep both.

    A validation failure that `_is_near_miss` calls a near-miss (issue #131,
    generalized by #170, ported to this test-generation path by #188: only
    a handful of sentence-localized uncited-assertion findings -- nothing
    structural, and never a reversed-condition finding, which
    `validate_test_doc` can't produce, see `build_localized_test_patch_
    prompt`'s docstring) gets one cheap targeted-patch attempt
    (`build_localized_test_patch_prompt`) before counting against
    `max_attempts` -- it doesn't consume one of the full-regeneration
    attempts, since it asks for something far smaller than one. A response
    failing for any other reason, or where the patch attempt itself doesn't
    resolve everything (including the patch model call itself raising, the
    same exception risk as the main call above), falls straight through to
    the existing full-retry loop unchanged.

    Captures `_prior_fingerprint_for(out_path)` once, before this call's
    first write to `out_path` -- see that function's docstring (issue #195
    review): every `validate_test_doc` call below passes it through, since
    a freshly-generated candidate's own front matter never carries
    `test_case_fingerprint` itself, and by the time validation runs
    `out_path` has already been overwritten with that candidate."""
    prior_fingerprint = _prior_fingerprint_for(out_path)
    retry_note = None
    input_tokens = output_tokens = 0
    problems: list[str] = []
    attempt = 0
    for attempt in range(1, max_attempts + 1):
        prompt = build_test_prompt(brief, writing_rules, template, language, framework, retry_note)
        try:
            response = caller(prompt)
        except Exception as exc:
            logger.warning(
                "%s: model call raised %s on attempt %d/%d: %s",
                member_name, exc.__class__.__name__, attempt, max_attempts, exc,
            )
            problems = problems + [f"model call raised {exc.__class__.__name__}: {exc}"]
            retry_note = "\n".join(f"- {p}" for p in problems)
            continue
        input_tokens += response.input_tokens
        output_tokens += response.output_tokens
        text = _fix_generated_by_version(response.text)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        result = validate_test_doc(
            conn, out_path, _prior_fingerprint=prior_fingerprint, _render_time=True,
        )
        if result["ok"]:
            write_test_doc_with_sidecar(conn, member_name, out_path, text, language)
            return DocResult(member_name, str(out_path), True, attempt, input_tokens, output_tokens, [])

        if _is_near_miss(result):
            uncited, reversed_findings = _localized_findings(result)

            if uncited:
                # Issue #171's deterministic, no-model-call auto-citation
                # pass applies here exactly as it does for module docs --
                # it operates only on `brief`/`current_text`, neither of
                # which is module-doc-specific.
                auto = _auto_cite_uncited_assertions(brief, text, uncited)
                if auto is not None:
                    candidate_text, remaining_uncited = auto
                    candidate_result = validate_test_doc(
                        conn, out_path, _text=candidate_text, _prior_fingerprint=prior_fingerprint,
                        _render_time=True,
                    )
                    if candidate_result["ok"]:
                        logger.info(
                            "%s: %d near-miss uncited assertion(s) auto-cited from the "
                            "brief with no model call; document now validates clean",
                            member_name, len(uncited) - len(remaining_uncited),
                        )
                        out_path.write_text(candidate_text, encoding="utf-8")
                        write_test_doc_with_sidecar(conn, member_name, out_path, candidate_text, language)
                        return DocResult(
                            member_name, str(out_path), True, attempt, input_tokens,
                            output_tokens, [],
                        )
                    if _is_near_miss(candidate_result):
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
                    # else: the candidate is no longer a near-miss -- discard
                    # it and fall through to the patch prompt using the
                    # original, unpatched text/uncited/reversed_findings.

            logger.warning(
                "%s: validation failed on attempt %d/%d with %d near-miss "
                "localized finding(s) only (%d uncited, %d reversed-condition) "
                "-- trying a targeted patch before a full retry",
                member_name, attempt, max_attempts,
                len(uncited) + len(reversed_findings), len(uncited), len(reversed_findings),
            )
            patch_prompt = build_localized_test_patch_prompt(brief, text, uncited, reversed_findings)
            try:
                patch_response = caller(patch_prompt)
            except Exception as exc:
                logger.warning(
                    "%s: targeted patch model call raised %s on attempt %d/%d: %s -- "
                    "falling back to a full retry",
                    member_name, exc.__class__.__name__, attempt, max_attempts, exc,
                )
                # Issue #188 review: this attempt's own validation problems
                # (accumulated into `problems` below via `result["problems"]`)
                # would otherwise be the only record of what went wrong on
                # this attempt -- the patch call's own exception must be
                # preserved too, or a final failure's diagnostics and the
                # next attempt's retry_note silently drop it.
                problems = problems + [
                    f"targeted patch model call raised {exc.__class__.__name__}: {exc}"
                ]
            else:
                input_tokens += patch_response.input_tokens
                output_tokens += patch_response.output_tokens
                text = _fix_generated_by_version(patch_response.text)
                out_path.write_text(text, encoding="utf-8")
                result = validate_test_doc(
                    conn, out_path, _prior_fingerprint=prior_fingerprint, _render_time=True,
                )
                if result["ok"]:
                    write_test_doc_with_sidecar(conn, member_name, out_path, text, language)
                    return DocResult(
                        member_name, str(out_path), True, attempt, input_tokens, output_tokens, [],
                    )
                logger.warning(
                    "%s: targeted patch attempt did not resolve validation (%d problem(s)); "
                    "falling back to a full retry",
                    member_name, len(result["problems"]),
                )

        logger.warning(
            "%s: validation failed on attempt %d/%d (%d problem(s))",
            member_name, attempt, max_attempts, len(result["problems"]),
        )
        problems = problems + result["problems"]
        retry_note = "\n".join(f"- {p}" for p in problems)
    return DocResult(member_name, str(out_path), False, attempt, input_tokens, output_tokens, problems)


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
        f"generated_by: legacy-functional-docs {__version__}",
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


# Every column `validate.validate_doc` writes into `doc_claim` for one
# document path -- see db.SCHEMA's `doc_claim` table. `id` is deliberately
# excluded: it's an autoincrement surrogate key nothing else in the schema
# references (see db.py/validate.py), so `_readonly_validate_test_doc`'s
# restore doesn't need to reproduce the exact prior id values, only the
# same content under the same doc_path.
_DOC_CLAIM_COLUMNS = (
    "doc_path", "claim_id", "confidence", "citation", "member_name",
    "line_from", "line_to", "valid", "note",
)


def _readonly_validate_test_doc(conn, path: Path, _render_time: bool = False) -> dict:
    """The same result `validate_test_doc(conn, path)` returns, but leaves
    the `doc_claim` table exactly as it was before the call.
    `validate_test_doc` (via `validate.validate_doc`) deletes and
    reinserts every `doc_claim` row for `path` and commits as a side
    effect -- correct, and the point, for a real render (it's what keeps
    `doc_claim` in sync with what a document currently cites, for `mfdoc
    sample-citations` to read later), but not acceptable for
    `plan_test_batch`'s dry-run: its whole documented contract (like
    `mfdoc batch --dry-run`'s) is that it writes nothing, anywhere (issue
    #190 review flagged this: `_test_chunk_reuse_ok`'s revalidation of a
    cached chunk was mutating the index database even though it never
    touches the filesystem). Snapshots this one path's rows first and
    restores them verbatim afterward in a `finally` (so a raised exception
    still restores before propagating), rather than skip the revalidation
    -- the reuse check still needs today's real `ok` verdict, just without
    the persistent side effect."""
    path_str = str(path)
    before = conn.execute(
        f"SELECT {', '.join(_DOC_CLAIM_COLUMNS)} FROM doc_claim WHERE doc_path=?",
        (path_str,),
    ).fetchall()
    try:
        return validate_test_doc(conn, path, _render_time=_render_time)
    finally:
        conn.execute("DELETE FROM doc_claim WHERE doc_path=?", (path_str,))
        if before:
            placeholders = ", ".join("?" * len(_DOC_CLAIM_COLUMNS))
            conn.executemany(
                f"INSERT INTO doc_claim ({', '.join(_DOC_CLAIM_COLUMNS)}) VALUES ({placeholders})",
                [tuple(row[c] for c in _DOC_CLAIM_COLUMNS) for row in before],
            )
        conn.commit()


def _test_chunk_reuse_ok(conn, prior_chunks: dict | None, i: int, brief_hash: str,
                          chunk_path: Path, readonly: bool = False) -> bool:
    """Whether chunk `i` can be reused verbatim -- no model call -- given a
    prior run's chunk_state and this chunk's freshly-computed brief hash:
    the prior run must have recorded this exact chunk as clean (`ok` True)
    with the same `brief_sha256`, its output file must still exist on disk,
    and it must still validate today (`validate_test_doc`'s own logic can
    have changed since it was last checked, even though the content
    hasn't).

    A failed chunk is never a reuse candidate: it leaves its last (invalid)
    attempt on disk, so treating it as reusable would re-validate the same
    bad file forever and the chunk would never re-render.

    Mirrors batch.py's `_chunk_reuse_ok` (issue #160) verbatim except for
    calling `validate_test_doc` instead of `validate_doc` -- test docs carry
    `language`/`framework` front matter and `MEMBER:BR-nnn` scenario
    references `validate_doc` alone doesn't check, so this can't just
    import batch.py's version, same reason `_generate_member_test_doc_
    chunked` doesn't call `_generate_module_doc_chunked`. Shared by that
    function's real reuse path and `plan_test_batch`'s dry-run estimate
    (issue #190) so both apply exactly the same reuse rule -- a preview
    that used a second, slightly different copy of this logic could drift
    from what a real run actually does.

    `readonly` (only ever set by `plan_test_batch`) routes the
    revalidation through `_readonly_validate_test_doc` instead of
    `validate_test_doc` directly, so a dry-run's reuse check can't leave
    the `doc_claim` table changed even though it makes no model call and
    writes no file -- the real chunked-render path leaves this False, so
    its own revalidation keeps refreshing `doc_claim` exactly as before.

    A chunk whose sidecar `validate_test_doc` reports as `sidecar_stale`
    (issue #195) is deliberately never reusable, even when `ok` comes back
    True: `ok=True` there only means the staleness was tolerated by
    falling back to scanning the document body, not that the on-disk
    sidecar source itself is still accurate. Reusing it verbatim would
    leave that stale sidecar (and its now-wrong BR-nnn comments) on disk
    indefinitely across every future resumed/dry-run pass, since nothing
    would ever call `write_test_doc_with_sidecar` again to refresh it.
    Treating it as a cache miss instead forces the normal render path,
    which -- once it validates -- rewrites the sidecar with fresh content
    the usual way.

    `_render_time=True` passed to the revalidation below (issue #195
    review): a *legacy* chunk file (written before `test_case_fingerprint`
    existed, so it carries none) would otherwise fall to the id-overlap
    fallback here too, which can read a corpus change as "still current"
    the same way it can for a fresh render -- `brief_hash` alone doesn't
    close this, since `_generate_member_test_doc_chunked`'s per-chunk
    hash is computed from `test_case_brief_chunk`'s content only, not from
    `member_rule_fingerprint`, so a `rule_candidate` change elsewhere in
    the member (this member's own resume skip already re-enters this
    function once its own hash changes, but a *chunk* whose own brief
    text happens to be unaffected can still reach here with a stale
    legacy sidecar). The one-time cost: every legacy chunk gets forced
    through a real re-render exactly once, the same transition every
    other legacy document goes through, rather than being reused forever
    with a sidecar that can never earn a fingerprint because nothing ever
    calls `write_test_doc_with_sidecar` on a chunk this function keeps
    calling reusable."""
    prior_chunk = (prior_chunks or {}).get(str(i))
    reusable = (
        isinstance(prior_chunk, dict) and prior_chunk.get("ok") is True
        and prior_chunk.get("brief_sha256") == brief_hash
        and chunk_path.exists()
    )
    if not reusable:
        return False
    validator = _readonly_validate_test_doc if readonly else validate_test_doc
    result = validator(conn, chunk_path, _render_time=True)
    return result["ok"] and not result.get("sidecar_stale")


def _generate_member_test_doc_chunked(conn, member_name: str, system: str | None, rows: list,
                                       language: str, framework: str, out_path: Path,
                                       caller: ModelCaller, writing_rules: str, template: str,
                                       redact: Redactor, max_attempts: int, chunk_size: int,
                                       prior_chunks: dict | None = None,
                                       sme_notes: dict | None = None) -> DocResult:
    """Render one member as several independent chunk documents plus a
    deterministic index doc at `out_path`, instead of asking one completion
    to cover every scenario. Each chunk goes through the exact same
    call/validate/retry path (_generate_test_doc_from_brief) a normal
    single-call member does, scoped to a routine-aware slice via
    brief.routine_aware_chunk_ranges -- the same grouping module-doc
    chunking uses (batch.py), joined here through each row's originating
    rule_candidate (test_case.rule_candidate_id) -- so one bad chunk
    retries and reports on its own, rather than forcing a full-member
    re-generation, and (short of a single oversized routine) no chunk's
    prompt is much larger than a normal small member's. A row with no
    rule_candidate_id (rule_line_no is NULL) is treated as belonging to no
    routine, same as brief.py's main-body facts.

    `prior_chunks` (from a previous run's state, keyed by chunk index as a
    string) lets a chunk whose own rule-range brief is unchanged from that
    prior run -- and whose file on disk still validates -- skip the model
    call entirely and reuse the existing file, mirroring
    batch._generate_module_doc_chunked's identical `prior_chunks` parameter
    verbatim: this is what makes a fix affecting only one routine's worth
    of source cheap to pick up on a retry, rather than the member-level
    resume check's only choice (reuse every chunk, or re-render all of
    them)."""
    routines = fetch_routines(conn, rows[0]["member_id"])
    line_nos = [r["rule_line_no"] if r["rule_line_no"] is not None else -1 for r in rows]
    ranges = routine_aware_chunk_ranges(line_nos, routines, chunk_size)
    chunk_count = len(ranges)
    # Source-density estimate per chunk (issue #105) -- same rationale and
    # shared implementation as batch.py's module-doc chunking: scenario
    # count alone (what `ranges` is packed by) doesn't say how content-dense
    # a chunk's *source* actually is. No rule_candidate.depth is joined into
    # `rows` here (see fetch_test_case_rows), so this uses only the
    # lines-per-item signal, not nesting depth -- still enough to flag a
    # chunk whose scenarios are packed far more sparsely across source than
    # its siblings.
    density_metrics = flag_density_outliers(chunk_density_metrics(line_nos, ranges))
    input_tokens = output_tokens = 0
    chunk_entries: list[tuple[int, Path, DocResult]] = []
    problems: list[str] = []
    chunk_state: dict[str, dict] = {}

    chunk_width = len(str(chunk_count))
    expected_chunk_names = {
        f"{out_path.stem}.chunk{n:0{chunk_width}d}{out_path.suffix}" for n in range(1, chunk_count + 1)
    }
    _prune_stale_test_chunk_files(out_path, expected_chunk_names, language)
    # A leftover sidecar from a *prior single-document* render of this same
    # member (issue #195 review): the index document at `out_path` never
    # gets its own sidecar -- each chunk gets its own
    # (`out_path.chunk{N}.py`, split independently below) -- but
    # `sidecar_path_for(out_path, language)` is purely path-based and can't
    # tell an index doc from a single-doc one, so a member that grows past
    # the chunking threshold between runs would otherwise leave its old
    # single-doc sidecar sitting right where the index doc's own
    # (irrelevant) "sidecar" would be looked up, and `validate_test_doc`
    # would cross-check the index's aggregated manifest against that
    # unrelated leftover content. Removed here, unconditionally, before
    # the index is ever written -- `_prune_stale_chunk_files` above only
    # ever touches `.chunk<N>` files, deliberately, so this is a separate
    # cleanup step, not something to fold into it.
    stale_index_sidecar = sidecar_path_for(out_path, language)
    if stale_index_sidecar is not None and stale_index_sidecar.exists():
        try:
            stale_index_sidecar.unlink()
        except OSError:
            # Best-effort, same as _prune_stale_chunk_files above -- a file
            # some other process is holding open must not abort the batch.
            # Accepted residual risk (Copilot review): if this unlink
            # genuinely fails (a lock, a permission-restricted directory),
            # `_render_time=True` on the index's own validation below only
            # protects *this* run's in-process check -- a later standalone
            # `mfdoc test-validate` sweep (not render-time) would still
            # find the leftover file and could cross-check against it.
            # Not escalated to a hard failure here for the same reason
            # `_prune_stale_chunk_files` doesn't: a filesystem-level lock
            # is exactly the kind of transient condition that shouldn't
            # abort an otherwise-successful chunked render over cosmetic
            # cleanup, and the next successful cleanup attempt (any
            # future chunked render of this member) removes it then.
            pass
    for i, (start, end) in enumerate(ranges, start=1):
        chunk_rows = rows[start - 1:end]
        chunk_path = out_path.with_name(f"{out_path.stem}.chunk{i:0{chunk_width}d}{out_path.suffix}")
        brief = test_case_brief_chunk(
            member_name, system, chunk_rows, i, chunk_count, redact=redact, routines=routines,
            sme_notes=sme_notes,
        )
        brief_hash = hashlib.sha256(brief.encode("utf-8")).hexdigest()
        result = None
        if _test_chunk_reuse_ok(conn, prior_chunks, i, brief_hash, chunk_path):
            result = DocResult(member_name, str(chunk_path), True, 0, 0, 0, [])
            logger.debug("%s: chunk %d/%d reused (unchanged)", member_name, i, chunk_count)
        if result is None:
            # See _invalidate_sidecar_if_range_changed's docstring (issue
            # #195 review): about to regenerate this chunk index as a
            # cache miss regardless -- if it still has an on-disk sidecar
            # from a *prior* run whose range no longer matches this run's
            # `chunk_rows` (boundaries moved even though the member-wide
            # rule_candidate ordering didn't), drop it now so the
            # about-to-run validation can't wrongly treat that
            # wrong-range sidecar as authoritative just because the
            # member-wide fingerprint still happens to match.
            expected_ids = {r["scenario_name"].upper() for r in chunk_rows}
            _invalidate_sidecar_if_range_changed(chunk_path, language, expected_ids)
            logger.info("%s: chunk %d/%d generating", member_name, i, chunk_count)
            result = _generate_test_doc_from_brief(
                conn, member_name, brief, language, framework, chunk_path, caller,
                writing_rules, template, max_attempts=max_attempts,
            )
        input_tokens += result.input_tokens
        output_tokens += result.output_tokens
        chunk_entries.append((i, chunk_path, result))
        chunk_state[str(i)] = {"ok": result.ok, "brief_sha256": brief_hash}
        if result.ok:
            logger.debug("%s: chunk %d/%d complete", member_name, i, chunk_count)
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
    #
    # `_render_time=True` here too (issue #195 review): the leftover
    # single-doc sidecar this function just tried to remove above is
    # best-effort (an OSError there is swallowed, same as
    # `_prune_stale_chunk_files`) -- if that unlink somehow failed, this
    # index document (which never gets its own fingerprint, being
    # deterministic rather than model-authored) would otherwise still be
    # exposed to the exact stale-sidecar cross-check this whole mechanism
    # exists to bypass at render time.
    index_validation = validate_test_doc(conn, out_path, _render_time=True)
    if not index_validation["ok"]:
        problems = problems + [f"index document: {p}" for p in index_validation["problems"]]

    return DocResult(
        member_name, str(out_path), not problems, chunk_count, input_tokens, output_tokens,
        problems, chunked=True, chunk_state=chunk_state,
    )


def generate_member_test_doc(conn, member_name: str, language: str, framework: str,
                              out_path: Path, caller: ModelCaller, writing_rules: str,
                              template: str, redact: Redactor = NULL_REDACTOR,
                              max_attempts: int = 2,
                              max_scenarios_per_call: int | None = None,
                              prior_chunks: dict | None = None,
                              sme_notes: dict | None = None) -> DocResult:
    """Single-member version: brief -> call -> validate -> retry once.
    Used directly by `mfdoc test-gen` and by run_test_batch's per-item work.

    A member whose test_case set exceeds `max_scenarios_per_call` (default
    DEFAULT_MAX_SCENARIOS_PER_CALL) renders as several independent chunk
    documents instead -- see _generate_member_test_doc_chunked (`prior_chunks`
    is only meaningful on that path; a single-call member has nothing to
    reuse per-chunk). The ambiguous-name and no-test_case-rows cases fall
    through to the original single-call path unchanged (test_case_brief
    already reports both as prose in the brief itself, which the model then
    fails to turn into a valid document -- existing, unchanged behaviour,
    not something this change alters).

    A member that *shrinks* back under the chunking threshold between
    runs (issue #195 review) is cleaned up here symmetrically to the
    chunked path's own cleanup: any `.chunk<N>{suffix}` file (and its
    sidecar) left over from a prior chunked render of this same member is
    now orphaned -- this single-document path never revisits or
    overwrites them, but a full tree walk (`mfdoc test-validate`) still
    finds and validates them independently, where their stale manifests/
    sidecars can produce the exact false staleness failures this whole
    mechanism exists to prevent."""
    system, rows, ambiguous_libs = fetch_test_case_rows(conn, member_name)
    threshold = _resolve_max_scenarios_per_call(max_scenarios_per_call)
    if not ambiguous_libs and rows and len(rows) > threshold:
        return _generate_member_test_doc_chunked(
            conn, member_name, system, rows, language, framework, out_path, caller,
            writing_rules, template, redact, max_attempts, threshold,
            prior_chunks=prior_chunks, sme_notes=sme_notes,
        )

    _prune_stale_test_chunk_files(out_path, set(), language)
    brief = test_case_brief(conn, member_name, redact=redact, sme_notes=sme_notes)
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
                       redact: Redactor = NULL_REDACTOR,
                       sme_notes: dict | None = None) -> str:
    """Fingerprint of every input to test_case_brief() that isn't the derive
    code itself, via batch._corpus_signature's `extra` hook, plus:

    - language/framework this run targets, since the same test_case rows
      render to a different file per target;
    - every test_case's full content (scenario_name, status, citation, and
      the given/when/then JSON blobs), since a human promoting a
      test-overlay.yml entry past `draft` (which testplan.py folds into
      test_case.status on the next `mfdoc test-plan`) changes what
      test_case_brief() renders for that scenario without touching any
      source_file -- the source-only signature above can't see that on its
      own, and this run's corpus-level skip must not treat it as unchanged.
      Hashing more than just (scenario_name, status) matters for issue
      #195's staleness guard too: a `classify-rules`/`derive` rebuild that
      happens to reassign the *same* `scenario_name` strings to
      *different* underlying `rule_candidate` rows (same count, same
      positional BR-numbering, different citation/condition/source
      excerpt behind each id) would leave (scenario_name, status) pairs
      unchanged even though the corpus genuinely changed underneath --
      `corpus_unchanged` would then wrongly gate every member through the
      fast skip path in `run_test_batch`/`plan_test_batch` below, bypassing
      `_test_chunk_reuse_ok`'s own per-chunk sidecar-staleness check
      entirely and never refreshing a sidecar that predates the rebuild.
      Including `citation` and the three JSON blobs closes that gap: any
      change to what a scenario actually asserts moves this signature,
      regardless of whether its `scenario_name` also moved;
    - each test_case's member's `system` (`test_case_brief()` includes it
      in the rendered header per `testplan.render_test_case_brief`), since
      re-ingesting after only a `project.yml`/source `system:` relabel
      changes what the brief -- and therefore the rendered document --
      says without touching any `test_case` row itself;
    - the effective max_scenarios_per_call threshold, since raising or
      lowering it can flip a member between the single-doc and chunked
      output shapes without any test_case row or status changing at all --
      the two checks above wouldn't see that either, and a stale "nothing
      changed" skip would leave the previous run's now-wrong-shaped output
      (or count of chunk files) in place;
    - every member's `rule_candidate` `(id, line_no)` sequence and
      `routine` boundary rows, in the same order `test_case_brief_chunk`'s
      routine-aware chunk planning
      (`brief.routine_aware_chunk_ranges`/`fetch_routines`) and
      `testplan.member_rule_fingerprint`'s BR-numbering both read them.
      `test_case`'s own columns above only capture a *derived* rule's
      content -- a `rule_candidate` inserted, removed, or reordered by a
      `derive` rebuild (the same shift `member_rule_fingerprint`'s
      per-document fingerprint exists to catch, see `validate.
      validate_test_doc`) can, before `mfdoc test-plan` re-runs to
      reflect it in `test_case`, leave every `test_case` row (and
      therefore everything hashed above) untouched while chunk boundaries
      and BR-numbering have already moved underneath. Without this, the
      corpus-level fast path here could gate every member through
      `corpus_unchanged` and skip straight past `_test_chunk_reuse_ok`'s
      own per-chunk sidecar-staleness check entirely;
    - each `test_case` row's own `rule_candidate_id` link, not just its
      derived content -- reassigning which existing `rule_candidate` row a
      scenario points to (without inserting/removing any row, or changing
      that scenario's own stored columns) is a narrower case than the
      point above, but the same principle: `test_case_brief_chunk`'s
      routine-aware chunk layout is keyed off this link
      (`rule_line_no`/`fetch_test_case_rows`), so a change here can move
      what a chunk renders even when nothing else this function already
      hashes would show it.
    """
    rows = conn.execute(
        "SELECT tc.scenario_name, tc.status, tc.citation, tc.given_json, tc.when_json, "
        "       tc.then_json, tc.rule_candidate_id, m.system "
        "FROM test_case tc JOIN member m ON m.id = tc.member_id "
        "ORDER BY tc.scenario_name"
    ).fetchall()
    extra = [language, framework, str(threshold)]
    for r in rows:
        extra.extend((r["scenario_name"], r["status"], r["citation"],
                      r["given_json"], r["when_json"], r["then_json"],
                      str(r["rule_candidate_id"]), r["system"] or ""))

    rc_rows = conn.execute(
        "SELECT rc.id, rc.member_id, rc.line_no FROM rule_candidate rc ORDER BY rc.member_id, rc.line_no, rc.id"
    ).fetchall()
    for r in rc_rows:
        extra.extend((str(r["member_id"]), str(r["line_no"]), str(r["id"])))

    routine_rows = conn.execute(
        "SELECT member_id, name, start_line, end_line FROM routine ORDER BY member_id, start_line, name"
    ).fetchall()
    for r in routine_rows:
        extra.extend((str(r["member_id"]), r["name"], str(r["start_line"]), str(r["end_line"])))

    return _base_corpus_signature(conn, redact=redact, sme_notes=sme_notes, extra=extra)


def _checkpoint(state: dict, state_path: Path | None, corpus_sig: str | None) -> None:
    """Persist `state` to `state_path` immediately -- called after every
    member this run finishes (single-call or chunked), not just once at the
    very end, so a crash partway through (a caller exception this harness
    doesn't already turn into an ok=False result, or an external kill) loses
    at most the one member in flight, not every member already completed
    since the run started. This is member-granular, not chunk-granular: a
    chunked member's chunks are all rendered by one
    generate_member_test_doc/_generate_member_test_doc_chunked call before
    its result is checkpointed here (mirrors batch.py's run_batch, which
    makes the identical member-wide trade-off for module docs), so a crash
    partway through one large chunked member's chunks still loses that
    member's progress on this pass, even though a sibling member's chunk
    failure is isolated and reported per-chunk within the same call. Folding
    `corpus_sig` into every checkpoint, not just the final one, is safe, not
    just convenient: a member this run hasn't reached yet has no "ok": True
    entry of its own, so a resumed run's corpus-level skip still can't
    wrongly skip it even though `_corpus_sha256` already matches -- and
    doing this early is what lets that fast path benefit the members that
    did finish before a crash, instead of only ones from a run that reached
    its own end cleanly. No-op when `state_path` is None (resume tracking
    disabled)."""
    if state_path is None:
        return
    state["_corpus_sha256"] = corpus_sig
    _save_state(state_path, state)


def _apply_test_cache_prefix(caller: ModelCaller, writing_rules: str, template: str,
                              language: str, framework: str) -> None:
    """Hand `caller` this run's stable test-prompt prefix (issue #168,
    mirroring batch.py's `_apply_cache_prefixes` from #159), once, up front
    -- every build_test_prompt call for this language/framework target in
    one project run shares the same writing_rules/template/language/
    framework text, so there's no reason to recompute or re-send this per
    call. Only a caller that opts in by exposing `set_cache_prefixes`
    (AnthropicCaller, VertexCaller) is touched at all -- `getattr(...,
    None)` leaves ClaudeCLICaller and the fake-echo test caller (neither has
    any such method, nor any equivalent to `cache_control`) completely
    untouched."""
    set_cache_prefixes = getattr(caller, "set_cache_prefixes", None)
    if set_cache_prefixes is None:
        return
    set_cache_prefixes([build_test_prompt_cache_prefix(writing_rules, template, language, framework)])


def run_test_batch(conn, members: list[str], language: str, framework: str, out_dir: Path,
                    caller: ModelCaller, writing_rules: str, template: str,
                    redact: Redactor = NULL_REDACTOR, concurrency: int = 4,
                    state_path: Path | None = None,
                    max_scenarios_per_call: int | None = None,
                    sme_notes: dict | None = None) -> TestBatchSummary:
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
    _apply_test_cache_prefix(caller, writing_rules, template, language, framework)
    state = _load_state(state_path) if state_path else {}
    corpus_sig = (
        _corpus_signature(conn, language, framework, threshold, redact, sme_notes) if state_path else None
    )
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
            logger.debug("skip %s: unchanged (corpus signature match, resumed)", name)
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
        #
        # `member_rule_fingerprint` folded in too (issue #195 review): a
        # `derive` rebuild that inserts/reorders this member's own
        # `rule_candidate` rows before `mfdoc test-plan` re-runs leaves
        # `test_case_brief()`'s output (and therefore `brief` above)
        # completely unchanged -- it only ever reads `test_case`, never
        # `rule_candidate` directly -- so without this, this per-member
        # skip (a *different* resume layer from `_corpus_signature`'s
        # global one, which this alone doesn't fix) would still wrongly
        # treat the member as unchanged and skip re-rendering, leaving a
        # stale sidecar in place indefinitely.
        #
        # Known, accepted narrower gap (Copilot review): `member_rule_
        # fingerprint` hashes `rule_candidate`'s own `(id, line_no)` set,
        # not which `test_case` row's `rule_candidate_id` points at which
        # one -- a hypothetical rebuild that *relinks* an existing
        # `test_case` row to a *different* existing `rule_candidate` row,
        # with every other column (both rows' own content, the
        # rule_candidate set/ordering itself) byte-identical, wouldn't
        # move either this fingerprint or `brief` above. Not fixed here:
        # `build_member_test_cases` derives `rule_candidate_id` and every
        # other `test_case` column together, positionally, from the same
        # `numbered_rule_candidates()` pass, so a relink with literally
        # nothing else different isn't a shape the real derive/test-plan
        # pipeline produces -- closing it would mean hashing
        # `fetch_test_case_rows`' relationship data on a purely
        # theoretical case this per-member skip has no real pathway to.
        brief = test_case_brief(conn, name, redact=redact, sme_notes=sme_notes)
        rule_fp = member_rule_fingerprint(conn, name) or ""
        brief_hash = hashlib.sha256(f"{brief}\x00{threshold}\x00{rule_fp}".encode("utf-8")).hexdigest()
        if prior_ok and prior.get("brief_sha256") == brief_hash:
            logger.debug("skip %s: unchanged (brief hash match, resumed)", name)
            results.append(_skip_result(name, out_path, prior))
            continue

        _, rows, ambiguous_libs = fetch_test_case_rows(conn, name)
        if not ambiguous_libs and rows and len(rows) > threshold:
            to_run_chunked.append((name, brief_hash, out_path))
        else:
            # This member is going through the single-document pool loop
            # below, not _generate_member_test_doc_chunked -- a member
            # that shrunk back under the threshold since a prior chunked
            # render (issue #195 review) needs the identical leftover-
            # chunk-file cleanup that path already gets, since this
            # dispatch loop (run_test_batch's own, not
            # generate_member_test_doc's) never calls that function at
            # all for a non-chunked member.
            _prune_stale_test_chunk_files(out_path, set(), language)
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
            input_tokens = output_tokens = 0
            attempts = 1
            # fut.result() re-raises whatever `caller` itself raised (a
            # `claude -p` timeout is a real subprocess.TimeoutExpired/
            # RuntimeError, not hypothetical -- see claude_cli_caller.py's
            # DEFAULT_TIMEOUT_S). Left uncaught, this single member's
            # exception would propagate out of the as_completed loop and
            # abort the whole batch -- discarding every other future's
            # (already-finished, or about to finish) result with no state
            # saved for any of them. Retried once here, synchronously,
            # exactly like a validation failure gets retried below -- a
            # transient timeout on the very first call deserves the same
            # second chance a bad-but-successful response gets, not an
            # immediate ok=False. `initial_exc_problem` (kept even when the
            # retry then produces a response) is prefixed into this
            # member's final problems on any later failure, so the original
            # exception is never silently lost the way a plain overwrite
            # would lose it.
            initial_exc_problem = None
            try:
                response = fut.result()
            except Exception as exc:
                initial_exc_problem = f"model call raised {exc.__class__.__name__}: {exc}"
                logger.warning("%s: %s, retrying once", name, initial_exc_problem)
                retry_prompt = build_test_prompt(
                    briefs[name], writing_rules, template, language, framework,
                    f"- {initial_exc_problem}",
                )
                try:
                    response = caller(retry_prompt)
                except Exception as exc2:
                    logger.error(
                        "%s: retry model call raised %s: %s", name, exc2.__class__.__name__, exc2,
                        exc_info=True,
                    )
                    result = DocResult(
                        name, str(out_path), False, 2, 0, 0,
                        [initial_exc_problem, f"retry model call raised {exc2.__class__.__name__}: {exc2}"],
                    )
                    results.append(result)
                    state[state_keys[name]] = {
                        "ok": False, "attempts": 2, "brief_sha256": brief_hash,
                    }
                    _checkpoint(state, state_path, corpus_sig)
                    continue
                attempts = 2
            input_tokens, output_tokens = response.input_tokens, response.output_tokens

            out_path.parent.mkdir(parents=True, exist_ok=True)
            # Captured before this call's first write to out_path -- see
            # _prior_fingerprint_for's docstring (issue #195 review): a
            # freshly-generated candidate's own front matter never carries
            # test_case_fingerprint itself, and out_path is about to be
            # overwritten with it.
            prior_fingerprint = _prior_fingerprint_for(out_path)
            final_text = _fix_generated_by_version(response.text)
            out_path.write_text(final_text, encoding="utf-8")
            validation = validate_test_doc(
                conn, out_path, _prior_fingerprint=prior_fingerprint, _render_time=True,
            )

            # Issue #188 review: this pool loop is the ordinary `mfdoc
            # test-batch` path for every non-chunked member -- the common
            # case a real run's cost is actually measured against.
            # `_generate_test_doc_from_brief`'s near-miss/targeted-patch
            # mechanism (see that function's docstring) only reaches
            # generate_member_test_doc's chunked members and a direct
            # single-member `mfdoc test-gen` call, never this loop, so it's
            # applied here too, inline -- mirroring that function's near-
            # miss branch as closely as this loop's own shape (the initial
            # response already dispatched to the pool, rather than built
            # fresh each attempt) allows.
            if not validation["ok"] and attempts == 1 and _is_near_miss(validation):
                uncited, reversed_findings = _localized_findings(validation)
                patched = False
                if uncited:
                    # Issue #171's deterministic, no-model-call auto-citation
                    # pass -- see _generate_test_doc_from_brief.
                    auto = _auto_cite_uncited_assertions(briefs[name], final_text, uncited)
                    if auto is not None:
                        candidate_text, remaining_uncited = auto
                        candidate_result = validate_test_doc(
                            conn, out_path, _text=candidate_text, _prior_fingerprint=prior_fingerprint,
                            _render_time=True,
                        )
                        if candidate_result["ok"]:
                            logger.info(
                                "%s: %d near-miss uncited assertion(s) auto-cited from the "
                                "brief with no model call; document now validates clean",
                                name, len(uncited) - len(remaining_uncited),
                            )
                            final_text = candidate_text
                            out_path.write_text(final_text, encoding="utf-8")
                            validation = candidate_result
                            patched = True
                        elif _is_near_miss(candidate_result):
                            logger.info(
                                "%s: %d/%d near-miss uncited assertion(s) auto-cited from "
                                "the brief with no model call; %d still need a targeted patch",
                                name, len(uncited) - len(remaining_uncited), len(uncited),
                                len(remaining_uncited),
                            )
                            final_text = candidate_text
                            out_path.write_text(final_text, encoding="utf-8")
                            validation = candidate_result
                            uncited, reversed_findings = _localized_findings(candidate_result)
                        # else: the candidate is no longer a near-miss --
                        # discard it and fall through to the patch prompt
                        # using the original, unpatched final_text/uncited/
                        # reversed_findings.
                if not patched:
                    logger.warning(
                        "%s: validation failed with %d near-miss localized finding(s) "
                        "only (%d uncited, %d reversed-condition) -- trying a targeted "
                        "patch before a full retry",
                        name, len(uncited) + len(reversed_findings),
                        len(uncited), len(reversed_findings),
                    )
                    patch_prompt = build_localized_test_patch_prompt(
                        briefs[name], final_text, uncited, reversed_findings
                    )
                    try:
                        patch_response = caller(patch_prompt)
                    except Exception as exc:
                        logger.warning(
                            "%s: targeted patch model call raised %s: %s -- falling "
                            "back to a full retry",
                            name, exc.__class__.__name__, exc,
                        )
                        validation = {
                            "ok": False,
                            "problems": list(validation["problems"]) + [
                                f"targeted patch model call raised {exc.__class__.__name__}: {exc}"
                            ],
                        }
                    else:
                        input_tokens += patch_response.input_tokens
                        output_tokens += patch_response.output_tokens
                        final_text = _fix_generated_by_version(patch_response.text)
                        out_path.write_text(final_text, encoding="utf-8")
                        validation = validate_test_doc(
                            conn, out_path, _prior_fingerprint=prior_fingerprint, _render_time=True,
                        )
                        if validation["ok"]:
                            patched = True
                        else:
                            logger.warning(
                                "%s: targeted patch attempt did not resolve validation "
                                "(%d problem(s)); falling back to a full retry",
                                name, len(validation["problems"]),
                            )
                # A resolved near-miss (auto-cited or model-patched) doesn't
                # consume the one full-retry attempt below -- `attempts`
                # stays 1, matching _generate_test_doc_from_brief's
                # contract. An unresolved one falls straight into the
                # existing full-retry block below, unchanged, using
                # whatever final_text/validation this near-miss attempt
                # left behind.

            if not validation["ok"] and attempts == 1:
                logger.warning(
                    "%s: validation failed (%d problem(s)), retrying once",
                    name, len(validation["problems"]),
                )
                retry_note = "\n".join(f"- {p}" for p in validation["problems"])
                retry_prompt = build_test_prompt(
                    briefs[name], writing_rules, template, language, framework, retry_note
                )
                try:
                    retry_response = caller(retry_prompt)
                except Exception as exc:
                    logger.error(
                        "%s: retry model call raised %s: %s", name, exc.__class__.__name__, exc,
                        exc_info=True,
                    )
                    validation = {
                        "ok": False,
                        "problems": list(validation["problems"])
                        + [f"retry model call raised {exc.__class__.__name__}: {exc}"],
                    }
                    attempts = 2
                else:
                    input_tokens += retry_response.input_tokens
                    output_tokens += retry_response.output_tokens
                    final_text = _fix_generated_by_version(retry_response.text)
                    out_path.write_text(final_text, encoding="utf-8")
                    validation = validate_test_doc(
                        conn, out_path, _prior_fingerprint=prior_fingerprint, _render_time=True,
                    )
                    attempts = 2
            elif not validation["ok"] and initial_exc_problem is not None:
                # This response already came from the exception-triggered
                # retry above (attempts == 2 already) -- no third attempt;
                # just make sure the original exception isn't lost from the
                # reported problems.
                validation = {
                    "ok": False,
                    "problems": [initial_exc_problem] + list(validation["problems"]),
                }

            if validation["ok"]:
                write_test_doc_with_sidecar(conn, name, out_path, final_text, language)

            result = DocResult(
                name, str(out_path), validation["ok"], attempts, input_tokens, output_tokens,
                validation.get("problems", []),
            )
            results.append(result)
            state[state_keys[name]] = {
                "ok": result.ok, "attempts": attempts, "brief_sha256": brief_hash,
            }
            _checkpoint(state, state_path, corpus_sig)

    # Large members (chunked) render serially, on this thread, after the
    # pool above closes -- generate_member_test_doc touches `conn`
    # throughout (validate_test_doc between/after each chunk's model call),
    # and sqlite3 connections can't cross threads (the pool above only ever
    # calls `caller` off-thread, never `conn`, for exactly this reason). A
    # member large enough to need chunking is already the rare, expensive
    # case; trading its concurrency with the other members for correctness
    # here is the right call, not a regression worth chasing.
    for name, brief_hash, out_path in to_run_chunked:
        prior = state.get(state_keys[name])
        prior_chunks = prior.get("chunks") if isinstance(prior, dict) else None
        result = generate_member_test_doc(
            conn, name, language, framework, out_path, caller, writing_rules, template,
            redact=redact, max_scenarios_per_call=threshold, prior_chunks=prior_chunks,
            sme_notes=sme_notes,
        )
        results.append(result)
        state[state_keys[name]] = {
            "ok": result.ok, "attempts": result.attempts, "brief_sha256": brief_hash,
            "chunks": result.chunk_state,
        }
        _checkpoint(state, state_path, corpus_sig)

    _checkpoint(state, state_path, corpus_sig)

    return TestBatchSummary(
        results=sorted(results, key=lambda r: r.member),
        total_input_tokens=sum(r.input_tokens for r in results),
        total_output_tokens=sum(r.output_tokens for r in results),
        ok=sum(1 for r in results if r.ok),
        failed=sum(1 for r in results if not r.ok),
        skipped=sum(1 for r in results if r.skipped),
    )


@dataclass
class TestMemberPlan:
    """One member's resume estimate from `plan_test_batch` -- what
    `run_test_batch` would actually do for this member/target, computed the
    same way but with no model call and no write. `status` is one of:

    - "skip": corpus- or member-level resume hit -- run_test_batch would
      call `test_case_brief()` at most once (member-level check) and no
      model.
    - "render": a normal (non-chunked) member that will make exactly one
      model call (plus a possible validation retry).
    - "chunked": an over-threshold member rendered as several chunks --
      `chunk_count`/`chunks_reusable` describe how many of those chunks
      would actually need a model call versus be reused from prior state.

    Deliberately has no `narrative_reusable` equivalent to batch.py's
    `MemberPlan`: a chunked test-batch member's index document
    (`_render_chunk_index`) is built deterministically from already-
    validated chunks, never its own model call the way a chunked module
    doc's whole-module reconciliation call is -- there's nothing on that
    axis for a preview to estimate."""
    member: str
    status: str
    chunk_count: int | None = None
    chunks_reusable: int | None = None

    @property
    def chunks_to_render(self) -> int | None:
        if self.chunk_count is None or self.chunks_reusable is None:
            return None
        return self.chunk_count - self.chunks_reusable


@dataclass
class TestBatchPlan:
    """Whole-run resume estimate from `plan_test_batch` for one language/
    framework target -- see that function's docstring. Aggregates
    TestMemberPlan entries into the totals cli.py prints before a real
    `mfdoc test-batch` run spends any model calls (issue #190, porting
    batch.py's `BatchPlan` from #160). Scoped to a single target rather than
    a whole `--matrix` run, mirroring `run_test_batch` itself (one call per
    target) -- `cmd_test_batch` prints one of these per target in the loop
    it already has."""
    language: str
    framework: str
    corpus_unchanged: bool
    members: list[TestMemberPlan]

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


def plan_test_batch(conn, members: list[str], language: str, framework: str, out_dir: Path,
                     redact: Redactor = NULL_REDACTOR,
                     state_path: Path | None = None,
                     max_scenarios_per_call: int | None = None,
                     sme_notes: dict | None = None) -> TestBatchPlan:
    """Cheap, local, no-model-call preview of what `run_test_batch` over
    these same arguments would actually do -- every brief a real run would
    render is computed and hashed exactly the same way (corpus-level check,
    then per-member brief hash, then -- for an over-threshold member --
    each chunk's own brief hash via the same `_test_chunk_reuse_ok` a real
    chunked render uses), but no model is ever called and nothing is
    written to disk. Ports `batch.plan_batch` (issue #160) to testbatch.py's
    own independent `prior_chunks`/`brief_sha256` resume-state machinery
    (issue #190) -- meant to be read before committing to a real `mfdoc
    test-batch` run, the same way `mfdoc batch --dry-run` is read before a
    real `mfdoc batch` run.

    This exists because a "resume" can silently be a full regeneration in
    disguise: a scenario's `MEMBER:BR-nnn` id (see testplan.py's
    `numbered_rule_candidates`/`citations._rule_id`, the same numbering
    `batch.plan_batch`'s docstring describes for module docs) is a position
    in that member's whole rule list, so a single rule added or removed
    anywhere earlier in the same member renumbers every later scenario and
    changes every later chunk's brief hash, even when that chunk's own
    routine's facts are otherwise unchanged. There is no way to tell from
    the CLI invocation alone whether a given resume will actually hit the
    per-chunk cache or fully re-render every chunk -- this function does
    the same (cheap, deterministic) hashing work a real run would, up
    front, so that can be seen before it costs anything.

    Scoped to one `language`/`framework` target, mirroring `run_test_batch`
    itself -- a `--matrix` dry-run calls this once per target, exactly like
    a real `--matrix` run calls `run_test_batch` once per target."""
    threshold = _resolve_max_scenarios_per_call(max_scenarios_per_call)
    state = _load_state(state_path) if state_path else {}
    corpus_sig = (
        _corpus_signature(conn, language, framework, threshold, redact, sme_notes) if state_path else None
    )
    corpus_unchanged = bool(state_path) and state.get("_corpus_sha256") == corpus_sig

    plans: list[TestMemberPlan] = []
    for name in members:
        subdir = _output_subdir(conn, name)
        key = f"{subdir.as_posix()}::{name}::{language}::{framework}"
        out_path = out_dir / subdir / language / framework / f"{name}.md"
        prior = state.get(key)
        prior_ok = isinstance(prior, dict) and prior.get("ok") and out_path.exists()

        if corpus_unchanged and prior_ok:
            plans.append(TestMemberPlan(name, "skip"))
            continue

        brief = test_case_brief(conn, name, redact=redact, sme_notes=sme_notes)
        # Must match run_test_batch's own per-member brief_hash exactly
        # (rule_fp included, issue #195 review) -- a dry-run preview using
        # a differently-computed hash could report "skip" for a member the
        # real run would actually re-render, or vice versa.
        rule_fp = member_rule_fingerprint(conn, name) or ""
        brief_hash = hashlib.sha256(f"{brief}\x00{threshold}\x00{rule_fp}".encode("utf-8")).hexdigest()
        if prior_ok and prior.get("brief_sha256") == brief_hash:
            plans.append(TestMemberPlan(name, "skip"))
            continue

        system, rows, ambiguous_libs = fetch_test_case_rows(conn, name)
        if ambiguous_libs or not rows or len(rows) <= threshold:
            plans.append(TestMemberPlan(name, "render"))
            continue

        routines = fetch_routines(conn, rows[0]["member_id"])
        line_nos = [r["rule_line_no"] if r["rule_line_no"] is not None else -1 for r in rows]
        ranges = routine_aware_chunk_ranges(line_nos, routines, threshold)
        chunk_count = len(ranges)
        chunk_width = len(str(chunk_count))
        prior_chunks = prior.get("chunks") if isinstance(prior, dict) else None
        chunks_reusable = 0
        for i, (start, end) in enumerate(ranges, start=1):
            chunk_path = out_path.with_name(f"{out_path.stem}.chunk{i:0{chunk_width}d}{out_path.suffix}")
            chunk_rows = rows[start - 1:end]
            chunk_brief = test_case_brief_chunk(
                name, system, chunk_rows, i, chunk_count, redact=redact, routines=routines,
                sme_notes=sme_notes,
            )
            chunk_hash = hashlib.sha256(chunk_brief.encode("utf-8")).hexdigest()
            if _test_chunk_reuse_ok(conn, prior_chunks, i, chunk_hash, chunk_path, readonly=True):
                chunks_reusable += 1

        plans.append(TestMemberPlan(
            name, "chunked", chunk_count=chunk_count, chunks_reusable=chunks_reusable,
        ))

    return TestBatchPlan(
        language=language, framework=framework, corpus_unchanged=corpus_unchanged, members=plans,
    )

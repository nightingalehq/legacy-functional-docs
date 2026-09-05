"""mfdoc — command line for the legacy-functional-docs pipeline.

    mfdoc ingest   --config project.yml
    mfdoc derive   --config project.yml
    mfdoc coverage --config project.yml
    mfdoc gate     --config project.yml
    mfdoc calibrate --config project.yml --dialect mantis
    mfdoc brief    --config project.yml [--module NAME | --entity NAME | --system]
    mfdoc rules-register --config project.yml --out docs/functional/rules-register.md
    mfdoc test-plan --config project.yml --out docs/functional/test-plan-register.md --overlay test-overlay.yml
    mfdoc test-overlay-draft --config project.yml --out test-overlay.yml
    mfdoc test-advisory --config project.yml --out docs/functional/testability-report.md
    mfdoc test-gen   --config project.yml --member NAME --language python --framework pytest
    mfdoc test-batch --config project.yml --language python --framework pytest --out tests_generated
    mfdoc test-validate --config project.yml --docs tests_generated
    mfdoc batch    --config project.yml --out docs/functional/modules
    mfdoc validate --config project.yml --docs docs/functional
    mfdoc sample-citations --config project.yml --docs docs/functional --judge human
    mfdoc export   --config project.yml --json out/index.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

import yaml

from . import brief as brief_mod
from . import graph, normalise
from . import testadvisor as testadvisor_mod
from . import testplan as testplan_mod
from .db import add_gap, connect, insert, purge_member, purge_member_facts, set_metric, upsert_member
from .dialects import adabas, environment, mantis, natural, screen, supra
from .redact import Redactor

VERSION = "0.1.0"

DIALECT_ROUTER = {
    "natural": lambda conn, mid, lines, name: natural.extract(conn, mid, lines, name),
    "mantis": lambda conn, mid, lines, name: mantis.extract(conn, mid, lines, name),
    "adabas_fdt": lambda conn, mid, lines, name: adabas.extract_fdt(conn, mid, lines, name),
    "ddm": lambda conn, mid, lines, name: adabas.extract_ddm(conn, mid, lines, name),
    "supra_dir": lambda conn, mid, lines, name: supra.extract(conn, mid, lines, name),
    "sql_ddl": lambda conn, mid, lines, name: environment.extract_sql_ddl(conn, mid, lines, name),
    "cobol_copybook": lambda conn, mid, lines, name: environment.extract_copybook(conn, mid, lines, name),
    "jcl": lambda conn, mid, lines, name: environment.extract_jcl(conn, mid, lines, name),
    "cics_csd": lambda conn, mid, lines, name: environment.extract_cics_csd(conn, mid, lines, name),
    "mantis_screen": lambda conn, mid, lines, name: screen.extract(conn, mid, lines, name),
}

DIALECT_DEFAULT_TYPE = {
    "ddm": "ddm", "adabas_fdt": "fdt", "supra_dir": "directory",
    "sql_ddl": "ddl", "cobol_copybook": "copybook", "jcl": "job", "cics_csd": "csd",
    "mantis_screen": "map",
}


def load_config(path: str | Path) -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    cfg.setdefault("index_db", ".mfdoc/index.db")
    cfg.setdefault("sources", [])
    cfg.setdefault("options", {})
    return cfg


def cmd_ingest(args) -> int:
    cfg = load_config(args.config)
    base = Path(args.config).parent
    conn = connect(base / cfg["index_db"])
    run_id = insert(conn, "ingest_run", started_at=_dt.datetime.now().isoformat(timespec="seconds"),
                    tool_version=VERSION, config_json=json.dumps(cfg))
    opts = cfg["options"]
    splitters = dict(normalise.DEFAULT_SPLITTERS)
    for d, pats in (opts.get("splitters") or {}).items():
        splitters[d] = pats

    total_members = 0
    skipped_unchanged = 0
    for spec in cfg["sources"]:
        root = (base / spec["path"]).resolve()
        globs = spec.get("glob") or ["**/*"]
        hint = spec.get("dialect")
        library = spec.get("library")
        system = spec.get("system")
        forced_enc = spec.get("encoding")
        seq_cfg = spec.get("sequence_columns", "auto")

        files: list[Path] = []
        if root.is_file():
            files = [root]
        else:
            for g in globs:
                files.extend(p for p in root.glob(g) if p.is_file())
        if not files:
            print(f"  ! no files matched {root} {globs}", file=sys.stderr)

        for path in sorted(set(files)):
            try:
                lines, enc, sha = normalise.read_source(path, forced_enc)
            except normalise.SourceTooLargeError as exc:
                add_gap(conn, "source_too_large", str(exc), severity="high")
                print(f"  ! skipped {path}: {exc}", file=sys.stderr)
                continue

            # Incremental ingest: a file whose content hasn't changed since
            # the last run produces byte-identical facts if re-extracted, so
            # skip it outright rather than paying the parse+extract cost
            # again. A changed file keeps its source_file row (UPDATEd
            # below, not delete-and-reinsert) so that upsert_member can still
            # match this file's members by name/library/dialect and reuse
            # their existing ids -- member identity across a content change
            # should be stable for anything that references a member_id
            # externally, not just re-derived every time.
            existing_sf = conn.execute(
                "SELECT id, sha256 FROM source_file WHERE path=?", (str(path),)
            ).fetchone()
            if existing_sf and existing_sf["sha256"] == sha:
                skipped_unchanged += 1
                continue
            # Members this file owned before this (re-)ingest -- anything in
            # here that the new chunking doesn't touch no longer exists in
            # the changed file and must be purged outright, not left behind
            # as a stale row nothing will ever update again.
            prior_member_ids = set()
            if existing_sf:
                prior_member_ids = {
                    r["id"] for r in conn.execute(
                        "SELECT id FROM member WHERE source_file_id=?", (existing_sf["id"],)
                    ).fetchall()
                }

            leading_seq_width = None
            if seq_cfg == "auto":
                seq_cols = normalise.detect_seq_columns(lines)
                # Only look for a leading sequence-number prefix when the
                # (better-attested) trailing field wasn't found -- the two
                # are mutually exclusive in practice, and trailing detection
                # should win any tie.
                if not seq_cols:
                    leading_seq_width = normalise.detect_leading_seq_prefix(lines)
            elif seq_cfg in (None, False, "none"):
                seq_cols = None
            else:
                a, b = str(seq_cfg).split(":")
                seq_cols = (int(a) - 1, int(b))

            text = "\n".join(lines)
            dialect = normalise.detect_dialect(text, hint)
            ranking = normalise.dialect_confidence(text)
            if seq_cols:
                seq_cols_record = f"{seq_cols[0] + 1}:{seq_cols[1]}"
            elif leading_seq_width:
                # "L<width>" -- distinct format from the trailing "start:end"
                # form above, but any non-empty value here means the same
                # thing to every consumer of this column: source_line.text
                # is not a byte-for-byte match of the file on disk, it has
                # had a sequence number stripped out of it.
                seq_cols_record = f"L{leading_seq_width}"
            else:
                seq_cols_record = None
            if existing_sf:
                sf_id = existing_sf["id"]
                conn.execute(
                    "UPDATE source_file SET sha256=?, encoding_in=?, seq_cols=?, "
                    "line_count=?, ingest_run_id=? WHERE id=?",
                    (sha, enc, seq_cols_record, len(lines), run_id, sf_id),
                )
            else:
                sf_id = insert(conn, "source_file", path=str(path), origin_path=str(path),
                               sha256=sha, encoding_in=enc,
                               seq_cols=seq_cols_record,
                               line_count=len(lines), ingest_run_id=run_id)

            if dialect == "unknown":
                add_gap(conn, "ambiguous_dialect",
                        f"Could not determine the dialect of {path.name}; it was skipped. "
                        f"Set `dialect:` explicitly for this source in project config.",
                        severity="high")
                for stale_id in prior_member_ids:
                    purge_member(conn, stale_id)
                continue
            if len(ranking) > 1 and ranking[0][1] < ranking[1][1] * 2 and not hint:
                add_gap(conn, "ambiguous_dialect",
                        f"{path.name} matched several dialect signatures {ranking[:3]}; "
                        f"processed as '{dialect}'. Confirm and pin it in project config.",
                        severity="medium")

            member_name, ext_hint = normalise.derive_member_name(path)
            chunks = normalise.split_members(
                lines, dialect, default_name=member_name, seq_cols=seq_cols,
                splitters=splitters, library=library, leading_seq_width=leading_seq_width)
            touched_member_ids = set()
            for ch in chunks:
                otype = ch.object_type
                if dialect == "natural" and not otype:
                    otype = normalise.infer_natural_object_type(
                        [t for _, _, t in ch.lines], ext_hint)
                mid = upsert_member(
                    conn, ch.name, dialect,
                    object_type=otype or DIALECT_DEFAULT_TYPE.get(dialect),
                    library=ch.library, system=system, source_file_id=sf_id,
                    first_line=ch.first_line, last_line=ch.first_line + len(ch.lines) - 1)
                purge_member_facts(conn, mid)
                DIALECT_ROUTER[dialect](conn, mid, ch.lines, ch.name)
                touched_member_ids.add(mid)
                total_members += 1
            # A member this file owned before a content change that the new
            # chunking no longer produces (a concatenated member removed
            # from the file, a banner pattern that no longer matches) no
            # longer exists -- purge it outright rather than leaving a
            # source_file_id-linked row nothing will ever touch again.
            for stale_id in prior_member_ids - touched_member_ids:
                purge_member(conn, stale_id)
            conn.commit()
        print(f"  ingested {spec['path']} -> {total_members} members so far")

    conn.commit()
    # Report the true total in the index, not just what this run touched --
    # on an incremental run where every file was skipped, total_members is
    # 0, and "ingest complete: 0 members" would read as the index having
    # been emptied rather than confirmed unchanged.
    index_total = conn.execute("SELECT COUNT(*) FROM member").fetchone()[0]
    skip_note = f", {skipped_unchanged} unchanged file(s) skipped" if skipped_unchanged else ""
    print(f"ingest complete: {index_total} members in index "
          f"({total_members} (re-)processed this run{skip_note})")
    return 0


def cmd_derive(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    res = graph.run_all(conn)
    print(json.dumps(res, indent=2))
    return 0


def _write_or_print(out: str, out_path: str | None) -> None:
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(out, encoding="utf-8")
        print(f"wrote {out_path}")
    else:
        print(out)


def cmd_brief(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])
    lexicon = ((cfg["options"] or {}).get("narrative") or {}).get("lexicon") or {}
    if args.system:
        out = brief_mod.system_brief(conn, redact=redact)
    elif args.module:
        out = brief_mod.module_brief(conn, args.module, redact=redact, lexicon=lexicon)
    elif args.entity:
        out = brief_mod.entity_brief(conn, args.entity, redact=redact, lexicon=lexicon)
    else:
        print("specify --module, --entity or --system", file=sys.stderr)
        return 2
    _write_or_print(out, args.out)
    return 0


def cmd_rules_register(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])
    out = brief_mod.rules_register(conn, redact=redact)
    _write_or_print(out, args.out)
    return 0


def _testgen_config(cfg: dict) -> dict:
    return (cfg["options"] or {}).get("testgen") or {}


def _testgen_matrix(testgen_cfg: dict) -> list[dict]:
    """options.testgen.matrix entries, or [] if absent -- each a
    {"language": ..., "framework": ..., "template": optional} dict, read
    verbatim from config. No built-in default matrix -- the set of
    destination targets a team wants is theirs to declare, not ours to
    guess (same posture CLAUDE.md already takes for redaction patterns
    and dialect assumptions)."""
    return list(testgen_cfg.get("matrix") or [])


def _testgen_matrix_error(targets: list) -> str | None:
    """First problem found in a resolved --matrix target list, or None.

    Every other config-shape error `cmd_test_gen`/`cmd_test_batch` already
    handle (missing --language/--framework, empty matrix, --matrix/
    --language mutual exclusion) exits 2 with a readable message --
    `options.testgen.matrix` is a brand-new, user-authored config key where
    a typo (a missing `framework:`, a bare scalar entry) is likely, and an
    unguarded `target["language"], target["framework"]` in the per-target
    loop would otherwise surface as a raw KeyError/AttributeError traceback
    instead of the same clean exit-2 treatment. Called once by each of
    `cmd_test_gen`/`cmd_test_batch` right after resolving targets, before
    their per-target loops start."""
    for i, target in enumerate(targets):
        if not isinstance(target, dict):
            return f"options.testgen.matrix entry {i} is not a mapping with 'language'/'framework': {target!r}"
        language = target.get("language")
        framework = target.get("framework")
        if not language or not isinstance(language, str):
            return f"options.testgen.matrix entry {i} is missing 'language'/'framework': {target!r}"
        if not framework or not isinstance(framework, str):
            return f"options.testgen.matrix entry {i} is missing 'language'/'framework': {target!r}"
    return None


def cmd_test_plan(args) -> int:
    cfg = load_config(args.config)
    base = Path(args.config).parent
    conn = connect(base / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])
    overlay = args.overlay or _testgen_config(cfg).get("overlay_path")
    overlay_path = (base / overlay) if overlay else None
    res = testplan_mod.run_all(conn, member_name=args.member, overlay_path=overlay_path)
    print(json.dumps(res, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(testplan_mod.test_plan_register(conn, redact=redact), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


def _build_model_caller(args):
    """Construct a ModelCaller from --caller/--provider/--model/--gcp-* args.

    Shared by cmd_batch and cmd_sample_citations's --judge llm mode, so the
    fake-echo / Anthropic / Vertex selection logic (and the Vertex
    --model-is-required guard) exists in exactly one place. Returns None
    (having already printed the error) on a configuration problem the
    caller should treat as an exit-1, rather than raising -- matches this
    module's existing convention of printing a human-readable reason before
    a non-zero exit, not a traceback.
    """
    from . import batch as batch_mod

    # getattr, not args.provider: any pre-existing caller building a bare
    # args object (a script, a notebook, an older test) without a
    # `provider` attribute must keep working exactly as it did before this
    # flag existed, not raise AttributeError.
    provider = getattr(args, "provider", "anthropic")

    if args.caller == "fake-echo":
        # For dry runs / CI smoke tests: no network call, no API key needed.
        def caller(prompt: str) -> batch_mod.ModelResponse:
            return batch_mod.ModelResponse(text=prompt, input_tokens=0, output_tokens=0)
        return caller
    if provider == "vertex":
        from .vertex_caller import VertexCaller
        if not args.model:
            print(
                "--provider vertex requires --model. Current-generation Claude models "
                "(e.g. claude-sonnet-4-5, claude-opus-4-1) use the same bare id on Vertex AI "
                "as on the direct Anthropic API; only legacy models use a Vertex-specific "
                "dated-snapshot id with an '@' separator (e.g. claude-3-5-sonnet-v2@20241022, "
                "not claude-3-5-sonnet-v2-20241022). See Vertex AI Model Garden for the "
                "current id for this model."
            )
            return None
        return VertexCaller(model=args.model, project=args.gcp_project, region=args.gcp_region)
    if provider == "claude-code":
        from .claude_cli_caller import ClaudeCLICaller
        # getattr, not args.claude_code_timeout: same bare-namespace
        # backward-compat concern as `provider` above.
        return ClaudeCLICaller(model=args.model, timeout=getattr(args, "claude_code_timeout", None))
    from .anthropic_caller import AnthropicCaller
    return AnthropicCaller(model=args.model or "claude-sonnet-4-5")


def cmd_test_overlay_draft(args) -> int:
    from . import testoverlay as testoverlay_mod
    from . import testbatch as testbatch_mod

    cfg = load_config(args.config)
    base = Path(args.config).parent
    conn = connect(base / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])
    out = args.out or _testgen_config(cfg).get("overlay_path") or "test-overlay.yml"

    members = ([m.strip().upper() for m in args.members.split(",")] if args.members
               else testbatch_mod.select_test_batch_members(conn))
    if not members:
        print("no test_case rows in the index -- run `mfdoc test-plan` first")
        return 0

    module_docs = {}
    if args.docs:
        from .batch import _output_subdir

        docs_dir = base / args.docs
        for name in members:
            doc_path = docs_dir / _output_subdir(conn, name) / f"{name}.md"
            if doc_path.exists():
                module_docs[name] = doc_path.read_text(encoding="utf-8")

    caller = _build_model_caller(args)
    if caller is None:
        return 1

    summary = testoverlay_mod.run_overlay_draft(conn, members, caller, base / out, module_docs, redact=redact)
    print(f"drafted {summary['drafted']} entr{'y' if summary['drafted']==1 else 'ies'} "
          f"across {summary['members']} member(s), {summary['skipped_promoted']} already "
          f"human-promoted entr{'y' if summary['skipped_promoted']==1 else 'ies'} left untouched")
    for p in summary["problems"]:
        print(f"  - {p}")
    print(f"wrote {out}")
    return 0


def cmd_test_advisory(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    out = testadvisor_mod.testability_report(conn)
    _write_or_print(out, args.out)
    return 0


def _test_template_path(base: Path, language: str, framework: str, override: str | None) -> Path:
    if override:
        return base / override
    return base / "templates" / "tests" / f"{language}_{framework}.md"


def cmd_test_gen(args) -> int:
    from . import testbatch as testbatch_mod

    cfg = load_config(args.config)
    base = Path(args.config).parent
    conn = connect(base / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])
    testgen_cfg = _testgen_config(cfg)

    if args.matrix and (args.language or args.framework):
        print("--matrix and --language/--framework are mutually exclusive -- "
              "pass one or the other", file=sys.stderr)
        return 2
    if args.matrix and args.out:
        print("--matrix renders multiple targets -- --out (a single path) doesn't "
              "apply; omit --out to use each target's default path", file=sys.stderr)
        return 2

    if args.matrix:
        targets = _testgen_matrix(testgen_cfg)
        if not targets:
            print("--matrix given, but no options.testgen.matrix entries in --config",
                  file=sys.stderr)
            return 2
        error = _testgen_matrix_error(targets)
        if error:
            print(error, file=sys.stderr)
            return 2
    else:
        language = args.language or testgen_cfg.get("default_language")
        framework = args.framework or testgen_cfg.get("default_framework")
        if not language or not framework:
            print("no --language/--framework given, and no options.testgen.default_language/"
                  "default_framework in --config", file=sys.stderr)
            return 2
        targets = [{"language": language, "framework": framework}]

    writing_rules = (base / "reference" / "test-writing-rules.md").read_text(encoding="utf-8")
    caller = _build_model_caller(args)
    if caller is None:
        return 1

    from .batch import _output_subdir

    member = args.member.strip().upper()
    out_dir = testgen_cfg.get("out_dir") or "tests_generated"
    any_failed = False
    for target in targets:
        language, framework = target["language"], target["framework"]
        template_override = target.get("template") or args.template
        template_path = _test_template_path(base, language, framework, template_override)
        if not template_path.exists():
            print(f"no template at {template_path} -- pass --template, or add one for "
                  f"--language {language} --framework {framework}", file=sys.stderr)
            if not args.matrix:
                # Single-target usage error -- exit 2 immediately, matching
                # this command's pre-existing (pre-matrix) behavior, rather
                # than falling through to the matrix path's "skip this
                # target, keep going" treatment below.
                return 2
            any_failed = True
            continue
        template = template_path.read_text(encoding="utf-8")

        out_path = (base / args.out if args.out
                    else base / out_dir / _output_subdir(conn, member) / language / framework / f"{member}.md")
        result = testbatch_mod.generate_member_test_doc(
            conn, member, language, framework, out_path, caller,
            writing_rules, template, redact=redact,
            max_scenarios_per_call=testgen_cfg.get("max_scenarios_per_call"),
        )
        status = "OK" if result.ok else "FAIL"
        print(f"{status} {result.member} [{language}/{framework}] -> {result.path} "
              f"attempts={result.attempts} in={result.input_tokens} out={result.output_tokens}")
        for p in result.problems:
            print(f"  - {p}")
        any_failed = any_failed or not result.ok
    return 1 if any_failed else 0


def cmd_test_batch(args) -> int:
    """Batch harness for generated tests -- the same option-C treatment
    `mfdoc batch` gives module docs, applied to test_case rows instead of
    module facts. Run `mfdoc test-plan` first; this never derives facts."""
    from . import testbatch as testbatch_mod

    cfg = load_config(args.config)
    base = Path(args.config).parent
    conn = connect(base / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])
    testgen_cfg = _testgen_config(cfg)

    if args.matrix and (args.language or args.framework):
        print("--matrix and --language/--framework are mutually exclusive -- "
              "pass one or the other", file=sys.stderr)
        return 2

    if args.matrix:
        targets = _testgen_matrix(testgen_cfg)
        if not targets:
            print("--matrix given, but no options.testgen.matrix entries in --config",
                  file=sys.stderr)
            return 2
        error = _testgen_matrix_error(targets)
        if error:
            print(error, file=sys.stderr)
            return 2
    else:
        language = args.language or testgen_cfg.get("default_language")
        framework = args.framework or testgen_cfg.get("default_framework")
        if not language or not framework:
            print("no --language/--framework given, and no options.testgen.default_language/"
                  "default_framework in --config", file=sys.stderr)
            return 2
        targets = [{"language": language, "framework": framework}]

    out_dir = args.out or testgen_cfg.get("out_dir") or "tests_generated"

    members = ([m.strip().upper() for m in args.members.split(",")] if args.members
               else testbatch_mod.select_test_batch_members(conn))
    if not members:
        print("no test_case rows in the index -- run `mfdoc test-plan` first")
        return 0

    writing_rules = (base / "reference" / "test-writing-rules.md").read_text(encoding="utf-8")
    caller = _build_model_caller(args)
    if caller is None:
        return 1

    grand_ok = grand_failed = grand_skipped = 0
    any_target_failed = False
    for target in targets:
        language, framework = target["language"], target["framework"]
        template_override = target.get("template") or args.template
        template_path = _test_template_path(base, language, framework, template_override)
        if not template_path.exists():
            print(f"no template at {template_path} -- pass --template, or add one for "
                  f"--language {language} --framework {framework}; skipping this target",
                  file=sys.stderr)
            if not args.matrix:
                # Single-target usage error -- exit 2 immediately, matching
                # this command's pre-existing (pre-matrix) behavior.
                return 2
            any_target_failed = True
            continue
        template = template_path.read_text(encoding="utf-8")

        if len(targets) > 1:
            print(f"\n=== {language}/{framework} ===")
        summary = testbatch_mod.run_test_batch(
            conn, members, language, framework, base / out_dir, caller,
            writing_rules, template, redact=redact, concurrency=args.concurrency,
            state_path=(base / args.state) if args.state else None,
            max_scenarios_per_call=testgen_cfg.get("max_scenarios_per_call"),
        )
        for r in summary.results:
            status = "SKIP" if r.skipped else ("OK  " if r.ok else "FAIL")
            print(f"{status} {r.member:<20} attempts={r.attempts} in={r.input_tokens} out={r.output_tokens}")
            for p in r.problems:
                print(f"       - {p}")
        print(f"\n{summary.ok}/{len(summary.results)} ok, {summary.failed} failed, "
              f"{summary.skipped} skipped (unchanged)")
        print(f"tokens: {summary.total_input_tokens} in, {summary.total_output_tokens} out")
        grand_ok += summary.ok
        grand_failed += summary.failed
        grand_skipped += summary.skipped
        any_target_failed = any_target_failed or summary.failed > 0

    if len(targets) > 1:
        print(f"\n=== grand total across {len(targets)} targets ===")
        print(f"{grand_ok} ok, {grand_failed} failed, {grand_skipped} skipped (unchanged)")

    return 1 if any_target_failed else 0


def cmd_batch(args) -> int:
    """Batch harness for the high-volume, formulaic module docs (option C).

    System overview, process flows and the gap register are deliberately
    not covered here -- generate those through the interactive CLI/Claude
    Code path in SKILL.md, where judgement calls about grouping and
    narrative structure matter more than throughput.
    """
    from . import batch as batch_mod

    cfg = load_config(args.config)
    base = Path(args.config).parent
    conn = connect(base / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])

    # Normalise the same way ingest does (normalise.derive_member_name /
    # split_members both .upper() the stored name) -- an un-normalised
    # --members value could otherwise collide with a reserved state-file key
    # such as run_batch()'s "_corpus_sha256" sentinel.
    members = ([m.strip().upper() for m in args.members.split(",")] if args.members
               else batch_mod.select_batch_members(conn))
    if not members:
        print("no batchable (natural/mantis program-level) members in the index")
        return 0

    writing_rules = (base / "reference" / "writing-rules.md").read_text(encoding="utf-8")
    template = (base / "templates" / "module.md").read_text(encoding="utf-8")

    caller = _build_model_caller(args)
    if caller is None:
        return 1

    narrative_opts = (cfg["options"] or {}).get("narrative") or {}
    pricing = narrative_opts.get("pricing") or {}
    lexicon = narrative_opts.get("lexicon") or {}
    summary = batch_mod.run_batch(
        conn, members, base / args.out, caller, writing_rules, template, redact=redact,
        concurrency=args.concurrency,
        state_path=(base / args.state) if args.state else None,
        cost_per_mtok_in=pricing.get("input_per_mtok"),
        cost_per_mtok_out=pricing.get("output_per_mtok"),
        lexicon=lexicon,
        max_rules_per_call=narrative_opts.get("max_rules_per_call"),
    )

    for r in summary.results:
        status = "SKIP" if r.skipped else ("OK  " if r.ok else "FAIL")
        print(f"{status} {r.member:<20} attempts={r.attempts} in={r.input_tokens} out={r.output_tokens}")
        for p in r.problems:
            print(f"       - {p}")

    print(f"\n{summary.ok}/{len(summary.results)} ok, {summary.failed} failed, "
          f"{summary.skipped} skipped (unchanged), {summary.retried} retried")
    print(f"tokens: {summary.total_input_tokens} in, {summary.total_output_tokens} out")
    if summary.cost_usd is not None:
        print(f"cost: ${summary.cost_usd:.4f}")
    else:
        print("cost: unknown -- set options.narrative.pricing.input_per_mtok/output_per_mtok in project.yml")
    return 0 if summary.failed == 0 else 1


# Where to go looking when a dialect's recognition rate is weak. Not a
# promise that these are the only tables involved -- a starting point for
# the two or three iterations calibration normally takes.
DIALECT_CALIBRATION_HINTS = {
    "natural": ("src/mfdoc/dialects/natural.py",
                "the RE_* statement patterns, or CONTINUATION_TAIL if conditions look truncated"),
    "mantis": ("src/mfdoc/dialects/mantis.py",
               "DECL_TYPES, COMMENT_PREFIXES, or the call/screen verb patterns"),
    "supra_dir": ("src/mfdoc/dialects/supra.py",
                  "LABELS or SUPRA_DML -- or override dialects.supra.labels in project config"),
    "adabas_fdt": ("src/mfdoc/dialects/adabas.py", "RE_FDT_PIPE / RE_FDT_WS field-row patterns"),
    "ddm": ("src/mfdoc/dialects/adabas.py", "RE_DDM_FIELD / RE_DDM_SUPER field-row patterns"),
    "jcl": ("src/mfdoc/dialects/environment.py", "RE_EXEC / RE_DD / INFRASTRUCTURE_DDS"),
    "cics_csd": ("src/mfdoc/dialects/environment.py", "the CSD resource-definition patterns"),
    "sql_ddl": ("src/mfdoc/dialects/environment.py", "the DDL statement patterns"),
    "cobol_copybook": ("src/mfdoc/dialects/environment.py", "the copybook PIC-clause patterns"),
}


def cmd_calibrate(args) -> int:
    """Rank unparsed_line gaps for one dialect by leading-keyword shape.

    Promotes the shape-analysis snippet that used to live embedded in
    reference/mantis-supra.md into a real command, so it cannot drift from
    the tool and does not depend on someone finding a code block in a doc.
    """
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    rows = conn.execute(
        """
        SELECT g.raw FROM gap g JOIN member m ON m.id = g.member_id
         WHERE g.gap_kind='unparsed_line' AND g.raw IS NOT NULL AND m.dialect=?
        """,
        (args.dialect,),
    ).fetchall()
    if not rows:
        print(f"no unparsed_line gaps for dialect '{args.dialect}' -- either it recognises "
              f"everything ingested, or nothing of this dialect was ingested")
        return 0

    shapes: dict[str, dict] = {}
    for r in rows:
        raw = (r["raw"] or "").strip()
        if not raw:
            continue
        kw = raw.split()[0].upper()
        entry = shapes.setdefault(kw, {"count": 0, "sample": raw})
        entry["count"] += 1

    hint_file, hint_constants = DIALECT_CALIBRATION_HINTS.get(
        args.dialect, (f"src/mfdoc/dialects/{args.dialect}.py", "the dialect's keyword tables"))
    print(f"unparsed-line shapes for dialect '{args.dialect}', ranked by frequency:")
    print(f"add recognised keywords to {hint_file} -- likely {hint_constants}")
    print()
    for kw, entry in sorted(shapes.items(), key=lambda kv: -kv[1]["count"])[: args.top]:
        print(f"{entry['count']:5}  {kw:<20} e.g. {entry['sample'][:100]!r}")
    return 0


def cmd_coverage(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    cov = graph.coverage(conn)
    conn.commit()
    print(json.dumps(cov, indent=2))
    gaps = conn.execute(
        "SELECT gap_kind, severity, COUNT(*) n FROM gap GROUP BY gap_kind, severity "
        "ORDER BY severity DESC, n DESC"
    ).fetchall()
    print("\ngaps by kind:")
    for g in gaps:
        print(f"  {g['severity']:>6}  {g['gap_kind']:<22} {g['n']}")
    json_out = getattr(args, "json", None)
    if json_out:
        # Stdout above is JSON followed by human-readable gap text on the
        # same stream -- not machine-parseable as-is. This writes just the
        # coverage numbers, cleanly, for a caller (e.g. CI) that wants them
        # as a standalone artifact.
        Path(json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(json_out).write_text(json.dumps(cov, indent=2), encoding="utf-8")
    return 0


# Each gate: (options key, coverage key, comparison, what a failure blocks).
# comparison is "min" (coverage must be >= threshold) or "max" (coverage must
# be <= threshold).
GATES = [
    ("min_line_recognition_rate", "line_recognition_rate", "min",
     "the dialect scanner is mismatched to this codebase; narrative built on "
     "unrecognised lines will miss business rules silently"),
    ("min_call_resolution_rate", "call_resolution_rate", "min",
     "source is missing for too many call targets; process-flow and process "
     "documentation will be incomplete"),
    ("min_entity_definition_rate", "entity_definition_rate", "min",
     "data definitions are missing for too many stores; field-level meaning "
     "cannot be documented and must not be guessed from field names"),
    ("max_high_severity_gaps", "gaps_high", "max",
     "too many unresolved high-severity items to write reliable narrative "
     "from; resolve or triage them first"),
    # Sampling-derived (mfdoc sample-citations --judge human), not computed
    # from facts -- absent from coverage() until that command has recorded
    # at least one verdict. cmd_gate's cov.get(cov_key, 0) then evaluates
    # this gate against 0, i.e. fails it -- correct: a codebase where
    # citation accuracy has never been sampled has no basis to claim any,
    # and a configured gate should say so rather than silently pass.
    ("min_citation_accuracy_rate", "citation_accuracy_rate", "min",
     "citation accuracy has not been sampled (or sampled claims were found "
     "inaccurate) -- run `mfdoc sample-citations --judge human` before "
     "representing citations as more than resolution-checked"),
]


def cmd_gate(args) -> int:
    """Evaluate coverage against options.quality_gates and exit non-zero on
    failure, so a weak index is a stop rather than an instruction a model
    (or a person under deadline) can skip."""
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    cov = graph.coverage(conn)
    conn.commit()
    gates = (cfg["options"] or {}).get("quality_gates") or {}

    failed = []
    for opt_key, cov_key, kind, blocks in GATES:
        if opt_key not in gates:
            continue
        threshold = gates[opt_key]
        actual = cov.get(cov_key, 0)
        ok = actual >= threshold if kind == "min" else actual <= threshold
        rel = ">=" if kind == "min" else "<="
        status = "PASS" if ok else "FAIL"
        print(f"{status}  {cov_key} = {actual}  (needs {rel} {threshold})")
        if not ok:
            gap = (threshold - actual) if kind == "min" else (actual - threshold)
            failed.append((opt_key, cov_key, actual, threshold, gap, blocks))

    if not failed:
        print("\nall configured gates passed")
        return 0

    print(f"\n{len(failed)} gate(s) failed:")
    for opt_key, cov_key, actual, threshold, gap, blocks in failed:
        print(f"  - {opt_key}: {cov_key}={actual}, needed {threshold} (off by {gap:.4g})")
        print(f"    blocks: {blocks}")
    return 1


def _print_problem_list(items: list, header: str) -> None:
    """Print `header` (a `str.format`-style template taking the item count)
    followed by one indented bullet per item in `items` -- shared by every
    report section in `cmd_validate` that's just a count-and-bullets summary,
    so a third such section (after `completeness_problems`,
    `omitted_statement_targets`) never needs to reinvent this shape."""
    if not items:
        return
    print(f"\n{header.format(len(items))}")
    for p in items:
        print(f"  - {p}")


def cmd_validate(args) -> int:
    from .validate import validate_tree
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    res = validate_tree(conn, Path(args.docs))
    for r in res["results"]:
        status = "OK " if r["ok"] else "FAIL"
        print(f"{status} {r['path']}  citations={r['citations']} invalid={r['invalid_citations']}")
        for p in r["problems"]:
            print(f"       - {p}")
    print(f"\n{res['documents_ok']}/{res['documents']} documents clean, "
          f"{res['invalid_citations']} invalid citations of {res['total_citations']}")
    _print_problem_list(res["completeness_problems"], "{} member(s) with incomplete rule coverage:")
    _print_problem_list(
        res["omitted_statement_targets"],
        "{} statement(s) referenced in cited ranges but not named in surrounding prose "
        "(advisory, does not fail validation):",
    )
    return 0 if (
        res["invalid_citations"] == 0
        and res["documents_ok"] == res["documents"]
        and not res["completeness_problems"]
    ) else 1


def cmd_test_validate(args) -> int:
    from .validate import validate_tests_tree
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    res = validate_tests_tree(conn, Path(args.docs))
    for r in res["results"]:
        status = "OK " if r["ok"] else "FAIL"
        print(f"{status} {r['path']}  citations={r['citations']} invalid={r['invalid_citations']} "
              f"invalid_scenario_refs={r.get('invalid_scenario_refs', 0)}")
        for p in r["problems"]:
            print(f"       - {p}")
    print(f"\n{res['documents_ok']}/{res['documents']} documents clean, "
          f"{res['invalid_citations']} invalid citations of {res['total_citations']}, "
          f"{res['invalid_scenario_refs']} invalid scenario refs")
    return 0 if res["invalid_citations"] == 0 and res["invalid_scenario_refs"] == 0 \
        and res["documents_ok"] == res["documents"] else 1


def cmd_sample_citations(args) -> int:
    """Sample generated claims against their cited source line(s) and record
    a verdict -- human first, to calibrate what "the source supports the
    claim" means for this kind of prose; an optional LLM-judge pass second,
    reported against the human labels rather than trusted standalone.

    mfdoc validate proves every citation resolves; this is the closest this
    project gets to proving a citation is right. See
    docs/guides/security-and-compliance.md for what the resulting
    citation_accuracy_rate figure does and does not guarantee -- it is
    always a sample, never a full-corpus check.
    """
    from . import sample as sample_mod

    cfg = load_config(args.config)
    base = Path(args.config).parent
    conn = connect(base / cfg["index_db"])
    state_path = base / args.state
    state = sample_mod.load_state(state_path)

    if args.judge != "report":
        doc_paths = sorted(Path(args.docs).rglob("*.md"))
        if not doc_paths:
            print(f"no documents found under {args.docs}")
            return 1
        samples = sample_mod.sample_claims(conn, doc_paths, args.n_per_doc, args.seed)
        state = sample_mod.merge_samples(state, samples)
        sample_mod.save_state(state_path, state)

    if args.judge == "human":
        pending = [sid for sid in state["samples"] if sid not in state["verdicts"]["human"]]
        print(f"{len(pending)} claim(s) awaiting a human verdict "
              f"({len(state['samples']) - len(pending)} already labelled)")
        for sid in pending:
            s = state["samples"][sid]
            print("\n" + "=" * 70)
            print(f"CLAIM  ({s['doc_path']}):\n  {s['claim']}")
            print(f"\nCITED SOURCE ({s['citation']}):\n  " + s["source_text"].replace("\n", "\n  "))
            answer = input("\nDoes the cited source support this claim? [y/n/skip]: ").strip().lower()
            if answer in ("y", "yes"):
                state["verdicts"]["human"][sid] = {"accurate": True, "note": ""}
            elif answer in ("n", "no"):
                note = input("one-line reason: ").strip()
                state["verdicts"]["human"][sid] = {"accurate": False, "note": note}
            else:
                continue
            sample_mod.save_state(state_path, state)  # resumable -- same reasoning as batch's state
    elif args.judge == "llm":
        if not state["verdicts"]["human"]:
            print("no human verdicts recorded yet -- run --judge human first. This project's "
                  "own writing-rules discipline applies here too: an LLM judge is not trusted "
                  "standalone until its agreement with a human pass has been checked.")
            return 1
        caller = _build_model_caller(args)
        if caller is None:
            return 1
        redact = Redactor.from_options(cfg["options"])
        for sid, s in state["samples"].items():
            if sid in state["verdicts"]["llm"]:
                continue
            verdict = sample_mod.judge_with_llm(caller, s, redact)
            state["verdicts"]["llm"][sid] = {"accurate": verdict.accurate, "note": verdict.reason}
            sample_mod.save_state(state_path, state)

    human_rate = sample_mod.accuracy_rate(state, "human")
    llm_rate = sample_mod.accuracy_rate(state, "llm")
    agreement = sample_mod.agreement_rate(state)
    print(f"\n{len(state['samples'])} claim(s) sampled")
    if human_rate is not None:
        print(f"human-judged accuracy: {human_rate:.2%} ({len(state['verdicts']['human'])} labelled)")
        # Human verdicts are the calibration ground truth for this feature,
        # so they're what backs the persisted metric coverage()/gate read --
        # an LLM-only rate never overwrites it, and report-mode with no
        # human verdicts yet correctly leaves the metric untouched.
        set_metric(conn, "global", "citation_accuracy_rate", human_rate)
        conn.commit()
    if llm_rate is not None:
        print(f"llm-judged accuracy:   {llm_rate:.2%} ({len(state['verdicts']['llm'])} labelled)")
    if agreement is not None:
        shared = len(set(state["verdicts"]["human"]) & set(state["verdicts"]["llm"]))
        print(f"human/llm agreement:   {agreement:.2%} (over {shared} shared claim(s))")
    return 0


def cmd_export(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(brief_mod.json_index(conn), encoding="utf-8")
    print(f"wrote {args.json}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mfdoc", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, fn in (("ingest", cmd_ingest), ("derive", cmd_derive), ("coverage", cmd_coverage),
                     ("gate", cmd_gate)):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True)
        p.set_defaults(func=fn)
    sub.choices["coverage"].add_argument(
        "--json", help="also write the coverage numbers (only -- not the gap breakdown "
                        "printed alongside them) as JSON to this path")

    p = sub.add_parser("calibrate")
    p.add_argument("--config", required=True)
    p.add_argument("--dialect", required=True)
    p.add_argument("--top", type=int, default=30)
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("brief")
    p.add_argument("--config", required=True)
    p.add_argument("--module")
    p.add_argument("--entity")
    p.add_argument("--system", action="store_true")
    p.add_argument("--out")
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("rules-register")
    p.add_argument("--config", required=True)
    p.add_argument("--out", help="write to this path instead of stdout")
    p.set_defaults(func=cmd_rules_register)

    p = sub.add_parser("test-plan")
    p.add_argument("--config", required=True)
    p.add_argument("--member", help="rebuild the plan for one member; default: every batchable member")
    p.add_argument("--out", help="also write the test-plan register to this path")
    p.add_argument("--overlay", help="test-overlay.yml path, relative to --config's directory; "
                                      "default: options.testgen.overlay_path from --config; omit "
                                      "both to leave every scenario at its default "
                                      "'characterization' status")
    p.set_defaults(func=cmd_test_plan)

    p = sub.add_parser("test-overlay-draft")
    p.add_argument("--config", required=True)
    p.add_argument("--out", default=None,
                    help="merged into this file -- an existing entry a human already "
                         "promoted past 'draft' is left untouched; default: "
                         "options.testgen.overlay_path from --config, else test-overlay.yml")
    p.add_argument("--members", help="comma-separated member names; default: every member "
                                      "with test_case rows")
    p.add_argument("--docs", help="directory of generated module docs (mfdoc batch's --out) to "
                                   "compare intended behaviour against; omit to draft from the "
                                   "test brief alone (divergence proposals will be rarer/absent)")
    p.add_argument("--model", default=None)
    p.add_argument("--caller", choices=["anthropic", "fake-echo"], default="anthropic",
                    help="fake-echo makes no network call -- for CI/dry-run smoke tests")
    p.add_argument("--provider", choices=["anthropic", "vertex", "claude-code"], default="anthropic")
    p.add_argument("--gcp-project")
    p.add_argument("--gcp-region")
    p.add_argument("--claude-code-timeout", type=int, default=None,
                    help="--provider claude-code only; seconds before a `claude -p` call is "
                         "killed as hung, default 600 (claude_cli_caller.DEFAULT_TIMEOUT_S) -- "
                         "raise this for a member with an unusually large fact brief/test-case count")
    p.set_defaults(func=cmd_test_overlay_draft)

    p = sub.add_parser("test-advisory")
    p.add_argument("--config", required=True)
    p.add_argument("--out", help="write to this path instead of stdout")
    p.set_defaults(func=cmd_test_advisory)

    for name, fn in (("test-gen", cmd_test_gen), ("test-batch", cmd_test_batch)):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True)
        p.add_argument("--language", default=None,
                        help="e.g. python, java; default: options.testgen.default_language "
                             "from --config -- no built-in default either way")
        p.add_argument("--framework", default=None,
                        help="e.g. pytest, junit5; default: options.testgen.default_framework "
                             "from --config -- no built-in default either way")
        p.add_argument("--matrix", action="store_true",
                        help="render every {language, framework} pair in "
                             "options.testgen.matrix from --config, instead of one "
                             "--language/--framework target; mutually exclusive with "
                             "--language/--framework")
        p.add_argument("--template", help="override the default templates/tests/{language}_{framework}.md")
        p.add_argument("--model", default=None)
        p.add_argument("--caller", choices=["anthropic", "fake-echo"], default="anthropic",
                        help="fake-echo makes no network call -- for CI/dry-run smoke tests")
        p.add_argument("--provider", choices=["anthropic", "vertex", "claude-code"], default="anthropic")
        p.add_argument("--gcp-project")
        p.add_argument("--gcp-region")
        p.add_argument("--claude-code-timeout", type=int, default=None,
                        help="--provider claude-code only; seconds before a `claude -p` call is "
                             "killed as hung, default 600 (claude_cli_caller.DEFAULT_TIMEOUT_S) -- "
                             "raise this for a member with an unusually large fact brief/test-case count")
        p.set_defaults(func=fn)
    sub.choices["test-gen"].add_argument("--member", required=True)
    sub.choices["test-gen"].add_argument("--out", help="default: tests_generated/<language>/<MEMBER>.md")
    sub.choices["test-batch"].add_argument(
        "--out", default=None,
        help="default: options.testgen.out_dir from --config, else tests_generated")
    sub.choices["test-batch"].add_argument(
        "--members", help="comma-separated member names; default: every member with test_case rows")
    sub.choices["test-batch"].add_argument("--concurrency", type=int, default=4)
    sub.choices["test-batch"].add_argument(
        "--state", default=".mfdoc/test-batch-state.json",
        help="resume-state file path, relative to --config's directory; empty string disables resume tracking")

    p = sub.add_parser("batch")
    p.add_argument("--config", required=True)
    p.add_argument("--out", default="docs/functional/modules")
    p.add_argument("--members", help="comma-separated member names; default: all batchable members")
    p.add_argument("--model", default=None,
                    help="defaults to claude-sonnet-4-5 for --provider anthropic; required "
                         "(no default) for --provider vertex, so a stale hardcoded default "
                         "can't silently point at a retired model. Current-generation Claude "
                         "models use the same bare id on Vertex AI as on the direct Anthropic "
                         "API; only legacy models need a Vertex-specific dated-snapshot id "
                         "with an '@' separator (e.g. claude-3-5-sonnet-v2@20241022) -- see "
                         "Vertex AI Model Garden for the current id for this model. For "
                         "--provider claude-code this is optional -- omit it to use the "
                         "local `claude` CLI's own default/configured model")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--state", default=".mfdoc/batch-state.json",
                    help="resume-state file path, relative to --config's directory; "
                         "empty string disables resume tracking")
    p.add_argument("--caller", choices=["anthropic", "fake-echo"], default="anthropic",
                    help="fake-echo makes no network call -- for CI/dry-run smoke tests")
    p.add_argument("--provider", choices=["anthropic", "vertex", "claude-code"], default="anthropic",
                    help="which egress path serves the model call when --caller=anthropic: "
                         "the Anthropic API directly, Claude via Google Cloud Vertex AI "
                         "(needs `pip install 'mfdoc[vertex]'`), or the local `claude` CLI "
                         "(needs it installed and authenticated -- no ANTHROPIC_API_KEY)")
    p.add_argument("--gcp-project", help="Vertex only; default ANTHROPIC_VERTEX_PROJECT_ID or "
                                          "GOOGLE_CLOUD_PROJECT env var")
    p.add_argument("--gcp-region", help="Vertex only; default CLOUD_ML_REGION env var, "
                                        "or us-east5")
    p.add_argument("--claude-code-timeout", type=int, default=None,
                    help="--provider claude-code only; seconds before a `claude -p` call is "
                         "killed as hung, default 600 (claude_cli_caller.DEFAULT_TIMEOUT_S) -- "
                         "raise this for a module with an unusually large fact brief")
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("validate")
    p.add_argument("--config", required=True)
    p.add_argument("--docs", required=True)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("test-validate")
    p.add_argument("--config", required=True)
    p.add_argument("--docs", required=True)
    p.set_defaults(func=cmd_test_validate)

    p = sub.add_parser("sample-citations")
    p.add_argument("--config", required=True)
    p.add_argument("--docs", default="docs/functional",
                   help="documents to sample from; ignored when --judge report")
    p.add_argument("--judge", choices=["human", "llm", "report"], default="human",
                   help="human: interactive terminal labelling (run this first, to calibrate); "
                        "llm: judge unlabelled samples with a model, requires human verdicts "
                        "already recorded; report: print the current rates without sampling "
                        "or judging anything new")
    p.add_argument("--n-per-doc", type=int, default=3,
                   help="claims to sample per document (default: 3)")
    p.add_argument("--seed", type=int, default=42,
                   help="sampling RNG seed, for a reproducible sample across runs (default: 42)")
    p.add_argument("--state", default=".mfdoc/citation-sample-state.json",
                   help="resume-state file path, relative to --config's directory")
    p.add_argument("--model", default=None,
                   help="--judge llm only; defaults to claude-sonnet-4-5 for --provider "
                        "anthropic, required for --provider vertex (see `mfdoc batch --help`)")
    p.add_argument("--caller", choices=["anthropic", "fake-echo"], default="anthropic",
                   help="--judge llm only; fake-echo makes no network call, for CI/dry-run "
                        "smoke tests")
    p.add_argument("--provider", choices=["anthropic", "vertex", "claude-code"], default="anthropic",
                   help="--judge llm only; which egress path serves the model call")
    p.add_argument("--gcp-project", help="--judge llm + --provider vertex only")
    p.add_argument("--gcp-region", help="--judge llm + --provider vertex only")
    p.set_defaults(func=cmd_sample_citations)

    p = sub.add_parser("export")
    p.add_argument("--config", required=True)
    p.add_argument("--json", required=True)
    p.set_defaults(func=cmd_export)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

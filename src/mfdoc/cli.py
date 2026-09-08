"""mfdoc — command line for the legacy-functional-docs pipeline.

    mfdoc ingest   --config project.yml
    mfdoc derive   --config project.yml
    mfdoc coverage --config project.yml
    mfdoc coverage --config project.yml --history
    mfdoc gate     --config project.yml
    mfdoc calibrate --config project.yml --dialect mantis
    mfdoc brief    --config project.yml [--module NAME | --entity NAME | --system]
    mfdoc rules-register --config project.yml --out docs/functional/rules-register.md
    mfdoc complexity --config project.yml --out docs/functional/complexity-heatmap.md
    mfdoc classify-rules --config project.yml [--llm-fallback]
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
import logging
import re
import sys
from pathlib import Path

import yaml

from . import brief as brief_mod
from . import classify
from . import graph, normalise
from . import structural
from . import testadvisor as testadvisor_mod
from . import testplan as testplan_mod
from .config_validate import ConfigError, raise_if_invalid
from .db import (
    add_gap, connect, coverage_history, insert, purge_member, purge_member_facts,
    record_coverage_history, set_metric, upsert_member,
)
from .dialects import adabas, environment, mantis, natural, screen, supra
from .redact import Redactor

# Single source of truth for the tool's own version -- previously a second,
# separately-hardcoded constant here, which silently drifted from
# mfdoc.__version__ (still "0.1.0" here after __version__ was bumped to
# "0.2.0") and would have recorded the wrong tool_version in ingest_run,
# undermining the staleness-check work that depends on __version__ being
# accurate.
from . import __version__ as VERSION

#: Each entry takes `(conn, mid, lines, name, options)` -- `options` is the
#: resolved `cfg["options"]` dict from the project config being ingested.
#: Almost every dialect ignores it today (its extractor has no config-driven
#: overrides yet); `supra_dir` is the one that reads
#: `options.dialects.supra.labels` (see `supra.labels_from_options`).
DIALECT_ROUTER = {
    "natural": lambda conn, mid, lines, name, options: natural.extract(conn, mid, lines, name),
    "mantis": lambda conn, mid, lines, name, options: mantis.extract(conn, mid, lines, name),
    "adabas_fdt": lambda conn, mid, lines, name, options: adabas.extract_fdt(conn, mid, lines, name),
    "ddm": lambda conn, mid, lines, name, options: adabas.extract_ddm(conn, mid, lines, name),
    "supra_dir": lambda conn, mid, lines, name, options: supra.extract(conn, mid, lines, name, options=options),
    "sql_ddl": lambda conn, mid, lines, name, options: environment.extract_sql_ddl(conn, mid, lines, name),
    "cobol_copybook": lambda conn, mid, lines, name, options: environment.extract_copybook(conn, mid, lines, name),
    "jcl": lambda conn, mid, lines, name, options: environment.extract_jcl(conn, mid, lines, name),
    "cics_csd": lambda conn, mid, lines, name, options: environment.extract_cics_csd(conn, mid, lines, name),
    "mantis_screen": lambda conn, mid, lines, name, options: screen.extract(conn, mid, lines, name),
}

DIALECT_DEFAULT_TYPE = {
    "ddm": "ddm", "adabas_fdt": "fdt", "supra_dir": "directory",
    "sql_ddl": "ddl", "cobol_copybook": "copybook", "jcl": "job", "cics_csd": "csd",
    "mantis_screen": "map",
}


def load_config(path: str | Path) -> dict:
    try:
        cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"invalid project config -- 1 problem(s):\n"
            f"  - invalid YAML: {exc}"
        ) from exc
    if cfg is None:
        cfg = {}
    if not isinstance(cfg, dict):
        # A project.yml that parses to a list/string/number/etc (e.g. a
        # stray leading '-' making the whole file one YAML sequence) has no
        # keys to .setdefault() below -- raise the same ConfigError shape
        # everything else here does, rather than letting that call fail
        # with a raw AttributeError before validation ever runs.
        raise ConfigError(
            f"invalid project config -- 1 problem(s):\n"
            f"  - config root must be a mapping, got {cfg!r}"
        )
    cfg.setdefault("index_db", ".mfdoc/index.db")
    cfg.setdefault("sources", [])
    cfg.setdefault("options", {})
    # Validated here, once, before any command does anything else with the
    # config -- every cmd_* calls load_config first, so this is the single
    # upfront check point for the whole CLI (see config_validate.py). A
    # malformed project.yml is then a clear error at startup instead of a
    # run failing partway through at whichever ad hoc point-of-use check
    # happens to be reached first.
    raise_if_invalid(cfg)
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
                DIALECT_ROUTER[dialect](conn, mid, ch.lines, ch.name, opts)
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
    from . import sme_notes as sme_notes_mod

    cfg = load_config(args.config)
    base = Path(args.config).parent
    conn = connect(base / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])
    lexicon = ((cfg["options"] or {}).get("narrative") or {}).get("lexicon") or {}
    notes = sme_notes_mod.load(cfg, base)
    if args.system:
        out = brief_mod.system_brief(conn, redact=redact)
    elif args.module:
        out = brief_mod.module_brief(conn, args.module, redact=redact, lexicon=lexicon, sme_notes=notes)
    elif args.entity:
        out = brief_mod.entity_brief(conn, args.entity, redact=redact, lexicon=lexicon, sme_notes=notes)
    elif args.executive:
        out = brief_mod.executive_brief(conn, args.executive, redact=redact, sme_notes=notes)
    elif args.interface_matrix:
        from .conditions import dispatch_field_from_options, mode_field_from_options

        out = brief_mod.interface_matrix_brief(
            conn, redact=redact, dispatch_field=dispatch_field_from_options(cfg["options"]),
            mode_field=mode_field_from_options(cfg["options"]),
        )
    else:
        print("specify --module, --entity, --system, --executive or --interface-matrix", file=sys.stderr)
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


def cmd_gap_summary(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    out = structural.gap_summary(conn)
    _write_or_print(out, args.out)
    return 0


def cmd_data_flow(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    out = structural.data_flow_diagram(conn)
    _write_or_print(out, args.out)
    return 0


def cmd_dispatch_map(args) -> int:
    from .conditions import dispatch_field_from_options

    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    out = structural.dispatch_map(conn, dispatch_field=dispatch_field_from_options(cfg["options"]))
    _write_or_print(out, args.out)
    return 0


def _write_contained(target: Path, content: str, resolved_out_dir: Path) -> None:
    """Write `content` to `target`, refusing anything that would land or
    write outside `resolved_out_dir` -- whether via an unsafe path
    component (caller's job to have already sanitized that) or via
    `target` itself already existing as a symlink pointing elsewhere
    (write_text follows symlinks, so a pre-planted one at this exact
    filename would otherwise silently escape --out on write)."""
    if target.is_symlink():
        raise ValueError(f"refusing to write through an existing symlink: {target}")
    if resolved_out_dir not in target.resolve().parents:
        raise ValueError(f"refusing to write outside --out directory: {target}")
    target.write_text(content, encoding="utf-8")


def cmd_call_graph(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    diagrams_cfg = ((cfg["options"] or {}).get("overview") or {}).get("diagrams") or {}
    diagrams = structural.call_graph_diagram(
        conn,
        cluster_by=diagrams_cfg.get("cluster_by", "module"),
        max_nodes_inline=diagrams_cfg.get("max_nodes_inline", 40),
        direction=diagrams_cfg.get("direction", "LR"),
    )
    out_dir = Path(args.out) if args.out else None
    if out_dir is None:
        print(diagrams["inline"])
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved_out_dir = out_dir.resolve()
    _write_contained(out_dir / "call-graph.md", diagrams["inline"], resolved_out_dir)
    for name, content in diagrams.items():
        if name == "inline":
            continue
        # structural.safe_cluster_filename is also what call_graph_diagram
        # itself uses when it labels this file in the collapsed view's
        # node text -- using anything else here would desync the label
        # from the file actually written.
        target = out_dir / f"call-graph-{structural.safe_cluster_filename(name)}.md"
        _write_contained(target, content, resolved_out_dir)
    print(f"wrote {len(diagrams)} file(s) to {out_dir}")
    return 0


def cmd_complexity(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    complexity_cfg = ((cfg["options"] or {}).get("overview") or {}).get("complexity") or {}
    out = structural.complexity_heatmap(conn, metric=complexity_cfg.get("metric", "rule_depth"))
    _write_or_print(out, args.out)
    return 0


def cmd_rules_theme_register(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])
    out = structural.thematic_rules_register(conn, redact=redact)
    _write_or_print(out, args.out)
    return 0


def cmd_glossary(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])
    out = structural.glossary(conn, redact=redact)
    _write_or_print(out, args.out)
    return 0


def cmd_lang_guide(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])
    out = structural.language_guide(conn, args.dialect, redact=redact)
    _write_or_print(out, args.out)
    return 0


def cmd_classify_rules(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    themes_cfg = ((cfg["options"] or {}).get("overview") or {}).get("themes") or {}
    taxonomy = themes_cfg.get("taxonomy") or {}
    counts = classify.classify_rules_deterministic(conn, taxonomy)
    print(f"keyword: {counts['keyword']}, structural: {counts['structural']}")
    if getattr(args, "llm_fallback", None) is None:
        use_llm = bool(themes_cfg.get("llm_fallback"))
    else:
        use_llm = args.llm_fallback
    if use_llm:
        from . import batch as batch_mod

        redact = Redactor.from_options(cfg["options"])
        caller = _build_model_caller(args)
        if caller is None:
            return 1
        def _print_progress(i: int, total: int) -> None:
            print(f"classify-rules: {i}/{total} rows sent to the model")

        result = classify.classify_rules_llm(
            conn, caller, redact=redact, taxonomy=taxonomy, limit=getattr(args, "limit", None),
            progress_callback=_print_progress,
        )
        print(f"llm reclassified: {result['reclassified']}")
        narrative_opts = (cfg["options"] or {}).get("narrative") or {}
        pricing = narrative_opts.get("pricing") or {}
        cost_per_mtok_in = pricing.get("input_per_mtok")
        cost_per_mtok_out = pricing.get("output_per_mtok")
        print(f"tokens: {result['input_tokens']} in, {result['output_tokens']} out")
        cost = batch_mod.estimate_cost(
            result["input_tokens"], result["output_tokens"], cost_per_mtok_in, cost_per_mtok_out
        )
        if cost is not None:
            print(f"cost: ${cost:.4f}")
        else:
            print("cost: unknown -- set options.narrative.pricing.input_per_mtok/output_per_mtok in project.yml")
    return 0


def _testgen_config(cfg: dict) -> dict:
    return (cfg["options"] or {}).get("testgen") or {}


def _testgen_default_out_dir(cfg: dict) -> str:
    """Default `options.testgen.out_dir` when a project.yml doesn't set one
    explicitly -- nested under `docs_root` (as `<docs_root>/tests`) when the
    config sets it, so a project's generated tests land inside its own
    output tree next to its narrative docs, instead of a same-named literal
    sitting in an unrelated top-level directory (see #142: a multi-project
    workspace running several `project.yml`s otherwise ends up with docs
    under each project's own `docs_root` but every project's generated
    tests comingled under one shared top-level `tests_generated/`).

    A config that doesn't set `docs_root` at all keeps today's bare
    "tests_generated" literal, unchanged -- this is a default-only change,
    so an existing setup that relies on the current default (or has
    `docs_root` unset) sees no behavior change. An explicit
    `options.testgen.out_dir` (or `--out`) always overrides this, exactly
    as before this function existed."""
    docs_root = cfg.get("docs_root")
    return str(Path(docs_root) / "tests") if docs_root else "tests_generated"


def _project_namespace(cfg: dict) -> str:
    """Filesystem-safe slug identifying this project config -- used to
    namespace `mfdoc test-batch`'s default resume-state file path and
    generated-test output subdirectory, so two project configs that happen
    to point at the same working directory (e.g. two `project.yml` files
    documenting different systems from one shared checkout, each run with
    its own `--config`) get genuinely separate generated-test output trees
    (`tests_generated/`, or `<docs_root>/tests` when `docs_root` is set --
    see `_testgen_default_out_dir`) and `.mfdoc/test-batch-state.json`-shaped
    resume-state files instead of silently sharing -- and clobbering --
    one another's.

    Mirrors the existing per-project `index_db` convention (each
    `project.yml` sets its own `index_db` path so two configs never share
    one fact store) rather than inventing a new one: keyed by `system` (a
    project's short code, e.g. "MOM") when present, else `project` (the
    human-readable name), else the literal string "default" when neither
    is set. Deliberately never derived from `index_db` itself -- a bare
    default `index_db` of ".mfdoc/index.db" is itself the *un*-namespaced
    default every project starts from, so using it as the namespace key
    would just move today's collision from one shared name to another,
    not actually separate the two configs.

    Applied to `--state`'s default and to test-batch/test-gen's output
    directory (whether that comes from `options.testgen.out_dir` in
    --config or its own default -- see `_testgen_default_out_dir`) -- but
    never to a full path a caller gave explicitly (`--out`/`--state` on the
    command line), which is used exactly as given, the same as `index_db`
    itself always is. `options.testgen.out_dir` doesn't get index_db's same
    "explicit means distinguishing" treatment: unlike index_db, which a
    project.yml essentially always sets to something genuinely
    project-specific, out_dir is commonly left unset -- exempting it from
    namespacing would leave the exact collision this function exists to
    prevent."""
    raw = cfg.get("system") or cfg.get("project") or "default"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(raw).strip()).strip("-").lower()
    # Strip leading/trailing dots so a slug of "." or ".." (or anything that
    # reduces to just dots once punctuation is stripped) can never become a
    # literal "." or ".." path segment in the output directory -- that would
    # resolve to the parent (or same) directory instead of a real namespace
    # subfolder, enabling path traversal and the exact cross-project
    # clobbering this function exists to prevent.
    slug = slug.strip(".")
    return slug or "default"


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
        return VertexCaller(model=args.model, project=args.gcp_project, region=args.gcp_region,
                            timeout=getattr(args, "api_timeout", None))
    if provider == "claude-code":
        from .claude_cli_caller import ClaudeCLICaller
        # getattr, not args.claude_code_timeout: same bare-namespace
        # backward-compat concern as `provider` above.
        return ClaudeCLICaller(model=args.model, timeout=getattr(args, "claude_code_timeout", None))
    from .anthropic_caller import AnthropicCaller
    return AnthropicCaller(model=args.model or "claude-sonnet-4-5",
                            timeout=getattr(args, "api_timeout", None))


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
    from . import sme_notes as sme_notes_mod
    from . import testbatch as testbatch_mod

    cfg = load_config(args.config)
    base = Path(args.config).parent
    conn = connect(base / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])
    testgen_cfg = _testgen_config(cfg)
    sme_notes = sme_notes_mod.load(cfg, base)

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
    # Only used below when --out isn't given (a per-target default path).
    # Namespaced per project config (see _project_namespace) regardless of
    # whether options.testgen.out_dir is configured or falls back to
    # _testgen_default_out_dir's default: unlike index_db (which a
    # project.yml essentially always sets to something genuinely
    # distinguishing), out_dir is commonly left unset -- so treating an
    # explicit-but-still-shared out_dir as exempt would leave the exact
    # collision this namespacing exists to prevent.
    out_dir = Path(testgen_cfg.get("out_dir") or _testgen_default_out_dir(cfg)) / _project_namespace(cfg)
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
            sme_notes=sme_notes,
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
    from . import sme_notes as sme_notes_mod
    from . import testbatch as testbatch_mod

    cfg = load_config(args.config)
    base = Path(args.config).parent
    conn = connect(base / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])
    testgen_cfg = _testgen_config(cfg)
    sme_notes = sme_notes_mod.load(cfg, base)

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

    # An explicit --out (a full override, like index_db) is respected
    # exactly as given; otherwise mirror cmd_test_gen's default out_dir --
    # namespaced per project config (see _project_namespace) regardless of
    # whether options.testgen.out_dir is configured or falls back to
    # _testgen_default_out_dir's default, since out_dir is commonly left
    # unset -- an "explicit but still shared" out_dir would otherwise leave
    # the exact collision this namespacing exists to prevent.
    out_dir = (
        Path(args.out) if args.out
        else Path(testgen_cfg.get("out_dir") or _testgen_default_out_dir(cfg)) / _project_namespace(cfg)
    )

    # --state similarly: empty string disables resume tracking (unchanged);
    # an explicit path is used exactly as given; not given at all (None,
    # the argparse default) falls back to a state file namespaced per
    # project config, so two configs sharing a working directory don't
    # silently share (and clobber) one resume-state file.
    if args.state is None:
        state_rel = f".mfdoc/{_project_namespace(cfg)}-test-batch-state.json"
    elif args.state == "":
        state_rel = None
    else:
        state_rel = args.state

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
            state_path=(base / state_rel) if state_rel else None,
            max_scenarios_per_call=testgen_cfg.get("max_scenarios_per_call"),
            sme_notes=sme_notes,
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
    from . import sme_notes as sme_notes_mod

    cfg = load_config(args.config)
    base = Path(args.config).parent
    conn = connect(base / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])
    sme_notes = sme_notes_mod.load(cfg, base)

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
    # Optional: only used to enrich the whole-module-overview reconciliation
    # prompt for a chunked member (see batch.py's build_reconciliation_prompt) --
    # a project without this file (e.g. one predating this feature) still
    # gets that call, just without the section-naming contract folded in.
    index_template_path = base / "templates" / "module-index.md"
    index_template = index_template_path.read_text(encoding="utf-8") if index_template_path.exists() else None

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
        index_template=index_template,
        cost_per_mtok_in=pricing.get("input_per_mtok"),
        cost_per_mtok_out=pricing.get("output_per_mtok"),
        lexicon=lexicon,
        max_rules_per_call=narrative_opts.get("max_rules_per_call"),
        sme_notes=sme_notes,
    )

    for r in summary.results:
        status = "SKIP" if r.skipped else ("OK  " if r.ok else "FAIL")
        print(f"{status} {r.member:<20} attempts={r.attempts} in={r.input_tokens} out={r.output_tokens} "
              f"duration={r.duration_s:.1f}s retries={r.retries}")
        for p in r.problems:
            print(f"       - {p}")

    print(f"\n{summary.ok}/{len(summary.results)} ok, {summary.failed} failed, "
          f"{summary.skipped} skipped (unchanged), {summary.retried} retried")
    print(f"tokens: {summary.total_input_tokens} in, {summary.total_output_tokens} out")
    # total_duration_s is summed call time, not this run's own elapsed time
    # (members in the concurrent pool overlap) -- see BatchSummary's own
    # docstring note. Still the right number for "is this run stuck or just
    # slow": a per-member average, and total_retries, are what a multi-
    # hundred-module run needs to tell those apart (issue #84).
    avg_duration = summary.total_duration_s / len(summary.results) if summary.results else 0.0
    print(f"call time: {summary.total_duration_s:.1f}s total ({avg_duration:.1f}s avg/member), "
          f"{summary.total_retries} transient retries")
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
                  "LABELS or SUPRA_DML -- either edit LABELS in supra.py directly, or override "
                  "individual keys per project via `options.dialects.supra.labels` in "
                  "project.yml (merged over the LABELS defaults, see supra.labels_from_options)"),
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
    shapes = graph.unparsed_line_shapes(conn, args.dialect)
    if not shapes:
        print(f"no unparsed_line gaps for dialect '{args.dialect}' -- either it recognises "
              f"everything ingested, or nothing of this dialect was ingested")
        return 0

    hint_file, hint_constants = DIALECT_CALIBRATION_HINTS.get(
        args.dialect, (f"src/mfdoc/dialects/{args.dialect}.py", "the dialect's keyword tables"))
    print(f"unparsed-line shapes for dialect '{args.dialect}', ranked by frequency:")
    print(f"add recognised keywords to {hint_file} -- likely {hint_constants}")
    print()
    for entry in shapes[: args.top]:
        print(f"{entry['count']:5}  {entry['keyword']:<20} e.g. {entry['sample'][:100]!r}")
    return 0


def _print_coverage_history(history: list[dict]) -> None:
    """Render recorded coverage snapshots as a plain trend table -- oldest
    first, so a reader sees drift/improvement in the order it happened."""
    if not history:
        print("no coverage history recorded yet -- run `mfdoc coverage` or "
              "`mfdoc gate` at least once to start tracking a trend")
        return
    cols = ("recorded_at", "source", "line_recognition_rate", "call_resolution_rate",
            "entity_definition_rate", "gaps_high", "gaps_total")
    header = "  ".join(f"{c:<24}" if c == "recorded_at" else f"{c:>22}" for c in cols)
    print(header)
    for row in history:
        cells = []
        for c in cols:
            v = row.get(c, "")
            cells.append(f"{v:<24}" if c == "recorded_at" else f"{v!s:>22}")
        print("  ".join(cells))


def cmd_coverage(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    if getattr(args, "history", False):
        if getattr(args, "json", None):
            print("--history and --json are mutually exclusive -- --history prints "
                  "previously recorded snapshots and computes nothing new to write",
                  file=sys.stderr)
            return 2
        # A view over previously recorded snapshots -- deliberately does not
        # itself compute or record a fresh one, so repeatedly checking the
        # trend can't pollute it with runs that were never a real
        # `mfdoc coverage`/`mfdoc gate` invocation.
        _print_coverage_history(coverage_history(conn))
        return 0
    cov = graph.coverage(conn)
    record_coverage_history(conn, cov, source="coverage")
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
    record_coverage_history(conn, cov, source="gate")
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
    from .conditions import outcome_field_from_options
    from .validate import validate_tree
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    res = validate_tree(conn, Path(args.docs), outcome_field=outcome_field_from_options(cfg["options"]))
    for r in res["results"]:
        status = "OK " if r["ok"] else "FAIL"
        print(f"{status} {r['path']}  citations={r['citations']} invalid={r['invalid_citations']}")
        for p in r["problems"]:
            print(f"       - {p}")
    print(f"\n{res['documents_ok']}/{res['documents']} documents clean, "
          f"{res['invalid_citations']} invalid citations of {res['total_citations']}")
    _print_problem_list(res["completeness_problems"], "{} member(s) with incomplete rule coverage:")
    _print_problem_list(
        res["statement_coverage_problems"],
        "{} member(s) with a call/interaction statement never covered by any citation:",
    )
    _print_problem_list(
        res["artifact_problems"],
        "{} structural artifact(s) inconsistent with the fact store:",
    )
    _print_problem_list(
        res["omitted_statement_targets"],
        "{} statement(s) referenced in cited ranges but not named in surrounding prose "
        "(advisory, does not fail validation):",
    )
    _print_problem_list(
        res["deferred_references"],
        "{} deferred reference(s) to another chunk with no chunk number named "
        "(advisory, does not fail validation):",
    )
    _print_problem_list(
        res["forward_reference_problems"],
        "{} forward reference(s) to another chunk that either doesn't exist or "
        "doesn't actually fulfil the reference (advisory, does not fail validation):",
    )
    _print_problem_list(
        res["stale_documents"],
        "{} document(s) generated by a different mfdoc version than what's installed "
        "(advisory, does not fail validation):",
    )
    _print_problem_list(
        res["out_of_scope_documents"],
        "{} document(s) skipped as out of scope (front matter names sources not found in "
        "this project's loaded fact store -- likely another project's docs under a shared "
        "--docs directory; advisory, does not fail validation):",
    )
    return 0 if (
        res["invalid_citations"] == 0
        and res["documents_ok"] == res["documents"]
        and not res["completeness_problems"]
        and not res["statement_coverage_problems"]
        and not res["artifact_problems"]
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
    _print_problem_list(
        res["out_of_scope_documents"],
        "{} document(s) skipped as out of scope (front matter names sources not found in "
        "this project's loaded fact store -- likely another project's docs under a shared "
        "--docs directory; advisory, does not fail validation):",
    )
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


def _configure_logging(verbose: bool, log_file: str | None) -> None:
    """Set up the root logger for this process -- issue #83. Every `mfdoc`
    module's own progress/diagnostic logging (batch.py, testbatch.py,
    retry.py, ...) goes through `logging.getLogger("mfdoc.*")`, which
    propagates up to the root logger configured here; this is the one
    place that decides where those records actually go and at what level,
    so an individual module never needs to know about `--verbose`/
    `--log-file` itself.

    Deliberately separate from the real CLI output every `cmd_*` function
    still prints directly (coverage numbers, gate pass/fail, the batch
    summary table, ...) -- those stay on stdout via `print()` regardless of
    this configuration, so piping/scripting against them is unaffected.

    `force=True` lets this be called more than once within the same
    process (e.g. a test harness invoking `main()` repeatedly) and still
    take effect each time, rather than being a no-op after the first call
    the way plain `logging.basicConfig` would be.
    """
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mfdoc", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verbose", "-v", action="store_true",
                     help="emit DEBUG-level diagnostic logging (e.g. per-chunk batch "
                          "progress, retries) in addition to INFO -- applies to every "
                          "subcommand, not just batch/test-batch")
    ap.add_argument("--log-file",
                     help="also write diagnostic logging to this path, in addition to "
                          "stderr -- useful for an unattended, engagement-scale "
                          "`mfdoc batch`/`mfdoc test-batch` run")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, fn in (("ingest", cmd_ingest), ("derive", cmd_derive), ("coverage", cmd_coverage),
                     ("gate", cmd_gate)):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True)
        p.set_defaults(func=fn)
    sub.choices["coverage"].add_argument(
        "--json", help="also write the coverage numbers (only -- not the gap breakdown "
                        "printed alongside them) as JSON to this path")
    sub.choices["coverage"].add_argument(
        "--history", action="store_true",
        help="print the trend of previously recorded `mfdoc coverage`/`mfdoc gate` "
             "snapshots instead of computing a new one")

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
    p.add_argument("--executive", help="member name; emits the cited-facts brief for the "
                                        "executive-summary narrative template (templates/executive-summary.md)")
    p.add_argument("--interface-matrix", action="store_true",
                    help="whole-system cited-facts brief for the screen-and-key interface "
                         "matrix narrative template (templates/interface-matrix.md)")
    p.add_argument("--out")
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("rules-register")
    p.add_argument("--config", required=True)
    p.add_argument("--out", help="write to this path instead of stdout")
    p.set_defaults(func=cmd_rules_register)

    p = sub.add_parser("gap-summary")
    p.add_argument("--config", required=True)
    p.add_argument("--out", help="write to this path instead of stdout")
    p.set_defaults(func=cmd_gap_summary)

    p = sub.add_parser("data-flow")
    p.add_argument("--config", required=True)
    p.add_argument("--out", help="write to this path instead of stdout")
    p.set_defaults(func=cmd_data_flow)

    p = sub.add_parser("call-graph")
    p.add_argument("--config", required=True)
    p.add_argument("--out", help="directory to write call-graph*.md files into; omit to print the inline diagram to stdout")
    p.set_defaults(func=cmd_call_graph)

    p = sub.add_parser("dispatch-map")
    p.add_argument("--config", required=True)
    p.add_argument("--out", help="write to this path instead of stdout")
    p.set_defaults(func=cmd_dispatch_map)

    p = sub.add_parser("complexity")
    p.add_argument("--config", required=True)
    p.add_argument("--out", help="write to this path instead of stdout")
    p.set_defaults(func=cmd_complexity)

    p = sub.add_parser("rules-theme-register")
    p.add_argument("--config", required=True)
    p.add_argument("--out", help="write to this path instead of stdout")
    p.set_defaults(func=cmd_rules_theme_register)

    p = sub.add_parser("glossary")
    p.add_argument("--config", required=True)
    p.add_argument("--out", help="write to this path instead of stdout")
    p.set_defaults(func=cmd_glossary)

    p = sub.add_parser("lang-guide")
    p.add_argument("--config", required=True)
    p.add_argument("--dialect", required=True, choices=sorted(DIALECT_ROUTER))
    p.add_argument("--out", help="write to this path instead of stdout")
    p.set_defaults(func=cmd_lang_guide)

    p = sub.add_parser("classify-rules")
    p.add_argument("--config", required=True)
    p.add_argument("--llm-fallback", dest="llm_fallback", action="store_true", default=None,
                    help="override options.overview.themes.llm_fallback from --config")
    p.add_argument("--limit", type=int, default=None,
                    help="cap how many structural rows are sent to the LLM in this run")
    p.add_argument("--model", default=None)
    p.add_argument("--caller", choices=["anthropic", "fake-echo"], default="anthropic")
    p.add_argument("--provider", choices=["anthropic", "vertex", "claude-code"], default="anthropic")
    p.add_argument("--gcp-project")
    p.add_argument("--gcp-region")
    p.add_argument("--claude-code-timeout", type=int, default=None)
    p.add_argument("--api-timeout", type=int, default=None,
                    help="request timeout in seconds for --provider anthropic/vertex "
                    "(default: 600, matching --claude-code-timeout's default)")
    p.set_defaults(func=cmd_classify_rules)

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
    p.add_argument("--api-timeout", type=int, default=None,
                    help="request timeout in seconds for --provider anthropic/vertex "
                    "(default: 600, matching --claude-code-timeout's default)")
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
        p.add_argument("--api-timeout", type=int, default=None,
                        help="request timeout in seconds for --provider anthropic/vertex "
                        "(default: 600, matching --claude-code-timeout's default)")
        p.set_defaults(func=fn)
    sub.choices["test-gen"].add_argument("--member", required=True)
    sub.choices["test-gen"].add_argument(
        "--out", help="default: <out_dir>/<project-namespace>/<dialect>/<library>/<language>/"
                      "<framework>/<MEMBER>.md, where <out_dir> is options.testgen.out_dir "
                      "from --config (else <docs_root>/tests if --config sets docs_root, else "
                      "tests_generated), <project-namespace> is --config's system or project "
                      "key, and <dialect>/<library> come from the member's own fact-store row "
                      "(see _output_subdir)")
    sub.choices["test-batch"].add_argument(
        "--out", default=None,
        help="default: options.testgen.out_dir from --config (else <docs_root>/tests if "
             "--config sets docs_root, else tests_generated), then /<project-namespace> "
             "(see --config's system or project key -- namespaced so two configs sharing a "
             "working directory don't share one output tree)")
    sub.choices["test-batch"].add_argument(
        "--members", help="comma-separated member names; default: every member with test_case rows")
    sub.choices["test-batch"].add_argument("--concurrency", type=int, default=4)
    sub.choices["test-batch"].add_argument(
        "--state", default=None,
        help="resume-state file path, relative to --config's directory; empty string disables "
             "resume tracking; default: .mfdoc/<project-namespace>-test-batch-state.json (see "
             "--config's system or project key -- namespaced so two configs sharing a working "
             "directory don't share, and clobber, one resume-state file)")

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
    p.add_argument("--api-timeout", type=int, default=None,
                    help="request timeout in seconds for --provider anthropic/vertex "
                    "(default: 600, matching --claude-code-timeout's default)")
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
    p.add_argument("--claude-code-timeout", type=int, default=None,
                    help="--provider claude-code only; seconds before a `claude -p` call is "
                         "killed as hung, default 600 (claude_cli_caller.DEFAULT_TIMEOUT_S)")
    p.add_argument("--api-timeout", type=int, default=None,
                    help="request timeout in seconds for --provider anthropic/vertex "
                    "(default: 600, matching --claude-code-timeout's default)")
    p.add_argument("--gcp-project", help="--judge llm + --provider vertex only")
    p.add_argument("--gcp-region", help="--judge llm + --provider vertex only")
    p.set_defaults(func=cmd_sample_citations)

    p = sub.add_parser("export")
    p.add_argument("--config", required=True)
    p.add_argument("--json", required=True)
    p.set_defaults(func=cmd_export)

    args = ap.parse_args(argv)
    try:
        _configure_logging(args.verbose, args.log_file)
    except OSError as exc:
        # e.g. --log-file pointing at an unwritable path or a missing
        # parent directory -- a bad CLI argument, not an internal error,
        # so it gets the same "clean message, exit 2" treatment as
        # ConfigError below rather than an uncaught traceback. Scoped to
        # just this call (not the args.func(args) dispatch below) so an
        # OSError raised from inside a subcommand itself still surfaces as
        # its own uncaught traceback rather than being misreported as a
        # --log-file problem.
        print(f"error: could not open --log-file {args.log_file!r}: {exc}",
              file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except ConfigError as exc:
        # Same "clean message, exit 2" idiom as the config-shape checks
        # cmd_test_gen/cmd_test_batch already do inline (e.g.
        # _testgen_matrix_error) -- a config problem is a usage error, not
        # an unhandled traceback.
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""mfdoc — command line for the legacy-functional-docs pipeline.

    mfdoc ingest   --config project.yml
    mfdoc derive   --config project.yml
    mfdoc coverage --config project.yml
    mfdoc gate     --config project.yml
    mfdoc calibrate --config project.yml --dialect mantis
    mfdoc brief    --config project.yml [--module NAME | --entity NAME | --system]
    mfdoc validate --config project.yml --docs docs/functional
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
from .db import add_gap, connect, insert, upsert_member
from .dialects import adabas, environment, mantis, natural, supra
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
}

DIALECT_DEFAULT_TYPE = {
    "ddm": "ddm", "adabas_fdt": "fdt", "supra_dir": "directory",
    "sql_ddl": "ddl", "cobol_copybook": "copybook", "jcl": "job", "cics_csd": "csd",
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
            if seq_cfg == "auto":
                seq_cols = normalise.detect_seq_columns(lines)
            elif seq_cfg in (None, False, "none"):
                seq_cols = None
            else:
                a, b = str(seq_cfg).split(":")
                seq_cols = (int(a) - 1, int(b))

            text = "\n".join(lines)
            dialect = normalise.detect_dialect(text, hint)
            ranking = normalise.dialect_confidence(text)
            sf_id = insert(conn, "source_file", path=str(path), origin_path=str(path),
                           sha256=sha, encoding_in=enc,
                           seq_cols=f"{seq_cols[0] + 1}:{seq_cols[1]}" if seq_cols else None,
                           line_count=len(lines), ingest_run_id=run_id)

            if dialect == "unknown":
                add_gap(conn, "ambiguous_dialect",
                        f"Could not determine the dialect of {path.name}; it was skipped. "
                        f"Set `dialect:` explicitly for this source in project config.",
                        severity="high")
                continue
            if len(ranking) > 1 and ranking[0][1] < ranking[1][1] * 2 and not hint:
                add_gap(conn, "ambiguous_dialect",
                        f"{path.name} matched several dialect signatures {ranking[:3]}; "
                        f"processed as '{dialect}'. Confirm and pin it in project config.",
                        severity="medium")

            member_name, ext_hint = normalise.derive_member_name(path)
            chunks = normalise.split_members(
                lines, dialect, default_name=member_name, seq_cols=seq_cols,
                splitters=splitters, library=library)
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
                conn.execute("DELETE FROM source_line WHERE member_id=?", (mid,))
                DIALECT_ROUTER[dialect](conn, mid, ch.lines, ch.name)
                total_members += 1
            conn.commit()
        print(f"  ingested {spec['path']} -> {total_members} members so far")

    conn.commit()
    print(f"ingest complete: {total_members} members")
    return 0


def cmd_derive(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    res = graph.run_all(conn)
    print(json.dumps(res, indent=2))
    return 0


def cmd_brief(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])
    if args.system:
        out = brief_mod.system_brief(conn, redact=redact)
    elif args.module:
        out = brief_mod.module_brief(conn, args.module, redact=redact)
    elif args.entity:
        out = brief_mod.entity_brief(conn, args.entity, redact=redact)
    else:
        print("specify --module, --entity or --system", file=sys.stderr)
        return 2
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(out)
    return 0


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
    return 0 if res["invalid_citations"] == 0 and res["documents_ok"] == res["documents"] else 1


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

    p = sub.add_parser("validate")
    p.add_argument("--config", required=True)
    p.add_argument("--docs", required=True)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("export")
    p.add_argument("--config", required=True)
    p.add_argument("--json", required=True)
    p.set_defaults(func=cmd_export)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

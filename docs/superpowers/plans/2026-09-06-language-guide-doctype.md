# `language-guide` document type — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an eighth document type, `language-guide`, that reports which
Natural/Mantis/Supra constructs a given codebase actually uses (grouped by
keyword, counted, one cited example each), built entirely from facts
already in the fact store — a basic deterministic tier (`mfdoc lang-guide`)
and a narrative tier (`templates/language-guide.md`, written interactively).

**Architecture:** `graph.language_profile(conn, dialect)` groups seven
already-populated fact-store columns by keyword (spec:
`docs/superpowers/specs/2026-09-06-language-guide-doctype-design.md`, section
mapping table). `graph.unparsed_line_shapes(conn, dialect)` is
`cmd_calibrate`'s existing shape-ranking logic, extracted so the
appendix and `mfdoc calibrate` share one implementation.
`structural.language_guide(conn, dialect, redact)` renders both into the
basic-tier markdown (`doc_type: register`). `cli.cmd_lang_guide` wires it
to `mfdoc lang-guide --config --dialect --out`. `templates/language-guide.md`
is the narrative tier's shape guide, read by a human/interactive session,
not by any Python code. No dialect-scanner or `validate.py` change.

**Tech Stack:** Python 3.10+, stdlib only — no new runtime dependency.

## Global Constraints

- Python ≥ 3.10 syntax only (`X | Y` unions, walrus) — matches
  `pyproject.toml`'s `requires-python`.
- `graph.language_profile` must be dialect-neutral: it groups by column
  values already populated identically by every dialect's `extract()`.
  Never special-case `dialect == "natural"` vs `"mantis"` in the
  aggregation SQL/Python — the only place a specific dialect's keywords
  show up is in what a given project's own fact-store rows happen to
  contain.
- No fixture, test, docstring, commit message, or PR text may name a real
  client, codename, or business term — invented Natural- and Mantis-shaped
  fixtures only (`CLAUDE.md`).
- Byte-identical regeneration on unchanged source: every list this produces
  must be sorted deterministically (count desc, keyword asc — ties broken
  by keyword, never by incidental row/insert order).
- `example_text` goes through `Redactor` before it reaches the rendered
  document, same as `glossary`/`thematic_rules_register`.
- `mfdoc calibrate`'s printed output must be byte-identical before and
  after the refactor — this is a pure extraction of existing logic, not a
  behavior change.

---

### Task 1: `graph.unparsed_line_shapes` — extract `cmd_calibrate`'s shape logic

**Files:**
- Modify: `src/mfdoc/graph.py` (add function)
- Modify: `src/mfdoc/cli.py:820-857` (`cmd_calibrate` — call the new function instead of computing shapes inline)
- Test: `tests/test_graph.py` (or nearest existing graph-function test file), `tests/test_cli.py` (existing calibrate tests must still pass unchanged)

**Interfaces:**
- Produces: `graph.unparsed_line_shapes(conn, dialect: str) -> list[dict]`,
  each `{"keyword": str, "count": int, "sample": str}`, sorted by count
  descending then keyword ascending.

- [ ] **Step 1: Write the failing test**

```python
def test_unparsed_line_shapes_groups_by_leading_keyword(tmp_path):
    conn = _fixture_conn_with_gaps(tmp_path)  # invented rows: gap_kind='unparsed_line',
                                               # dialect='mantis', two 'FOO ...' lines, one 'BAR ...'
    shapes = graph.unparsed_line_shapes(conn, "mantis")
    assert shapes[0] == {"keyword": "FOO", "count": 2, "sample": "FOO X Y"}
    assert shapes[1]["keyword"] == "BAR"
```

Build `_fixture_conn_with_gaps` (or reuse an existing in-memory-DB helper
already in the test suite — check `tests/test_graph.py`/`tests/conftest.py`
first) by inserting invented `member`/`gap` rows directly via `db.py`'s
schema, not by running the pipeline against source — this is a pure
data-shape test.

- [ ] **Step 2: Run test to verify it fails** — `pytest tests/test_graph.py -k unparsed_line_shapes -v`, expect `AttributeError`.

- [ ] **Step 3: Implement** — move the shape-grouping loop out of
  `cmd_calibrate` (lines ~841-848 in `cli.py`) into `graph.py`:

```python
def unparsed_line_shapes(conn, dialect: str) -> list[dict]:
    """Group gap(gap_kind='unparsed_line') rows for one dialect by leading
    keyword, counted, with one sample line each -- shared by `mfdoc
    calibrate` (cli.cmd_calibrate) and the language-guide appendix
    (structural.language_guide), so the two can never drift out of sync."""
    rows = conn.execute(
        """
        SELECT g.raw FROM gap g JOIN member m ON m.id = g.member_id
         WHERE g.gap_kind='unparsed_line' AND g.raw IS NOT NULL AND m.dialect=?
        """,
        (dialect,),
    ).fetchall()
    shapes: dict[str, dict] = {}
    for r in rows:
        raw = (r["raw"] or "").strip()
        if not raw:
            continue
        kw = raw.split()[0].upper()
        entry = shapes.setdefault(kw, {"keyword": kw, "count": 0, "sample": raw})
        entry["count"] += 1
    return sorted(shapes.values(), key=lambda e: (-e["count"], e["keyword"]))
```

  Rewrite `cmd_calibrate` to call it and keep the printed format identical:

```python
def cmd_calibrate(args) -> int:
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
```

- [ ] **Step 4: Run full calibrate test coverage** — `pytest tests/test_cli.py -k calibrate -v`, confirm unchanged output.

---

### Task 2: `graph.language_profile(conn, dialect)`

**Files:**
- Modify: `src/mfdoc/graph.py` (add function + section/column table + `_aggregate_profile_rows` helper)
- Test: `tests/test_graph.py` (new test function(s))

**Interfaces:**
- Produces: `graph.language_profile(conn, dialect: str) -> dict[str, list[dict]]`
  with keys `structure`, `control_flow`, `data_access`,
  `entity_relationships`, `screen_interaction`, `transactions`,
  `calling_conventions`. Each list entry:
  `{"keyword", "count", "example_member", "example_line", "example_text"}`.

- [ ] **Step 1: Write the failing test**

Build an in-memory fact store with invented Natural- and Mantis-shaped rows
covering every one of the seven columns (at least two distinct keyword
values per column, with differing counts so sort order is actually
exercised), across **two dialects** in the same store, and assert:

```python
def test_language_profile_groups_by_keyword_and_dialect(tmp_path):
    conn = _fixture_conn_with_language_facts(tmp_path)
    natural = graph.language_profile(conn, "natural")
    mantis = graph.language_profile(conn, "mantis")

    assert natural["control_flow"][0]["keyword"] == "IF"
    assert natural["control_flow"][0]["count"] == 2
    assert natural["control_flow"][0]["example_member"]
    assert natural["control_flow"][0]["example_line"]
    assert natural["control_flow"][0]["example_text"]  # the actual source_line text

    # Mantis facts never leak into the Natural profile and vice versa.
    mantis_keywords = {e["keyword"] for e in mantis["control_flow"]}
    assert "IF" not in mantis_keywords or mantis["control_flow"] != natural["control_flow"]

    # A dialect with no rows in some table renders an empty, not missing, section.
    empty = graph.language_profile(conn, "sql_ddl")
    assert empty["screen_interaction"] == []
```

Also assert the `entity_relationships` `via_member IS NOT NULL` filter
explicitly: insert one `entity_link` row with `via_member` set (dialect
`mantis`) and one with `via_member IS NULL`, and assert only the first
appears in `mantis["entity_relationships"]`.

- [ ] **Step 2: Run test to verify it fails** — expect `AttributeError`.

- [ ] **Step 3: Implement** in `graph.py`:

```python
_LANGUAGE_PROFILE_SECTIONS = {
    "structure": ("variable", "format"),
    "control_flow": ("rule_candidate", "construct"),
    "data_access": ("data_access", "verb"),
    "screen_interaction": ("interaction", "kind"),
    "transactions": ("transaction_marker", "marker"),
    "calling_conventions": ("call_edge", "call_kind"),
}


def _aggregate_profile_rows(rows) -> list[dict]:
    grouped: dict[str, dict] = {}
    for r in rows:
        kw = r["keyword"]
        entry = grouped.get(kw)
        if entry is None:
            entry = {
                "keyword": kw,
                "count": 0,
                "example_member": r["example_member"],
                "example_line": r["example_line"],
                "example_text": (r["example_text"] or "").strip(),
            }
            grouped[kw] = entry
        entry["count"] += 1
    return sorted(grouped.values(), key=lambda e: (-e["count"], e["keyword"]))


def language_profile(conn, dialect: str) -> dict[str, list[dict]]:
    """Group every already-populated "recognised construct" column by
    keyword, for one dialect -- see docs/superpowers/specs/
    2026-09-06-language-guide-doctype-design.md for the section-to-column
    mapping. Dialect-neutral: the aggregation is identical regardless of
    which dialect is requested, since every dialect's extract() populates
    these columns the same way -- only the *values* that show up differ
    per project."""
    profile: dict[str, list[dict]] = {}
    for section, (table, col) in _LANGUAGE_PROFILE_SECTIONS.items():
        rows = conn.execute(
            f"""
            SELECT t.{col} AS keyword, m.name AS example_member, t.line_no AS example_line,
                   sl.text AS example_text
              FROM {table} t
              JOIN member m ON m.id = t.member_id
              LEFT JOIN source_line sl ON sl.member_id = t.member_id AND sl.line_no = t.line_no
             WHERE m.dialect = ? AND t.{col} IS NOT NULL AND t.{col} <> ''
             ORDER BY t.{col}, m.name, t.line_no
            """,
            (dialect,),
        ).fetchall()
        profile[section] = _aggregate_profile_rows(rows)

    entity_rows = conn.execute(
        """
        SELECT el.link_kind AS keyword, m.name AS example_member, el.via_line AS example_line,
               sl.text AS example_text
          FROM entity_link el
          JOIN member m ON m.id = el.via_member
          LEFT JOIN source_line sl ON sl.member_id = el.via_member AND sl.line_no = el.via_line
         WHERE m.dialect = ? AND el.link_kind IS NOT NULL AND el.link_kind <> ''
         ORDER BY el.link_kind, m.name, el.via_line
        """,
        (dialect,),
    ).fetchall()
    profile["entity_relationships"] = _aggregate_profile_rows(entity_rows)
    return profile
```

  Note `table`/`col` are drawn only from the fixed `_LANGUAGE_PROFILE_SECTIONS`
  dict above (never from caller input), so the f-string interpolation is
  not a SQL-injection surface.

- [ ] **Step 4: Run test** — `pytest tests/test_graph.py -k language_profile -v`.

---

### Task 3: `structural.language_guide` — basic-tier renderer

**Files:**
- Modify: `src/mfdoc/structural.py` (add function)
- Test: `tests/test_structural_call_graph.py` or a new `tests/test_structural_language_guide.py` (whichever matches the existing per-renderer test file convention — check before choosing)

**Interfaces:**
- Consumes: `graph.language_profile`, `graph.unparsed_line_shapes`, `citations._cite`, `redact.Redactor`.
- Produces: `structural.language_guide(conn, dialect: str, redact: Redactor = NULL_REDACTOR) -> str`.

- [ ] **Step 1: Write the failing test**

Assert on: front matter (`doc_type: register`, title contains the dialect
name), section headings present in fixed order, a keyword/count/citation
row rendered correctly for a populated section, "None recorded." for an
empty section, the appendix present and populated from
`unparsed_line_shapes`, and that `example_text` is passed through the given
`Redactor` (use a stub redactor that uppercases input, assert the rendered
table cell reflects that transform — the same technique
`tests/test_structural_call_graph.py`/glossary tests already use for
redaction coverage; check there first for the exact pattern to match).

- [ ] **Step 2: Run test to verify it fails.**

- [ ] **Step 3: Implement**, following `glossary()`'s shape (front matter
  block, one heading + table per section, `_cite` for citations,
  `redact()` applied to `example_text` only — never to the keyword or
  count, which aren't free text):

```python
_SECTION_TITLES = [
    ("structure", "Structure / declarations"),
    ("control_flow", "Control flow"),
    ("data_access", "Data access (DML)"),  # entity_relationships renders as a subsection below this one
    ("screen_interaction", "Screen interaction"),
    ("transactions", "Transactions"),
    ("calling_conventions", "Calling conventions"),
]


def _render_profile_table(entries: list[dict], redact: Redactor) -> list[str]:
    if not entries:
        return ["None recorded.", ""]
    out = ["| keyword | count | example |", "|---|---|---|"]
    for e in entries:
        cite = _cite(e["example_member"], e["example_line"])
        text = redact(e["example_text"]).replace("|", "\\|") if e["example_text"] else ""
        out.append(f"| `{e['keyword']}` | {e['count']} | {cite} `{text}` |")
    out.append("")
    return out


def language_guide(conn, dialect: str, redact: Redactor = NULL_REDACTOR) -> str:
    """Every recognised construct in `dialect`'s source, grouped by keyword
    with a frequency count and one cited example each -- the basic,
    deterministic tier of the language-guide document type. See
    docs/superpowers/specs/2026-09-06-language-guide-doctype-design.md."""
    profile = graph.language_profile(conn, dialect)
    unparsed = graph.unparsed_line_shapes(conn, dialect)

    out = ["---", f'title: "{dialect} — language guide"', "doc_type: register", "---", "",
           f"# {dialect} — language guide", "", (
        f"Every recognised construct actually in use in this codebase's "
        f"`{dialect}` source, grouped by keyword with a frequency count and "
        f"one cited example each. Regenerate with `mfdoc lang-guide "
        f"--dialect {dialect}` after any source change; do not hand-edit. "
        f"See `templates/language-guide.md` for the narrative tier that "
        f"adds connective prose on top of this."
    ), ""]

    for key, title in _SECTION_TITLES:
        out.append(f"## {title}")
        out.append("")
        out.extend(_render_profile_table(profile[key], redact))
        if key == "data_access":
            out.append("### Entity relationships")
            out.append("")
            out.extend(_render_profile_table(profile["entity_relationships"], redact))

    out.append("## Not yet recognized")
    out.append("")
    out.append(
        "Seen in source, not yet matched to a known construct -- ranked by "
        "frequency; see `mfdoc calibrate --dialect "
        f"{dialect}` for the full list and where to add recognition."
    )
    out.append("")
    if not unparsed:
        out.append("None recorded.")
        out.append("")
    else:
        out.append("| keyword | count | sample |")
        out.append("|---|---|---|")
        for e in unparsed:
            sample = e["sample"].replace("|", "\\|")
            out.append(f"| `{e['keyword']}` | {e['count']} | `{sample}` |")
        out.append("")
    return "\n".join(out) + "\n"
```

- [ ] **Step 4: Run test.**

---

### Task 4: `mfdoc lang-guide` CLI command

**Files:**
- Modify: `src/mfdoc/cli.py` (add `cmd_lang_guide`, register subparser)
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `mfdoc lang-guide --config project.yml --dialect mantis --out <path>`, exit 0.

- [ ] **Step 1: Write the failing test** — run the command against a small
  invented fixture project (or an in-process fact store + `_write_or_print`
  equivalent, matching how existing `test_cli.py` tests for `gap-summary`/
  `glossary` are structured — follow that pattern exactly), assert exit
  code 0 and that the written file's front matter has `doc_type: register`
  and the title names the dialect.

- [ ] **Step 2: Run test to verify it fails.**

- [ ] **Step 3: Implement:**

```python
def cmd_lang_guide(args) -> int:
    cfg = load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])
    out = structural.language_guide(conn, args.dialect, redact=redact)
    _write_or_print(out, args.out)
    return 0
```

  Register in `main()`, next to `glossary`'s subparser:

```python
p = sub.add_parser("lang-guide")
p.add_argument("--config", required=True)
p.add_argument("--dialect", required=True)
p.add_argument("--out", help="write to this path instead of stdout")
p.set_defaults(func=cmd_lang_guide)
```

- [ ] **Step 4: Run test.**

---

### Task 5: `templates/language-guide.md`

**Files:**
- Add: `templates/language-guide.md`

- [ ] **Step 1: Write it**, following `module.md`'s full front-matter shape
  (see design spec) plus the same section order as Task 3's basic tier,
  each section carrying a short instruction for the narrative addition
  expected (one short paragraph of connective prose after the table,
  cited or hedged, describing idioms — e.g. "these constructs commonly
  appear together as X" — never inventing a claim the basic-tier table
  doesn't already support). No test — this is prose read by a human/
  interactive session, not executed code; `tests/test_validate.py`'s
  front-matter-shape test (Task 6) is the check that keeps it honest.

- [ ] **Step 2: Sanity-check by hand**: run `mfdoc validate` against a copy
  of this template with the placeholders filled in from an invented
  fixture (same technique Task 6's `tests/test_validate.py` case uses) —
  confirms the front matter and citation shape actually pass.

---

### Task 6: `validate.py` coverage — test only, no code change expected

**Files:**
- Test: `tests/test_validate.py`

- [ ] **Step 1: Write a test** that hand-builds a small `doc_type: register`
  document (Task 3's shape) and a small `doc_type: language-guide` document
  (Task 5's shape, filled in with invented content and real citations
  against an invented fixture fact store) and asserts both validate clean
  through `validate.validate_doc`/`validate_tree` — confirming the design
  spec's "no `validate.py` change required" claim rather than just
  asserting it in prose.

- [ ] **Step 2: Run it.** If it fails, that's new information contradicting
  the spec — stop and re-examine `REQUIRED_FRONTMATTER`/
  `REQUIRED_REGISTER_FRONTMATTER` before changing `validate.py`, rather than
  patching it reflexively.

---

### Task 7: `SKILL.md`, `CLAUDE.md`, `docs/guides/architecture.md`, `docs/plans/legacy-functional-docs-plan.md`

**Files:**
- Modify: `SKILL.md` ("Suggested document set" — insert after `coverage-report.md`)
- Modify: `CLAUDE.md` ("Commands" section — add `mfdoc lang-guide` next to `mfdoc calibrate`/`mfdoc glossary`)
- Modify: `docs/guides/architecture.md` (doc-type list / structural-overview description)
- Modify: `docs/plans/legacy-functional-docs-plan.md` (dated progress entry)

- [ ] **Step 1:** `SKILL.md` — insert as item 7 (existing item 7,
  `executive-summary.md`, becomes item 8), with a short note: run
  `mfdoc lang-guide --config project.yml --dialect <dialect> --out
  <path>` first (basic tier), then write the narrative layer from
  `templates/language-guide.md` using that file as the cited fact source
  — same pattern as the existing executive-summary note's
  "run `mfdoc classify-rules` first."

- [ ] **Step 2:** `CLAUDE.md` — add a line under the "optional structural
  overview reports" block:
  `mfdoc lang-guide --config project.yml --dialect mantis --out docs/functional/reference/language-guide.md`

- [ ] **Step 3:** `docs/guides/architecture.md` — one sentence added to the
  structural-overview bullet naming `language_profile`/`language_guide`
  alongside `gap_summary`/`data_flow_diagram`/etc., and the eighth
  document-type entry wherever the doc-type list is enumerated.

- [ ] **Step 4:** `docs/plans/legacy-functional-docs-plan.md` — append
  a `**Progress (2026-09-06):**` entry near the top (above the existing
  most-recent entry), summarizing: issue #64 implemented, `graph.
  language_profile`/`graph.unparsed_line_shapes` added, `mfdoc lang-guide`
  basic tier, `templates/language-guide.md` narrative tier, `SKILL.md`
  updated, tests added, no `validate.py` change required.

---

### Task 8: Full-suite verification

- [ ] **Step 1:** `pytest` (full suite) — must pass.
- [ ] **Step 2:** Against `examples/inputs` (no `ANTHROPIC_API_KEY` needed —
  this whole feature is deterministic):

```bash
mfdoc ingest   --config project.yml
mfdoc derive   --config project.yml
mfdoc coverage --config project.yml
mfdoc lang-guide --config project.yml --dialect natural --out /tmp/lang-guide-natural.md
mfdoc lang-guide --config project.yml --dialect mantis --out /tmp/lang-guide-mantis.md
mfdoc validate --config project.yml --docs examples
```

  Confirm both `lang-guide` runs exit 0 and produce non-crashing output
  (empty sections are fine if `examples/inputs`' fixtures don't happen to
  exercise every column) — do **not** commit these two files into
  `examples/` (per `CLAUDE.md`, keep `examples/` limited to what's already
  golden-tested there; this is a smoke check, not a new golden fixture).
- [ ] **Step 3:** Confirm `mfdoc calibrate --config project.yml --dialect
  mantis` output is unchanged from before Task 1's refactor (diff against a
  pre-refactor capture, or re-read Task 1's byte-identical-output
  constraint and eyeball it).

# Per-statement completeness check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a non-blocking `validate.py` check that flags a `call_edge`/`interaction`/`data_access` row inside an already-correctly-cited line range whose target name never appears anywhere in the paragraph that cites the range — catching a narrated branch that silently drops one of several real statements without any citation being wrong.

**Architecture:** One new pure function (`_statement_completeness_problems`) modeled directly on the existing `_reversed_condition_problems`, called from the same spot in `validate_doc`'s citation loop, gated by the same `doc_type: module` scoping variable (renamed `module_doc_checks` since it now gates two checks). Results land in a new, separate result key (`omitted_statement_targets`) that is never folded into `problems`/`ok`, so it cannot fail `mfdoc validate`/`mfdoc gate` — advisory only, per the design's non-goals. A small boundary-matching helper (`_name_mentioned`) and a paragraph-extraction helper (`_containing_paragraph`, factored out of the existing `_containing_sentence`) are the only other new pieces.

**Tech Stack:** Python ≥3.10, stdlib `re`, SQLite via the existing `conn.execute(...).fetchall()` pattern (rows behave as `sqlite3.Row`, indexable by column name). pytest for tests.

**Spec:** `docs/superpowers/specs/2026-09-05-statement-completeness-check-design.md`

## Global Constraints

- Non-blocking: `omitted_statement_targets` must never be added to `problems`, and must never affect `result["ok"]`, `validate_tree`'s `documents_ok` count, or `cmd_validate`'s exit code.
- Scoped to `doc_type: module` documents only — reuses the same condition the reversed-condition check already uses.
- Skip a `call_edge` row when `dynamic=1` or `callee_name` is blank; skip `interaction`/`data_access` rows when their target column is blank. Never guess a target for these.
- Match a target as a whole token, case-insensitive, against the **paragraph** containing the citation (not just its sentence) — reuses the same boundary trick `BR_REF` uses (`#@$&-_.` are valid, non-word name characters, so plain `\b` under-matches).
- No schema changes. No change to `ingest`/`derive`/dialect extraction.

---

## File Structure

- **Modify:** `src/mfdoc/validate.py` — split `_containing_sentence` to expose `_containing_paragraph`; add `_name_mentioned`; add `_statement_completeness_problems`; wire into `validate_doc`; extend `validate_tree`.
- **Modify:** `src/mfdoc/cli.py` — `cmd_validate` prints the new advisory section.
- **Modify:** `tests/test_validate.py` — all new tests live here, alongside the existing reversed-condition and completeness tests they're modeled on.

---

### Task 1: Split `_containing_sentence` to expose `_containing_paragraph`

**Files:**
- Modify: `src/mfdoc/validate.py:167-187` (`_containing_sentence`)
- Test: `tests/test_validate.py`

**Interfaces:**
- Produces: `_containing_paragraph(body: str, start: int, end: int) -> tuple[str, int]` — returns `(paragraph_text, rel_start)`, where `rel_start` is `start`'s offset within `paragraph_text`. `_containing_sentence` becomes a thin wrapper that calls this and then applies `SENTENCE_SPLIT` within the returned paragraph.
- Consumes: nothing new — `SENTENCE_SPLIT` (module-level regex, already defined at `validate.py:88`).

This is a pure refactor: behavior of `_containing_sentence` must be provably unchanged (existing reversed-condition tests already cover it end-to-end), and `_containing_paragraph` becomes available for Task 3.

- [ ] **Step 1: Write the failing test for the new function**

Add to `tests/test_validate.py` (near the top, after imports — add `_containing_paragraph` to the import from `mfdoc.validate`... actually import it inline in the test since it's a private helper, matching how `module_completeness_problems` is imported inline in its tests):

```python
def test_containing_paragraph_returns_the_full_paragraph_and_relative_offset():
    from mfdoc.validate import _containing_paragraph

    body = "First paragraph, one sentence.\n\nSecond paragraph. It has [[X:1]] a citation. And more text.\n\nThird paragraph."
    cite_start = body.index("[[X:1]]")
    cite_end = cite_start + len("[[X:1]]")

    para, rel_start = _containing_paragraph(body, cite_start, cite_end)

    assert para == "Second paragraph. It has [[X:1]] a citation. And more text."
    assert para[rel_start:rel_start + len("[[X:1]]")] == "[[X:1]]"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_validate.py::test_containing_paragraph_returns_the_full_paragraph_and_relative_offset -v`
Expected: FAIL with `ImportError: cannot import name '_containing_paragraph'`

- [ ] **Step 3: Refactor `_containing_sentence` to introduce `_containing_paragraph`**

Replace `src/mfdoc/validate.py:167-187`:

```python
def _containing_sentence(body: str, start: int, end: int) -> str:
    """The sentence in `body` that spans byte offset `start`..`end`.

    Reuses `SENTENCE_SPLIT` (the same boundary `_logical_units` splits on) so
    a citation's surrounding claim is read the same way whether checked for
    an uncited assertion or for a reversed comparison. Falls back to the
    whole enclosing paragraph if no sentence boundary is found, which is
    always at least as much text as the citation itself sits in.
    """
    para_start = body.rfind("\n\n", 0, start)
    para_start = 0 if para_start == -1 else para_start + 2
    para_end = body.find("\n\n", end)
    para_end = len(body) if para_end == -1 else para_end
    para = body[para_start:para_end]
    rel_start = start - para_start

    bounds = [0] + [m.start() for m in SENTENCE_SPLIT.finditer(para)] + [len(para)]
    for lo, hi in zip(bounds, bounds[1:]):
        if lo <= rel_start < hi:
            return para[lo:hi]
    return para
```

with:

```python
def _containing_paragraph(body: str, start: int, end: int) -> tuple[str, int]:
    """The paragraph in `body` that spans byte offset `start`..`end`, and
    `start`'s offset relative to that paragraph's own start.

    Factored out of `_containing_sentence` so a caller that needs the whole
    paragraph (e.g. `_statement_completeness_problems`, which tolerates a
    target named anywhere in the paragraph, not just the citing sentence)
    doesn't duplicate this boundary-finding.
    """
    para_start = body.rfind("\n\n", 0, start)
    para_start = 0 if para_start == -1 else para_start + 2
    para_end = body.find("\n\n", end)
    para_end = len(body) if para_end == -1 else para_end
    return body[para_start:para_end], start - para_start


def _containing_sentence(body: str, start: int, end: int) -> str:
    """The sentence in `body` that spans byte offset `start`..`end`.

    Reuses `SENTENCE_SPLIT` (the same boundary `_logical_units` splits on) so
    a citation's surrounding claim is read the same way whether checked for
    an uncited assertion or for a reversed comparison. Falls back to the
    whole enclosing paragraph if no sentence boundary is found, which is
    always at least as much text as the citation itself sits in.
    """
    para, rel_start = _containing_paragraph(body, start, end)
    bounds = [0] + [m.start() for m in SENTENCE_SPLIT.finditer(para)] + [len(para)]
    for lo, hi in zip(bounds, bounds[1:]):
        if lo <= rel_start < hi:
            return para[lo:hi]
    return para
```

- [ ] **Step 4: Run the new test and the full existing reversed-condition suite**

Run: `pytest tests/test_validate.py::test_containing_paragraph_returns_the_full_paragraph_and_relative_offset tests/test_validate.py -k "reversed or containing" -v`
Expected: all PASS — the reversed-condition tests prove `_containing_sentence`'s behavior is unchanged.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: all PASS (no regressions from the refactor).

- [ ] **Step 6: Commit**

```bash
git add src/mfdoc/validate.py tests/test_validate.py
git commit -m "Factor _containing_paragraph out of _containing_sentence"
```

---

### Task 2: Add `_name_mentioned` boundary-matching helper

**Files:**
- Modify: `src/mfdoc/validate.py` (add near `BR_REF`, after the existing regex constants around line 53)
- Test: `tests/test_validate.py`

**Interfaces:**
- Produces: `_name_mentioned(text: str, name: str) -> bool`
- Consumes: nothing (pure regex, no DB).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_validate.py`:

```python
def test_name_mentioned_finds_a_whole_token_match():
    from mfdoc.validate import _name_mentioned

    assert _name_mentioned("The program calls PGMX02 to continue.", "PGMX02")


def test_name_mentioned_is_case_insensitive():
    from mfdoc.validate import _name_mentioned

    assert _name_mentioned("the program calls pgmx02 to continue.", "PGMX02")


def test_name_mentioned_rejects_a_substring_match():
    """PGMX02 must not match inside PGMX023 -- a longer identifier that
    happens to share a prefix is not a real mention."""
    from mfdoc.validate import _name_mentioned

    assert not _name_mentioned("The program calls PGMX023 to continue.", "PGMX02")


def test_name_mentioned_matches_a_name_containing_special_charset_characters():
    """Member/program/file names legitimately contain #@$&-_. -- these are
    non-word characters that a plain \\b boundary would mishandle."""
    from mfdoc.validate import _name_mentioned

    assert _name_mentioned("See #GS-WKAREA for the shared area.", "#GS-WKAREA")
    assert not _name_mentioned("See #GS-WKAREA-EXT for the shared area.", "#GS-WKAREA")


def test_name_mentioned_returns_false_when_absent():
    from mfdoc.validate import _name_mentioned

    assert not _name_mentioned("The program calls another routine.", "PGMX02")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_validate.py -k name_mentioned -v`
Expected: FAIL with `ImportError: cannot import name '_name_mentioned'`

- [ ] **Step 3: Implement `_name_mentioned`**

Add to `src/mfdoc/validate.py`, directly after the `BR_REF` constant (after line 53, before `REQUIRED_TEST_FRONTMATTER` at line 55):

```python
def _name_mentioned(text: str, name: str) -> bool:
    """Whether `name` appears in `text` as a whole token, case-insensitive.

    Reuses the same non-word-boundary trick `BR_REF` already uses instead of
    `\\b`: a Natural/Mantis member, program, map, or file name can contain
    `#@$&-_.`, all non-word characters that `\\b` would treat as a boundary
    even mid-name -- e.g. `\\bPGMX02\\b` would happily match inside
    `PGMX02-EXT`. `re.escape` is required since a target name may itself
    contain regex-special characters (`.`, `$`).
    """
    pattern = re.compile(
        rf"(?<![A-Z0-9#@$&.\-_]){re.escape(name)}(?![A-Z0-9#@$&.\-_])", re.I
    )
    return bool(pattern.search(text))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_validate.py -k name_mentioned -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/mfdoc/validate.py tests/test_validate.py
git commit -m "Add _name_mentioned boundary-matching helper"
```

---

### Task 3: Add `_statement_completeness_problems` and wire into `validate_doc`

**Files:**
- Modify: `src/mfdoc/validate.py:190` (add new function before `_reversed_condition_problems`), `:265-389` (`validate_doc`)
- Test: `tests/test_validate.py`

**Interfaces:**
- Consumes: `_containing_paragraph` (Task 1), `_name_mentioned` (Task 2).
- Produces: `_statement_completeness_problems(conn, member: str, member_id: int, lf: int, lt: int | None, body: str, cite_start: int, cite_end: int) -> list[str]`. `validate_doc`'s return dict gains `"omitted_statement_targets": list[str]`.

Reference for the exact wiring, `src/mfdoc/validate.py:306` and `:350-360` today:

```python
    check_reversed_conditions = fm is not None and fm.get("doc_type") == "module"
```//
```python
        if valid and lf is not None:
            row = rows[0]
            maxline = row["maxline"] or 0
            if lf < 1 or lf > maxline or (lt and lt > maxline):
                valid, note = 0, f"line {lf}{'-' + str(lt) if lt and lt != lf else ''} outside 1..{maxline}"
            elif check_reversed_conditions:
                problems.extend(
                    _reversed_condition_problems(
                        conn, member, row["id"], lf, lt, body, m.start(), m.end()
                    )
                )
```

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_validate.py`. First a shared fixture builder, modeled on `_member_with_return_code_if_else`:

```python
def _member_with_statements(**extra_rows):
    """A minimal in-memory index with one member (TESTSTMT) and, per
    `extra_rows`, a `call_edge`/`interaction`/`data_access` row at line 692
    inside a 691-693 source range -- mirrors the real DECIDE/FETCH/ESCAPE
    case from issue #59 without needing the dialect scanner to parse it.

    `extra_rows` keys: "call_edge", "interaction", "data_access", each a
    dict of column overrides merged onto a minimal valid row for that table.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    mid = insert(conn, "member", name="TESTSTMT", dialect="natural", object_type="subprogram")
    for line_no in range(691, 694):
        conn.execute(
            "INSERT INTO source_line (member_id, line_no, text) VALUES (?, ?, ?)",
            (mid, line_no, f"line {line_no}"),
        )
    if "call_edge" in extra_rows:
        row = {"caller_id": mid, "callee_name": "PGMX02", "call_kind": "FETCH",
               "dynamic": 0, "line_no": 692}
        row.update(extra_rows["call_edge"])
        insert(conn, "call_edge", **row)
    if "interaction" in extra_rows:
        row = {"member_id": mid, "target": "MAPX02", "kind": "CONVERSE", "line_no": 692}
        row.update(extra_rows["interaction"])
        insert(conn, "interaction", **row)
    if "data_access" in extra_rows:
        row = {"member_id": mid, "entity_name": "CUSTOMER-FILE", "verb": "READ",
               "crud": "R", "raw": "READ CUSTOMER-FILE", "line_no": 692}
        row.update(extra_rows["data_access"])
        insert(conn, "data_access", **row)
    conn.commit()
    return conn


STMT_FRONTMATTER = """---
title: Test doc
doc_type: module
system: MOM
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-01-01T00:00:00"
review_status: draft
confidence_summary:
  verified: 1
sources:
  - TESTSTMT
---
# Test doc
"""


def test_validator_flags_an_omitted_call_target(tmp_path):
    conn = _member_with_statements(call_edge={})
    doc = tmp_path / "doc.md"
    doc.write_text(
        STMT_FRONTMATTER
        + "\nThe branch exits the transaction [[TESTSTMT:691-693]].\n"
    )
    result = validate_doc(conn, doc)
    assert result["ok"], result["problems"]  # non-blocking: must not fail the doc
    assert any("PGMX02" in p and "FETCH" in p for p in result["omitted_statement_targets"])


def test_validator_ignores_a_call_target_named_elsewhere_in_the_paragraph(tmp_path):
    conn = _member_with_statements(call_edge={})
    doc = tmp_path / "doc.md"
    doc.write_text(
        STMT_FRONTMATTER
        + "\nThe branch first transfers control to PGMX02. "
          "It then exits the transaction [[TESTSTMT:691-693]].\n"
    )
    result = validate_doc(conn, doc)
    assert result["omitted_statement_targets"] == []


def test_validator_never_flags_a_dynamic_call_target(tmp_path):
    conn = _member_with_statements(call_edge={"dynamic": 1, "callee_name": "*PGM-NAME"})
    doc = tmp_path / "doc.md"
    doc.write_text(
        STMT_FRONTMATTER
        + "\nThe branch exits the transaction [[TESTSTMT:691-693]].\n"
    )
    result = validate_doc(conn, doc)
    assert result["omitted_statement_targets"] == []


def test_validator_flags_an_omitted_interaction_target(tmp_path):
    conn = _member_with_statements(interaction={})
    doc = tmp_path / "doc.md"
    doc.write_text(
        STMT_FRONTMATTER
        + "\nThe branch exits the transaction [[TESTSTMT:691-693]].\n"
    )
    result = validate_doc(conn, doc)
    assert any("MAPX02" in p and "CONVERSE" in p for p in result["omitted_statement_targets"])


def test_validator_flags_an_omitted_data_access_target(tmp_path):
    conn = _member_with_statements(data_access={})
    doc = tmp_path / "doc.md"
    doc.write_text(
        STMT_FRONTMATTER
        + "\nThe branch exits the transaction [[TESTSTMT:691-693]].\n"
    )
    result = validate_doc(conn, doc)
    assert any("CUSTOMER-FILE" in p and "READ" in p for p in result["omitted_statement_targets"])


def test_validator_scopes_statement_completeness_to_module_docs(tmp_path):
    """A register doc echoes source syntax/field-inventory phrasing verbatim
    -- same reasoning as why the reversed-condition check is module-only."""
    conn = _member_with_statements(call_edge={})
    doc = tmp_path / "doc.md"
    doc.write_text(
        "---\ntitle: Register\ndoc_type: register\n---\n"
        "# Register\n\nThe branch exits the transaction [[TESTSTMT:691-693]].\n"
    )
    result = validate_doc(conn, doc)
    assert result["omitted_statement_targets"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_validate.py -k "omitted or statement_completeness" -v`
Expected: FAIL — `KeyError: 'omitted_statement_targets'` (key doesn't exist on the result dict yet).

- [ ] **Step 3: Implement `_statement_completeness_problems`**

Insert into `src/mfdoc/validate.py` directly before `_reversed_condition_problems` (before line 190):

```python
_STATEMENT_SOURCES = [
    ("callee_name", "call_kind",
     "SELECT line_no, call_kind, callee_name FROM call_edge "
     "WHERE caller_id=? AND line_no BETWEEN ? AND ? AND dynamic=0 "
     "AND callee_name IS NOT NULL AND callee_name != ''"),
    ("target", "kind",
     "SELECT line_no, kind, target FROM interaction "
     "WHERE member_id=? AND line_no BETWEEN ? AND ? "
     "AND target IS NOT NULL AND target != ''"),
    ("entity_name", "verb",
     "SELECT line_no, verb, entity_name FROM data_access "
     "WHERE member_id=? AND line_no BETWEEN ? AND ? "
     "AND entity_name IS NOT NULL AND entity_name != ''"),
]


def _statement_completeness_problems(
    conn, member: str, member_id: int, lf: int, lt: int | None, body: str, cite_start: int, cite_end: int
) -> list[str]:
    """Flag a `call_edge`/`interaction`/`data_access` row inside a cited
    range whose target name never appears anywhere in the paragraph citing
    that range.

    Deliberately paragraph-scoped, not sentence-scoped (unlike
    `_reversed_condition_problems`): a branch's narration legitimately spans
    several sentences in one paragraph (a setup sentence, then one sentence
    per statement), and a target named two sentences after the citation is
    still a real mention. `dynamic=1` call_edge rows are excluded at the SQL
    level -- their target is a variable, not a literal name, so there is
    nothing meaningful to search prose for.

    Advisory only: the caller must not add these to `problems`. This is a
    deliberately different shape of check from the ones already built (see
    issue #59) and its false-positive rate against real generated docs is
    not yet known.
    """
    hi = lt or lf
    para, _ = _containing_paragraph(body, cite_start, cite_end)

    range_str = f"{lf}{'-' + str(lt) if lt and lt != lf else ''}"
    problems = []
    for target_col, kind_col, sql in _STATEMENT_SOURCES:
        for row in conn.execute(sql, (member_id, lf, hi)).fetchall():
            target = row[target_col]
            if _name_mentioned(para, target):
                continue
            problems.append(
                f"statement inside [[{member}:{range_str}]] targets '{target}' "
                f"({row[kind_col]} at line {row['line_no']}) but '{target}' is not "
                f"named anywhere in the citing paragraph"
            )
    return problems
```

Then wire it into `validate_doc`. Replace line 306:

```python
    check_reversed_conditions = fm is not None and fm.get("doc_type") == "module"
```

with:

```python
    # Scoped to narrative module docs only -- see the comment on
    # `_reversed_condition_problems` above for why a generated-test doc or a
    # flat register would make either of these checks noise, not signal.
    module_doc_checks = fm is not None and fm.get("doc_type") == "module"
```

Replace every remaining use of `check_reversed_conditions` (there is exactly one more, in the citation loop) and extend that branch. Replace lines 350-360:

```python
        if valid and lf is not None:
            row = rows[0]
            maxline = row["maxline"] or 0
            if lf < 1 or lf > maxline or (lt and lt > maxline):
                valid, note = 0, f"line {lf}{'-' + str(lt) if lt and lt != lf else ''} outside 1..{maxline}"
            elif check_reversed_conditions:
                problems.extend(
                    _reversed_condition_problems(
                        conn, member, row["id"], lf, lt, body, m.start(), m.end()
                    )
                )
```

with:

```python
        if valid and lf is not None:
            row = rows[0]
            maxline = row["maxline"] or 0
            if lf < 1 or lf > maxline or (lt and lt > maxline):
                valid, note = 0, f"line {lf}{'-' + str(lt) if lt and lt != lf else ''} outside 1..{maxline}"
            elif module_doc_checks:
                problems.extend(
                    _reversed_condition_problems(
                        conn, member, row["id"], lf, lt, body, m.start(), m.end()
                    )
                )
                omitted_targets.extend(
                    _statement_completeness_problems(
                        conn, member, row["id"], lf, lt, body, m.start(), m.end()
                    )
                )
```

Add the `omitted_targets` accumulator next to where `problems` is initialized (`validate_doc`'s top, where `problems: list[str] = []` is declared just after `text = path.read_text(...)`):

```python
    problems: list[str] = []
    omitted_targets: list[str] = []
```

Finally, add the new key to the returned dict. In the `return { ... }` block at the end of `validate_doc` (currently ending `"_fm": fm, "_body": body,`), add:

```python
        "omitted_statement_targets": omitted_targets,
```

placed alongside `"uncited_assertions": uncited,` for symmetry (both are informational lists derived from the same document).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_validate.py -k "omitted or statement_completeness or name_mentioned or containing_paragraph" -v`
Expected: all PASS

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: all PASS — in particular, `test_validator_accepts_the_worked_example_unchanged` must still pass unchanged (the new check cannot make an already-passing real document fail, since it never touches `problems`).

- [ ] **Step 6: Commit**

```bash
git add src/mfdoc/validate.py tests/test_validate.py
git commit -m "Add non-blocking per-statement completeness check to validate_doc"
```

---

### Task 4: Aggregate in `validate_tree` and report in `cmd_validate`

**Files:**
- Modify: `src/mfdoc/validate.py:525-534` (`validate_tree`)
- Modify: `src/mfdoc/cli.py:908-928` (`cmd_validate`)
- Test: `tests/test_validate.py`, `tests/test_cli.py` (create if it doesn't already exist — check first with `ls tests/test_cli.py`)

**Interfaces:**
- Consumes: `result["omitted_statement_targets"]` from Task 3.
- Produces: `validate_tree(...)` return dict gains `"omitted_statement_targets": list[str]` (the flat concatenation across all `results`, mirroring how `total_citations` sums a per-doc field). `cmd_validate`'s printed output and exit code.

- [ ] **Step 1: Write the failing test for `validate_tree` aggregation**

Add to `tests/test_validate.py`:

```python
def test_validate_tree_aggregates_omitted_statement_targets_across_documents(tmp_path):
    conn = _member_with_statements(call_edge={})
    (tmp_path / "doc1.md").write_text(
        STMT_FRONTMATTER + "\nThe branch exits the transaction [[TESTSTMT:691-693]].\n"
    )
    (tmp_path / "doc2.md").write_text(
        STMT_FRONTMATTER + "\nThe branch exits the transaction [[TESTSTMT:691-693]].\n"
    )
    res = validate_tree(conn, tmp_path)
    assert len(res["omitted_statement_targets"]) == 2
    # Advisory only -- must never affect pass/fail.
    assert res["documents_ok"] == res["documents"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_validate.py::test_validate_tree_aggregates_omitted_statement_targets_across_documents -v`
Expected: FAIL with `KeyError: 'omitted_statement_targets'`

- [ ] **Step 3: Update `validate_tree`**

Replace `src/mfdoc/validate.py:525-534`:

```python
def validate_tree(conn, root: Path) -> dict:
    results = [validate_doc(conn, p) for p in sorted(root.rglob("*.md")) if _is_pipeline_doc(p)]
    return {
        "documents": len(results),
        "documents_ok": sum(1 for r in results if r["ok"]),
        "total_citations": sum(r["citations"] for r in results),
        "invalid_citations": sum(r["invalid_citations"] for r in results),
        "results": results,
        "completeness_problems": module_completeness_problems(conn, results),
    }
```

with:

```python
def validate_tree(conn, root: Path) -> dict:
    results = [validate_doc(conn, p) for p in sorted(root.rglob("*.md")) if _is_pipeline_doc(p)]
    return {
        "documents": len(results),
        "documents_ok": sum(1 for r in results if r["ok"]),
        "total_citations": sum(r["citations"] for r in results),
        "invalid_citations": sum(r["invalid_citations"] for r in results),
        "results": results,
        "completeness_problems": module_completeness_problems(conn, results),
        # Advisory only (see _statement_completeness_problems) -- never
        # subtracted from documents_ok and never affects a caller's exit code.
        "omitted_statement_targets": [
            p for r in results for p in r.get("omitted_statement_targets", [])
        ],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_validate.py::test_validate_tree_aggregates_omitted_statement_targets_across_documents -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for `cmd_validate`'s reporting**

Check first whether `tests/test_cli.py` exists:

Run: `ls tests/test_cli.py`

If it does not exist, create it with this content; if it exists, add this test to it (with the necessary imports merged in):

```python
"""CLI-level smoke tests for commands whose behavior isn't fully covered by
calling the underlying validate.py/graph.py functions directly."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mfdoc import cli  # noqa: E402


def test_cmd_validate_reports_omitted_statement_targets_without_failing(cli_args, capsys):
    """The advisory section must print and must not affect the exit code --
    this only proves the real pipeline's own fixtures currently have no
    omissions (exit 0 either way), and that the section is skipped cleanly
    when there is nothing to report."""
    args = SimpleNamespace(config=cli_args.config, docs=str(REPO_ROOT / "examples" / "outputs" / "docs"))
    exit_code = cli.cmd_validate(args)
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "advisory, does not fail validation" not in captured.out
```

- [ ] **Step 6: Confirm this test doesn't already pass for the wrong reason**

Run: `grep -n "advisory" src/mfdoc/cli.py`
Expected: no match — `cmd_validate` doesn't print an "advisory" section at all yet, so this test currently passes trivially (there's nothing to print, on any input). It's a regression guard, not a positive-case test: the positive case (a real omission produces a real advisory line) is already covered by Task 3's unit tests against `validate_doc` directly. This CLI test exists only to prove `cmd_validate` wires the new field in without crashing or changing the exit code against the real, larger fixture tree.

- [ ] **Step 7: Update `cmd_validate`**

Replace `src/mfdoc/cli.py:908-928`:

```python
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
    if res["completeness_problems"]:
        print(f"\n{len(res['completeness_problems'])} member(s) with incomplete rule coverage:")
        for p in res["completeness_problems"]:
            print(f"  - {p}")
    return 0 if (
        res["invalid_citations"] == 0
        and res["documents_ok"] == res["documents"]
        and not res["completeness_problems"]
    ) else 1
```

with:

```python
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
    if res["completeness_problems"]:
        print(f"\n{len(res['completeness_problems'])} member(s) with incomplete rule coverage:")
        for p in res["completeness_problems"]:
            print(f"  - {p}")
    if res["omitted_statement_targets"]:
        print(f"\n{len(res['omitted_statement_targets'])} statement(s) referenced in cited ranges "
              f"but not named in surrounding prose (advisory, does not fail validation):")
        for p in res["omitted_statement_targets"]:
            print(f"  - {p}")
    return 0 if (
        res["invalid_citations"] == 0
        and res["documents_ok"] == res["documents"]
        and not res["completeness_problems"]
    ) else 1
```

Note the exit-code expression is untouched — `omitted_statement_targets` is deliberately not one of its conditions.

- [ ] **Step 8: Run the CLI smoke test and the full suite**

Run: `pytest tests/test_cli.py -v && pytest -q`
Expected: all PASS.

- [ ] **Step 9: Run the pipeline against bundled fixtures, per CLAUDE.md's pre-push check**

Run:
```bash
mfdoc ingest   --config project.yml
mfdoc derive   --config project.yml
mfdoc coverage --config project.yml
mfdoc validate --config project.yml --docs examples
```
Expected: same pass/fail result as before this change (the new section is additive-only); if it now prints an "advisory" section against real fixtures, read the flagged lines to sanity-check they're genuine (not a bug in the new check) before proceeding — do not silence or filter them away.

- [ ] **Step 10: Commit**

```bash
git add src/mfdoc/validate.py src/mfdoc/cli.py tests/test_validate.py tests/test_cli.py
git commit -m "Report per-statement completeness findings from mfdoc validate (advisory only)"
```

---

## Self-Review Notes

- **Spec coverage:** call_edge/interaction/data_access cross-reference (Task 3), module-doc scoping (Task 3), paragraph-level matching via `_containing_paragraph` (Task 1, used in Task 3), boundary-safe name matching (Task 2), non-blocking result placement (Task 3), `validate_tree`/`cmd_validate` reporting (Task 4), dynamic-call exclusion (Task 3) — all covered. The design doc's "Follow-up" section (promoting to blocking later) is explicitly out of scope for this plan, matching the spec's own non-goals.
- **Placeholder scan:** none — every step has literal, runnable code.
- **Type consistency:** `_statement_completeness_problems` signature matches its call site in Task 3 exactly (`conn, member, member_id, lf, lt, body, cite_start, cite_end`). `_containing_paragraph(body, start, end) -> tuple[str, int]` is used identically in Task 1's refactor and Task 3's new function.

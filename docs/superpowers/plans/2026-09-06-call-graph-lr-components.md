# Plan: connectivity-aware call-graph splitting, LR direction

Date: 2026-09-06
Issue: nightingalehq/legacy-functional-docs#68
Design: `docs/superpowers/specs/2026-09-06-call-graph-lr-components-design.md`

## Global Constraints

- No LLM involvement anywhere in this change — `graph.py` and
  `structural.py` stay pure, deterministic Python.
- No change to `call_edge` population in any dialect module.
- Preserve today's exact output shape when the call graph is a single
  connected component (the common case) — this is a splitting feature, not
  a rewrite of the existing single-graph behaviour.
- `pytest` must stay green throughout; run it after every task, not just at
  the end.

## File Structure

- `src/mfdoc/graph.py` — add `connected_components`.
- `src/mfdoc/structural.py` — extend `call_graph_diagram` with `direction`
  and the component split.
- `src/mfdoc/cli.py` — thread `options.overview.diagrams.direction` through
  `cmd_call_graph`.
- `project.yml` — document the new `direction` key.
- `tests/test_structural_call_graph.py` — new/updated tests.
- `docs/plans/legacy-functional-docs-plan.md` — progress entry.

### Task 1: `graph.connected_components`

Add, near `orphans`/`call_closure` in `graph.py`:

```python
def connected_components(conn) -> list[set[int]]:
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    rows = conn.execute("SELECT DISTINCT caller_id, callee_id FROM call_edge").fetchall()
    for r in rows:
        caller_id = r["caller_id"]
        if caller_id not in parent:
            parent[caller_id] = caller_id
        callee_id = r["callee_id"]
        if callee_id is not None:
            if callee_id not in parent:
                parent[callee_id] = callee_id
            union(caller_id, callee_id)

    groups: dict[int, set[int]] = defaultdict(set)
    for node in parent:
        groups[find(node)].add(node)
    return list(groups.values())
```

Docstring covers: undirected treatment of `call_edge`, why unresolved
callers still get a singleton, and why a shared unresolved-callee *name*
deliberately does not bridge two components (see design doc's Non-goals /
Design sections for the exact wording to reuse).

**Tests** (`tests/test_graph_connected_components.py`, new file — this is
`graph.py`-level, separate from the `structural.py` call-graph test file):

1. Two disconnected subgraphs (A→B, C→D, no edge between the two pairs)
   yield exactly two disjoint sets, each containing the right member ids.
2. A caller with only an unresolved call (`callee_id IS NULL`) still
   appears as its own singleton set.
3. Two callers in otherwise-unrelated parts of the graph that each have an
   unresolved call to the same missing callee *name* remain in separate
   components (regression guard for the deliberate non-merge decision).
4. A caller calling itself indirectly through a chain (A→B→C→A) collapses
   to one component, not three (sanity check on the union-find itself).
5. Empty `call_edge` table → empty list.

Run `pytest tests/test_graph_connected_components.py -v` before moving on.

### Task 2: `structural.call_graph_diagram` — direction parameter

- Add `direction: str = "LR"` to the signature.
- Validate against `{"LR", "TD"}` at the top of the function (mirror
  `build_call_graph`'s `cluster_by` ValueError message style), before any
  other work.
- Replace every hardcoded `"graph TD"` literal (there are three: the single
  inline render, the legacy collapsed-cluster view, and — after Task 3 —
  the new per-component index) with an f-string using `direction`.
- `render()` (the inner closure) already closes over the outer scope, so it
  just needs its literal changed to `f"graph {direction}"` — no new
  parameter on `render()` itself.

**Tests**: extend `tests/test_structural_call_graph.py`:
- default direction is `LR` (assert on the real `indexed_db` fixture, since
  it's already ingested for other tests in this file).
- `direction="TD"` still produces `graph TD` and no `graph LR` anywhere in
  the output.
- unsupported `direction` (e.g. `"sideways"`) raises `ValueError` with the
  bad value in the message.

Update the pre-existing `test_inline_diagram_below_threshold` assertion
from `"graph TD" in out["inline"]` to `"graph LR" in out["inline"]` — this
one line is a direct, intentional behaviour change from this issue, not a
side effect to work around.

Run `pytest tests/test_structural_call_graph.py -v` before moving on —
expect only the direction-related lines to need touching at this point;
the component-split changes land in Task 3.

### Task 3: `structural.call_graph_diagram` — connected-component split

1. Factor the existing node-set computation
   (`{node_key(cid, ...) for cid, entry in graph_data.items()} | {...}`)
   into a local helper `nodes_for(callers: dict[int, dict]) -> set[...]`
   parameterised on a callers dict, so it can be reused both for the
   whole-graph legacy check and per-component below. `nodes = nodes_for(graph_data)`
   replaces the old inline computation.
2. Factor the existing over-threshold branch (the `clusters` dict build,
   the collapsed-view lines, and the per-cluster `render()` calls) into a
   local helper `cluster_collapse(callers, title_prefix, key_prefix) -> dict[str, str]`
   that returns `{"": <collapsed view content>, <key_prefix><cluster>: <diagram>, ...}`
   — the empty-string key is a placeholder the caller pops off and renames
   (either to `"inline"` for the whole-graph case, or to the component's own
   key for the scoped case). `key_prefix` lets the per-component fallback's
   keys (`<component_name>-<cluster_name>`) stay distinct from the
   whole-graph fallback's bare cluster names.
3. Call `graph.connected_components(conn)`, filter out any empty sets
   defensively, and branch:
   - `len(components) <= 1`: exactly today's two-way branch
     (`len(nodes) <= max_nodes_inline` → single inline; else
     `cluster_collapse(graph_data, "Call graph", "")`, repackaging its `""`
     key as `"inline"`).
   - `len(components) > 1`:
     - Build `id_to_component: dict[member_id, component_index]`.
     - Group `graph_data` callers by component into
       `callers_by_component: dict[int, dict[int, dict]]`.
     - Compute a `stability_key(idx)` = the lexicographically-smallest
       `(library, name, dialect)` triple among `id_info` entries for that
       component's member ids (fall back to `("", "", "")` if none are in
       `id_info` — shouldn't happen in practice, but stay defensive rather
       than raising).
     - `ordered = sorted(range(len(components)), key=stability_key)`.
     - For each component, compute its dominant cluster name (the single
       shared `entry["cluster"]` value across its callers, or `None` if
       they differ).
     - Count how many components share each non-`None` dominant name;
       assign final names in `ordered` sequence: unique dominant name → use
       it as-is; `None` or a name shared by more than one component →
       `f"{dom}-{rank}"` / `f"component-{rank}"` using that component's
       1-based position in `ordered` (guarantees uniqueness without a
       second counting pass).
     - For each component in `ordered`: if its own node count
       (`nodes_for(callers_by_component[idx])`) is under
       `max_nodes_inline`, `result[name] = render(...)`; else
       `result[name], result[...] = cluster_collapse(callers_by_component[idx], f"Call graph — {name}", f"{name}-")`
       (popping/renaming the `""` key to `name` as in the single-component
       case).
     - Build the `"inline"` index diagram: one node per component in
       `ordered`, each labelled `f"{name} (see call-graph-{safe_cluster_filename(name)}.md)"`
       — same label shape the existing collapsed-cluster view already uses,
       just one level up.
4. Update the function's docstring per the design doc (return-shape
   description, component naming rules, direction parameter).

**Tests**: extend `tests/test_structural_call_graph.py` per the design
doc's Testing section — in particular the two new tests that didn't exist
before this issue:
- `test_disconnected_subgraphs_render_as_separate_diagrams_even_under_threshold`
  — two small disconnected subsystems, combined node count far under
  `max_nodes_inline`, must still produce two separate component diagrams,
  each containing only its own subsystem's nodes and none of the other's.
- `test_disconnected_same_cluster_components_get_unique_names` — two
  disconnected components that both dominate to the same `cluster_by`
  name get distinct, numbered filenames, neither colliding nor silently
  merging.

Also fix up the three pre-existing tests the design doc flags as
incidentally exercising multiple components as a side effect of testing
something else:
- `test_cluster_by_subsystem_changes_clustering`: add one resolved call
  edge connecting its two synthetic members so they land in one component
  (this test is about `cluster_by`'s effect on the over-threshold fallback,
  not about the new component split — keep it that way).
- `test_ambiguous_member_name_renders_as_two_distinct_nodes`: its two
  synthetic call chains are genuinely unrelated and *should* land in two
  components under the new logic — change its assertions to check
  `"\n".join(out.values())` instead of assuming everything is in
  `out["inline"]`.
- `test_call_graph_cli_sanitizes_unsafe_cluster_names`: its monkeypatched
  `fake_call_graph_diagram` needs a `direction="LR"` parameter added to its
  signature to match the new call site in `cli.cmd_call_graph`.

Add one test against the real bundled `examples/` fixtures confirming they
already split into more than one component today (documents the real-world
motivation, and gives at least one test that exercises the multi-component
path against non-synthetic data).

Run `pytest tests/test_structural_call_graph.py -v` — everything in the
file must pass before moving on.

### Task 4: `cli.cmd_call_graph` — direction option

One-line change: read `diagrams_cfg.get("direction", "LR")` and pass it as
`direction=` to `structural.call_graph_diagram(...)`. No other change —
`cli.py`'s file-writing loop is already generic over dict keys.

### Task 5: `project.yml` — document the new key

Add `direction: LR` under `options.overview.diagrams`, with a comment
matching the existing `cluster_by`/`max_nodes_inline` comment style, and
update `max_nodes_inline`'s own comment to note it's now applied per
component.

### Task 6: full-suite check

- `pytest` (whole suite) — must be green.
- `mfdoc ingest --config project.yml && mfdoc derive --config project.yml`
  against the repo's own fixtures.
- `mfdoc call-graph --config project.yml --out <scratch dir>` — manually
  inspect the resulting files: confirm multiple `call-graph-*.md` files are
  written (matching the "6 components" finding from the design doc), each
  containing `graph LR`, and that `call-graph.md`'s index correctly
  references each file that was actually written.
- `mfdoc validate --config project.yml --docs examples` — confirm no new
  validation failures relative to a pre-change baseline run (this doc type
  isn't itself validated against citations, but a full pipeline run is
  cheap insurance against an unrelated regression).

### Task 7: docs

- `docs/plans/legacy-functional-docs-plan.md`: one new
  `**Progress (2026-09-06):**` entry near the top, in the file's existing
  style, summarising the direction default change and the connectivity
  split, referencing issue #68.
- `docs/guides/architecture.md`: only touch this if it names TD/node-count
  behaviour specifically for `call_graph_diagram` — check first; if it only
  describes `structural.py`'s renderers at the "pure extraction, no model
  call" level (which is unchanged), leave it alone.

## Self-Review Notes

- Double-check `safe_cluster_filename` is applied to every component name
  before it's used in a filename reference or an actual `cli.py` file
  write — component names come from `member.library`/`member.system`
  fact-store data, exactly as untrusted as existing cluster names, and the
  existing symlink/path-escape tests must keep passing unmodified.
- Confirm the `"inline"` key is present in every return shape (single
  component under/over threshold, and multi-component) — every existing
  caller (`cli.cmd_call_graph`, `SKILL.md`'s system-overview assembly)
  expects to find it unconditionally.
- Re-read `CLAUDE.md`'s "never commit client-specific content" section
  before writing any test fixture names or PR text — invented names only
  (`ALPHA`/`BETA`/`SHAREDLIB`/etc., not anything resembling a real client's
  program or system names).

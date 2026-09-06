# Design: connectivity-aware call-graph splitting, LR direction

Date: 2026-09-06
Status: approved
Issue: nightingalehq/legacy-functional-docs#68

## Problem

`structural.call_graph_diagram` (`mfdoc call-graph`) has two readability
issues on a real, non-trivial call graph:

1. **Layout direction.** It always renders `graph TD` (top-down).
   `data_flow_diagram` already renders `graph LR` for the same reason a call
   graph would benefit from it — a module with many siblings (callers or
   callees) produces a very *wide* shape, which reads far better
   left-to-right than stacked top-to-bottom. TD is inconsistent with the LR
   convention `data-flow.md` already established.

2. **Splitting is purely a node-count threshold, not connectivity.** Above
   `max_nodes_inline`, the diagram collapses to one node per `cluster_by`
   group (module/library/subsystem) with a full diagram per cluster. That's
   a reasonable fallback, but it doesn't reflect the graph's actual *shape*:
   two genuinely independent (disconnected) subsystems that both happen to
   be small are still forced into the same inline diagram if their combined
   node count is under the threshold, and a single large, densely-connected
   cluster still renders as one big diagram with no further breakdown.
   Confirmed against this repo's own bundled example fixtures (`examples/`):
   ingesting Natural, Mantis, JCL and CSD fixtures together produces six
   separate connected components today, all silently flattened into one
   `graph TD` diagram by the existing node-count-only logic.

## Non-goals

- No change to `data_flow_diagram` — it's already `graph LR` and has no
  splitting logic of its own (crud_matrix rows aren't a call graph).
- No change to `cluster_by`'s own semantics (module/library/subsystem
  grouping) — the connectivity split sits *above* it, and the existing
  collapse-then-per-cluster fallback is preserved verbatim, just scoped to
  one component instead of the whole graph.
- No LLM involvement — connected-component computation is a solved,
  deterministic graph algorithm; it belongs in `graph.py` next to
  `call_closure`/`orphans`, not in the narrative path.
- Not a change to how `call_edge` rows are populated by any dialect —
  `call_edge` is already dialect-neutral (every dialect extractor populates
  it the same way), so this needs no Natural/Mantis-specific work.
- Not a cap on the number of components or a re-merge heuristic for "too
  many tiny components" — if the real call graph is that fragmented, that's
  itself useful information for a reader, not something to paper over.

## Design

### `graph.connected_components(conn) -> list[set[int]]`

New function alongside `call_closure`/`orphans` in `graph.py`. Union-find
over `call_edge`, treating direction as irrelevant: a caller and a callee it
resolves to (`callee_id IS NOT NULL`, i.e. `graph.resolve()` matched it to a
real ingested member) are unioned into the same component regardless of
which one calls the other. A caller whose only outgoing calls are
unresolved (`callee_id IS NULL` — a missing source file, a dynamic target,
or a genuinely undefined callee) still gets its own singleton component: it
has no member id to union with anything, but must not be dropped, since
`call_graph_diagram`'s node universe includes every caller that appears in
`call_edge` at all, resolved or not.

Deliberately does **not** treat two callers as connected merely because they
each have an unresolved call to the same missing callee *name*. An
unresolved target has no member id — it's exactly as ambiguous as any other
bare name in this codebase (see `build_call_graph`'s own name-collision
handling) — and treating a shared missing-callee name as a bridge between
components would let a common utility name (or two coincidentally identical
missing-program names in unrelated subsystems) silently merge unrelated
components back together, defeating the point of the split. Each caller's
own unresolved-callee pseudo-node is rendered locally, inside whichever
component that caller ends up in — so the same missing name can legitimately
appear as a leaf in more than one component's diagram. This is a deliberate
scoping decision, not an oversight: the alternative (giving every distinct
missing name its own cross-component identity) adds real complexity for a
case (two unrelated call sites happening to reference an identically-named
missing program) that isn't what this issue is trying to fix.

Pure Python, no model call, no new SQL beyond `SELECT DISTINCT caller_id,
callee_id FROM call_edge` — matches `graph.py`'s existing contract
("pure extraction, no judgement call, byte-identical output on unchanged
source").

### `structural.call_graph_diagram`: direction and component split

Two new/changed parameters:

- `direction: str = "LR"` (new). Validated against `{"LR", "TD"}`,
  `ValueError` on anything else — the same posture `cluster_by` and the
  complexity-heatmap `metric` parameter already take for an unsupported
  value, rather than silently falling back. Threaded into every `render()`
  call as `graph {direction}` instead of the hardcoded `graph TD`.
- `max_nodes_inline` (unchanged signature, changed *scope*): applied per
  connected component, not to the combined node count across all of them.

Behaviour:

- **Whole graph is one connected component (including the empty-graph
  case)** — unchanged from before this issue, byte-for-byte apart from the
  direction: `{"inline": <diagram>}` if under threshold, else
  `{"inline": <collapsed cluster view>, <cluster>: <diagram>, ...}`. This is
  deliberately preserved rather than folded into the general multi-component
  path, per the issue's explicit scope note: *"keep the existing single-file
  output when there's only one component overall (don't force multi-file
  output as a behavior change for the common/small case)"*.
- **2+ connected components** — always split by component, regardless of
  the combined node count (the core fix): `{"inline": <index diagram, one
  node per component>, <component_name>: <that component's diagram>, ...}`.
  A component that is itself still over `max_nodes_inline` falls back to
  today's cluster-collapse behaviour, scoped to just that component's own
  members, contributing `<component_name>: <collapsed view>` plus
  `<component_name>-<cluster_name>: <diagram>` per cluster inside it.

### Component naming (the issue leaves this "TBD")

Chosen convention, applied in a deterministic order (see below):

1. If every caller member in a component shares the same `cluster_by`
   grouping (module/library or subsystem), name the component after that
   cluster — e.g. `PAYROLL`, matching the existing per-cluster file
   convention (`call-graph-PAYROLL.md`) a reader is already used to from the
   over-threshold fallback. This is the common case: most genuinely
   disconnected subsystems in a real codebase also live in their own
   library.
2. If a component spans more than one cluster, it has no single honest
   name — fall back to `component-<n>` (`call-graph-component-1.md`, ...).
3. **Collision handling** (the issue's own motivating example: "two members
   in the same library that never call each other"): if two *different*
   components both resolve to the same dominant cluster name under rule 1,
   that name can't be used unqualified for either — both get renumbered to
   `<name>-<n>` instead (e.g. `SHAREDLIB-1`, `SHAREDLIB-2`), so the two
   files never collide and a reader isn't misled into thinking they're one
   diagram's cluster label. This directly fixes the bug: previously those
   two members forced their way into one shared file even though they never
   call each other.

Numbering (`<n>`) is assigned in a **content-stable order**: components are
sorted by the lexicographically-smallest `(library, name, dialect)` triple
among their member ids (using the same `id_info` table
`call_graph_diagram` already builds for node labelling), not by `member.id`
or dict/set iteration order. This mirrors `mermaid_node_id()`'s own existing
rationale one function up — a component's number must not churn between
regenerations just because SQLite reassigned rowids on an unrelated ingest.

### `cli.cmd_call_graph`

No structural change needed. It already writes `"inline"` to
`call-graph.md` and iterates every other key to
`call-graph-<safe_cluster_filename(key)>.md` — component names (rule 1/2/3
above) pass through `safe_cluster_filename` exactly like today's cluster
names, and the per-cluster fallback's compound keys
(`<component_name>-<cluster_name>`) are just another string. Only the
`options.overview.diagrams.direction` plumbing (below) is new in `cli.py`.

### `project.yml` / options wiring

`options.overview.diagrams.direction`, following the exact
replace-not-merge convention `cluster_by`/`max_nodes_inline` already use:
read once in `cmd_call_graph` via
`diagrams_cfg.get("direction", "LR")` and passed straight through to
`structural.call_graph_diagram(..., direction=...)`. No merge with a
default dict, no partial-override semantics — same as its two siblings.

## Testing

- `graph.connected_components`: a fixture with two disconnected subgraphs
  yields two disjoint sets; a caller with only an unresolved call still
  gets its own singleton; two callers sharing an unresolved callee *name*
  in otherwise-unrelated parts of the graph do **not** get merged.
- `structural.call_graph_diagram`:
  - single connected component under threshold → unchanged single
    `{"inline": ...}` shape (regression guard for the "don't change the
    common case" requirement).
  - default direction is `LR`; `direction="TD"` still available;
    unsupported `direction` raises `ValueError`.
  - **the disconnected-subgraph fixture the issue calls out**: two small,
    unrelated subsystems, combined node count far under
    `max_nodes_inline`, must render as two separate diagrams, each
    containing only its own subsystem's nodes.
  - two disconnected components that share one dominant cluster name both
    get unique, numbered filenames rather than colliding.
  - the repo's own bundled example fixtures (`examples/`), which really do
    split into multiple components today, exercise the multi-component path
    end-to-end (not just synthetic fixtures).
- Existing tests that happened to construct multiple disconnected members
  as an implementation detail of testing something else (name-collision
  disambiguation, cluster_by grouping) were adjusted to either assert
  across all returned diagrams rather than assuming everything lands in
  `"inline"`, or to add one connecting call edge so they keep exercising the
  single-component code path they were actually designed to test.

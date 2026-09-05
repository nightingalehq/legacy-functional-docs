"""Deterministic, non-LLM structural renderers built on the fact store.

Every function here follows brief.rules_register()'s guarantee: pure
extraction, no judgement call, byte-identical output on unchanged
source (no embedded timestamps). If a doc needs synthesis/prose, it
belongs in brief.py's narrative-brief functions instead, not here.
"""

from __future__ import annotations

import hashlib

from . import graph
from .citations import _cite, _rule_id
from .redact import NULL_REDACTOR, Redactor


def gap_summary(conn) -> str:
    """Gap counts by kind and severity, for the top of system-overview.md
    -- surfaces documentation-confidence caveats before the narrative body."""
    rows = conn.execute(
        "SELECT gap_kind, severity, COUNT(*) AS n FROM gap "
        "GROUP BY gap_kind, severity ORDER BY n DESC, gap_kind, severity"
    ).fetchall()

    out = ["---", 'title: "Gap summary"', "doc_type: register", "---", "",
           "# Gap summary", "", (
        "Counts of unresolved items by kind and severity. See the per-module "
        "docs' inline gap notes, or `mfdoc brief --system`, for detail on "
        "each one. Regenerate with `mfdoc gap-summary` after any source "
        "change; do not hand-edit."
    ), ""]
    if not rows:
        out.append("No gaps recorded.")
        out.append("")
        return "\n".join(out) + "\n"

    out.append("| gap_kind | severity | count |")
    out.append("|---|---|---|")
    for r in rows:
        out.append(f"| `{r['gap_kind']}` | {r['severity']} | {r['n']} |")
    out.append("")
    return "\n".join(out) + "\n"


def _mermaid_id(name: str) -> str:
    """A Mermaid-safe node id: alnum/underscore only, so a source-derived
    name with spaces, hyphens, or punctuation can't break the diagram
    syntax.

    The alnum-or-underscore substitution alone is not injective: distinct
    names that differ only in punctuation-vs-underscore (e.g. `MILL-CERT`
    and `MILL_CERT`, a real collision shape between a DDM entity and a
    SQL-derived one in this repo's own fixtures) would otherwise map to
    the same id and silently merge two distinct nodes into one. Append a
    short deterministic hash of the *original* name to keep the mapping
    injective -- no randomness, same id every regeneration for the same
    name."""
    sanitized = "".join(c if c.isalnum() else "_" for c in name)
    # usedforsecurity=False: this hash only needs to be deterministic and
    # collision-resistant enough for diagram node ids, never a security
    # boundary -- without the flag, md5 raises under a FIPS-enforcing
    # Python build, which would break diagram generation entirely rather
    # than just being theoretically weak.
    suffix = hashlib.md5(name.encode("utf-8"), usedforsecurity=False).hexdigest()[:6]
    return f"n_{sanitized}_{suffix}"


def data_flow_diagram(conn) -> str:
    """Which modules read/write which entities -- a thin Mermaid wrapper
    over graph.crud_matrix(), which already does the join; no new
    extraction here."""
    rows = graph.crud_matrix(conn)

    out = ["---", 'title: "Data-flow diagram"', "doc_type: register", "---", "",
           "# Data-flow diagram", "", (
        "Module-to-entity read/write edges, derived from every recorded "
        "data-access statement. Regenerate with `mfdoc data-flow` after "
        "any source change; do not hand-edit."
    ), ""]
    if not rows:
        out.append("No data access recorded.")
        out.append("")
        return "\n".join(out) + "\n"

    out.append("```mermaid")
    out.append("graph LR")
    seen_nodes: set[str] = set()
    for row in rows:
        mod_id, ent_id = _mermaid_id(row["module"]), _mermaid_id(row["entity"])
        if mod_id not in seen_nodes:
            mod_label = row["module"].replace('"', '\\"')
            out.append(f'    {mod_id}["{mod_label}"]')
            seen_nodes.add(mod_id)
        if ent_id not in seen_nodes:
            ent_label = row["entity"].replace('"', '\\"')
            out.append(f'    {ent_id}[("{ent_label}")]')
            seen_nodes.add(ent_id)
        out.append(f'    {mod_id} -->|{row["crud"]}| {ent_id}')
    out.append("```")
    out.append("")
    return "\n".join(out) + "\n"


def build_call_graph(conn, cluster_by: str = "module") -> dict[int, dict]:
    """Every member that appears as a caller in at least one call_edge row
    (a callee-only member, with no outgoing calls of its own, gets no
    top-level entry here -- it still appears as a callee inside another
    member's "calls" list, and call_graph_diagram accounts for it
    separately when computing the diagram's total node count), with its
    outgoing calls and cluster label. cluster_by picks which column feeds
    the cluster label: "subsystem" uses member.system, "module"/"library"
    (aliases for the same grouping) use member.library -- clustering by
    subsystem/module happens at render time in call_graph_diagram, this
    just carries the raw fact under the requested grouping. Any other
    value raises ValueError rather than silently falling back to library
    clustering (e.g. a config typo like "subsytem" would otherwise
    cluster by library with no warning), matching complexity_heatmap's
    posture for its own unsupported-value case.

    Keyed by the caller's member.id, not its bare name: `member.name` is
    only unique together with (library, dialect) (see the
    `UNIQUE(name, library, dialect)` constraint in db.py), so two
    distinct members in different libraries can share a name, and
    keying by name alone would silently conflate them into one node
    (Finding 1). The caller's own display name/library are carried
    inside the entry so a caller can still render/label it.

    Each entry's "calls" list carries, per call_edge row: "callee_id"
    (graph.resolve()'s real, unambiguous foreign key when the edge is
    resolved and the callee is an ingested member -- None for a genuinely
    unresolved call, or for an internal PERFORM/PERFORM-like target that
    has no member row of its own), "callee_name" (always present, the
    display name), "callee_library" (the callee member's library, only
    when callee_id is set), and "resolved" (bool). A caller must key off
    "callee_id" when present rather than "callee_name" -- that name alone
    is exactly as ambiguous as the caller's own bare name is."""
    valid_cluster_by = {"module", "library", "subsystem"}
    if cluster_by not in valid_cluster_by:
        raise ValueError(
            f"unsupported cluster_by {cluster_by!r}; expected one of {sorted(valid_cluster_by)}"
        )
    cluster_column = "system" if cluster_by == "subsystem" else "library"
    rows = conn.execute(
        f"""
        SELECT m.id AS caller_id, m.name AS caller, m.library AS caller_library,
               m.dialect AS caller_dialect, m.{cluster_column} AS caller_cluster,
               ce.callee_name, ce.resolved, ce.callee_id,
               cm.library AS callee_library, cm.dialect AS callee_dialect
          FROM call_edge ce
          JOIN member m ON m.id = ce.caller_id
          LEFT JOIN member cm ON cm.id = ce.callee_id
         ORDER BY m.name, m.id, ce.line_no, ce.callee_name
        """
    ).fetchall()

    graph_data: dict[int, dict] = {}
    for r in rows:
        entry = graph_data.setdefault(
            r["caller_id"],
            {
                "name": r["caller"],
                "library": r["caller_library"] or "unknown",
                "dialect": r["caller_dialect"] or "unknown",
                "cluster": r["caller_cluster"] or "unknown",
                "calls": [],
            },
        )
        callee_id = r["callee_id"]
        entry["calls"].append({
            "callee_id": callee_id,
            "callee_name": r["callee_name"],
            "callee_library": (r["callee_library"] or "unknown") if callee_id is not None else None,
            "callee_dialect": (r["callee_dialect"] or "unknown") if callee_id is not None else None,
            "resolved": bool(r["resolved"]),
        })
    return graph_data


def call_graph_diagram(conn, cluster_by: str = "module", max_nodes_inline: int = 40) -> dict[str, str]:
    """Mermaid call-graph DAG. If total distinct nodes <= max_nodes_inline,
    returns {"inline": <one diagram for system-overview.md>}. Otherwise
    returns {"inline": <collapsed cluster-level view>, <cluster_name>:
    <that cluster's full diagram>, ...} -- callers write "inline" into
    system-overview.md and every other key to call-graph-<cluster>.md.

    Unresolved calls render as dashed edges to their own node, labeled
    with the missing callee's name (not a single anonymous shared sink)
    -- so a reader can see *which* program is missing, not just that
    something is. Both resolved and unresolved edges are deduped per
    (caller, callee) pair -- a caller invoking the same callee from
    multiple lines produces one edge, not one per call site.

    Nodes are identified by member id wherever build_call_graph gives us
    one (every caller; a callee whose call_edge resolved to a real
    member via graph.resolve()'s callee_id), never by bare name --
    member.name is only unique together with (library, dialect), so two
    distinct members sharing a name in different libraries must render
    as two distinct nodes (Finding 1), not merge into one. A callee with
    no callee_id (a genuinely unresolved call, or an internal
    PERFORM-style target with no member row of its own) still has only
    its bare name to key/label off -- that's all the data supports, and
    matches the existing unresolved-edge rendering. When a name is
    shared by more than one known member id, each id's node is labeled
    "NAME (LIBRARY)" instead of the bare name, so a reader can tell them
    apart -- mirroring complexity_heatmap's explicit ambiguous-row
    convention for the identical underlying ambiguity.

    The *mermaid* node id rendered into the diagram (see
    mermaid_node_id() below) is derived from the (library, name, dialect)
    triple, not the member.id rowid used to key/group internally here --
    so node ids stay stable across regenerations even when ingest order
    changes and rowids get renumbered."""
    graph_data = build_call_graph(conn, cluster_by=cluster_by)

    # id_info maps every known member id (caller or resolved callee) to its
    # (name, library); name_to_ids inverts that to detect which bare names
    # are actually shared by more than one id, so only genuinely ambiguous
    # names get the disambiguating "(LIBRARY)" suffix in their label.
    id_info: dict[int, tuple[str, str, str]] = {}
    for caller_id, entry in graph_data.items():
        id_info[caller_id] = (entry["name"], entry["library"], entry["dialect"])
    for entry in graph_data.values():
        for call in entry["calls"]:
            if call["callee_id"] is not None:
                id_info.setdefault(
                    call["callee_id"],
                    (
                        call["callee_name"],
                        call["callee_library"] or "unknown",
                        call["callee_dialect"] or "unknown",
                    ),
                )
    name_to_ids: dict[str, set[int]] = {}
    for member_id, (name, _library, _dialect) in id_info.items():
        name_to_ids.setdefault(name, set()).add(member_id)

    def node_key(callee_id: int | None, callee_name: str) -> tuple[str, object]:
        return ("id", callee_id) if callee_id is not None else ("name", callee_name)

    def node_label(key: tuple[str, object]) -> str:
        kind, val = key
        if kind == "name":
            return str(val)
        name, library, _dialect = id_info[val]
        if len(name_to_ids.get(name, ())) > 1:
            return f"{name} ({library})"
        return name

    def mermaid_node_id(key: tuple[str, object]) -> str:
        # Derived from the content-stable (library, name, dialect) triple --
        # the same uniqueness constraint as member's own
        # UNIQUE(name, library, dialect) -- rather than member.id (a SQLite
        # rowid). Keying by rowid meant inserting one new source file
        # earlier in ingest order renumbered every downstream member id,
        # which churned every node id in call-graph.md even though nothing
        # about the actual members changed (Finding 1 follow-up). This
        # composite still disambiguates two members sharing a bare name in
        # different libraries, since that's the real DB uniqueness
        # constraint being mirrored here.
        kind, val = key
        if kind == "name":
            return _mermaid_id(str(val))
        name, library, dialect = id_info[val]
        return _mermaid_id(f"{library}|{name}|{dialect}")

    nodes = {node_key(cid, entry["name"]) for cid, entry in graph_data.items()} | {
        node_key(c["callee_id"], c["callee_name"]) for e in graph_data.values() for c in e["calls"]
    }

    def render(callers: dict[int, dict], title: str) -> str:
        lines = ["```mermaid", "graph TD"]
        seen: set[str] = set()
        seen_edges: set[tuple[str, str, bool]] = set()
        for caller_member_id, entry in callers.items():
            caller_key = node_key(caller_member_id, entry["name"])
            caller_node_id = mermaid_node_id(caller_key)
            if caller_node_id not in seen:
                caller_label = node_label(caller_key).replace('"', '\\"')
                lines.append(f'    {caller_node_id}["{caller_label}"]')
                seen.add(caller_node_id)
            for call in entry["calls"]:
                callee_key = node_key(call["callee_id"], call["callee_name"])
                callee_node_id = mermaid_node_id(callee_key)
                edge_key = (caller_node_id, callee_node_id, call["resolved"])
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                if call["resolved"]:
                    if callee_node_id not in seen:
                        callee_label = node_label(callee_key).replace('"', '\\"')
                        lines.append(f'    {callee_node_id}["{callee_label}"]')
                        seen.add(callee_node_id)
                    lines.append(f"    {caller_node_id} --> {callee_node_id}")
                else:
                    if callee_node_id not in seen:
                        callee_label = node_label(callee_key).replace('"', '\\"')
                        lines.append(f'    {callee_node_id}(["{callee_label} (unresolved)"])')
                        seen.add(callee_node_id)
                    lines.append(f"    {caller_node_id} -.->|unresolved| {callee_node_id}")
        lines.append("```")
        return (
            f'---\ntitle: "{title}"\ndoc_type: register\n---\n\n'
            f"# {title}\n\n" + "\n".join(lines) + "\n"
        )

    if len(nodes) <= max_nodes_inline:
        return {"inline": render(graph_data, "Call graph")}

    clusters: dict[str, dict] = {}
    for caller_member_id, entry in graph_data.items():
        clusters.setdefault(entry["cluster"], {})[caller_member_id] = entry

    collapsed_lines = ["```mermaid", "graph TD"]
    for cluster_name in clusters:
        cluster_label = f"{cluster_name} (see call-graph-{cluster_name}.md)".replace('"', '\\"')
        collapsed_lines.append(f'    {_mermaid_id(cluster_name)}["{cluster_label}"]')
    collapsed_lines.append("```")
    result = {
        "inline": (
            '---\ntitle: "Call graph (collapsed)"\ndoc_type: register\n---\n\n'
            "# Call graph (collapsed)\n\n"
            "Too many nodes for one inline diagram -- one node per cluster below; "
            "see the standalone file for each cluster's full graph.\n\n"
            + "\n".join(collapsed_lines) + "\n"
        )
    }
    for cluster_name, members in clusters.items():
        result[cluster_name] = render(members, f"Call graph — {cluster_name}")
    return result


def _complexity_rows(conn, metric: str = "rule_depth") -> list[dict]:
    """The per-member structured data complexity_heatmap() renders into a
    markdown table -- factored out so a caller that needs this data (e.g.
    brief.executive_brief's Risk section) can look a member up by id
    directly, instead of re-parsing complexity_heatmap()'s markdown output
    (which would silently break the moment the table's format changes).

    Returns one dict per unambiguous member with at least one
    rule_candidate row -- {"ambiguous": False, "member_id", "member"
    (name), "rule_count", "max_depth", "in_degree", "out_degree",
    "risk_score"} -- plus one dict per *ambiguous* bare name (a name
    shared by >1 member.id, only unique together with library+dialect --
    see the `UNIQUE(name, library, dialect)` constraint in db.py):
    {"ambiguous": True, "member": name, "member_ids": [...], "libraries":
    [...]}. A caller holding a specific member_id should match on
    "member_id" for a normal row, or check membership in "member_ids" for
    an ambiguous one -- mirroring rules_register()'s refusal to guess
    which of the colliding members a bare name means, instead of
    silently merging their counts into one row or dropping one entirely.
    """
    if metric != "rule_depth":
        raise ValueError(f"unsupported complexity metric {metric!r}; only 'rule_depth' is implemented")

    # `member.name` is only unique together with library+dialect (see the
    # `UNIQUE(name, library, dialect)` constraint in db.py) -- two distinct
    # members can share a bare name across libraries/dialects. Group by
    # m.id, not m.name, so each member's own rule_count/max_depth stays
    # distinct even when names collide; a colliding name is then rendered
    # as an explicit ambiguous row below (mirroring rules_register's
    # refusal for the identical case) instead of silently merging the two
    # members' counts into one row and dropping the other entirely.
    rule_rows = conn.execute(
        """
        SELECT m.id AS member_id, m.name AS member, m.library AS library,
               COUNT(*) AS rule_count, MAX(rc.depth) AS max_depth
          FROM rule_candidate rc JOIN member m ON m.id = rc.member_id
         GROUP BY m.id
        """
    ).fetchall()
    if not rule_rows:
        return []

    rows_by_name: dict[str, list] = {}
    for r in rule_rows:
        rows_by_name.setdefault(r["member"], []).append(r)

    # Keyed by member id (not name) so in/out-degree is attributed to the
    # correct member even when its bare name collides with another one --
    # build_call_graph's own keys/callee_id already carry that distinction
    # (see Finding 1), so degree must be looked up the same way here rather
    # than re-flattening back onto bare names.
    graph_data = build_call_graph(conn)
    out_degree_by_id = {member_id: len(entry["calls"]) for member_id, entry in graph_data.items()}
    in_degree_by_id: dict[int, int] = {}
    for entry in graph_data.values():
        for call in entry["calls"]:
            if call["callee_id"] is not None:
                in_degree_by_id[call["callee_id"]] = in_degree_by_id.get(call["callee_id"], 0) + 1

    raw_scores = []
    ambiguous_names: set[str] = set()
    for name, matches in rows_by_name.items():
        if len(matches) != 1:
            ambiguous_names.add(name)
            continue
        r = matches[0]
        ind = in_degree_by_id.get(r["member_id"], 0)
        outd = out_degree_by_id.get(r["member_id"], 0)
        raw = (r["rule_count"] + (r["max_depth"] or 0)) * (ind + outd + 1)
        raw_scores.append((r["member_id"], name, r["rule_count"], r["max_depth"] or 0, ind, outd, raw))

    max_raw = (max(s[-1] for s in raw_scores) if raw_scores else 0) or 1
    result = [
        {
            "ambiguous": False,
            "member_id": member_id,
            "member": member,
            "rule_count": rc,
            "max_depth": md,
            "in_degree": ind,
            "out_degree": outd,
            "risk_score": round(100 * raw / max_raw, 1),
        }
        for member_id, member, rc, md, ind, outd, raw in raw_scores
    ]
    result.sort(key=lambda row: row["risk_score"], reverse=True)

    for name in sorted(ambiguous_names):
        matches = rows_by_name[name]
        result.append(
            {
                "ambiguous": True,
                "member": name,
                "member_ids": [m["member_id"] for m in matches],
                "libraries": sorted({m["library"] or "unknown" for m in matches}),
            }
        )
    return result


def complexity_heatmap(conn, metric: str = "rule_depth") -> str:
    """Risk-ranked member table. Only 'rule_depth' is implemented: rule
    count + max nesting depth per member (both already recorded in
    rule_candidate at extraction time -- no new parsing), combined with
    call-graph in/out-degree from build_call_graph(). 'cyclomatic' is a
    documented-but-unimplemented future option (see
    options.overview.complexity.metric in project.yml) so a project can
    already select it in config without a later migration; requesting
    it today raises rather than silently falling back to rule_depth.

    Pure rendering over _complexity_rows()'s structured data -- see that
    function for how ambiguous bare names are represented."""
    rows = _complexity_rows(conn, metric=metric)
    if not rows:
        return (
            '---\ntitle: "Complexity/risk heatmap"\ndoc_type: register\n---\n\n'
            "# Complexity/risk heatmap\n\nNo rule candidates recorded.\n"
        )

    out = ["---", 'title: "Complexity/risk heatmap"', "doc_type: register", "---", "",
           "# Complexity/risk heatmap", "", (
        "risk_score = (rule_count + max_depth) * (in_degree + out_degree + 1), "
        "normalized 0-100 across this run's members. A simple v1 proxy for "
        "where to focus review, not a certified complexity metric."
    ), "",
           "| member | rule_count | max_depth | in_degree | out_degree | risk_score |",
           "|---|---|---|---|---|---|"]
    for row in rows:
        if row["ambiguous"]:
            libs = ", ".join(row["libraries"])
            out.append(
                f"| `{row['member']}` | — | — | — | — | ambiguous: name is ambiguous across "
                f"libraries ({libs}) -- re-run against a library-qualified export |"
            )
        else:
            out.append(
                f"| `{row['member']}` | {row['rule_count']} | {row['max_depth']} | "
                f"{row['in_degree']} | {row['out_degree']} | {row['risk_score']} |"
            )
    out.append("")
    return "\n".join(out) + "\n"


def thematic_rules_register(conn, redact: Redactor = NULL_REDACTOR) -> str:
    """Same rows as brief.rules_register(), grouped by rule_theme.theme
    instead of by member -- lets a reviewer see every rule about one
    business concept across the whole system in one place.

    BR-ID numbering is computed exactly the way rules_register() computes
    it -- per member, in ascending line_no order -- and only afterwards
    bucketed by theme for display. This ordering separation matters: a
    single member's rules can be split across multiple themes, and if the
    per-member sequence counter were driven by a query ordered by theme
    first, a member's own rules would be numbered in theme order rather
    than line order, diverging from rules_register()'s IDs for that
    member. Computing the sequence from a member_id/line_no-ordered pass
    keeps `MEMBER:BR-nnn` identical between the two documents regardless
    of how theme classification splits a member's rules across sections.

    Ambiguous member names (a bare name shared across >1 library) are
    skipped from numbering entirely, mirroring rules_register()'s refusal
    to guess -- see the comment there for why silently picking one would
    misattribute rules. Unlike rules_register() though, an ambiguous
    member's rules can't be filed under any theme heading (there's no
    single member to resolve the rows from), so each ambiguous name gets
    one explicit row -- in the same format rules_register() renders for
    the identical case -- under a trailing `## (ambiguous)` section,
    rather than silently vanishing from the document.

    Each `## {theme}` section opens with a one-line count breakdown by
    `rule_theme.source` (e.g. `9 rule(s) (7 keyword, 2 structural)`) --
    provenance is surfaced once per theme rather than as a per-row column,
    since the row table is already seven columns wide.
    """
    from .batch import select_batch_members  # local: avoids a circular import at load time

    names = list(dict.fromkeys(select_batch_members(conn)))
    placeholders = ",".join("?" * len(names))
    member_rows = (
        conn.execute(
            f"SELECT id, name, library FROM member WHERE name IN ({placeholders})", names
        ).fetchall()
        if names
        else []
    )
    rows_by_name: dict[str, list] = {}
    for row in member_rows:
        rows_by_name.setdefault(row["name"], []).append(row)

    resolved_names = [name for name in names if len(rows_by_name.get(name, [])) == 1]
    ambiguous_names = [name for name in names if len(rows_by_name.get(name, [])) != 1]
    id_to_name = {rows_by_name[name][0]["id"]: name for name in resolved_names}
    resolved_ids = list(id_to_name)

    id_placeholders = ",".join("?" * len(resolved_ids))
    rule_rows = (
        conn.execute(
            f"""
            SELECT rc.*, COALESCE(rt.theme, 'uncategorized') AS theme,
                   COALESCE(rt.source, 'uncategorized') AS theme_source
              FROM rule_candidate rc
              LEFT JOIN rule_theme rt ON rt.rule_candidate_id = rc.id
             WHERE rc.member_id IN ({id_placeholders})
             ORDER BY rc.member_id, rc.line_no
            """,
            resolved_ids,
        ).fetchall()
        if resolved_ids
        else []
    )

    # Sequence numbers assigned in member_id/line_no order (matching
    # rules_register()'s numbering exactly) -- theme grouping happens only
    # in the second pass below, after every rule_id is already fixed.
    per_member_seq: dict[int, int] = {}
    by_theme: dict[str, list[str]] = {}
    source_counts_by_theme: dict[str, dict[str, int]] = {}
    total = 0
    for r in rule_rows:
        member_name = id_to_name[r["member_id"]]
        per_member_seq[r["member_id"]] = per_member_seq.get(r["member_id"], 0) + 1
        rule_id = _rule_id(member_name, per_member_seq[r["member_id"]])
        cond = redact(r["condition"]).replace("|", "\\|") if r["condition"] else ""
        lits = redact(r["literals"]).replace("|", "\\|") if r["literals"] else ""
        by_theme.setdefault(r["theme"], []).append(
            f"| **{rule_id}** | `{member_name}` | {_cite(member_name, r['line_no'])} | "
            f"{r['depth']} | `{r['construct']}` | `{cond}` | `{lits}` |"
        )
        theme_counts = source_counts_by_theme.setdefault(r["theme"], {})
        theme_counts[r["theme_source"]] = theme_counts.get(r["theme_source"], 0) + 1
        total += 1

    out = ["---", 'title: "Rules register — by theme"', "doc_type: register", "---", "",
           "# Rules register — by theme", "", (
        "Every candidate business rule, grouped by business theme instead "
        "of by module. Same `MEMBER:BR-nnn` IDs as the per-module docs and "
        "`mfdoc rules-register`. Regenerate with `mfdoc rules-theme-register` "
        "after any source or taxonomy change; do not hand-edit."
    ), ""]
    _SOURCE_ORDER = ("keyword", "llm", "structural", "uncategorized")
    for theme in sorted(by_theme):
        out.append(f"## {theme}")
        out.append("")
        theme_counts = source_counts_by_theme[theme]
        breakdown = ", ".join(
            f"{theme_counts[source]} {source}"
            for source in _SOURCE_ORDER
            if theme_counts.get(source)
        )
        out.append(f"{len(by_theme[theme])} rule(s) ({breakdown})")
        out.append("")
        out.append("| BR-ID | member | line | depth | construct | condition | literals |")
        out.append("|---|---|---|---|---|---|---|")
        out.extend(by_theme[theme])
        out.append("")

    if ambiguous_names:
        out.append("## (ambiguous)")
        out.append("")
        out.append("| BR-ID | member | line | depth | construct | condition | literals |")
        out.append("|---|---|---|---|---|---|---|")
        for member_name in sorted(ambiguous_names):
            libs = ", ".join(sorted({m["library"] or "unknown" for m in rows_by_name[member_name]}))
            out.append(
                f"| — | `{member_name}` | — | — | ambiguous | name is ambiguous across "
                f"libraries ({libs}) -- re-run `mfdoc brief --module {member_name}` "
                "per library | — |"
            )
        out.append("")

    if ambiguous_names:
        out.append(
            f"Total: {total} rule candidate(s) across {len(by_theme)} theme(s) and "
            f"{len(resolved_names)} unambiguous batchable module(s); rules belonging to "
            f"{len(ambiguous_names)} ambiguous-named module(s) are listed under "
            '"(ambiguous)" above and excluded from this count.'
        )
    else:
        out.append(f"Total: {total} rule candidate(s) across {len(by_theme)} theme(s).")
    out.append("")
    return "\n".join(out) + "\n"


def glossary(conn, redact: Redactor = NULL_REDACTOR) -> str:
    """One entry per (entity name, kind) pair with its fields nested
    underneath -- reads directly from entity.notes / entity_field.remark,
    which already carry description text at extraction time. Deliberately
    does not parse already-generated data-entity.md prose: the DB is the
    source of truth and this stays independent of doc-generation order.

    A name is only unique together with kind (a legitimate case in this
    repo's own fixtures: `MILL-ORDER` exists both as a `ddm` and as an
    `adabas_file`, each with its own notes/fields) -- so this renders one
    `### NAME` heading per distinct (name, kind) pair rather than
    deduplicating to one row per bare name, which would silently drop
    every kind but the first one `ORDER BY name` happened to return.
    `ORDER BY name, kind` gives that ordering an explicit, deterministic
    tie-break so which kind's block comes first no longer depends on
    incidental row order -- restoring the byte-identical-regeneration
    guarantee for *content*, not just line order."""
    entities = conn.execute(
        "SELECT id, name, kind, notes FROM entity ORDER BY name, kind"
    ).fetchall()

    out = ["---", 'title: "Glossary"', "doc_type: register", "---", "",
           "# Glossary", "", (
        "Every known entity (Adabas file, DDM, table, dataset, ...) and "
        "its fields, deduplicated across the whole system. A name shared "
        "by more than one kind (e.g. a DDM and its underlying Adabas "
        "file) gets one heading per kind, labelled accordingly. "
        "Regenerate with `mfdoc glossary` after any source change; do "
        "not hand-edit."
    ), ""]
    name_seen_count: dict[str, int] = {}
    for e in entities:
        name_seen_count[e["name"]] = name_seen_count.get(e["name"], 0) + 1
    for e in entities:
        heading = e["name"]
        if name_seen_count[e["name"]] > 1:
            heading = f"{e['name']} ({e['kind']})"
        out.append(f"### {heading}")
        out.append("")
        out.append(f"- kind: `{e['kind']}`")
        if e["notes"]:
            out.append(f"- notes: {redact(e['notes'])}")
        fields = conn.execute(
            "SELECT name, format, length, remark FROM entity_field WHERE entity_id=? ORDER BY name",
            (e["id"],),
        ).fetchall()
        if fields:
            out.append("")
            out.append("| field | format | length | remark |")
            out.append("|---|---|---|---|")
            for f in fields:
                remark = redact(f["remark"]).replace("|", "\\|") if f["remark"] else ""
                out.append(f"| `{f['name']}` | {f['format'] or ''} | {f['length'] or ''} | {remark} |")
        out.append("")
    return "\n".join(out) + "\n"

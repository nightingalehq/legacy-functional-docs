"""Deterministic, non-LLM structural renderers built on the fact store.

Every function here follows brief.rules_register()'s guarantee: pure
extraction, no judgement call, byte-identical output on unchanged
source (no embedded timestamps). If a doc needs synthesis/prose, it
belongs in brief.py's narrative-brief functions instead, not here.
"""

from __future__ import annotations

from . import graph


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
    syntax."""
    return "n_" + "".join(c if c.isalnum() else "_" for c in name)


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


def build_call_graph(conn, cluster_by: str = "module") -> dict[str, dict]:
    """Every member with at least one call edge (as caller or callee),
    with its outgoing calls and cluster label. cluster_by picks which
    column feeds the cluster label: "subsystem" uses member.system,
    anything else (the "module"/"library" default) uses member.library --
    clustering by subsystem/module happens at render time in
    call_graph_diagram, this just carries the raw fact under the
    requested grouping."""
    cluster_column = "system" if cluster_by == "subsystem" else "library"
    rows = conn.execute(
        f"""
        SELECT m.name AS caller, m.{cluster_column} AS caller_cluster,
               ce.callee_name, ce.resolved
          FROM call_edge ce JOIN member m ON m.id = ce.caller_id
        """
    ).fetchall()

    graph_data: dict[str, dict] = {}
    for r in rows:
        entry = graph_data.setdefault(
            r["caller"], {"cluster": r["caller_cluster"] or "unknown", "calls": []}
        )
        entry["calls"].append({"callee": r["callee_name"], "resolved": bool(r["resolved"])})
    return graph_data


def call_graph_diagram(conn, cluster_by: str = "module", max_nodes_inline: int = 40) -> dict[str, str]:
    """Mermaid call-graph DAG. If total distinct nodes <= max_nodes_inline,
    returns {"inline": <one diagram for system-overview.md>}. Otherwise
    returns {"inline": <collapsed cluster-level view>, <cluster_name>:
    <that cluster's full diagram>, ...} -- callers write "inline" into
    system-overview.md and every other key to call-graph-<cluster>.md.

    Unresolved calls render as dashed edges into a single shared
    "unresolved" sink node, so gaps are visible in the diagram instead
    of silently dropped."""
    graph_data = build_call_graph(conn, cluster_by=cluster_by)
    nodes = set(graph_data) | {c["callee"] for e in graph_data.values() for c in e["calls"]}

    def render(callers: dict[str, dict], title: str) -> str:
        lines = ["```mermaid", "graph TD"]
        seen: set[str] = set()
        has_unresolved = False
        for caller, entry in callers.items():
            caller_id = _mermaid_id(caller)
            if caller_id not in seen:
                caller_label = caller.replace('"', '\\"')
                lines.append(f'    {caller_id}["{caller_label}"]')
                seen.add(caller_id)
            for call in entry["calls"]:
                callee_id = _mermaid_id(call["callee"])
                if call["resolved"]:
                    if callee_id not in seen:
                        callee_label = call["callee"].replace('"', '\\"')
                        lines.append(f'    {callee_id}["{callee_label}"]')
                        seen.add(callee_id)
                    lines.append(f"    {caller_id} --> {callee_id}")
                else:
                    has_unresolved = True
                    lines.append(f"    {caller_id} -.->|unresolved| unresolved_sink")
        if has_unresolved:
            lines.append('    unresolved_sink["external / unresolved"]')
        lines.append("```")
        return (
            f'---\ntitle: "{title}"\ndoc_type: register\n---\n\n'
            f"# {title}\n\n" + "\n".join(lines) + "\n"
        )

    if len(nodes) <= max_nodes_inline:
        return {"inline": render(graph_data, "Call graph")}

    clusters: dict[str, dict] = {}
    for caller, entry in graph_data.items():
        clusters.setdefault(entry["cluster"], {})[caller] = entry

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


def complexity_heatmap(conn, metric: str = "rule_depth") -> str:
    """Risk-ranked member table. Only 'rule_depth' is implemented: rule
    count + max nesting depth per member (both already recorded in
    rule_candidate at extraction time -- no new parsing), combined with
    call-graph in/out-degree from build_call_graph(). 'cyclomatic' is a
    documented-but-unimplemented future option (see
    options.overview.complexity.metric in project.yml) so a project can
    already select it in config without a later migration; requesting
    it today raises rather than silently falling back to rule_depth."""
    if metric != "rule_depth":
        raise ValueError(f"unsupported complexity metric {metric!r}; only 'rule_depth' is implemented")

    rule_rows = conn.execute(
        """
        SELECT m.name AS member, COUNT(*) AS rule_count, MAX(rc.depth) AS max_depth
          FROM rule_candidate rc JOIN member m ON m.id = rc.member_id
         GROUP BY m.name
        """
    ).fetchall()
    if not rule_rows:
        return (
            '---\ntitle: "Complexity/risk heatmap"\ndoc_type: register\n---\n\n'
            "# Complexity/risk heatmap\n\nNo rule candidates recorded.\n"
        )

    graph_data = build_call_graph(conn)
    out_degree = {name: len(entry["calls"]) for name, entry in graph_data.items()}
    in_degree: dict[str, int] = {}
    for entry in graph_data.values():
        for call in entry["calls"]:
            in_degree[call["callee"]] = in_degree.get(call["callee"], 0) + 1

    raw_scores = []
    for r in rule_rows:
        ind, outd = in_degree.get(r["member"], 0), out_degree.get(r["member"], 0)
        raw = (r["rule_count"] + (r["max_depth"] or 0)) * (ind + outd + 1)
        raw_scores.append((r["member"], r["rule_count"], r["max_depth"] or 0, ind, outd, raw))

    max_raw = max(s[-1] for s in raw_scores) or 1
    scored = [
        (member, rc, md, ind, outd, round(100 * raw / max_raw, 1))
        for member, rc, md, ind, outd, raw in raw_scores
    ]
    scored.sort(key=lambda row: row[-1], reverse=True)

    out = ["---", 'title: "Complexity/risk heatmap"', "doc_type: register", "---", "",
           "# Complexity/risk heatmap", "", (
        "risk_score = (rule_count + max_depth) * (in_degree + out_degree + 1), "
        "normalized 0-100 across this run's members. A simple v1 proxy for "
        "where to focus review, not a certified complexity metric."
    ), "",
           "| member | rule_count | max_depth | in_degree | out_degree | risk_score |",
           "|---|---|---|---|---|---|"]
    for member, rc, md, ind, outd, score in scored:
        out.append(f"| `{member}` | {rc} | {md} | {ind} | {outd} | {score} |")
    out.append("")
    return "\n".join(out) + "\n"

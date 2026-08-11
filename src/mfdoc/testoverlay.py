"""Bug/spec curation overlay -- the one place a human (or a model drafting
*for* a human) is allowed to say "this rule is a known defect" without that
claim being invented by the render stage.

`test-overlay.yml` maps a scenario's stable `MEMBER:BR-nnn` id to a status
override. Every entry an LLM drafts (`mfdoc test-overlay-draft`) is written
with `review_status: draft` and a citation; testplan.py only ever applies
an override once review_status has moved past `draft` -- i.e. a human
looked at it. An overlay file with no entries, or one where every entry is
still `draft`, changes nothing: every scenario stays `characterization`,
the same as if the overlay didn't exist. This mirrors validate.py's
`review_status` vocabulary and the "human must promote it" rule the
architecture doc lays out for narrative docs.
"""

from __future__ import annotations

import json

import yaml

from .validate import VALID_REVIEW

STATUS_CHOICES = {"characterization", "spec", "bug-current", "bug-desired"}
# review_status values that count as "a human looked at this" -- draft alone
# (the model's own unreviewed output) never takes effect.
PROMOTED_REVIEW_STATUSES = {"in_review", "sme_approved", "signed_off"}


def load_overlay(path) -> dict:
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {}
    return data


def save_overlay(path, overlay: dict) -> None:
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(overlay, sort_keys=True), encoding="utf-8")


def overlay_status_for(overlay: dict, scenario_name: str) -> str:
    entry = overlay.get(scenario_name)
    if not isinstance(entry, dict):
        return "characterization"
    if entry.get("review_status") not in PROMOTED_REVIEW_STATUSES:
        return "characterization"
    status = entry.get("status")
    return status if status in STATUS_CHOICES else "characterization"


def validate_overlay_entries(entries: dict, known_scenarios: set) -> list[str]:
    """Structural checks a drafted (or hand-edited) overlay must pass before
    being written/merged -- not a citation resolver (scenario ids already
    carry their own citation via test_case.citation), just the shape."""
    problems = []
    for name, entry in entries.items():
        if name not in known_scenarios:
            problems.append(f"{name}: not a known test_case scenario id")
            continue
        if not isinstance(entry, dict):
            problems.append(f"{name}: entry must be a mapping")
            continue
        status = entry.get("status")
        if status not in STATUS_CHOICES:
            problems.append(f"{name}: status '{status}' not one of {sorted(STATUS_CHOICES)}")
        rs = entry.get("review_status")
        if rs not in VALID_REVIEW:
            problems.append(f"{name}: review_status '{rs}' not one of {sorted(VALID_REVIEW)}")
        if not entry.get("note"):
            problems.append(f"{name}: missing note explaining the divergence")
    return problems


def build_overlay_draft_prompt(member_name: str, test_brief: str, module_doc: str | None) -> str:
    parts = [
        "You are drafting *candidate* bug-vs-spec annotations for one legacy "
        "mainframe module's derived test scenarios. For each scenario below, "
        "decide whether its cited source excerpt looks like it diverges from "
        "the module's documented/intended behaviour. Only propose an entry "
        "when you can point at a concrete divergence with a citation already "
        "present in the brief -- do not propose an entry just to have "
        "coverage, and do not invent an intended behaviour that isn't stated "
        "in the module doc. If nothing looks like a divergence, output an "
        "empty mapping (`{}`).\n\n"
        "Output *only* YAML: a mapping from scenario id (e.g. `MMP0100:BR-004`) "
        "to `{status, note, review_status}`. `status` is one of "
        f"{sorted(STATUS_CHOICES)}. `review_status` MUST be `draft` -- you are "
        "proposing, not confirming; a human decides whether to promote it. "
        "`note` must state the specific divergence and cite the exact source "
        "line(s) involved.",
        "# Test brief\n\n" + test_brief,
    ]
    if module_doc:
        parts.append("# Module doc (documented/intended behaviour)\n\n" + module_doc)
    else:
        parts.append(
            "# Module doc\n\nNone available for this member -- without a stated "
            "intended behaviour to compare against, output `{}` for it."
        )
    return "\n\n---\n\n".join(parts)


def parse_overlay_response(text: str, known_scenarios: set) -> tuple[dict, list[str]]:
    """Best-effort YAML extraction (a model may wrap it in a fenced block
    despite instructions) plus the same structural checks a hand-edited
    overlay would face."""
    import re

    fenced = re.search(r"```(?:ya?ml)?\s*\n(.*?)```", text, re.S)
    raw = fenced.group(1) if fenced else text
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        return {}, [f"unparseable YAML: {exc}"]
    if not isinstance(data, dict):
        return {}, ["response is not a YAML mapping"]
    problems = validate_overlay_entries(data, known_scenarios)
    # Never trust a model-reported review_status other than draft, even if
    # it slipped one past the structural check above (e.g. copied a status
    # from an example) -- promotion is a human action, not a model output.
    for entry in data.values():
        if isinstance(entry, dict):
            entry["review_status"] = "draft"
    return data, problems


def draft_overlay_for_member(conn, member_name: str, caller, module_doc: str | None = None,
                              max_attempts: int = 2, redact=None) -> dict:
    from .testplan import test_case_brief

    # Same refusal test_case_brief/brief.module_brief make for the identical
    # case -- a bare name ambiguous across libraries must not have its
    # scenario ids pooled into one member's known-scenario set.
    from .db import resolve_member_by_name

    _matches, ambiguous_libs = resolve_member_by_name(conn, member_name, columns="1")
    if ambiguous_libs:
        return {"member": member_name, "entries": {}, "problems": [
            "member name is ambiguous across libraries -- re-run with a library-qualified name"
        ]}

    known = {
        r["scenario_name"] for r in conn.execute(
            "SELECT tc.scenario_name FROM test_case tc JOIN member m ON m.id = tc.member_id "
            "WHERE UPPER(m.name)=UPPER(?)", (member_name,),
        ).fetchall()
    }
    if not known:
        return {"member": member_name, "entries": {}, "problems": [
            "no test_case rows for this member -- run `mfdoc test-plan` first"
        ]}

    brief = test_case_brief(conn, member_name, redact=redact)
    problems: list[str] = []
    for attempt in range(1, max_attempts + 1):
        prompt = build_overlay_draft_prompt(member_name, brief, module_doc)
        if attempt > 1:
            prompt += "\n\n---\n\nPrevious attempt's problems:\n" + "\n".join(f"- {p}" for p in problems)
        response = caller(prompt)
        entries, problems = parse_overlay_response(response.text, known)
        if not problems:
            return {"member": member_name, "entries": entries, "problems": []}
    return {"member": member_name, "entries": {}, "problems": problems}


def run_overlay_draft(conn, members: list[str], caller, out_path,
                       module_docs: dict[str, str] | None = None, redact=None) -> dict:
    """Draft entries for `members` and merge them into `out_path`.

    A merge, not an overwrite: an existing entry whose review_status has
    already moved past `draft` (a human decision) is left untouched even if
    this member is re-drafted -- only draft-or-absent entries for a given
    scenario are replaced by a fresh draft.
    """
    module_docs = module_docs or {}
    overlay = load_overlay(out_path)
    summary = {"members": 0, "drafted": 0, "skipped_promoted": 0, "problems": []}
    for name in members:
        result = draft_overlay_for_member(conn, name, caller, module_docs.get(name), redact=redact)
        summary["members"] += 1
        if result["problems"]:
            summary["problems"].extend(f"{name}: {p}" for p in result["problems"])
            continue
        for scenario, entry in result["entries"].items():
            existing = overlay.get(scenario)
            if isinstance(existing, dict) and existing.get("review_status") in PROMOTED_REVIEW_STATUSES:
                summary["skipped_promoted"] += 1
                continue
            overlay[scenario] = entry
            summary["drafted"] += 1
    save_overlay(out_path, overlay)
    return summary

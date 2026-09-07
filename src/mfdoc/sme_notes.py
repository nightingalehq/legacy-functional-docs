"""Parser for the optional SME-authored notes file (`sme-notes.md`).

An SME can freely edit a single semi-structured markdown file, sitting next
to `project.yml`, to add business context, known gotchas, or corrections
they want weighed during doc generation -- without touching code or the
fact store. Path is configured via `options.sme_notes` in project.yml, the
same convention as `docs_root`/`index_db`.

Schema:

- Content before the first `##` heading (or under an explicit `## General`
  heading) applies to every member/entity generated for the project.
- Each subsequent `## <member-or-entity-name>` heading scopes its content
  to just that member/entity, matched case-insensitively against
  `member_name`/entity names used elsewhere in the tool (e.g. `LMCORE`,
  `TTPL021P`).
- Body text under each heading is freeform prose/bullets -- no further
  structure required. This is intentionally *semi*-structured (heading =
  scope, body = free text), not a rigid record format, so SMEs don't need
  tooling to edit it.
- A missing file is a no-op (nothing to inject), not an error -- this stays
  fully optional.

This module only parses and looks up notes; it deliberately does not call
into brief.py/batch.py/testplan.py or any narrative code path -- wiring the
parsed notes into brief generation is separate follow-on work.
"""

from __future__ import annotations

import re
from pathlib import Path

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

# Sections keyed by lowercased heading text; the general section (content
# before the first heading, or an explicit `## General` heading) is keyed by
# None so it can never collide with a real member/entity name.
Notes = dict[str | None, str]


def parse(path: str | Path) -> Notes:
    """Parse an sme-notes.md file into `{None: general_text, "lmcore": ...}`.

    Returns `{}` if the file is missing or empty -- absence is a no-op, not
    an error, since the whole file is optional.
    """
    p = Path(path)
    if not p.is_file():
        return {}
    text = p.read_text(encoding="utf-8")
    return _parse_text(text)


def _parse_text(text: str) -> Notes:
    headings = list(_HEADING_RE.finditer(text))
    if not headings:
        # No `##` headings at all -- the whole file is general text.
        body = text.strip()
        return {None: body} if body else {}

    notes: Notes = {}

    # Content before the first heading is general, unless empty.
    preamble = text[: headings[0].start()].strip()
    if preamble:
        notes[None] = preamble

    for i, match in enumerate(headings):
        heading_text = match.group(1).strip()
        start = match.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        body = text[start:end].strip()
        if not body:
            continue
        key = heading_text.lower()
        # An explicit `## General` heading feeds the same general bucket as
        # the preamble, combined in document order if both are present.
        if key == "general":
            notes[None] = f"{notes[None]}\n\n{body}" if notes.get(None) else body
        else:
            notes[key] = body

    return notes


def notes_for(notes: Notes, member_name: str | None) -> str | None:
    """Combine the general section with `member_name`'s section, general
    first then specific, in document order. Returns None if there is
    nothing to say for this member (no general text and no match)."""
    parts = []
    general = notes.get(None)
    if general:
        parts.append(general)
    if member_name is not None:
        specific = notes.get(member_name.lower())
        if specific:
            parts.append(specific)
    if not parts:
        return None
    return "\n\n".join(parts)


def load(cfg: dict, base: str | Path) -> Notes:
    """Convenience wrapper: read `options.sme_notes` from a loaded project
    config and parse the file it points at, relative to `base` (normally
    the project.yml's parent directory). Returns `{}` if the key is unset
    or the file doesn't exist."""
    configured = (cfg.get("options") or {}).get("sme_notes")
    if not configured:
        return {}
    return parse(Path(base) / configured)

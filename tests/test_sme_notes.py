"""Tests for sme_notes.py -- parsing the optional SME-authored notes file.

See src/mfdoc/sme_notes.py for the schema: content before the first `##`
heading (or under an explicit `## General` heading) applies to every
member/entity; each subsequent `## <name>` heading scopes its body to just
that member/entity, matched case-insensitively.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mfdoc import cli, sme_notes  # noqa: E402


def test_missing_file_is_noop(tmp_path):
    missing = tmp_path / "sme-notes.md"
    parsed = sme_notes.parse(missing)
    assert parsed == {}
    assert sme_notes.notes_for(parsed, "LMCORE") is None


def test_empty_file(tmp_path):
    path = tmp_path / "sme-notes.md"
    path.write_text("", encoding="utf-8")
    parsed = sme_notes.parse(path)
    assert parsed == {}
    assert sme_notes.notes_for(parsed, "LMCORE") is None


def test_general_only(tmp_path):
    path = tmp_path / "sme-notes.md"
    path.write_text("This applies to everything.\n", encoding="utf-8")
    parsed = sme_notes.parse(path)
    assert parsed == {None: "This applies to everything."}
    assert sme_notes.notes_for(parsed, "LMCORE") == "This applies to everything."


def test_no_headings_treated_as_general(tmp_path):
    """A file with no `##` headings at all is entirely general text."""
    path = tmp_path / "sme-notes.md"
    path.write_text("Line one.\nLine two.\n", encoding="utf-8")
    parsed = sme_notes.parse(path)
    assert parsed == {None: "Line one.\nLine two."}
    assert sme_notes.notes_for(parsed, "TTPL021P") == "Line one.\nLine two."


def test_member_only(tmp_path):
    path = tmp_path / "sme-notes.md"
    path.write_text(
        "## LMCORE\n\nKnown gotcha: the balance calc rounds oddly.\n",
        encoding="utf-8",
    )
    parsed = sme_notes.parse(path)
    assert parsed == {"lmcore": "Known gotcha: the balance calc rounds oddly."}
    assert sme_notes.notes_for(parsed, "LMCORE") == "Known gotcha: the balance calc rounds oddly."
    assert sme_notes.notes_for(parsed, "TTPL021P") is None


def test_general_and_member(tmp_path):
    path = tmp_path / "sme-notes.md"
    path.write_text(
        "General context here.\n"
        "\n"
        "## LMCORE\n"
        "\n"
        "LMCORE-specific note.\n"
        "\n"
        "## TTPL021P\n"
        "\n"
        "TTPL021P-specific note.\n",
        encoding="utf-8",
    )
    parsed = sme_notes.parse(path)
    assert parsed[None] == "General context here."
    assert parsed["lmcore"] == "LMCORE-specific note."
    assert parsed["ttpl021p"] == "TTPL021P-specific note."

    combined = sme_notes.notes_for(parsed, "LMCORE")
    assert combined == "General context here.\n\nLMCORE-specific note."
    # General text must come first, then the member-specific text.
    assert combined.index("General context here.") < combined.index("LMCORE-specific note.")

    assert sme_notes.notes_for(parsed, "TTPL021P") == "General context here.\n\nTTPL021P-specific note."
    assert sme_notes.notes_for(parsed, "TTPL021S") == "General context here."


def test_explicit_general_heading(tmp_path):
    path = tmp_path / "sme-notes.md"
    path.write_text(
        "## General\n"
        "\n"
        "Explicit general section.\n"
        "\n"
        "## LMCORE\n"
        "\n"
        "LMCORE note.\n",
        encoding="utf-8",
    )
    parsed = sme_notes.parse(path)
    assert parsed[None] == "Explicit general section."
    assert parsed["lmcore"] == "LMCORE note."
    assert sme_notes.notes_for(parsed, "LMCORE") == "Explicit general section.\n\nLMCORE note."


def test_unknown_heading_names_are_parsed_but_never_matched(tmp_path):
    path = tmp_path / "sme-notes.md"
    path.write_text(
        "## SOME-UNKNOWN-MEMBER\n"
        "\n"
        "Notes about something that isn't looked up.\n",
        encoding="utf-8",
    )
    parsed = sme_notes.parse(path)
    assert parsed == {"some-unknown-member": "Notes about something that isn't looked up."}
    # Never matched by lookup for any real member -- not an error, just no hit.
    assert sme_notes.notes_for(parsed, "LMCORE") is None
    assert sme_notes.notes_for(parsed, "SOME-UNKNOWN-MEMBER") == "Notes about something that isn't looked up."


def test_case_insensitivity(tmp_path):
    path = tmp_path / "sme-notes.md"
    path.write_text(
        "## lmcore\n"
        "\n"
        "Lowercase heading in the file.\n",
        encoding="utf-8",
    )
    parsed = sme_notes.parse(path)
    assert parsed == {"lmcore": "Lowercase heading in the file."}
    # Lookup is case-insensitive regardless of how the caller casings the name.
    assert sme_notes.notes_for(parsed, "LMCORE") == "Lowercase heading in the file."
    assert sme_notes.notes_for(parsed, "LmCoRe") == "Lowercase heading in the file."
    assert sme_notes.notes_for(parsed, "lmcore") == "Lowercase heading in the file."


def test_load_returns_notes_for_missing_config_key(tmp_path):
    """The `load()` convenience wraps parse() for a config dict, defaulting
    to a no-op when options.sme_notes isn't set."""
    base = tmp_path
    cfg = {"options": {}}
    parsed = sme_notes.load(cfg, base)
    assert parsed == {}


def test_load_reads_configured_path(tmp_path):
    base = tmp_path
    notes_path = base / "sme-notes.md"
    notes_path.write_text("## LMCORE\n\nA note.\n", encoding="utf-8")
    cfg = {"options": {"sme_notes": "sme-notes.md"}}
    parsed = sme_notes.load(cfg, base)
    assert parsed == {"lmcore": "A note."}


def test_options_sme_notes_readable_via_cli_load_config(tmp_path):
    """End-to-end wiring check: a real project.yml's `options.sme_notes`
    key survives cli.load_config() unmodified, and sme_notes.load() can
    consume that loaded config directly -- confirming the new key is
    readable through the same config-loading path every other options.*
    key goes through, without requiring any brief/batch/CLI integration
    (that part is out of scope here, see #94)."""
    config_path = tmp_path / "project.yml"
    config_path.write_text(
        "project: Test Project\n"
        "options:\n"
        "  sme_notes: sme-notes.md\n",
        encoding="utf-8",
    )
    (tmp_path / "sme-notes.md").write_text("## LMCORE\n\nA note.\n", encoding="utf-8")

    cfg = cli.load_config(config_path)
    assert cfg["options"]["sme_notes"] == "sme-notes.md"

    parsed = sme_notes.load(cfg, config_path.parent)
    assert parsed == {"lmcore": "A note."}

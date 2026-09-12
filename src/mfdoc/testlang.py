"""Shared language->extension mapping for generated-test sidecar files.

A tiny module on purpose: both `testbatch.py` (writes the sidecar) and
`validate.py` (cross-checks it) need this mapping, and neither should
import the other for it. No built-in guess at an unlisted language's
extension -- an unrecognised `language` means "keep the code embedded in
the .md, don't split it," never a fabricated extension.
"""

from __future__ import annotations

from pathlib import Path

LANGUAGE_EXTENSIONS = {
    "python": "py",
    "java": "java",
    "natural": "nsp",
    "mantis": "mantis",
}


def sidecar_path_for(doc_path: Path, language: object | None) -> Path | None:
    """The sidecar source file `doc_path` (a generated-test .md) would pair
    with, or None if `language` isn't in `LANGUAGE_EXTENSIONS` -- never
    guess an extension for a language this module doesn't know.

    `language` is typed loosely (not just `str | None`) on purpose: both
    callers pass a raw `fm.get("language")` from a document's own front
    matter, which a malformed/hand-edited document can make anything a
    YAML scalar allows (a list, a number, a mapping) -- `dict.get` on an
    unhashable value (e.g. `language: [python]`) would otherwise raise
    `TypeError` here instead of this function's own documented "unknown
    language" `None` (Copilot review on issue #195's fix: this used to
    crash `mfdoc test-validate` on exactly that malformed shape)."""
    if not isinstance(language, str):
        return None
    ext = LANGUAGE_EXTENSIONS.get(language)
    if not ext:
        return None
    return doc_path.with_suffix(f".{ext}")

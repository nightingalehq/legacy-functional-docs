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


def sidecar_path_for(doc_path: Path, language: str | None) -> Path | None:
    """The sidecar source file `doc_path` (a generated-test .md) would pair
    with, or None if `language` isn't in `LANGUAGE_EXTENSIONS` -- never
    guess an extension for a language this module doesn't know."""
    if not language:
        return None
    ext = LANGUAGE_EXTENSIONS.get(language)
    if not ext:
        return None
    return doc_path.with_suffix(f".{ext}")

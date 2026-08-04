"""Redaction of literal values before they reach a prompt or generated document.

Mainframe source routinely contains customer names, account numbers and
occasionally credentials in literals, comments and PARM strings. The fact
store keeps everything (it is the audit trail), but the *brief* is what
actually gets sent to a model, so redaction is applied there -- not only at
document-render time, when it would already be too late.

Patterns are supplied by the engagement via `options.redact.patterns` in
project.yml: exact values or regexes for whatever has already been found in
this codebase. There are no built-in default patterns, because guessing at
what counts as sensitive in an unfamiliar client's source is exactly the
kind of invention this project's own writing rules forbid elsewhere.
"""

from __future__ import annotations

import re

PLACEHOLDER = "[REDACTED]"


class Redactor:
    def __init__(self, patterns: list[str] | None = None, enabled: bool = False):
        self.enabled = bool(enabled)
        self._compiled = [re.compile(p) for p in (patterns or [])]

    def __call__(self, text: str | None) -> str | None:
        if not self.enabled or text is None or not self._compiled:
            return text
        for pattern in self._compiled:
            text = pattern.sub(PLACEHOLDER, text)
        return text

    @classmethod
    def from_options(cls, options: dict | None) -> "Redactor":
        redact_cfg = (options or {}).get("redact") or {}
        return cls(patterns=redact_cfg.get("patterns"), enabled=redact_cfg.get("enabled", False))


NULL_REDACTOR = Redactor(enabled=False)

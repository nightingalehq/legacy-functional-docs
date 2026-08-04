"""Stage 0 — ingest and normalise.

Mainframe exports arrive in shapes that break naive tooling: EBCDIC code pages,
sequence numbers in columns 73-80, several logical members concatenated into one
unload file, trailing pad blanks, and CRLF/NL mixtures.

This stage produces a clean UTF-8 copy plus a member manifest, and records the
line numbering that every downstream citation depends on. Get the line numbers
wrong here and every citation in the final documentation is wrong, so the
original ordinal within each member is preserved verbatim and the stripped
sequence number is kept alongside it for cross-checking against a mainframe
listing.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

EBCDIC_CODEPAGES = ("cp037", "cp500", "cp1047", "cp285", "cp273")

# A byte that is common in EBCDIC text (0x40 = space) but rare as a leading byte
# in UTF-8 prose. Detection is heuristic; the config can force an encoding.
EBCDIC_SPACE = 0x40


def sniff_encoding(raw: bytes, forced: str | None = None) -> str:
    if forced:
        return forced
    try:
        text = raw.decode("utf-8")
        # If it decodes as UTF-8 and looks like text, take it.
        printable = sum(ch.isprintable() or ch in "\n\r\t" for ch in text)
        if printable / max(len(text), 1) > 0.95:
            return "utf-8"
    except UnicodeDecodeError:
        pass
    if raw and raw.count(bytes([EBCDIC_SPACE])) / len(raw) > 0.10:
        for cp in EBCDIC_CODEPAGES:
            try:
                text = raw.decode(cp)
            except Exception:
                continue
            printable = sum(ch.isprintable() or ch in "\n\r\t" for ch in text)
            if printable / max(len(text), 1) > 0.95:
                return cp
    return "latin-1"


def detect_seq_columns(lines: list[str], cols: tuple[int, int] = (72, 80)) -> tuple[int, int] | None:
    """Return (start, end) 0-based slice if a consistent sequence-number field is present.

    Only strips when the field is numeric (or blank) on a strong majority of
    non-blank lines *and* at least some lines are long enough to have it. This
    avoids mangling free-format source that happens to have digits at the end.
    """
    start, end = cols
    candidates = [ln for ln in lines if len(ln.rstrip()) > start]
    if len(candidates) < 5:
        return None
    hits = 0
    for ln in candidates:
        chunk = ln[start:end]
        if chunk.strip() == "" or chunk.strip().isdigit():
            hits += 1
    if hits / len(candidates) >= 0.9:
        return (start, end)
    return None


DIALECT_SIGNATURES: list[tuple[str, re.Pattern]] = [
    ("jcl", re.compile(r"^//\S*\s+(JOB|EXEC|DD)\b", re.M)),
    ("cics_csd", re.compile(r"^\s*DEFINE\s+(PROGRAM|TRANSACTION|FILE|MAPSET)\s*\(", re.I | re.M)),
    ("adabas_fdt", re.compile(r"FIELD\s+DEFINITION\s+TABLE|^\s*\d+\s+[A-Z0-9]{2}\s+\d+\s+[ABPUFGIL]\s", re.I | re.M)),
    ("ddm", re.compile(r"^\s*(DDM\s+NAME|DEFAULT\s+SEQUENCE|DB\s*:\s*\d+\s+FILE\s*:\s*\d+)", re.I | re.M)),
    ("supra_dir", re.compile(r"\b(LINKPATH|DATA-?SET\s+NAME|PRIMARY\s+DATA\s*SET|RELATED\s+DATA\s*SET|ELEMENT\s+NAME)\b", re.I)),
    ("sql_ddl", re.compile(r"^\s*CREATE\s+(TABLE|UNIQUE\s+INDEX|INDEX|VIEW|TABLESPACE)\b", re.I | re.M)),
    ("natural", re.compile(r"^\s*(DEFINE\s+DATA\b|END-DEFINE\b|CALLNAT\b|FETCH\s+RETURN\b)", re.I | re.M)),
    ("mantis", re.compile(r"^\s*(ENTRY\b|CONVERSE\b|OBTAIN\b|EXIT\s*$|PROGRAM\s+\"|EXTERNAL\b)", re.I | re.M)),
    ("cobol_copybook", re.compile(r"^\s*\d\d\s+[A-Z0-9\-]+\s+(PIC|PICTURE)\s", re.I | re.M)),
]


def detect_dialect(text: str, hint: str | None = None) -> str:
    if hint:
        return hint
    scores: dict[str, int] = {}
    for name, pat in DIALECT_SIGNATURES:
        n = len(pat.findall(text))
        if n:
            scores[name] = n
    if not scores:
        return "unknown"
    # Natural and Mantis can both look 4GL-ish; prefer the stronger signal but
    # keep the runner-up so the caller can raise an ambiguity gap.
    return max(scores, key=scores.get)


def dialect_confidence(text: str) -> list[tuple[str, int]]:
    out = []
    for name, pat in DIALECT_SIGNATURES:
        n = len(pat.findall(text))
        if n:
            out.append((name, n))
    return sorted(out, key=lambda t: -t[1])


@dataclass
class MemberChunk:
    name: str
    dialect: str
    object_type: str | None = None
    library: str | None = None
    first_line: int = 1
    implicit: bool = False  # created because no banner matched, not by a banner
    lines: list[tuple[int, str | None, str]] = field(default_factory=list)  # (line_no, seq, text)


# Default splitters. Export formats vary by site and by utility version, so these
# are deliberately conservative and overridable from project config.
DEFAULT_SPLITTERS = {
    # Software AG Object Handler / NATUNLD style banners seen in text unloads
    "natural": [
        r"^\*+\s*>{2,}\s*(?P<type>\w+)?\s*(?P<name>[A-Z0-9#\-_&$]{1,32})\s*<{2,}",
        r"^\s*\*\s*MEMBER\s*[:=]\s*(?P<name>[A-Z0-9#\-_&$]{1,32})",
        r"^--+\s*(?P<name>[A-Z0-9#\-_&$]{1,32})\s*\((?P<type>[A-Z]{1,12})\)\s*--+",
    ],
    "mantis": [
        r"^\s*PROGRAM\s+\"(?P<name>[^\"]{1,40})\"",
        r"^\s*\*\s*MANTIS\s+(?:PROGRAM|OBJECT)\s*[:=]\s*(?P<name>\S{1,40})",
    ],
    "jcl": [r"^//(?P<name>[A-Z0-9#@$]{1,8})\s+JOB\b"],
}

# Dialects whose member separators are generated by an unload utility rather than
# being part of the stored member. Their banner lines are dropped so that line
# numbers match what a mainframe LIST of the member shows.
UTILITY_BANNER_DIALECTS = {"natural"}

# Natural object type inferred from the conventional file extension used by
# SYSOBJH / Natural Studio exports. Content-based inference in
# `infer_natural_object_type` refines this.
NATURAL_EXTENSION_MAP = {
    ".nsp": "program", ".nsn": "subprogram", ".nss": "subroutine",
    ".nsc": "copycode", ".nsh": "helproutine", ".nsm": "map",
    ".nsl": "lda", ".nsg": "gda", ".nsa": "pda", ".nst": "text",
    ".nsd": "ddm", ".nsx": "class",
}


def infer_natural_object_type(lines: list[str], ext_hint: str | None) -> str | None:
    """Infer Natural object type from content, falling back to the extension.

    Content beats extension because exports get renamed. A DEFINE DATA PARAMETER
    block means the object receives arguments, which in practice means it is a
    subprogram invoked by CALLNAT rather than a program started by a job or menu.
    """
    body = "\n".join(lines[:200])
    if re.search(r"^\s*DEFINE\s+SUBROUTINE\b", body, re.I | re.M) and not re.search(
        r"^\s*(DEFINE\s+DATA|END\s*$)", body, re.I | re.M
    ):
        return "subroutine"
    if re.search(r"^\s*DEFINE\s+DATA\s+PARAMETER\b", body, re.I | re.M) or re.search(
        r"^\s*PARAMETER\s*$", body, re.I | re.M
    ):
        return "subprogram"
    if ext_hint in NATURAL_EXTENSION_MAP:
        return NATURAL_EXTENSION_MAP[ext_hint]
    if re.search(r"^\s*DEFINE\s+DATA\b", body, re.I | re.M):
        return "program"
    return None


NATURAL_TYPE_MAP = {
    "P": "program", "PROGRAM": "program",
    "N": "subprogram", "SUBPROGRAM": "subprogram",
    "S": "subroutine", "SUBROUTINE": "subroutine",
    "C": "copycode", "COPYCODE": "copycode",
    "H": "helproutine", "HELPROUTINE": "helproutine",
    "M": "map", "MAP": "map",
    "L": "lda", "LDA": "lda", "LOCAL": "lda",
    "G": "gda", "GDA": "gda", "GLOBAL": "gda",
    "A": "pda", "PDA": "pda", "PARAMETER": "pda",
    "T": "text", "TEXT": "text",
    "V": "ddm", "DDM": "ddm", "VIEW": "ddm",
}


# Extensions that are packaging or dialect markers rather than part of the member
# name. Files transferred off a mainframe commonly arrive with a chain of them —
# `MMP0100.NSP.TXT` after an FTP through a text-mode gateway — and a member called
# `MMP0100.NSP` will not match a `CALLNAT 'MMP0100'`, so every call edge to it goes
# unresolved and the module looks like dead code.
STRIPPABLE_EXTENSIONS = {
    ".txt", ".text", ".dat", ".data", ".lst", ".list", ".listing", ".prn", ".out",
    ".src", ".source", ".sav", ".bak", ".export", ".unload",
    ".nsp", ".nsn", ".nss", ".nsc", ".nsh", ".nsm", ".nsl", ".nsg", ".nsa",
    ".nst", ".nsd", ".nsx", ".nat", ".natural",
    ".mantis", ".mts", ".ddm", ".fdt", ".rep", ".dir", ".jcl", ".job", ".proc",
    ".csd", ".cbl", ".cpy", ".cob", ".sql", ".ddl",
}


def derive_member_name(path: Path) -> tuple[str, str | None]:
    """Return (member_name, dialect_extension_hint) for a source file.

    The hint is the innermost recognised dialect extension, which is what should
    inform object-type inference — not the outermost, which is usually just how the
    file reached the filesystem.
    """
    name = path.name
    hint: str | None = None
    while True:
        stem, dot, ext = name.rpartition(".")
        if not dot or f".{ext.lower()}" not in STRIPPABLE_EXTENSIONS:
            break
        candidate = f".{ext.lower()}"
        # Remember dialect-bearing extensions; ignore transport ones.
        if candidate not in {".txt", ".text", ".dat", ".data", ".lst", ".list",
                             ".listing", ".prn", ".out", ".src", ".source",
                             ".sav", ".bak", ".export", ".unload"}:
            hint = candidate
        name = stem
    return (name or path.stem).upper(), hint


def read_source(path: Path, forced_encoding: str | None = None) -> tuple[list[str], str, str]:
    raw = path.read_bytes()
    enc = sniff_encoding(raw, forced_encoding)
    text = raw.decode(enc, errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.split("\n"), enc, hashlib.sha256(raw).hexdigest()


def split_members(
    lines: list[str],
    dialect: str,
    default_name: str,
    seq_cols: tuple[int, int] | None,
    splitters: dict | None = None,
    library: str | None = None,
) -> list[MemberChunk]:
    """Split a file into logical members, preserving per-member line numbering."""
    splitters = splitters or DEFAULT_SPLITTERS
    pats = [re.compile(p, re.I) for p in splitters.get(dialect, [])]

    def strip_seq(raw: str) -> tuple[str | None, str]:
        if seq_cols and len(raw) > seq_cols[0]:
            seq = raw[seq_cols[0]:seq_cols[1]].strip() or None
            return seq, raw[: seq_cols[0]].rstrip()
        return None, raw.rstrip()

    chunks: list[MemberChunk] = []
    current: MemberChunk | None = None
    counter = 0

    for idx, raw in enumerate(lines, start=1):
        seq, body = strip_seq(raw)
        matched = None
        for pat in pats:
            m = pat.match(body)
            if m:
                matched = m
                break
        if matched:
            name = (matched.groupdict().get("name") or f"{default_name}_{len(chunks) + 1}").strip().upper()
            otype = matched.groupdict().get("type")
            current = MemberChunk(
                name=name,
                dialect=dialect,
                object_type=NATURAL_TYPE_MAP.get((otype or "").upper()) if dialect == "natural" else (otype or None),
                library=library,
                first_line=idx,
            )
            chunks.append(current)
            counter = 0
            # The banner line is usually real source (a JCL JOB card, a Mantis
            # PROGRAM statement) and must be counted, or every citation in that
            # member is off by one against the real listing. Only unload-utility
            # banners are discarded, since those bytes do not exist in the
            # library member the SME will open.
            if dialect not in UTILITY_BANNER_DIALECTS:
                counter += 1
                current.lines.append((counter, seq, body))
            continue
        if current is None:
            current = MemberChunk(name=default_name.upper(), dialect=dialect, library=library,
                                  first_line=idx, implicit=True)
            chunks.append(current)
            counter = 0
        counter += 1
        current.lines.append((counter, seq, body))

    if not chunks:
        chunks = [MemberChunk(name=default_name.upper(), dialect=dialect, library=library,
                              implicit=True)]
    return _merge_preambles(chunks)


_PREAMBLE_CODE = re.compile(r"^\s*[A-Z0-9]", re.I)


def _merge_preambles(chunks: list[MemberChunk]) -> list[MemberChunk]:
    """Fold a comment-only implicit leading chunk into the member that follows it.

    Header comment blocks sitting above a member banner are frequently the only
    surviving human description of what a module does, so discarding them as a
    phantom member loses the single most useful prose in the codebase. They are
    merged in and the member's line numbering restarts from the first preamble
    line, which is also what a mainframe LIST of that member would show.
    """
    out: list[MemberChunk] = []
    i = 0
    while i < len(chunks):
        ch = chunks[i]
        is_preamble = (
            ch.implicit
            and i + 1 < len(chunks)
            and not any(_PREAMBLE_CODE.match(t) for _, _, t in ch.lines)
        )
        if is_preamble:
            nxt = chunks[i + 1]
            merged: list[tuple[int, str | None, str]] = []
            for n, (_, seq, text) in enumerate(ch.lines + nxt.lines, start=1):
                merged.append((n, seq, text))
            nxt.lines = merged
            nxt.first_line = ch.first_line
            i += 1
            continue
        out.append(ch)
        i += 1
    return out

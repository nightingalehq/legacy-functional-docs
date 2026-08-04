"""Stage 4 — validation.

The traceability promise is only worth something if it is checked by a machine.
This module re-reads the generated markdown, extracts every `[[MEMBER:LINE]]`
citation, and confirms the member exists and the line is within range. It also
enforces the front-matter contract so a document cannot silently omit its
confidence and review fields.

A citation that points at the wrong line is more damaging than no citation, because
a reviewer who spot-checks two citations and finds them correct will trust the
rest. Hence: check all of them, every build.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .db import insert

CITATION = re.compile(r"\[\[(?P<member>[A-Z0-9#@$&\-_.]+)(?::(?P<from>\d+)(?:-(?P<to>\d+))?)?\]\]", re.I)

REQUIRED_FRONTMATTER = [
    "title", "doc_type", "system", "generated_by", "generated_at",
    "review_status", "confidence_summary", "sources",
]
VALID_CONFIDENCE = {"verified", "inferred", "unresolved"}
VALID_REVIEW = {"draft", "in_review", "sme_approved", "signed_off"}

# Sentences that assert behaviour must carry a citation. These openers are the
# common shapes of an uncited functional claim.
ASSERTIVE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:The system|The program|The module|This module|The job|The transaction|"
    r"Users?|The user|On |When |If |The process|It )",
    re.I)

HEDGE = re.compile(r"\b(?:unresolved|not determined|could not be determined|needs confirmation|"
                   r"SME|to be confirmed|unknown)\b", re.I)


# Sentence boundary: a terminator followed by whitespace and something that starts a
# new sentence. Citations legitimately sit before the full stop, so `]].` must end a
# sentence rather than being treated as an abbreviation.
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"(*`\[])")

SKIP_BLOCK = re.compile(r"^\s*(\||>|#{1,6}\s|```|---\s*$)")


def _logical_units(body: str) -> list[str]:
    """Unwrap markdown into logical units: list items and paragraph sentences.

    Checking line by line produces false failures on any wrapped sentence whose
    citation happens to land on the following line, which trains the author to
    ignore the validator — the opposite of what it is for.
    """
    units: list[str] = []
    in_fence = False
    for para in re.split(r"\n\s*\n", body):
        buf: list[str] = []

        def flush():
            if buf:
                joined = " ".join(s.strip() for s in buf).strip()
                if joined:
                    units.extend(p for p in SENTENCE_SPLIT.split(joined) if p.strip())
                buf.clear()

        for line in para.split("\n"):
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence or SKIP_BLOCK.match(line):
                continue
            # A new list item or numbered item starts a new logical unit.
            if re.match(r"^\s*(?:[-*+]\s+|\d+\.\s+)", line):
                flush()
            buf.append(re.sub(r"^\s*(?:[-*+]\s+|\d+\.\s+)", "", line))
        flush()
    return units


def _uncited_assertions(body: str) -> list[str]:
    return [
        u.strip()[:140]
        for u in _logical_units(body)
        if ASSERTIVE.match(u) and not CITATION.search(u) and not HEDGE.search(u)
    ]


def _split_frontmatter(text: str) -> tuple[dict | None, str, str | None]:
    if not text.startswith("---"):
        return None, text, "missing YAML front matter"
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text, "malformed YAML front matter"
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        return None, parts[2], f"unparseable YAML front matter: {exc}"
    return fm, parts[2], None


def validate_doc(conn, path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []
    fm, body, fm_err = _split_frontmatter(text)
    if fm_err:
        problems.append(fm_err)
    if fm is not None:
        for key in REQUIRED_FRONTMATTER:
            if key not in fm:
                problems.append(f"front matter missing required key: {key}")
        rs = fm.get("review_status")
        if rs and rs not in VALID_REVIEW:
            problems.append(f"review_status '{rs}' not one of {sorted(VALID_REVIEW)}")
        cs = fm.get("confidence_summary") or {}
        if isinstance(cs, dict):
            for k in cs:
                if k not in VALID_CONFIDENCE:
                    problems.append(f"confidence_summary key '{k}' not one of {sorted(VALID_CONFIDENCE)}")
        else:
            problems.append("confidence_summary should be a mapping of confidence level to count")

    conn.execute("DELETE FROM doc_claim WHERE doc_path=?", (str(path),))

    cites = list(CITATION.finditer(body))
    good = bad = 0
    for m in cites:
        member = m.group("member").upper()
        lf = int(m.group("from")) if m.group("from") else None
        lt = int(m.group("to")) if m.group("to") else lf
        rows = conn.execute(
            "SELECT id, name, library, (SELECT MAX(line_no) FROM source_line WHERE member_id=member.id) AS maxline "
            "FROM member WHERE UPPER(name)=?", (member,)
        ).fetchall()
        valid, note = 1, None
        if not rows:
            valid, note = 0, f"member '{member}' is not in the index"
        elif len(rows) > 1:
            # The citation format [[MEMBER:LINE]] carries no library, but
            # `member` allows the same name in different libraries. Picking
            # one arbitrarily can validate a citation against the wrong
            # member's line range, so flag it instead of guessing.
            libs = ", ".join(sorted({r["library"] or "?" for r in rows}))
            valid, note = 0, f"member name '{member}' is ambiguous across libraries ({libs}); citation needs a library qualifier"
        elif lf is not None:
            row = rows[0]
            maxline = row["maxline"] or 0
            if lf < 1 or lf > maxline or (lt and lt > maxline):
                valid, note = 0, f"line {lf}{'-' + str(lt) if lt and lt != lf else ''} outside 1..{maxline}"
        insert(conn, "doc_claim", doc_path=str(path), confidence="verified",
               citation=m.group(0), member_name=member, line_from=lf, line_to=lt,
               valid=valid, note=note)
        if valid:
            good += 1
        else:
            bad += 1
            problems.append(f"invalid citation {m.group(0)}: {note}")

    uncited = _uncited_assertions(body)
    if uncited:
        problems.append(f"{len(uncited)} assertive statement(s) carry no citation and no hedge")

    conn.commit()
    return {
        "path": str(path),
        "citations": len(cites),
        "valid_citations": good,
        "invalid_citations": bad,
        "uncited_assertions": uncited,
        "problems": problems,
        "ok": not problems,
    }


def validate_tree(conn, root: Path) -> dict:
    results = [validate_doc(conn, p) for p in sorted(root.rglob("*.md"))]
    return {
        "documents": len(results),
        "documents_ok": sum(1 for r in results if r["ok"]),
        "total_citations": sum(r["citations"] for r in results),
        "invalid_citations": sum(r["invalid_citations"] for r in results),
        "results": results,
    }

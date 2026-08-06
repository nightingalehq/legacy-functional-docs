"""Citation-accuracy sampling (issue #8, Phase 5.2).

`mfdoc validate` proves every `[[MEMBER:LINE]]` citation *resolves* to a real
member and a real line range. It does not prove the citation is *right* --
that the cited line(s) actually support the claim sitting next to it. A
citation pointing at the wrong line passes validation and is the failure
mode most likely to survive review and reach a business sign-off undetected.

This module samples N claims per generated document, pairs each with its
cited source line(s), and records a verdict -- a human pass first (to
calibrate what "supports the claim" means for this kind of prose), then an
optional LLM-judge pass whose agreement with the human labels is reported
before it's trusted standalone. Neither pass is exercised until a human (or
`mfdoc sample-citations --judge llm`) explicitly runs it; nothing here runs
during `mfdoc ingest`/`derive`/`coverage`.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from .validate import CITATION, _logical_units, _split_frontmatter


@dataclass
class ClaimSample:
    id: str
    doc_path: str
    claim: str
    citation: str
    member: str
    line_from: int | None
    line_to: int | None
    source_text: str


def _sample_id(doc_path: str, claim: str, citation: str) -> str:
    # Stable across repeated sampling runs against the same doc content, so
    # a human's earlier verdict still matches up after a re-sample (e.g. to
    # add the llm pass) rather than looking like a fresh, unlabelled claim.
    return hashlib.sha256(f"{doc_path}\x00{claim}\x00{citation}".encode()).hexdigest()[:16]


def claim_citation_pairs(body: str) -> list[tuple[str, list[str]]]:
    """Every logical unit (sentence/list item) carrying >=1 citation, paired
    with the citation token(s) it carries. Reuses validate.py's own
    sentence-splitting so a sampled claim is exactly what a reader would
    see as one assertion, not an arbitrary regex window around a citation.
    """
    pairs = []
    for unit in _logical_units(body):
        cites = [m.group(0) for m in CITATION.finditer(unit)]
        if cites:
            pairs.append((unit.strip(), cites))
    return pairs


def sample_claims(conn, doc_paths: list[Path], n_per_doc: int, seed: int) -> list[ClaimSample]:
    """Pick up to `n_per_doc` claims per document, each resolved against its
    cited source line(s) via the fact store."""
    rng = random.Random(seed)
    samples: list[ClaimSample] = []
    for path in sorted(doc_paths):
        text = path.read_text(encoding="utf-8")
        _fm, body, _err = _split_frontmatter(text)
        pairs = claim_citation_pairs(body)
        if not pairs:
            continue
        chosen = pairs if len(pairs) <= n_per_doc else rng.sample(pairs, n_per_doc)
        for claim, cites in chosen:
            # A claim naming several citations samples as one unit (that's
            # what a reader judges at once) but resolves each citation's own
            # source text separately, concatenated for the judge to read.
            source_chunks = []
            member = line_from = line_to = None
            for citation in cites:
                m = CITATION.match(citation)
                if not m:
                    continue
                member = m.group("member").upper()
                line_from = int(m.group("from")) if m.group("from") else None
                line_to = int(m.group("to")) if m.group("to") else line_from
                if line_from is None:
                    continue
                rows = conn.execute(
                    "SELECT sl.text FROM source_line sl JOIN member mm ON mm.id = sl.member_id "
                    "WHERE UPPER(mm.name) = ? AND sl.line_no BETWEEN ? AND ? ORDER BY sl.line_no",
                    (member, line_from, line_to),
                ).fetchall()
                if rows:
                    # citation already carries its own "[[...]]" delimiters
                    # (CITATION.finditer's m.group(0)) -- no extra bracket.
                    source_chunks.append(f"{citation}\n" + "\n".join(r["text"] for r in rows))
            if not source_chunks:
                continue
            samples.append(ClaimSample(
                id=_sample_id(str(path), claim, ",".join(cites)),
                doc_path=str(path), claim=claim, citation=",".join(cites),
                member=member, line_from=line_from, line_to=line_to,
                source_text="\n---\n".join(source_chunks),
            ))
    return samples


# ------------------------------------------------------------------- state

def load_state(state_path: Path) -> dict:
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {"samples": {}, "verdicts": {"human": {}, "llm": {}}}


def save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def merge_samples(state: dict, samples: list[ClaimSample]) -> dict:
    for s in samples:
        state["samples"].setdefault(s.id, {
            "doc_path": s.doc_path, "claim": s.claim, "citation": s.citation,
            "member": s.member, "line_from": s.line_from, "line_to": s.line_to,
            "source_text": s.source_text,
        })
    return state


# --------------------------------------------------------------- LLM judge

LLM_JUDGE_PROMPT = """\
You are checking whether a cited source line genuinely supports a claim in \
generated documentation. Answer only ACCURATE or INACCURATE on the first \
line, then a one-sentence reason on the second line.

CLAIM:
{claim}

CITED SOURCE ({citation}):
{source_text}
"""


@dataclass
class JudgeVerdict:
    accurate: bool
    reason: str


def parse_llm_verdict(text: str) -> JudgeVerdict:
    first_line = (text.strip().splitlines() or [""])[0].strip().upper()
    accurate = first_line.startswith("ACCURATE")
    reason_lines = text.strip().splitlines()[1:]
    return JudgeVerdict(accurate=accurate, reason=" ".join(reason_lines).strip()[:300])


def judge_with_llm(caller, sample: dict, redact=None) -> JudgeVerdict:
    """Send one sampled claim + its cited source to the model for a verdict.

    `redact` (a Redactor, see redact.py) runs here, on the claim and source
    text, before either reaches the prompt -- same discipline as
    brief.py/batch.py: redaction happens at the point content is about to
    be sent to a model, not left to the caller to remember.
    """
    claim, source_text = sample["claim"], sample["source_text"]
    if redact is not None:
        claim, source_text = redact(claim), redact(source_text)
    prompt = LLM_JUDGE_PROMPT.format(claim=claim, citation=sample["citation"], source_text=source_text)
    response = caller(prompt)
    return parse_llm_verdict(response.text)


# -------------------------------------------------------------- reporting

def accuracy_rate(state: dict, judge: str = "human") -> float | None:
    verdicts = state["verdicts"].get(judge) or {}
    if not verdicts:
        return None
    accurate = sum(1 for v in verdicts.values() if v["accurate"])
    return round(accurate / len(verdicts), 4)


def agreement_rate(state: dict) -> float | None:
    human = state["verdicts"].get("human") or {}
    llm = state["verdicts"].get("llm") or {}
    shared = set(human) & set(llm)
    if not shared:
        return None
    agree = sum(1 for sid in shared if human[sid]["accurate"] == llm[sid]["accurate"])
    return round(agree / len(shared), 4)

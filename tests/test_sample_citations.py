"""Guards on citation-accuracy sampling (issue 5.2/#8).

mfdoc validate proves a citation resolves; it doesn't prove the citation is
right. These tests exercise sample.py's pure claim/citation extraction and
verdict-arithmetic directly, then the full mfdoc sample-citations command
against an isolated project (never the shared session indexed_db -- this
command persists a metric via set_metric, and mutating the shared index
would leak into test_coverage_snapshot.py's exact-dict-equality check for
any test file that happens to run after this one).
"""

from __future__ import annotations

import yaml
from pathlib import Path
from types import SimpleNamespace

from mfdoc import cli, graph
from mfdoc.db import connect
from mfdoc import sample as sample_mod
from mfdoc.redact import Redactor

DOC_TEXT = """---
title: "TESTPGM"
doc_type: module
system: TEST
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-01-01"
review_status: draft
confidence_summary:
  verified: 1
sources: ["TESTPGM"]
---

# TESTPGM

The program moves the confirmed status code into `#STATUS` [[TESTPGM:5]].
"""

PROGRAM_TEXT = """DEFINE DATA LOCAL
1 #STATUS (A4)
END-DEFINE
*
MOVE 'CONF' TO #STATUS
END
"""


def _connect(args):
    cfg = cli.load_config(args.config)
    return connect(Path(args.config).parent / cfg["index_db"])


def _isolated_project(tmp_path):
    natural_dir = tmp_path / "natural"
    natural_dir.mkdir()
    (natural_dir / "TESTPGM.nsp").write_text(PROGRAM_TEXT, encoding="utf-8")
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "TESTPGM.md").write_text(DOC_TEXT, encoding="utf-8")
    cfg = {
        "project": "Sample citations test", "system": "TEST", "index_db": ".mfdoc/index.db",
        "sources": [{
            "path": str(natural_dir), "glob": ["*.nsp"], "dialect": "natural",
            "library": "TESTLIB", "system": "TEST", "sequence_columns": "none",
        }],
        "options": {"quality_gates": {}},
    }
    config_path = tmp_path / "project.yml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    args = SimpleNamespace(
        config=str(config_path), docs=str(docs_dir), judge="human", n_per_doc=5, seed=42,
        state=".mfdoc/citation-sample-state.json", model=None, caller="fake-echo",
        provider="anthropic", gcp_project=None, gcp_region=None,
    )
    assert cli.cmd_ingest(args) == 0
    conn = _connect(args)
    graph.run_all(conn)
    conn.commit()
    return args


# ------------------------------------------------------------------- units

def test_claim_citation_pairs_extracts_the_sentence_and_its_citation():
    pairs = sample_mod.claim_citation_pairs(DOC_TEXT.split("---", 2)[2])
    assert len(pairs) == 1
    claim, cites = pairs[0]
    assert cites == ["[[TESTPGM:5]]"]
    assert "confirmed status code" in claim


def test_sample_id_is_stable_across_calls():
    a = sample_mod._sample_id("doc.md", "claim text", "[[X:1]]")
    b = sample_mod._sample_id("doc.md", "claim text", "[[X:1]]")
    assert a == b
    assert a != sample_mod._sample_id("doc.md", "different claim", "[[X:1]]")


def test_parse_llm_verdict_reads_the_first_line_only():
    v = sample_mod.parse_llm_verdict("ACCURATE\nthe line matches the claim")
    assert v.accurate is True
    assert v.reason == "the line matches the claim"
    v = sample_mod.parse_llm_verdict("INACCURATE\nno such value in source")
    assert v.accurate is False


def test_accuracy_rate_is_none_with_no_verdicts():
    state = {"samples": {}, "verdicts": {"human": {}, "llm": {}}}
    assert sample_mod.accuracy_rate(state, "human") is None


def test_accuracy_and_agreement_rates():
    state = {
        "samples": {},
        "verdicts": {
            "human": {"a": {"accurate": True}, "b": {"accurate": False}, "c": {"accurate": True}},
            "llm": {"a": {"accurate": True}, "b": {"accurate": True}},
        },
    }
    assert sample_mod.accuracy_rate(state, "human") == round(2 / 3, 4)
    assert sample_mod.accuracy_rate(state, "llm") == 1.0
    # Agreement is over the shared ids (a, b) only: they agree on 'a', not 'b'.
    assert sample_mod.agreement_rate(state) == 0.5


# --------------------------------------------------------------- end-to-end

def test_sample_claims_resolves_against_the_fact_store(tmp_path):
    args = _isolated_project(tmp_path)
    conn = _connect(args)
    samples = sample_mod.sample_claims(conn, [Path(args.docs) / "TESTPGM.md"], n_per_doc=5, seed=1)
    assert len(samples) == 1
    s = samples[0]
    assert s.citation == "[[TESTPGM:5]]"
    assert "MOVE 'CONF' TO #STATUS" in s.source_text


def test_judge_llm_requires_human_verdicts_first(tmp_path, capsys):
    args = _isolated_project(tmp_path)
    args.judge = "llm"
    assert cli.cmd_sample_citations(args) == 1
    assert "run --judge human first" in capsys.readouterr().out


def test_human_judge_records_verdicts_and_persists_the_metric(tmp_path, monkeypatch, capsys):
    args = _isolated_project(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *_a: "y")
    assert cli.cmd_sample_citations(args) == 0
    out = capsys.readouterr().out
    assert "human-judged accuracy: 100.00%" in out

    conn = _connect(args)
    row = conn.execute(
        "SELECT value FROM metric WHERE scope='global' AND name='citation_accuracy_rate'"
    ).fetchone()
    assert row is not None and float(row["value"]) == 1.0
    cov = graph.coverage(conn)
    assert cov["citation_accuracy_rate"] == 1.0


def test_llm_judge_runs_after_human_and_reports_agreement(tmp_path, monkeypatch, capsys):
    args = _isolated_project(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *_a: "y")
    assert cli.cmd_sample_citations(args) == 0

    args.judge = "llm"
    assert cli.cmd_sample_citations(args) == 0
    out = capsys.readouterr().out
    # fake-echo returns the prompt itself, which doesn't start with
    # "ACCURATE" -- confirms the llm path actually ran and was parsed as a
    # real (if wrong, for this caller) verdict rather than silently no-op'ing.
    assert "llm-judged accuracy:   0.00%" in out
    assert "human/llm agreement:   0.00%" in out


def test_coverage_omits_citation_accuracy_rate_until_sampled(tmp_path):
    args = _isolated_project(tmp_path)
    conn = _connect(args)
    assert "citation_accuracy_rate" not in graph.coverage(conn)


def test_min_citation_accuracy_rate_gate_is_registered():
    keys = {opt_key for opt_key, *_ in cli.GATES}
    assert "min_citation_accuracy_rate" in keys


def test_judge_with_llm_redacts_before_the_prompt_is_built():
    """Same discipline as batch.py/brief.py: redaction happens at the point
    content is about to reach a model, not left for the caller to remember."""
    redact = Redactor(patterns=[r"CONF"], enabled=True)
    seen_prompts = []

    def spy_caller(prompt):
        seen_prompts.append(prompt)
        from mfdoc.batch import ModelResponse
        return ModelResponse(text="ACCURATE\nok", input_tokens=0, output_tokens=0)

    sample = {"claim": "status is CONF", "citation": "[[X:1]]", "source_text": "MOVE 'CONF' TO #STATUS"}
    sample_mod.judge_with_llm(spy_caller, sample, redact)
    assert "CONF" not in seen_prompts[0]

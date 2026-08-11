"""Guards on EBCDIC encoding detection and decoding (issue #9, Phase 6).

`sniff_encoding`/`EBCDIC_CODEPAGES` had no fixture exercising a real EBCDIC
byte stream at all before this -- only synthetic assumptions. The two
fixtures here are examples/inputs/natural/MMP0200.nsp re-encoded to
cp037 and cp500 with Python's own codecs; they are not part of the main
project.yml source set (they live in a subdirectory the natural source
spec's non-recursive `*.nsp` glob never reaches), so they don't touch
the coverage snapshot.
"""

from __future__ import annotations

from pathlib import Path

from mfdoc import normalise

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "examples" / "inputs" / "natural"
ENCODING_DIR = FIXTURE_DIR / "encoding"
ORIGINAL_TEXT = (FIXTURE_DIR / "MMP0200.nsp").read_text(encoding="utf-8")


def test_ebcdic_fixtures_exist_and_are_not_valid_utf8():
    # Sanity check on the fixtures themselves: they must actually be EBCDIC
    # bytes, not e.g. accidentally re-saved as UTF-8 by an editor.
    for cp in ("cp037", "cp500"):
        raw = (ENCODING_DIR / f"MMP0200.{cp}.nsp").read_bytes()
        assert raw, f"{cp} fixture is empty"
        assert raw.decode(cp) == ORIGINAL_TEXT, f"{cp} fixture doesn't decode back to the original"


def test_sniff_encoding_recognises_ebcdic_bytes_as_some_ebcdic_codepage():
    # See the module docstring / reference/natural-adabas.md's "Traps"
    # section: cp037 and cp500 differ only in a handful of special
    # characters, and this fixture's content (plain business-program text,
    # no accented or special chars beyond '#') doesn't happen to contain
    # any of them. sniff_encoding tries EBCDIC_CODEPAGES in order and
    # returns the first that decodes plausibly -- so for content shaped
    # like this, a genuinely-cp500 file is auto-detected as cp037, because
    # cp037 is tried first and also decodes it just fine. That is a real,
    # documented limitation of the heuristic (hence project.yml's
    # `encoding:` override), not a bug in the fixture -- what this
    # asserts is the thing that actually matters: EBCDIC bytes are
    # recognised as *an* EBCDIC codepage rather than falling through to
    # "latin-1" (which would silently corrupt every non-ASCII byte).
    for cp in ("cp037", "cp500"):
        raw = (ENCODING_DIR / f"MMP0200.{cp}.nsp").read_bytes()
        detected = normalise.sniff_encoding(raw)
        assert detected in normalise.EBCDIC_CODEPAGES, (
            f"{cp} fixture: expected an EBCDIC codepage, got {detected!r}"
        )


def test_forced_encoding_roundtrips_through_read_source(tmp_path):
    # The documented escape hatch for the cp037/cp500 ambiguity above:
    # project.yml's `encoding:` forces sniff_encoding's return value
    # outright. Prove that path decodes correctly for both codepages, not
    # only whichever one auto-detection happens to guess.
    for cp in ("cp037", "cp500"):
        src = tmp_path / f"MMP0200.{cp}.nsp"
        src.write_bytes((ENCODING_DIR / f"MMP0200.{cp}.nsp").read_bytes())
        lines, enc, _sha = normalise.read_source(src, forced_encoding=cp)
        assert enc == cp
        assert lines == ORIGINAL_TEXT.split("\n")


def _ingest_one(case_root: Path, forced_encoding: str | None):
    # Each case gets its own root (and therefore its own .mfdoc/index.db,
    # resolved relative to the config file) -- isolated rather than sharing
    # one database across cases, even though sharing would happen to work
    # here (upsert_member would just overwrite the same member in place).
    import yaml
    from types import SimpleNamespace

    from mfdoc import cli
    from mfdoc.db import connect

    natural_dir = case_root / "natural"
    cfg = {
        "project": "EBCDIC test", "system": "TEST", "index_db": ".mfdoc/index.db",
        "sources": [{
            "path": str(natural_dir), "glob": ["*.nsp"], "dialect": "natural",
            "library": "TESTLIB", "system": "TEST", "sequence_columns": "none",
            **({"encoding": forced_encoding} if forced_encoding else {}),
        }],
        "options": {"quality_gates": {}},
    }
    config_path = case_root / "project.yml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    args = SimpleNamespace(config=str(config_path))
    assert cli.cmd_ingest(args) == 0
    conn = connect(case_root / cli.load_config(str(config_path))["index_db"])
    member_id = conn.execute("SELECT id FROM member WHERE name='MMP0200'").fetchone()["id"]
    return [
        r["text"] for r in conn.execute(
            "SELECT text FROM source_line WHERE member_id=? ORDER BY line_no", (member_id,)
        ).fetchall()
    ]


def test_ingest_through_full_pipeline_matches_the_utf8_original(tmp_path):
    """Same fact-store rows whether the source arrives as UTF-8 or EBCDIC."""
    utf8_root = tmp_path / "utf8"
    (utf8_root / "natural").mkdir(parents=True)
    (utf8_root / "natural" / "MMP0200.nsp").write_text(ORIGINAL_TEXT, encoding="utf-8")
    utf8_lines = _ingest_one(utf8_root, forced_encoding=None)

    for cp in ("cp037", "cp500"):
        ebcdic_root = tmp_path / cp
        (ebcdic_root / "natural").mkdir(parents=True)
        (ebcdic_root / "natural" / "MMP0200.nsp").write_bytes(
            (ENCODING_DIR / f"MMP0200.{cp}.nsp").read_bytes()
        )
        ebcdic_lines = _ingest_one(ebcdic_root, forced_encoding=cp)
        assert ebcdic_lines == utf8_lines, f"{cp}-encoded source produced different source_line rows"

"""Guards against off-by-one errors in banner/member-splitting handling.

Every `source_line` row must match the file on disk at the member's
`first_line` offset. Get this wrong and every citation generated from that
member is silently pointing at the wrong line -- the kind of bug that is
invisible in a diff and only shows up when someone opens the real listing.
"""

from __future__ import annotations

from pathlib import Path


def _file_lines(path: str) -> list[str]:
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    return text.split("\n")


def test_every_source_line_matches_the_file_on_disk(indexed_db):
    conn = indexed_db
    members = conn.execute(
        "SELECT id, name, dialect, source_file_id, first_line FROM member"
    ).fetchall()
    assert members, "expected at least one ingested member"

    checked = 0
    for m in members:
        sf = conn.execute(
            "SELECT path, seq_cols FROM source_file WHERE id=?", (m["source_file_id"],)
        ).fetchone()
        if sf is None or sf["seq_cols"]:
            # Sequence-column stripping (trailing "start:end" or leading
            # "L<width>" -- see cli.py's cmd_ingest) shifts content
            # independently of line numbering; that transform is covered by
            # normalise's own unit behaviour, not by a byte-for-byte file
            # comparison here.
            continue
        file_lines = _file_lines(sf["path"])
        rows = conn.execute(
            "SELECT line_no, text FROM source_line WHERE member_id=? ORDER BY line_no",
            (m["id"],),
        ).fetchall()
        for r in rows:
            file_idx = (m["first_line"] - 1) + (r["line_no"] - 1)
            assert 0 <= file_idx < len(file_lines), (
                f"{m['name']} ({m['dialect']}) line {r['line_no']} points outside "
                f"{sf['path']} ({len(file_lines)} lines)"
            )
            assert file_lines[file_idx].rstrip() == r["text"], (
                f"{m['name']} ({m['dialect']}) line {r['line_no']}: "
                f"db text {r['text']!r} != file text {file_lines[file_idx]!r} "
                f"at {sf['path']}:{file_idx + 1}"
            )
            checked += 1
    assert checked > 0, "no source_line rows were eligible for alignment checking"


def test_member_names_carry_no_extension_chain(indexed_db):
    """MMP0100, not MMP0100.NSP -- an extension in the name breaks call-edge
    resolution the moment source is moved between environments."""
    conn = indexed_db
    rows = conn.execute("SELECT name FROM member").fetchall()
    assert rows
    for r in rows:
        assert "." not in r["name"], f"member name {r['name']!r} still carries an extension"

"""Parser fingerprints must use fresh source, including loader-backed modules."""

import hashlib
import importlib
import linecache
import os
import sys
import zipfile
from types import ModuleType, SimpleNamespace

import pytest
import yaml

from mfdoc import cli
from mfdoc.db import connect, insert


@pytest.fixture(autouse=True)
def clear_parser_hash_cache():
    cli._dialect_parser_hash.cache_clear()
    yield
    cli._dialect_parser_hash.cache_clear()


def _expected_hash(dialect, module, source):
    digest = hashlib.sha256()
    digest.update(dialect.encode("utf-8"))
    digest.update(b"\x00" + module.__name__.encode("utf-8") + b"\x00")
    digest.update(source.encode("utf-8"))
    return digest.hexdigest()


def test_same_size_and_timestamp_edit_refreshes_parser_source(tmp_path, monkeypatch):
    module = ModuleType("same_metadata_parser")
    path = tmp_path / "same_metadata_parser.py"
    before, after = "VERSION = 1\n", "VERSION = 2\n"
    path.write_text(before, encoding="utf-8")
    module.__file__ = str(path)
    monkeypatch.setitem(cli.DIALECT_PARSER_MODULES, "same_metadata", (module,))
    monkeypatch.setitem(linecache.cache, str(path), None)
    linecache.cache.pop(str(path))
    original_stat = path.stat()

    first = cli._dialect_parser_hash("same_metadata")
    assert first == _expected_hash("same_metadata", module, before)
    path.write_text(after, encoding="utf-8")
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert path.stat().st_size == original_stat.st_size
    assert path.stat().st_mtime_ns == original_stat.st_mtime_ns
    cli._dialect_parser_hash.cache_clear()

    second = cli._dialect_parser_hash("same_metadata")
    assert second == _expected_hash("same_metadata", module, after)
    assert second != first
    cli._dialect_parser_hash.cache_clear()
    assert cli._dialect_parser_hash("same_metadata") == second


def test_parser_refresh_does_not_clear_unrelated_cached_source(tmp_path, monkeypatch):
    module = ModuleType("isolated_parser")
    path = tmp_path / "isolated_parser.py"
    path.write_text("VERSION = 2\n", encoding="utf-8")
    module.__file__ = str(path)
    stale = (12, path.stat().st_mtime, ["VERSION = 1\n"], str(path))
    other = (12, None, ["OTHER = 1\n"], "unrelated.py")
    monkeypatch.setitem(linecache.cache, str(path), stale)
    monkeypatch.setitem(linecache.cache, "unrelated.py", other)
    monkeypatch.setitem(cli.DIALECT_PARSER_MODULES, "isolated", (module,))

    assert cli._dialect_parser_hash("isolated") == _expected_hash(
        "isolated", module, "VERSION = 2\n"
    )
    assert linecache.cache["unrelated.py"] is other


def test_loader_source_is_refreshed_without_a_filesystem_file(tmp_path, monkeypatch):
    class SourceLoader:
        source = "VERSION = 1\n"
        calls = 0

        def get_source(self, fullname):
            assert fullname == "loader_parser"
            self.calls += 1
            return self.source

    loader = SourceLoader()
    module = ModuleType("loader_parser")
    module.__file__ = str(tmp_path / "missing_loader_parser.py")
    module.__loader__ = loader
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setitem(linecache.cache, module.__file__, None)
    linecache.cache.pop(module.__file__)
    monkeypatch.setitem(cli.DIALECT_PARSER_MODULES, "loader", (module,))

    first = cli._dialect_parser_hash("loader")
    assert first == _expected_hash("loader", module, loader.source)
    loader.source = "VERSION = 2\n"
    cli._dialect_parser_hash.cache_clear()
    second = cli._dialect_parser_hash("loader")

    assert second == _expected_hash("loader", module, loader.source)
    assert first != second
    assert loader.calls == 2
    assert not os.path.exists(module.__file__)


def test_zipimport_source_remains_supported(tmp_path, monkeypatch):
    archive = tmp_path / "parsers.zip"
    source = "VERSION = 1\n"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("zip_parser_fixture.py", source)
    monkeypatch.syspath_prepend(str(archive))
    # Register restoration before import so teardown removes the imported module.
    monkeypatch.setitem(sys.modules, "zip_parser_fixture", None)
    sys.modules.pop("zip_parser_fixture")
    module = importlib.import_module("zip_parser_fixture")
    monkeypatch.setitem(linecache.cache, module.__file__, None)
    linecache.cache.pop(module.__file__)
    monkeypatch.setitem(cli.DIALECT_PARSER_MODULES, "zip", (module,))

    assert not os.path.isfile(module.__file__)
    assert cli._dialect_parser_hash("zip") == _expected_hash("zip", module, source)
    cli._dialect_parser_hash.cache_clear()
    assert cli._dialect_parser_hash("zip") == _expected_hash("zip", module, source)


def test_real_parser_fingerprint_change_replaces_stale_ingested_facts(
    tmp_path, monkeypatch
):
    sources = tmp_path / "sources"
    sources.mkdir()
    program = sources / "SAMPLE.nsp"
    program.write_text(
        "DEFINE DATA LOCAL\n1 #FLAG (A1)\nEND-DEFINE\n"
        "MOVE 'A' TO #FLAG\nEND\n",
        encoding="utf-8",
    )
    config = tmp_path / "project.yml"
    config.write_text(
        yaml.safe_dump({
            "project": "Parser fingerprint regression",
            "system": "TEST",
            "index_db": ".mfdoc/index.db",
            "sources": [{
                "path": str(sources), "glob": ["*.nsp"], "dialect": "natural",
                "library": "TESTLIB", "sequence_columns": "none",
            }],
            "options": {"quality_gates": {}},
        }),
        encoding="utf-8",
    )
    parser = ModuleType("ingest_parser_fixture")
    parser_path = tmp_path / "ingest_parser_fixture.py"
    parser_path.write_text("VERSION = 1\n", encoding="utf-8")
    parser.__file__ = str(parser_path)
    monkeypatch.setitem(linecache.cache, str(parser_path), None)
    linecache.cache.pop(str(parser_path))
    monkeypatch.setitem(cli.DIALECT_PARSER_MODULES, "natural", (parser,))
    args = SimpleNamespace(config=str(config))

    assert cli.cmd_ingest(args) == 0
    db = connect(tmp_path / ".mfdoc/index.db")
    try:
        first = dict(db.execute("SELECT * FROM source_file").fetchone())
        member_id = db.execute("SELECT id FROM member").fetchone()["id"]
        count = db.execute("SELECT COUNT(*) FROM rule_candidate").fetchone()[0]
        insert(db, "rule_candidate", member_id=member_id, line_no=1,
               construct="MOVE", raw="STALE-FIXTURE-RULE")
        db.commit()
    finally:
        db.close()

    # Unchanged parser/source must keep the incremental skip behaviour.
    cli._dialect_parser_hash.cache_clear()
    assert cli.cmd_ingest(args) == 0
    db = connect(tmp_path / ".mfdoc/index.db")
    try:
        assert db.execute("SELECT COUNT(*) FROM rule_candidate").fetchone()[0] == count + 1
    finally:
        db.close()

    stat = parser_path.stat()
    parser_path.write_text("VERSION = 2\n", encoding="utf-8")
    os.utime(parser_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    cli._dialect_parser_hash.cache_clear()
    assert cli.cmd_ingest(args) == 0
    db = connect(tmp_path / ".mfdoc/index.db")
    try:
        second = dict(db.execute("SELECT * FROM source_file").fetchone())
        assert second["sha256"] == first["sha256"]
        assert second["dialect_hash"] != first["dialect_hash"]
        assert db.execute("SELECT id FROM member").fetchone()["id"] == member_id
        assert db.execute("SELECT COUNT(*) FROM rule_candidate").fetchone()[0] == count
        assert db.execute(
            "SELECT COUNT(*) FROM rule_candidate WHERE raw='STALE-FIXTURE-RULE'"
        ).fetchone()[0] == 0
    finally:
        db.close()

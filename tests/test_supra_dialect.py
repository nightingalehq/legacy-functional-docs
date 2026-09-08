"""Guards on the Supra directory-report scanner's config-driven label
override (`options.dialects.supra.labels`, `supra.labels_from_options`).

Synthetic directory-report snippets throughout -- invented dataset/field
names in a shape that mimics a Supra directory export, not any real site's
report.
"""

from __future__ import annotations

import sqlite3

from mfdoc.config_validate import validate_config
from mfdoc.db import SCHEMA
from mfdoc.dialects import supra


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'DIRRPT', 'supra_dir')")
    return conn


# A site whose directory report uses "FILE-ID:" for the dataset label
# instead of the shipped "DATA-SET:"/"DATASET:"/"FILE NAME:" wording -- the
# shipped `dataset` pattern in supra.LABELS does not match this, by design
# (it requires FILE to be followed by an optional NAME then ":"/"=", not an
# arbitrary suffix like "-ID").
NONSTANDARD_REPORT = [
    (1, None, "FILE-ID: WIDGETMSTR"),
    (2, None, "TYPE: MASTER"),
    (3, None, "CONTROL KEY: WIDGET-NO"),
]


def test_default_labels_do_not_recognise_a_nonstandard_dataset_label():
    """Baseline: with no project-config override, a report using a label
    wording the shipped LABELS patterns don't cover recognises zero
    datasets and records a high-severity unparsed_line gap -- confirming
    the default (unset) behaviour is unchanged by adding the override
    mechanism."""
    conn = _conn()
    counts = supra.extract(conn, 1, NONSTANDARD_REPORT, "DIRRPT")
    assert counts["datasets"] == 0

    gaps = conn.execute(
        "SELECT gap_kind, severity, detail FROM gap WHERE member_id=1"
    ).fetchall()
    assert any(
        g["gap_kind"] == "unparsed_line" and g["severity"] == "high"
        and "options.dialects.supra.labels" in g["detail"]
        for g in gaps
    )


def test_project_config_override_makes_the_nonstandard_label_recognised():
    """The same report, with `options.dialects.supra.labels` overriding just
    the `dataset` key to match this site's "FILE-ID:" wording, now
    recognises the dataset -- proving the config key is actually wired
    through to extract(), not just documented."""
    conn = _conn()
    options = {
        "dialects": {
            "supra": {
                "labels": {
                    "dataset": r"^\s*FILE-ID\s*[:=]\s*(?P<v>[A-Z0-9\-_#$]{1,32})",
                }
            }
        }
    }
    counts = supra.extract(conn, 1, NONSTANDARD_REPORT, "DIRRPT", options=options)
    assert counts["datasets"] == 1

    entity = conn.execute(
        "SELECT name, kind FROM entity WHERE name='WIDGETMSTR'"
    ).fetchone()
    assert entity is not None
    assert entity["kind"] == "supra_master"

    # No unparsed_line gap this time -- the override made the layout
    # recognisable, so nothing tells the calibrator to look further.
    gaps = conn.execute(
        "SELECT gap_kind FROM gap WHERE member_id=1 AND gap_kind='unparsed_line'"
    ).fetchall()
    assert gaps == []


def test_override_only_replaces_the_supplied_key_not_the_whole_dict():
    """Overriding `dataset` alone must not disturb recognition of the other
    LABELS keys (dataset_type, control_key) -- labels_from_options merges
    key-by-key over the defaults rather than replacing the whole mapping."""
    conn = _conn()
    options = {"dialects": {"supra": {"labels": {
        "dataset": r"FILE-ID\s*[:=]\s*(?P<v>[A-Z0-9\-_#$]{1,32})",
    }}}}
    counts = supra.extract(conn, 1, NONSTANDARD_REPORT, "DIRRPT", options=options)
    assert counts["datasets"] == 1

    field = conn.execute(
        "SELECT descriptor_kind FROM entity_field ef "
        "JOIN entity e ON e.id = ef.entity_id "
        "WHERE e.name='WIDGETMSTR' AND ef.name='WIDGET-NO'"
    ).fetchone()
    assert field is not None
    assert field["descriptor_kind"] == "primary_key"


def test_labels_from_options_with_no_override_returns_module_defaults():
    assert supra.labels_from_options(None) == supra.LABELS
    assert supra.labels_from_options({}) == supra.LABELS
    assert supra.labels_from_options({"dialects": {"supra": {}}}) == supra.LABELS


def test_config_validate_accepts_a_well_formed_labels_override():
    cfg = {
        "index_db": ".mfdoc/index.db",
        "sources": [],
        "options": {
            "dialects": {"supra": {"labels": {"dataset": r"FILE-ID\s*[:=]\s*(?P<v>\w+)"}}},
        },
    }
    assert validate_config(cfg) == []


def test_config_validate_rejects_an_unknown_supra_label_key():
    cfg = {
        "index_db": ".mfdoc/index.db",
        "sources": [],
        "options": {
            "dialects": {"supra": {"labels": {"dataset_typo": r"\w+"}}},
        },
    }
    problems = validate_config(cfg)
    assert any("dataset_typo" in p for p in problems)


def test_config_validate_rejects_an_invalid_regex():
    cfg = {
        "index_db": ".mfdoc/index.db",
        "sources": [],
        "options": {
            "dialects": {"supra": {"labels": {"dataset": "("}}},
        },
    }
    problems = validate_config(cfg)
    assert any("dataset" in p and "regex" in p for p in problems)


def test_config_validate_rejects_a_valid_regex_missing_the_v_group():
    """A syntactically valid override that omits the required `(?P<v>...)`
    named group must be rejected at config-validation time, not left to blow
    up in `supra._find`'s `m.group("v")` the first time it matches a line
    during `mfdoc ingest`."""
    cfg = {
        "index_db": ".mfdoc/index.db",
        "sources": [],
        "options": {
            "dialects": {"supra": {"labels": {"dataset": r"FILE-ID\s*[:=]\s*(\w+)"}}},
        },
    }
    problems = validate_config(cfg)
    assert any("dataset" in p and "(?P<v>" in p for p in problems)

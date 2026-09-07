"""Tests for the centralized config validation pass (config_validate.py),
and for load_config's use of it as a fail-fast gate at CLI startup."""

from __future__ import annotations

import copy

import pytest
import yaml

from mfdoc import cli
from mfdoc.config_validate import ConfigError, raise_if_invalid, validate_config


def _base_cfg() -> dict:
    """A minimal, fully valid resolved config -- same shape load_config
    produces (index_db/sources/options always present)."""
    return {
        "index_db": ".mfdoc/index.db",
        "sources": [],
        "options": {},
    }


def test_minimal_valid_config_has_no_problems():
    assert validate_config(_base_cfg()) == []


def test_fully_populated_valid_config_has_no_problems():
    cfg = _base_cfg()
    cfg["options"] = {
        "narrative": {
            "max_rules_per_call": 40,
            "pricing": {"input_per_mtok": 3.0, "output_per_mtok": 15.0},
        },
        "testgen": {
            "max_scenarios_per_call": 150,
            "default_language": "python",
            "default_framework": "pytest",
            "overlay_path": "test-overlay.yml",
            "out_dir": "tests_generated",
        },
        "quality_gates": {
            "min_line_recognition_rate": 0.85,
            "min_call_resolution_rate": 0.70,
            "min_entity_definition_rate": 0.80,
            "min_citation_accuracy_rate": 1.0,
            "max_high_severity_gaps": 50,
        },
        "redact": {"enabled": True, "patterns": [r"\bSECRET\d+\b", "literal-value"]},
        "validate": {"outcome_field_pattern": r"\bRESULT\b"},
        "overview": {
            "themes": {"llm_fallback": False},
            "complexity": {"metric": "rule_depth"},
            "diagrams": {"cluster_by": "subsystem", "max_nodes_inline": 40, "direction": "TD"},
            "dispatch_field_pattern": r"MENU-OPT\b",
        },
    }
    assert validate_config(cfg) == []


# --------------------------------------------------------------- top-level shape

@pytest.mark.parametrize("bad_index_db", [None, 123, ["a"]])
def test_index_db_must_be_a_string(bad_index_db):
    cfg = _base_cfg()
    cfg["index_db"] = bad_index_db
    problems = validate_config(cfg)
    assert any("index_db must be a string" in p for p in problems)


@pytest.mark.parametrize("bad_sources", [None, "not-a-list", {}])
def test_sources_must_be_a_list(bad_sources):
    cfg = _base_cfg()
    cfg["sources"] = bad_sources
    problems = validate_config(cfg)
    assert any("sources must be a list" in p for p in problems)


@pytest.mark.parametrize("bad_options", [None, "not-a-mapping", []])
def test_options_must_be_a_mapping(bad_options):
    cfg = _base_cfg()
    cfg["options"] = bad_options
    problems = validate_config(cfg)
    assert len(problems) == 1
    assert "options must be a mapping" in problems[0]


def test_options_block_not_a_mapping_short_circuits_leaf_checks():
    """A malformed `options:` block reports once, not once per OPTION_SPECS
    entry that would otherwise separately fail to walk into it."""
    cfg = _base_cfg()
    cfg["options"] = "oops"
    assert len(validate_config(cfg)) == 1


def test_option_subsection_not_a_mapping_is_a_specific_error():
    cfg = _base_cfg()
    cfg["options"] = {"redact": ["not", "a", "mapping"]}
    problems = validate_config(cfg)
    assert any("options.redact must be a mapping" in p for p in problems)


def test_option_subsection_not_a_mapping_is_reported_once_not_per_sibling_leaf():
    """options.redact has two OPTION_SPECS entries (enabled, patterns) --
    a malformed options.redact must report the root cause once, not once
    per sibling leaf declared under it."""
    cfg = _base_cfg()
    cfg["options"] = {"redact": ["not", "a", "mapping"]}
    problems = validate_config(cfg)
    matches = [p for p in problems if "options.redact must be a mapping" in p]
    assert len(matches) == 1


# --------------------------------------------------------------- missing fields

def test_missing_optional_fields_are_fine():
    cfg = _base_cfg()
    cfg["options"] = {"narrative": {}}
    assert validate_config(cfg) == []


# --------------------------------------------------------------- wrong types

@pytest.mark.parametrize("bad_value", ["40", 40.5, None, True, [40]])
def test_max_rules_per_call_wrong_type(bad_value):
    cfg = _base_cfg()
    cfg["options"] = {"narrative": {"max_rules_per_call": bad_value}}
    problems = validate_config(cfg)
    assert any("options.narrative.max_rules_per_call must be a positive integer" in p
               for p in problems)


def test_redact_enabled_must_be_bool_not_truthy_string():
    cfg = _base_cfg()
    cfg["options"] = {"redact": {"enabled": "true"}}
    problems = validate_config(cfg)
    assert any("options.redact.enabled must be a boolean" in p for p in problems)


def test_redact_patterns_must_be_a_list():
    cfg = _base_cfg()
    cfg["options"] = {"redact": {"patterns": "not-a-list"}}
    problems = validate_config(cfg)
    assert any("options.redact.patterns must be a list" in p for p in problems)


def test_redact_patterns_entries_must_be_strings():
    cfg = _base_cfg()
    cfg["options"] = {"redact": {"patterns": [123]}}
    problems = validate_config(cfg)
    assert any("entry 0 must be a string" in p for p in problems)


def test_redact_patterns_must_be_valid_regexes():
    cfg = _base_cfg()
    cfg["options"] = {"redact": {"patterns": ["valid", "(unclosed"]}}
    problems = validate_config(cfg)
    assert any("not a valid regex" in p for p in problems)


def test_quality_gate_rate_must_be_numeric():
    cfg = _base_cfg()
    cfg["options"] = {"quality_gates": {"min_line_recognition_rate": "high"}}
    problems = validate_config(cfg)
    assert any(
        "options.quality_gates.min_line_recognition_rate must be a number between 0 and 1" in p
        for p in problems
    )


# --------------------------------------------------------------- out-of-range values

@pytest.mark.parametrize("bad_value", [0, -1, -40])
def test_max_rules_per_call_must_be_positive(bad_value):
    cfg = _base_cfg()
    cfg["options"] = {"narrative": {"max_rules_per_call": bad_value}}
    problems = validate_config(cfg)
    assert any("must be a positive integer" in p for p in problems)


@pytest.mark.parametrize("bad_value", [-0.01, 1.01, 2])
def test_quality_gate_rate_out_of_range(bad_value):
    cfg = _base_cfg()
    cfg["options"] = {"quality_gates": {"min_call_resolution_rate": bad_value}}
    problems = validate_config(cfg)
    assert any("must be between 0 and 1" in p for p in problems)


def test_max_high_severity_gaps_must_be_non_negative():
    cfg = _base_cfg()
    cfg["options"] = {"quality_gates": {"max_high_severity_gaps": -1}}
    problems = validate_config(cfg)
    assert any("must be >= 0" in p for p in problems)


def test_pricing_must_be_non_negative():
    cfg = _base_cfg()
    cfg["options"] = {"narrative": {"pricing": {"input_per_mtok": -3.0}}}
    problems = validate_config(cfg)
    assert any("options.narrative.pricing.input_per_mtok" in p and "must be >= 0" in p
               for p in problems)


@pytest.mark.parametrize("field,bad_value", [
    ("cluster_by", "team"),
    ("direction", "diagonal"),
])
def test_overview_diagrams_enum_fields(field, bad_value):
    cfg = _base_cfg()
    cfg["options"] = {"overview": {"diagrams": {field: bad_value}}}
    problems = validate_config(cfg)
    assert any(f"options.overview.diagrams.{field}" in p and "must be one of" in p
               for p in problems)


def test_outcome_field_pattern_must_be_a_valid_regex():
    cfg = _base_cfg()
    cfg["options"] = {"validate": {"outcome_field_pattern": "(unclosed"}}
    problems = validate_config(cfg)
    assert any("options.validate.outcome_field_pattern" in p and "not a valid regex" in p
               for p in problems)


def test_dispatch_field_pattern_must_be_a_valid_regex():
    cfg = _base_cfg()
    cfg["options"] = {"overview": {"dispatch_field_pattern": "(unclosed"}}
    problems = validate_config(cfg)
    assert any("options.overview.dispatch_field_pattern" in p and "not a valid regex" in p
               for p in problems)


def test_overview_complexity_metric_only_rule_depth_supported():
    cfg = _base_cfg()
    cfg["options"] = {"overview": {"complexity": {"metric": "cyclomatic"}}}
    problems = validate_config(cfg)
    assert any("options.overview.complexity.metric" in p and "must be one of" in p
               for p in problems)


def test_max_nodes_inline_must_be_positive():
    cfg = _base_cfg()
    cfg["options"] = {"overview": {"diagrams": {"max_nodes_inline": 0}}}
    problems = validate_config(cfg)
    assert any("options.overview.diagrams.max_nodes_inline" in p and "positive" in p
               for p in problems)


# --------------------------------------------------------------- multiple problems

def test_multiple_problems_are_all_reported_not_just_the_first():
    cfg = _base_cfg()
    cfg["options"] = {
        "narrative": {"max_rules_per_call": -1},
        "redact": {"enabled": "true"},
        "overview": {"diagrams": {"direction": "diagonal"}},
    }
    problems = validate_config(cfg)
    assert len(problems) == 3


# --------------------------------------------------------------- raise_if_invalid / ConfigError

def test_raise_if_invalid_is_a_noop_for_a_valid_config():
    raise_if_invalid(_base_cfg())  # must not raise


def test_raise_if_invalid_raises_config_error_listing_every_problem():
    cfg = _base_cfg()
    cfg["options"] = {
        "narrative": {"max_rules_per_call": -1},
        "redact": {"enabled": "true"},
    }
    with pytest.raises(ConfigError) as exc_info:
        raise_if_invalid(cfg)
    message = str(exc_info.value)
    assert "max_rules_per_call" in message
    assert "redact.enabled" in message


def test_validate_config_does_not_mutate_input():
    cfg = _base_cfg()
    cfg["options"] = {"narrative": {"max_rules_per_call": 40}}
    before = copy.deepcopy(cfg)
    validate_config(cfg)
    assert cfg == before


# --------------------------------------------------------------- cli.load_config / main integration

def test_load_config_raises_config_error_for_malformed_project_yml(tmp_path):
    """The fail-fast gate this issue asks for: a bad project.yml is caught
    the moment it's loaded, before any command does real work with it --
    not only once processing happens to reach the one ad hoc check that
    used to guard this particular value."""
    config_path = tmp_path / "project.yml"
    config_path.write_text(
        yaml.safe_dump({
            "index_db": ".mfdoc/index.db",
            "sources": [],
            "options": {"narrative": {"max_rules_per_call": -5}},
        }),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="max_rules_per_call"):
        cli.load_config(str(config_path))


@pytest.mark.parametrize("bad_root", [
    ["not", "a", "mapping"],
    "just a string",
    42,
])
def test_load_config_raises_config_error_for_non_mapping_yaml_root(tmp_path, bad_root):
    """A project.yml that parses to a list/string/number (e.g. a stray
    leading '-') must fail with the same clean ConfigError, not a raw
    AttributeError from cfg.setdefault() on a non-dict."""
    config_path = tmp_path / "project.yml"
    config_path.write_text(yaml.safe_dump(bad_root), encoding="utf-8")
    with pytest.raises(ConfigError, match="config root must be a mapping"):
        cli.load_config(str(config_path))


def test_load_config_accepts_a_config_with_no_options_block(tmp_path):
    """Every OPTION_SPECS entry is optional -- a project.yml that configures
    none of them (the common case) must still load cleanly."""
    config_path = tmp_path / "project.yml"
    config_path.write_text(
        yaml.safe_dump({"index_db": ".mfdoc/index.db", "sources": []}),
        encoding="utf-8",
    )
    cfg = cli.load_config(str(config_path))
    assert cfg["options"] == {}


def test_main_exits_cleanly_with_message_on_malformed_config(tmp_path, capsys):
    """End-to-end: `mfdoc <cmd> --config bad.yml` must print a clear error
    and exit 2, not crash with an unhandled traceback -- the same "usage
    error" idiom the CLI already uses for other config-shape problems
    (e.g. cmd_test_gen's --matrix validation)."""
    config_path = tmp_path / "project.yml"
    config_path.write_text(
        yaml.safe_dump({
            "index_db": ".mfdoc/index.db",
            "sources": [],
            "options": {"quality_gates": {"max_high_severity_gaps": -1}},
        }),
        encoding="utf-8",
    )
    rc = cli.main(["gate", "--config", str(config_path)])
    assert rc == 2
    captured = capsys.readouterr()
    assert "max_high_severity_gaps" in captured.err

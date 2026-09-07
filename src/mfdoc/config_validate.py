"""Centralized validation of the resolved project.yml config.

Historically, `options.*` values were validated ad hoc at the point of use
(e.g. `batch._resolve_max_rules_per_call`, `testbatch._resolve_max_scenarios_
per_call`, `structural.call_graph_diagram`'s `direction`/`cluster_by` checks)
-- each correct in isolation, but each only discovered once a run actually
reached that code path, which can be well after ingest/derive have already
done real work. `load_config` calls `raise_if_invalid` on every resolved
config before any command touches it, so a malformed project.yml is a single
clear error at startup instead of a run failing partway through.

Adding a new config key's validation is a small addition to `OPTION_SPECS`
below, not a rewrite: each entry is declarative (a dotted path, the accepted
type(s), and an optional extra check), so a new key never needs its own
if/elif branch here. Per-key ad hoc checks at point of use are still allowed
to remain (they're a harmless second line of defence for any caller that
builds a config dict without going through `load_config`, e.g. a test) --
this module's job is only to catch the same problems earlier, not to be the
sole enforcement point.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

_MISSING = object()


class ConfigError(ValueError):
    """Raised when a resolved project.yml config fails validation. The
    message lists every problem found, not just the first, so a single
    fix-run-fix cycle can catch more than one typo."""


def _lookup(cfg: dict, dotted_path: str) -> tuple[Any, str | None]:
    """`(value, error)` for `dotted_path` walked through nested dicts in
    `cfg`. `value` is `_MISSING` when the path is simply absent (not itself
    an error -- every option here is optional unless `required=True`).
    `error` is set instead when a step along the path exists but isn't a
    mapping to walk into (e.g. `options.redact: []` -- `enabled`/`patterns`
    can't be looked up inside a list), which is a distinct, more specific
    problem than "missing". Keyed on the offending parent path alone (not
    the specific leaf being looked up), so validate_config can dedupe: every
    OptionSpec under the same malformed parent would otherwise report the
    identical root cause once per sibling leaf."""
    node = cfg
    parts = dotted_path.split(".")
    for i, part in enumerate(parts):
        if not isinstance(node, dict):
            parent = ".".join(parts[:i])
            return _MISSING, f"{parent} must be a mapping, got {node!r}"
        if part not in node:
            return _MISSING, None
        node = node[part]
    return node, None


def _type_ok(value: Any, types: tuple[type, ...]) -> bool:
    """`isinstance(value, types)`, except `bool` never satisfies a numeric
    spec (`int`/`float`) unless `bool` is itself one of `types` -- Python's
    `bool` is a subclass of `int`, so a plain `isinstance` check would
    silently accept `max_rules_per_call: true` as the integer 1."""
    if isinstance(value, bool):
        return bool in types
    return isinstance(value, types)


def _positive_int(value: int) -> str | None:
    if value <= 0:
        return f"must be a positive integer, got {value!r}"
    return None


def _non_negative(value: int | float) -> str | None:
    if value < 0:
        return f"must be >= 0, got {value!r}"
    return None


def _in_range(lo: float, hi: float) -> Callable[[float], str | None]:
    def _check(value: float) -> str | None:
        if not (lo <= value <= hi):
            return f"must be between {lo} and {hi}, got {value!r}"
        return None
    return _check


def _one_of(choices: set[str]) -> Callable[[str], str | None]:
    def _check(value: str) -> str | None:
        if value not in choices:
            return f"must be one of {sorted(choices)}, got {value!r}"
        return None
    return _check


def _regex_list(value: list) -> str | None:
    for i, pattern in enumerate(value):
        if not isinstance(pattern, str):
            return f"entry {i} must be a string, got {pattern!r}"
        try:
            re.compile(pattern)
        except re.error as exc:
            return f"entry {i} ({pattern!r}) is not a valid regex: {exc}"
    return None


@dataclass(frozen=True)
class OptionSpec:
    path: str
    """Dotted path from the config root, e.g. 'options.redact.enabled'."""
    types: tuple[type, ...]
    type_label: str
    """Human-readable description of `types`, used in error messages."""
    required: bool = False
    check: Callable[[Any], str | None] | None = None
    """Extra validation beyond type, e.g. range/allowed-values checks.
    Returns an error message fragment, or None if `value` is fine."""


# One entry per validated options.* leaf. Every entry is optional
# (required=False) unless flagged otherwise -- absence is a project simply
# not having configured that feature, which every reader in the codebase
# already falls back on a default for. New config keys: add a row here, not
# a new branch of logic.
OPTION_SPECS: list[OptionSpec] = [
    OptionSpec("options.narrative.max_rules_per_call", (int,),
               "a positive integer", check=_positive_int),
    OptionSpec("options.narrative.pricing.input_per_mtok", (int, float),
               "a non-negative number", check=_non_negative),
    OptionSpec("options.narrative.pricing.output_per_mtok", (int, float),
               "a non-negative number", check=_non_negative),

    OptionSpec("options.testgen.max_scenarios_per_call", (int,),
               "a positive integer", check=_positive_int),
    OptionSpec("options.testgen.default_language", (str,), "a string"),
    OptionSpec("options.testgen.default_framework", (str,), "a string"),
    OptionSpec("options.testgen.overlay_path", (str,), "a string"),
    OptionSpec("options.testgen.out_dir", (str,), "a string"),

    OptionSpec("options.quality_gates.min_line_recognition_rate", (int, float),
               "a number between 0 and 1", check=_in_range(0, 1)),
    OptionSpec("options.quality_gates.min_call_resolution_rate", (int, float),
               "a number between 0 and 1", check=_in_range(0, 1)),
    OptionSpec("options.quality_gates.min_entity_definition_rate", (int, float),
               "a number between 0 and 1", check=_in_range(0, 1)),
    OptionSpec("options.quality_gates.min_citation_accuracy_rate", (int, float),
               "a number between 0 and 1", check=_in_range(0, 1)),
    OptionSpec("options.quality_gates.max_high_severity_gaps", (int,),
               "a non-negative integer", check=_non_negative),

    OptionSpec("options.redact.enabled", (bool,), "a boolean"),
    OptionSpec("options.redact.patterns", (list,),
               "a list of valid regex strings", check=_regex_list),

    OptionSpec("options.overview.themes.llm_fallback", (bool,), "a boolean"),
    OptionSpec("options.overview.complexity.metric", (str,), "a string",
               check=_one_of({"rule_depth"})),
    OptionSpec("options.overview.diagrams.cluster_by", (str,), "a string",
               check=_one_of({"module", "library", "subsystem"})),
    OptionSpec("options.overview.diagrams.max_nodes_inline", (int,),
               "a positive integer", check=_positive_int),
    OptionSpec("options.overview.diagrams.direction", (str,), "a string",
               check=_one_of({"LR", "TD"})),
]


def validate_config(cfg: dict) -> list[str]:
    """Every problem found in `cfg` (the dict `load_config` builds), or []
    if it passes. Two passes: (1) the top-level shape every command already
    assumes before it does anything else (`index_db` a string, `sources` a
    list, `options` a mapping) -- checked first and short-circuited on
    `options` failing, since every check in pass 2 would otherwise just
    re-report the same root cause once per spec; (2) each declared
    `options.*` leaf in OPTION_SPECS."""
    problems: list[str] = []

    index_db = cfg.get("index_db")
    if not isinstance(index_db, str):
        problems.append(f"index_db must be a string, got {index_db!r}")

    sources = cfg.get("sources")
    if not isinstance(sources, list):
        problems.append(f"sources must be a list, got {sources!r}")

    options = cfg.get("options")
    if not isinstance(options, dict):
        problems.append(f"options must be a mapping, got {options!r}")
        return problems

    seen_walk_errors: set[str] = set()
    for spec in OPTION_SPECS:
        value, walk_error = _lookup(cfg, spec.path)
        if walk_error:
            # Every OptionSpec under the same malformed parent (e.g. both
            # options.redact.enabled and options.redact.patterns when
            # options.redact itself isn't a mapping) hits the same
            # walk_error -- report that parent once, not once per sibling
            # leaf that happens to be declared under it.
            if walk_error not in seen_walk_errors:
                problems.append(walk_error)
                seen_walk_errors.add(walk_error)
            continue
        if value is _MISSING:
            if spec.required:
                problems.append(f"{spec.path} is required but missing")
            continue
        if not _type_ok(value, spec.types):
            problems.append(f"{spec.path} must be {spec.type_label}, got {value!r}")
            continue
        if spec.check is not None:
            msg = spec.check(value)
            if msg:
                problems.append(f"{spec.path} {msg}")

    return problems


def raise_if_invalid(cfg: dict) -> None:
    """Raise ConfigError, listing every problem found, if `cfg` fails
    validate_config; a no-op otherwise."""
    problems = validate_config(cfg)
    if problems:
        bullets = "\n".join(f"  - {p}" for p in problems)
        raise ConfigError(
            f"invalid project config -- {len(problems)} problem(s):\n{bullets}"
        )

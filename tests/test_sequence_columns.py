"""Guards on leading-sequence-prefix detection (issue #26).

Some real-world exports (found via a smoke test against
SoftwareAG/adabas-natural-code-samples, per issue #19/#26) put the sequence
number at the *start* of each line rather than in the trailing 73-80 field
`detect_seq_columns` already handles, with no guaranteed separator before
the real content -- `0010DEFINE` has none, `0080  2 #LEAVE (N2)` has
padding spaces. These tests exercise `detect_leading_seq_prefix` and its
wiring into `split_members` directly, without the full pipeline.
"""

from __future__ import annotations

from mfdoc import normalise


def test_detects_leading_prefix_with_no_separator():
    lines = [
        "0010DEFINE DATA LOCAL",
        "0020  1 #COUNT (N4)",
        "0030END-DEFINE",
        "0040IF #COUNT = 0",
        "0050  #COUNT := 1",
        "0060END-IF",
    ]
    assert normalise.detect_leading_seq_prefix(lines) == 4


def test_detects_leading_prefix_with_space_padding():
    lines = [
        "0010  DEFINE DATA LOCAL",
        "0020  1 #COUNT (N4)",
        "0030  END-DEFINE",
        "0040  IF #COUNT = 0",
        "0050    #COUNT := 1",
        "0060  END-IF",
    ]
    assert normalise.detect_leading_seq_prefix(lines) == 4


def test_no_leading_prefix_detected_on_free_format_source():
    # Free-format source that merely happens to start with a digit on a few
    # lines must not be mistaken for a sequence-numbered export.
    lines = [
        "DEFINE DATA LOCAL",
        "1 #COUNT (N4)",
        "END-DEFINE",
        "IF #COUNT = 0",
        "  #COUNT := 1",
        "END-IF",
    ]
    assert normalise.detect_leading_seq_prefix(lines) is None


def test_requires_a_strong_majority_not_a_bare_match():
    # Only 2 of 6 long-enough lines have a 4-digit leading run -- must not fire.
    lines = [
        "0010DEFINE DATA LOCAL",
        "  1 #COUNT (N4)",
        "0030END-DEFINE",
        "IF #COUNT = 0",
        "  #COUNT := 1",
        "END-IF",
    ]
    assert normalise.detect_leading_seq_prefix(lines) is None


def test_split_members_strips_leading_prefix_before_dialect_matching():
    lines = [
        "0010DEFINE DATA LOCAL",
        "0020  1 #COUNT (N4)",
        "0030END-DEFINE",
    ]
    chunks = normalise.split_members(
        lines, "natural", default_name="TESTPGM", seq_cols=None, leading_seq_width=4,
    )
    assert len(chunks) == 1
    stripped = [text for _, _, text in chunks[0].lines]
    assert stripped[0] == "DEFINE DATA LOCAL"
    assert stripped[1] == "  1 #COUNT (N4)"
    assert stripped[2] == "END-DEFINE"


def test_split_members_leaves_short_lines_unstripped():
    # A line too short to carry the sequence-number width must pass through
    # unchanged rather than being truncated.
    lines = ["0010DEFINE DATA LOCAL", "*"]
    chunks = normalise.split_members(
        lines, "natural", default_name="TESTPGM", seq_cols=None, leading_seq_width=4,
    )
    stripped = [text for _, _, text in chunks[0].lines]
    assert stripped[1] == "*"

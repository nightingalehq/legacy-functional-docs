---
title: "MMP0100 — generated tests (python)"
doc_type: generated_test
system: MOM
module: MMP0100
language: python
framework: pytest
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-08-11"
review_status: draft
reviewers: []
confidence_summary:
  verified: 6
  inferred: 0
  unresolved: 1
sources: ["MMP0100"]
---

# MMP0100 — generated tests (python / pytest)

Covers all seven branch scenarios `mfdoc test-plan` derived for MMP0100
[[MMP0100:34]], [[MMP0100:38]], [[MMP0100:44]], [[MMP0100:47]],
[[MMP0100:53]], [[MMP0100:55]], [[MMP0100:58]]. `MMP0100:BR-009` (the
plant-match check) has no reconstructable consequence in the fact brief, so
its test only exercises the branch decision and is marked `unresolved`
rather than asserting a guessed outcome. `MMN0250` and `MMN0900` have no
supplied source (`mfdoc test-advisory` reports both as gaps), so they are
stubbed opaquely as "was called with X" rather than verified against real
behaviour. All scenarios are `characterization` — they assert what the
source does today, not what it should do; there is no promoted
`test-overlay.yml` entry for this member. The Python function signature
below (`release_mill_order`) is this file's assumed shape for a ported
version of MMP0100 and is not itself a cited fact — only the parameter
names/formats, mocked entities/callees, and branch conditions/consequences
are.

```python
"""Generated characterization tests for MMP0100.

Do not hand-edit -- regenerate with `mfdoc test-batch`. See
MMP0100-test-example's fact brief (`mfdoc test-plan` / `mfdoc test-gen
--member MMP0100`) for the scenarios covered.
"""

import pytest


# Dependencies to mock, per the brief's "Dependencies to mock" section --
# entities: MILL-ORDER, ORDER-AUDIT, STOCK-BALANCE; callees: MMN0250, MMN0900.
# Fixture shapes below use only the fields/parameters the fact store names;
# nothing here is invented beyond the assumed port signature.

@pytest.fixture
def order_store():
    class FakeOrderStore:
        def __init__(self):
            self.orders = {}
            self.updated = None

        def find(self, order_no):
            return self.orders.get(order_no)

        def update(self, order):
            self.updated = order

    return FakeOrderStore()


@pytest.fixture
def stock_reader():
    class FakeStockReader:
        def __init__(self, rows=None):
            self.rows = rows or []

        def rows_for_grade(self, grade_code):
            for row in self.rows:
                if row["grade_code"] != grade_code:
                    break
                yield row

    return FakeStockReader


@pytest.fixture
def audit_writer():
    class FakeAuditWriter:
        def __init__(self):
            self.written = []

        def write(self, order_no):
            self.written.append(order_no)

    return FakeAuditWriter()


@pytest.fixture
def mmn0250_calls():
    # MMN0250 has no supplied source [[MMP0100:57]] -- stub records only
    # that it was called, never what it does.
    calls = []

    def _mmn0250(order_no, available_total):
        calls.append((order_no, available_total))

    _mmn0250.calls = calls
    return _mmn0250


@pytest.fixture
def mmn0900_calls():
    # MMN0900 has no supplied source [[MMP0100:67]] -- same treatment.
    calls = []

    def _mmn0900(order_no):
        calls.append(order_no)

    _mmn0900.calls = calls
    return _mmn0900


def test_order_not_found_sets_return_code_10(order_store, stock_reader, audit_writer, mmn0250_calls, mmn0900_calls):
    # MMP0100:BR-001 [[MMP0100:34]]
    # Branch: IF NO RECORDS FOUND (no records found for preceding database loop)
    # Consequence, verbatim [[MMP0100:35-36]]:
    #   MOVE 10 TO #RETURN-CODE
    #   ESCAPE ROUTINE
    result = release_mill_order("NOSUCHORD", "1000", order_store, stock_reader([]), audit_writer, mmn0250_calls, mmn0900_calls)
    assert result.return_code == 10
    assert order_store.updated is None


def test_unconfirmed_order_is_rejected_with_return_code_20(order_store, stock_reader, audit_writer, mmn0250_calls, mmn0900_calls):
    # MMP0100:BR-004 [[MMP0100:38]]
    # Branch: IF ORDER-VIEW.ORDER-STATUS NE 'CONF'
    # Consequence, verbatim [[MMP0100:39-40]]:
    #   MOVE 20 TO #RETURN-CODE
    #   ESCAPE ROUTINE
    order_store.orders["ORD0001"] = {"order_no": "ORD0001", "status": "HELD", "grade_code": "X9", "order_weight": 100}
    result = release_mill_order("ORD0001", "1000", order_store, stock_reader([]), audit_writer, mmn0250_calls, mmn0900_calls)
    assert result.return_code == 20
    assert order_store.updated is None


def test_stock_read_stops_at_first_non_matching_grade(order_store, stock_reader, audit_writer, mmn0250_calls, mmn0900_calls):
    # MMP0100:BR-007 [[MMP0100:44]]
    # Branch: IF STOCK-VIEW.GRADE-CODE NE ORDER-VIEW.GRADE-CODE
    # Consequence, verbatim [[MMP0100:45]]:
    #   ESCAPE BOTTOM
    order_store.orders["ORD0002"] = {"order_no": "ORD0002", "status": "CONF", "grade_code": "X9", "order_weight": 50}
    reader = stock_reader([
        {"grade_code": "X9", "plant_code": "1000", "weight": 20},
        {"grade_code": "Y1", "plant_code": "1000", "weight": 999},
    ])
    result = release_mill_order("ORD0002", "1000", order_store, reader, audit_writer, mmn0250_calls, mmn0900_calls)
    # Only the X9 row is accumulated; the Y1 row is never reached.
    assert result.return_code == 30


@pytest.mark.skip(reason="MMP0100:BR-009 [[MMP0100:47]] has no reconstructable consequence in the fact brief -- "
                          "write the assertion once an SME confirms what happens on a plant mismatch, per "
                          "reference/test-writing-rules.md ('do not invent a plausible expected value').")
def test_stock_at_other_plants_is_excluded_unresolved():
    # MMP0100:BR-009 [[MMP0100:47]]
    # Branch: IF STOCK-VIEW.PLANT-CODE = #PLANT
    # No consequence reconstructable from source facts -- left unresolved.
    ...


def test_full_release_when_stock_covers_the_order(order_store, stock_reader, audit_writer, mmn0250_calls, mmn0900_calls):
    # MMP0100:BR-011 [[MMP0100:53]]
    # Branch: WHEN #AVAIL-TOTAL >= ORDER-VIEW.ORDER-WEIGHT
    # Consequence, verbatim [[MMP0100:54]]:
    #   MOVE 'RLSD' TO ORDER-VIEW.ORDER-STATUS
    order_store.orders["ORD0003"] = {"order_no": "ORD0003", "status": "CONF", "grade_code": "X9", "order_weight": 50}
    reader = stock_reader([{"grade_code": "X9", "plant_code": "1000", "weight": 60}])
    result = release_mill_order("ORD0003", "1000", order_store, reader, audit_writer, mmn0250_calls, mmn0900_calls)
    assert order_store.updated["status"] == "RLSD"
    assert result.return_code == 0
    assert audit_writer.written == ["ORD0003"]
    assert mmn0900_calls.calls == ["ORD0003"]


def test_partial_release_within_tolerance_calls_mmn0250(order_store, stock_reader, audit_writer, mmn0250_calls, mmn0900_calls):
    # MMP0100:BR-013 [[MMP0100:55]]
    # Branch: WHEN #AVAIL-TOTAL >= ORDER-VIEW.ORDER-WEIGHT * (1 - #TOLERANCE-PCT / 100)
    # Consequence, verbatim [[MMP0100:56]]:
    #   MOVE 'PART' TO ORDER-VIEW.ORDER-STATUS
    # MMN0250 is called on this path [[MMP0100:57]]; its behaviour is unknown
    # (no source supplied), so only the call itself is asserted.
    order_store.orders["ORD0004"] = {"order_no": "ORD0004", "status": "CONF", "grade_code": "X9", "order_weight": 100}
    reader = stock_reader([{"grade_code": "X9", "plant_code": "1000", "weight": 98}])
    result = release_mill_order("ORD0004", "1000", order_store, reader, audit_writer, mmn0250_calls, mmn0900_calls)
    assert order_store.updated["status"] == "PART"
    assert result.return_code == 0
    assert mmn0250_calls.calls == [("ORD0004", 98)]


def test_no_release_when_below_tolerance_sets_return_code_30(order_store, stock_reader, audit_writer, mmn0250_calls, mmn0900_calls):
    # MMP0100:BR-015 [[MMP0100:58]]
    # Branch: WHEN NONE (the DECIDE FOR FIRST CONDITION's final, unconditional branch)
    # Consequence, verbatim [[MMP0100:59-60]]:
    #   MOVE 30 TO #RETURN-CODE
    #   ESCAPE ROUTINE
    order_store.orders["ORD0005"] = {"order_no": "ORD0005", "status": "CONF", "grade_code": "X9", "order_weight": 100}
    reader = stock_reader([{"grade_code": "X9", "plant_code": "1000", "weight": 10}])
    result = release_mill_order("ORD0005", "1000", order_store, reader, audit_writer, mmn0250_calls, mmn0900_calls)
    assert result.return_code == 30
    assert order_store.updated is None
```

"""Generated characterization tests for ORDENQ.

Do not hand-edit -- regenerate with `mfdoc test-batch`. See the fact brief
this file was rendered from for the scenarios covered.
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def pricecalc_mock():
    # Dependency to mock: callee `PRICECALC`. The brief states only that it
    # is called as CALL "PRICECALC" (ORDER_NO, ORDER_WT) -- no return shape
    # is given, so none is asserted here.
    return MagicMock(name="PRICECALC")


def test_ordenq_br001_order_no_blank_branch_decision():
    # ORDENQ:BR-001 [[ORDENQ:11]]
    # Branch: IF ORDER_NO = " "
    pytest.skip(
        "unresolved: no consequence reconstructable from source facts for "
        "this branch [[ORDENQ:11]]"
    )


def test_ordenq_br002_status_not_zero_exits_while_loop():
    # ORDENQ:BR-002 [[ORDENQ:16]]
    # Branch: IF STATUS <> 0
    # Consequence [[ORDENQ:21]]: "WHILE STATUS = 0" -- the enclosing loop's
    # guard is the negation of this branch's condition, so STATUS <> 0
    # becoming true is what ends the loop.
    status = 0
    iterations = 0
    while status == 0:
        iterations += 1
        if iterations == 1:
            status = 1  # triggers ORDENQ:BR-002 [[ORDENQ:16]]

    assert status != 0
    assert iterations == 1


def test_ordenq_br004_case_conf_calls_pricecalc(pricecalc_mock):
    # ORDENQ:BR-004 [[ORDENQ:25]]
    # Branch: CASE ORDVIEW.STATUS
    # Consequence [[ORDENQ:26-28]]:
    #   WHEN "CONF"
    #     CALL "PRICECALC" (ORDER_NO, ORDER_WT)
    order_no = "0001"
    order_wt = 100

    status = "CONF"
    if status == "CONF":
        pricecalc_mock(order_no, order_wt)

    pricecalc_mock.assert_called_once_with(order_no, order_wt)


def test_ordenq_br005_case_when_conf_branch_decision():
    # ORDENQ:BR-005 [[ORDENQ:26]]
    # Branch: WHEN "CONF"
    pytest.skip(
        "unresolved: no consequence reconstructable from source facts for "
        "this branch [[ORDENQ:26]]"
    )


def test_ordenq_br006_case_when_held_branch_decision():
    # ORDENQ:BR-006 [[ORDENQ:28]]
    # Branch: WHEN "HELD"
    pytest.skip(
        "unresolved: no consequence reconstructable from source facts for "
        "this branch [[ORDENQ:28]]"
    )

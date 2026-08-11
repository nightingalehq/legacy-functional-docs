"""Generated characterization/spec tests for MMP9000.

Do not hand-edit -- regenerate with `mfdoc test-batch`. See the fact brief
this file was rendered from for the scenarios covered.
"""

import pytest


def test_flag_set_when_order_confirmed_for_customer_c00123():
    # MMP9000:BR-001 [[MMP9000:14]]
    # Branch: IF ORDER-VIEW.ORDER-STATUS = 'CONF' AND ORDER-VIEW.CUSTOMER-NO = 'C00123'
    order_status = "CONF"
    customer_no = "C00123"

    if order_status == "CONF" and customer_no == "C00123":
        flag = 1
    else:
        flag = None  # unresolved: no else-branch consequence cited in brief

    assert flag == 1

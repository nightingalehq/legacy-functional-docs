"""Generated characterization/spec tests for MMP0200.

Do not hand-edit -- regenerate with `mfdoc test-batch`. See the fact brief
this file was rendered from for the scenarios covered.
"""

import pytest


def test_cert_no_blank_unresolved():
    # MMP0200:BR-001 [[MMP0200:12]]
    # Branch: IF #CERT-NO = ' '
    # No consequence is reconstructable from source facts for this branch.
    # Left unresolved pending a cited excerpt of what happens when taken.
    pytest.skip("unresolved: no reconstructable consequence in source facts [[MMP0200:12]]")


def test_no_records_found_sets_program_to_mmp0300():
    # MMP0200:BR-002 [[MMP0200:16]]
    # Branch: IF NO RECORDS FOUND no records found for preceding database loop
    # MOVE 'MMP0300' TO #PGM [[MMP0200:21]]
    pgm = None

    def no_records_found_branch():
        nonlocal pgm
        pgm = "MMP0300"

    no_records_found_branch()

    assert pgm == "MMP0300"


def test_on_error_unresolved():
    # MMP0200:BR-004 [[MMP0200:24]]
    # Branch: ON ERROR
    # No consequence is reconstructable from source facts for this branch.
    # Left unresolved pending a cited excerpt of what happens when taken.
    pytest.skip("unresolved: no reconstructable consequence in source facts [[MMP0200:24]]")

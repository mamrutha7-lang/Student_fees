"""The role-preference sheet: parsed in priority order, matched by USN."""
import pytest
from fastapi import HTTPException

from app.routers.admin_role_preferences import parse_preferences

HEADER = (
    "#", "Full Name", "Gender", "University at Number (USN)", "Mobile Number",
    "FIRST PREFERENCE ", " SECOND PREFERENCE ", "THIRD PREFERENCE ", "Other role",
)


def test_preferences_keep_the_sheet_order_and_upper_case_the_usn():
    rows = [
        HEADER,
        (1, "A", "F", "1mp25ba003 ", 1, "Management Trainee", " Market Research  Associate", "BDE", "n/a"),
    ]
    found, skipped = parse_preferences(rows)
    assert found == {"1MP25BA003": ["Management Trainee", "Market Research Associate", "BDE"]}
    assert skipped == 0


def test_a_row_without_a_usn_or_a_preference_is_skipped_and_a_repeat_drops_out():
    rows = [
        HEADER,
        (1, "A", "F", None, 1, "X", "Y", "Z", None),
        (2, "B", "M", "1MP25BA033", 2, None, None, None, None),
        (3, "C", "M", "1MP25BA034", 3, "X", "X", "Y", None),
        (None,) * 9,
    ]
    found, skipped = parse_preferences(rows)
    assert found == {"1MP25BA034": ["X", "Y"]}
    assert skipped == 2


def test_a_sheet_without_the_columns_is_refused():
    with pytest.raises(HTTPException):
        parse_preferences([("Name", "Email")])

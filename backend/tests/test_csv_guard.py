"""Tests for the shared CSV formula-injection guard (app/core/csv.py) used by every CSV export."""

import pytest

from app.core.csv import csv_safe, strip_csv_guard


@pytest.mark.parametrize("value", ['=HYPERLINK("http://evil","x")', "+1", "-1", "@SUM(A1)", "\tx", "\rx"])
def test_formula_cells_are_quoted_and_round_trip(value: str) -> None:
    assert csv_safe(value) == "'" + value
    assert strip_csv_guard(csv_safe(value)) == value


@pytest.mark.parametrize("value", ["", "plain text", "'quoted", "a=b"])
def test_other_cells_are_left_alone(value: str) -> None:
    assert csv_safe(value) == value
    assert strip_csv_guard(value) == value

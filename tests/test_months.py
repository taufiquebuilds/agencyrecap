from datetime import date

from clientreport.months import label, last_n, month_bounds, previous_month, shift, short_label


def test_shift_across_year():
    assert shift("2026-01", -1) == "2025-12"
    assert shift("2025-12", 1) == "2026-01"


def test_previous_month():
    assert previous_month(date(2026, 11, 3)) == "2026-10"
    assert previous_month(date(2026, 1, 15)) == "2025-12"


def test_month_bounds_leap_year():
    assert month_bounds("2028-02") == (date(2028, 2, 1), date(2028, 2, 29))


def test_last_n():
    months = last_n("2026-10", 13)
    assert len(months) == 13
    assert months[0] == "2025-10"
    assert months[-1] == "2026-10"


def test_labels():
    assert label("2026-10") == "October 2026"
    assert short_label("2026-10") == "Oct 26"

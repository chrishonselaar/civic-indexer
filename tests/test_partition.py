"""Date-bracketing subdivision (ported from OLD__universal-crawlexer) + leaf assignment."""

from datetime import datetime

from engine.partition import DateBracketingStrategy, month_leaf


def test_month_leaf():
    assert month_leaf("2026-06-15") == "2026-06"
    assert month_leaf(None) == "?"
    assert month_leaf("") == "?"


def test_no_split_when_under_cap():
    strategy = DateBracketingStrategy(max_items=500)
    ranges = strategy.generate_date_ranges(
        datetime(2024, 1, 1), datetime(2024, 12, 31), lambda a, b: 300
    )
    assert len(ranges) == 1


def test_splits_recursively_until_under_cap():
    strategy = DateBracketingStrategy(max_items=500)

    def count(a: datetime, b: datetime) -> int:
        # uniform density: 4 items/day => a year (~366d) claims ~1460, needs splitting
        return (b - a).days * 4

    ranges = strategy.generate_date_ranges(
        datetime(2024, 1, 1), datetime(2024, 12, 31), count
    )
    assert len(ranges) >= 3
    # every leaf fits under the cap
    assert all(count(a, b) <= 500 for a, b in ranges)
    # coverage: ranges are contiguous and ordered
    for (a1, b1), (a2, b2) in zip(ranges, ranges[1:]):
        assert b1 < a2


def test_min_days_stops_splitting():
    strategy = DateBracketingStrategy(max_items=1, min_days=1)
    ranges = strategy.generate_date_ranges(
        datetime(2024, 1, 1), datetime(2024, 1, 2), lambda a, b: 1000
    )
    # cannot subdivide below min_days: the overfull leaf is returned, not lost
    assert ranges

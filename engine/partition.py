"""Partitioning: leaf assignment + adaptive date-range subdivision.

DateBracketingStrategy ported from
OLD__universal-crawlexer/backend/app/core/pagination/date_bracketing.py
(de-asynced; crawl4ai helpers dropped). It is the engine's generic answer to
result caps: recursively split a date range until every leaf fits under the cap.
The open.overheid source has no server-side date windows, so Stage A exercises
this via fixtures only — it is the seam Stage B sources plug into.
"""

from datetime import datetime, timedelta
from typing import Callable


def month_leaf(published: str | None) -> str:
    """Client-side month-bucket assignment from a YYYY-MM-DD value."""
    if published and len(published) >= 7:
        return published[:7]
    return "?"


class DateBracketingStrategy:
    """Recursively split [start, end] until each range claims <= max_items."""

    def __init__(self, max_items: int = 500, min_days: int = 1):
        self.max_items = max_items
        self.min_days = min_days

    def generate_date_ranges(
        self,
        start_date: datetime,
        end_date: datetime,
        count_function: Callable[[datetime, datetime], int],
    ) -> list[tuple[datetime, datetime]]:
        return self._split(start_date, end_date, count_function)

    def _split(
        self,
        start_date: datetime,
        end_date: datetime,
        count_function: Callable[[datetime, datetime], int],
    ) -> list[tuple[datetime, datetime]]:
        days = (end_date - start_date).days
        if days < self.min_days:
            return [(start_date, end_date)]
        try:
            count = count_function(start_date, end_date)
        except Exception:
            # On error, keep the range whole; the crawl run will surface it.
            return [(start_date, end_date)]
        if count <= self.max_items:
            return [(start_date, end_date)]
        mid = start_date + (end_date - start_date) / 2
        return self._split(start_date, mid, count_function) + self._split(
            mid + timedelta(seconds=1), end_date, count_function
        )

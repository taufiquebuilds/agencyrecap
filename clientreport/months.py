"""Helpers for report periods written as "YYYY-MM"."""

import calendar
from datetime import date


def _parse(period: str) -> tuple[int, int]:
    year, month = period.split("-")
    return int(year), int(month)


def shift(period: str, n: int) -> str:
    year, month = _parse(period)
    index = year * 12 + (month - 1) + n
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def previous_month(today: date) -> str:
    return shift(f"{today.year:04d}-{today.month:02d}", -1)


def month_bounds(period: str) -> tuple[date, date]:
    year, month = _parse(period)
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def last_n(period: str, n: int) -> list[str]:
    return [shift(period, i) for i in range(-(n - 1), 1)]


def label(period: str) -> str:
    year, month = _parse(period)
    return f"{calendar.month_name[month]} {year}"


def short_label(period: str) -> str:
    year, month = _parse(period)
    return f"{calendar.month_abbr[month]} {str(year)[2:]}"

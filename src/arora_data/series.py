"""Series records: the table and key each named series reads, and whether its data is current."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from arora_data import _readers
from arora_data.catalog import _Row
from arora_data.dates import today_et

# Days since the latest point before a series counts as stale: one period plus the usual publishing lag.
# shortcut: guesses from prod ages on 2026-11-03 (daily p90 7, weekly p90 13, monthly median 94, quarterly
# median 125, annual median 671 days); tune per series once named series exist.
MAX_AGE_DAYS: dict[str, int] = {"daily": 10, "weekly": 21, "monthly": 125, "quarterly": 230, "annual": 1100}


@dataclass(frozen=True)
class Series:
    """One named series. frequency=None uses the data's own label, which only labelled tables carry.

    Set frequency when the label is missing or wrong (AAA and BAA are labelled daily but are monthly).
    """

    table: str
    key: str
    dataset: str | None = None
    frequency: str | None = None

    def __post_init__(self) -> None:
        _readers._filter(self.table, self.key, self.dataset)
        if self.frequency is None and self.table not in _readers._VALUE_TABLES:
            raise ValueError(f"{self.table} has no frequency column, so the series must set frequency")
        if self.frequency is not None and self.frequency not in MAX_AGE_DAYS:
            raise ValueError(f"frequency must be one of {sorted(MAX_AGE_DAYS)}")


class Freshness(_Row):
    latest_date: dt.date | None
    frequency: str | None
    age_days: int | None
    max_age_days: int | None
    stale: bool


async def _read(series: Series, start: dt.date, end: dt.date) -> list[_readers.PriceBar] | list[_readers.Value]:
    if series.table in _readers._PRICE_TABLES:
        return await _readers._read_prices(series.table, series.key, start, end)
    return await _readers._read_values(series.table, series.key, start, end, series.dataset)


async def check_fresh(series: Series, today: dt.date | None = None) -> Freshness:
    """Whether the series' latest point is within MAX_AGE_DAYS of today in US/Eastern. Source: the series' table.

    Stale when the series is empty (RLS-blocked tables look empty) or its frequency is unknown.
    A latest date in the future counts as current.
    """
    latest, label = await _readers._latest(series.table, series.key, series.dataset)
    frequency = series.frequency or label
    max_age = MAX_AGE_DAYS.get(frequency) if frequency else None
    age = ((today or today_et()) - latest).days if latest else None
    stale = age is None or max_age is None or age > max_age
    return Freshness(latest_date=latest, frequency=frequency, age_days=age, max_age_days=max_age, stale=stale)

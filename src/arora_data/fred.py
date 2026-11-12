"""FRED macro series, one value per date. Source: FRED, via macro.observations.

The series are whatever macro.observations holds (the main repo's FRED_SERIES), read from the table on every call.
fred_series() maps each series id to its name, units, frequency and category, taken from the series' latest row
(older rows are relabelled only by the main repo's Saturday full re-read). Rows are served as stored, except gaps:
- a NULL value is a real gap and is left out, never filled: market holidays on daily series, the October 2025 BLS
  report that was never published (2025-10-01), a few GASREGW weeks in 1990-91, UMCSENT's months between its
  quarterly surveys (1952-77);
- monthly and quarterly values are dated at the period's start; FYOINT (annual) at the fiscal year's end, 09-30
  (06-30 before 1976);
- IORB carries a value dated one day ahead, so a default end (today) leaves it out; THREEFYTP10 lags about three
  business days;
- not here: Treasury yields are treasury(); GDP and real GDP (A191RC, A191RX) are in indicators.bea; the headline
  unemployment rate (LNS14000000) and payrolls (CES0000000001) are in indicators.bls; derived metrics such as the
  monthly Fed funds rate (the month's mean of DFF, to 2 dp, complete months only), T10Y2Y and T5YIFR are computed
  from stored series, never stored.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal

from arora_data import _readers
from arora_data._db import connection, fetch
from arora_data.catalog import _Row
from arora_data.dates import today_et

_TABLE = "macro.observations"

# One index probe per series (a skip scan), then each one's first and latest values and its latest row's labels.
# The first row always has a value (the main repo drops FRED's leading gaps); the latest may be a holiday gap.
_SERIES_SQL = """
WITH RECURSIVE s AS (
    SELECT min(series_id) AS series_id FROM macro.observations
    UNION ALL
    SELECT (SELECT min(series_id) FROM macro.observations WHERE series_id > s.series_id) FROM s WHERE s.series_id IS NOT NULL
)
SELECT s.series_id, l.series_name AS name, l.units, l.frequency, l.category, f.date AS first_date, z.date AS latest_date
  FROM s
 CROSS JOIN LATERAL (SELECT series_name, units, frequency, category FROM macro.observations o
                      WHERE o.series_id = s.series_id ORDER BY date DESC LIMIT 1) l
  LEFT JOIN LATERAL (SELECT date FROM macro.observations o
                      WHERE o.series_id = s.series_id ORDER BY date LIMIT 1) f ON true
  LEFT JOIN LATERAL (SELECT date FROM macro.observations o
                      WHERE o.series_id = s.series_id AND value IS NOT NULL ORDER BY date DESC LIMIT 1) z ON true
 ORDER BY s.series_id
"""


class FredSeries(_Row):
    """One stored series. The dates are its first and latest with a value."""

    series_id: str
    name: str | None
    units: str | None
    frequency: str | None
    category: str | None
    first_date: dt.date | None
    latest_date: dt.date | None


class FredValue(_Row):
    """One series' value on one date, in its units."""

    series_id: str
    name: str | None
    date: dt.date
    value: Decimal
    units: str | None


_HINT = "FRED series: use an id or name from fred_series(), such as DFF or 'Effective Fed Funds Rate'"


async def fred_series() -> list[FredSeries]:
    """Every stored FRED series by id, with its name, units, frequency and category. Source: FRED, via
    macro.observations."""
    async with connection() as conn:
        return [FredSeries(**dict(r)) for r in await fetch(conn, _SERIES_SQL)]


async def fred(series: str, start: dt.date, end: dt.date | None = None) -> list[FredValue]:
    """One FRED series' values from start to end (default: today in US/Eastern), oldest first. Source: FRED.

    series is a stored series' id or name from fred_series(), such as "DFF" or "effective fed funds rate" (any case).
    Dates with no value are left out. An unknown series, or a name two series share, raises ValueError.
    """
    end = end or today_et()
    _readers._check_range(start, end)
    # Read the input as an id alongside the series list, so the usual call is one round trip; a name reads again.
    guess = _readers._key(series).upper() if isinstance(series, str) else ""
    stored, values = await asyncio.gather(fred_series(), _readers._read_values(_TABLE, guess, start, end))
    picked = _readers._resolve(series, stored, _HINT)
    if picked.series_id != guess:
        values = await _readers._read_values(_TABLE, picked.series_id, start, end)
    return [FredValue(series_id=picked.series_id, name=picked.name, date=v.date, value=v.value, units=picked.units)
            for v in values if v.value is not None]

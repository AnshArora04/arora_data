"""BLS labor and price series, one value per month. Source: BLS, via indicators.bls.

The series are whatever indicators.bls holds (the main repo's bls_core source, dataset bls/core), read from the table
on every call. bls_series() maps each series id to its name, units, frequency and category, taken from the series'
latest row. Values are as BLS publishes them, unscaled, in the units bls_series() gives (LNS14000000 is percent: 4.4
is 4.4%). Rows are served as stored, except gaps:
- each month is dated at its first day; BLS's annual averages are not stored;
- a NULL value is a real gap and is left out, never filled: BLS never published October 2025 (2025-10-01) for
  LNS14000000 and CUUR0000SA0, because of the shutdown;
- the main repo re-reads BLS's recent months every weekday, so revised values replace earlier ones;
- not here: seasonally adjusted CPI is fred("CPIAUCSL"), and U-6 unemployment is fred("U6RATE").
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from arora_data import _readers
from arora_data._db import connection, fetch
from arora_data.catalog import _Row
from arora_data.dates import today_et

_TABLE = "indicators.bls"

# Each series' latest row's labels, with its first and latest dates that have a value.
# shortcut: two full scans, fine at 4k rows; switch to fred.py's skip scan if the table passes ~1M rows.
_SERIES_SQL = """
SELECT series_id, dataset, l.name, l.units, l.frequency, l.category, d.first_date, d.latest_date
  FROM (SELECT DISTINCT ON (dataset, series_id) dataset, series_id, series_name AS name, units, frequency, category
          FROM indicators.bls WHERE provider = 'bls' ORDER BY dataset, series_id, date DESC) l
  JOIN (SELECT dataset, series_id, min(date) FILTER (WHERE value IS NOT NULL) AS first_date,
               max(date) FILTER (WHERE value IS NOT NULL) AS latest_date
          FROM indicators.bls WHERE provider = 'bls' GROUP BY dataset, series_id) d USING (dataset, series_id)
 ORDER BY series_id, dataset
"""

_HINT = "BLS series: use an id or name from bls_series(), such as LNS14000000 or 'Unemployment rate (SA)'"


class BlsSeries(_Row):
    """One stored series. The dates are its first and latest with a value."""

    series_id: str
    dataset: str
    name: str | None
    units: str | None
    frequency: str | None
    category: str | None
    first_date: dt.date | None
    latest_date: dt.date | None


class BlsValue(_Row):
    """One series' value for one month, in its units."""

    series_id: str
    name: str | None
    date: dt.date
    value: Decimal
    units: str | None


async def bls_series() -> list[BlsSeries]:
    """Every stored BLS series by id, with its name, units, frequency and category. Source: BLS, via
    indicators.bls."""
    async with connection() as conn:
        return [BlsSeries(**dict(r)) for r in await fetch(conn, _SERIES_SQL)]


async def bls(series: str, start: dt.date, end: dt.date | None = None) -> list[BlsValue]:
    """One BLS series' monthly values from start to end (default: today in US/Eastern), oldest first. Source: BLS.

    series is a stored series' id or name from bls_series(), such as "LNS14000000" or "unemployment rate (sa)" (any
    case). Months with no value are left out. An unknown series, or a name two series share, raises ValueError.
    """
    end = end or today_et()
    _readers._check_range(start, end)
    picked = _readers._resolve(series, await bls_series(), _HINT)
    values = await _readers._read_values(_TABLE, picked.series_id, start, end, picked.dataset)
    return [BlsValue(series_id=picked.series_id, name=picked.name, date=v.date, value=v.value, units=picked.units)
            for v in values if v.value is not None]

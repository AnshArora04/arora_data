"""New York Fed reference rates and the Fed's portfolio (SOMA) by holding. Source: Federal Reserve Bank of New York,
via indicators.nyfed.

The series are whatever indicators.nyfed holds (the main repo's three nyfed_* sources), read from the table on every
call. nyfed_series() maps each series id to its dataset, name, units, frequency and category, taken from the series'
latest row. Ids are the New York Fed's own, lowercase: "sofr", "total". Rows are served as stored, except gaps:
- nyfed/reference_rates: SOFR, TGCR and BGCR (secured) and EFFR and OBFR (unsecured), daily, in percent (3.91 is
  3.91%), each dated at the business day it applies to; holidays have no row, and the secured rates also skip days
  the bond market alone is closed (Good Friday 2026-04-03 has EFFR and OBFR, not SOFR). The main repo
  fetches only the latest 250 days, so stored history starts in June 2025 and grows from there;
- nyfed/soma_summary: the System Open Market Account's holdings at par, weekly, in USD millions, dated at the
  summary's as-of day (a Wednesday, or the day next to it around a holiday), from July 2003; the parts add up to
  "total";
- a NULL value is a week the summary leaves a holding blank, and is left out, never filled: "frn" before 2014-05-21
  (the Fed held no floating-rate notes before then), and "agencies" from 2003-12-24 to 2008-09-17;
- the main repo re-reads both on every run, so a revised value replaces the earlier one;
- not here: EFFR's longer history is fred("DFF").
"""
from __future__ import annotations

import datetime as dt

from arora_data import _readers

_TABLE = "indicators.nyfed"

# Each series' latest row's labels, with its first and latest dates that have a value.
# shortcut: two full scans, fine at 16k rows; switch to fred.py's skip scan if the table passes ~1M rows.
_SERIES_SQL = """
SELECT series_id, dataset, l.name, l.units, l.frequency, l.category, d.first_date, d.latest_date
  FROM (SELECT DISTINCT ON (dataset, series_id) dataset, series_id, series_name AS name, units, frequency, category
          FROM indicators.nyfed WHERE provider = 'nyfed' ORDER BY dataset, series_id, date DESC) l
  JOIN (SELECT dataset, series_id, min(date) FILTER (WHERE value IS NOT NULL) AS first_date,
               max(date) FILTER (WHERE value IS NOT NULL) AS latest_date
          FROM indicators.nyfed WHERE provider = 'nyfed' GROUP BY dataset, series_id) d USING (dataset, series_id)
 ORDER BY series_id, dataset
"""

_HINT = "New York Fed series: use an id or name from nyfed_series(), such as sofr or 'SOMA total holdings (par)'"


NyfedSeries = _readers.IndicatorSeries
NyfedValue = _readers.IndicatorValue


async def nyfed_series() -> list[NyfedSeries]:
    """Every stored New York Fed series by id, with its dataset, name, units, frequency and category. Source:
    Federal Reserve Bank of New York, via indicators.nyfed."""
    return await _readers._indicator_series(_SERIES_SQL)


async def nyfed(series: str, start: dt.date, end: dt.date | None = None) -> list[NyfedValue]:
    """One New York Fed series' values from start to end (default: today in US/Eastern), oldest first. Source:
    Federal Reserve Bank of New York.

    series is a stored series' id or name from nyfed_series(), such as "sofr" or "soma total holdings (par)" (any
    case). Periods with no value are left out. An unknown series, or a name two series share, raises ValueError.
    """
    return await _readers._indicator(_TABLE, nyfed_series, _HINT, series, start, end)

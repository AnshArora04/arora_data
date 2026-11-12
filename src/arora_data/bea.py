"""BEA national accounts series: GDP, its parts, incomes and price indexes. Source: BEA, via indicators.bea.

The series are whatever indicators.bea holds (the main repo's six bea_* sources, one dataset per NIPA table), read
from the table on every call. bea_series() maps each series id to its dataset, name, units, frequency and category,
taken from the series' latest row. Rows are served as stored:
- dollar levels are in USD millions (FMP served GDP in billions), real ones in chained USD millions; A191RL is a
  percent change at an annual rate (2.5 is 2.5%); price indexes are index levels;
- most series are quarterly, dated at the quarter's first day (2026Q3 is 2026-07-01); the PCE price indexes
  (DPCERG, DPCCRG) are monthly, dated at the month's first day;
- national income and corporate profits (A032RC, A051RC) are first published with BEA's second GDP estimate, about
  a month after GDP, so for that month they end one quarter earlier;
- only BEA's latest estimate is kept: the main repo re-reads every table's whole history each weekday, so a revision
  replaces the earlier value;
- a NULL value would be a real gap and is left out, never filled; none are stored today;
- not here: monthly nominal and real PCE are fred("PCE") and fred("PCEC96").
"""
from __future__ import annotations

import datetime as dt

from arora_data import _readers

_TABLE = "indicators.bea"

# Each series' latest row's labels, with its first and latest dates that have a value.
# shortcut: two full scans, fine at 8k rows; switch to fred.py's skip scan if the table passes ~1M rows.
_SERIES_SQL = """
SELECT series_id, dataset, l.name, l.units, l.frequency, l.category, d.first_date, d.latest_date
  FROM (SELECT DISTINCT ON (dataset, series_id) dataset, series_id, series_name AS name, units, frequency, category
          FROM indicators.bea WHERE provider = 'bea' ORDER BY dataset, series_id, date DESC) l
  JOIN (SELECT dataset, series_id, min(date) FILTER (WHERE value IS NOT NULL) AS first_date,
               max(date) FILTER (WHERE value IS NOT NULL) AS latest_date
          FROM indicators.bea WHERE provider = 'bea' GROUP BY dataset, series_id) d USING (dataset, series_id)
 ORDER BY series_id, dataset
"""

_HINT = "BEA series: use an id or name from bea_series(), such as A191RC or 'Gross domestic product'"


BeaSeries = _readers.IndicatorSeries
BeaValue = _readers.IndicatorValue


async def bea_series() -> list[BeaSeries]:
    """Every stored BEA series by id, with its dataset, name, units, frequency and category. Source: BEA, via
    indicators.bea."""
    return await _readers._indicator_series(_SERIES_SQL)


async def bea(series: str, start: dt.date, end: dt.date | None = None) -> list[BeaValue]:
    """One BEA series' values from start to end (default: today in US/Eastern), oldest first. Source: BEA.

    series is a stored series' id or name from bea_series(), such as "A191RC" or "gross domestic product" (any case).
    Periods with no value are left out. An unknown series, or a name two series share, raises ValueError.
    """
    return await _readers._indicator(_TABLE, bea_series, _HINT, series, start, end)

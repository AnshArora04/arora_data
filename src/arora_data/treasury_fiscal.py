"""US federal debt, daily: Treasury's Debt to the Penny. Source: US Treasury Fiscal Data, via indicators.treasury_fiscal.

The series are whatever indicators.treasury_fiscal holds (the main repo's treasury_debt_to_penny source, dataset
treasury/debt_to_penny), read from the table on every call. treasury_fiscal_series() maps each series id to its
dataset, name, units, frequency and category, taken from the series' latest row. Ids are Treasury's own field names:
"tot_pub_debt_out_amt" (total public debt outstanding), "debt_held_public_amt" (debt held by the public) and
"intragov_hold_amt" (intragovernmental holdings). Rows are served as stored, except gaps:
- values are in USD millions, Treasury's dollar figures divided by 1,000,000 (40709780.7 is about $40.7 trillion);
- each row is dated at Treasury's record date, a business day; weekends and federal holidays have no row;
- the total starts 1993-04-01. Its two parts are NULL there until Treasury split it out, and a NULL is left out,
  never filled: the parts have a value only at fiscal year end from 1997-09-30 to 2000-09-29, at month end from
  2001-09-28 to 2005-03-31 (no January 2003), and every day from 2005-04-04;
- the parts add up to the total, as Treasury publishes them, except 2025-08-04, where Treasury's own figures put
  the parts $10 billion above the total;
- the main repo re-reads the whole history on every run, so a revised value replaces the earlier one;
- not here: quarterly total public debt is fred("GFDEBTN").
"""
from __future__ import annotations

import datetime as dt

from arora_data import _readers

_TABLE = "indicators.treasury_fiscal"

# Each series' latest row's labels, with its first and latest dates that have a value.
# shortcut: two full scans, fine at 25k rows; switch to fred.py's skip scan if the table passes ~1M rows.
_SERIES_SQL = """
SELECT series_id, dataset, l.name, l.units, l.frequency, l.category, d.first_date, d.latest_date
  FROM (SELECT DISTINCT ON (dataset, series_id) dataset, series_id, series_name AS name, units, frequency, category
          FROM indicators.treasury_fiscal WHERE provider = 'treasury_fiscal' ORDER BY dataset, series_id, date DESC) l
  JOIN (SELECT dataset, series_id, min(date) FILTER (WHERE value IS NOT NULL) AS first_date,
               max(date) FILTER (WHERE value IS NOT NULL) AS latest_date
          FROM indicators.treasury_fiscal WHERE provider = 'treasury_fiscal' GROUP BY dataset, series_id) d
       USING (dataset, series_id)
 ORDER BY series_id, dataset
"""

_HINT = ("US Treasury fiscal series: use an id or name from treasury_fiscal_series(), such as tot_pub_debt_out_amt "
         "or 'Debt Held by the Public'")


TreasuryFiscalSeries = _readers.IndicatorSeries
TreasuryFiscalValue = _readers.IndicatorValue


async def treasury_fiscal_series() -> list[TreasuryFiscalSeries]:
    """Every stored US Treasury fiscal series by id, with its dataset, name, units, frequency and category. Source:
    US Treasury Fiscal Data, via indicators.treasury_fiscal."""
    return await _readers._indicator_series(_SERIES_SQL)


async def treasury_fiscal(series: str, start: dt.date, end: dt.date | None = None) -> list[TreasuryFiscalValue]:
    """One US Treasury fiscal series' daily values from start to end (default: today in US/Eastern), oldest first.
    Source: US Treasury Fiscal Data.

    series is a stored series' id or name from treasury_fiscal_series(), such as "tot_pub_debt_out_amt" or "debt
    held by the public" (any case). Days with no value are left out. An unknown series, or a name two series share,
    raises ValueError.
    """
    return await _readers._indicator(_TABLE, treasury_fiscal_series, _HINT, series, start, end)

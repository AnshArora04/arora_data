"""US repo rates and volume, and money market fund assets: the OFR Short-term Funding Monitor. Source: Office of
Financial Research (OFR), US Treasury, via indicators.ofr.

The series are whatever indicators.ofr holds (the main repo's ofr_stfm source, dataset ofr/short_term_funding), read
from the table on every call. ofr_series() maps each series id to its dataset, name, units, frequency and category,
taken from the series' latest row. Ids are OFR's own mnemonics, such as "REPO-TRI_AR_OO-P" (tri-party repo average
rate, overnight). Rows are served as stored, except gaps:
- repo rates (DVP, GCF and tri-party) are daily, in percent (3.91 is 3.91%), each dated at the business day OFR
  dates it; both tri-party series come out a business day after DVP and GCF, so their latest day is one behind;
  DVP went below zero on 94 days from 2020-03-24 to 2022-03-14, as OFR publishes it;
- tri-party repo volume is daily and money market fund assets monthly, dated at the month's last calendar day; both
  are in USD millions, OFR's dollar figures divided by 1,000,000 (3951744.1 is about $3.95 trillion);
- a NULL value is a day OFR published no value, because the day failed its disclosure threshold, and is left out,
  never filled: DVP has 96 such days from 2018-05-07 to 2025-02-06. Tri-party's rate also has no row on 2022-06-17,
  a day its volume has;
- the main repo re-reads each series' whole history on every run, so OFR's revisions to its preliminary values
  replace the earlier ones;
- not here: SOFR, TGCR and BGCR, the New York Fed's repo reference rates, are nyfed().
"""
from __future__ import annotations

import datetime as dt

from arora_data import _readers

# OFR asks for credit when its data is reproduced, in the form it suggests: Office of Financial Research, "OFR
# Short-term Funding Monitor," refreshed daily, https://www.financialresearch.gov/short-term-funding-monitor/
# (accessed <date>). Its disclaimer: "This OFR monitor is presented solely for informative purposes and should not be
# relied upon for financial decisions; it is not intended to provide any investment or financial advice." Its repo
# rates are "not intended to be referenced in contracts." arora_data is not affiliated with or endorsed by the OFR.
# These stay here and in the notebook's text, never in what the functions return.

_TABLE = "indicators.ofr"

# Each series' latest row's labels, with its first and latest dates that have a value.
# shortcut: two full scans, fine at 11k rows; switch to fred.py's skip scan if the table passes ~1M rows.
_SERIES_SQL = """
SELECT series_id, dataset, l.name, l.units, l.frequency, l.category, d.first_date, d.latest_date
  FROM (SELECT DISTINCT ON (dataset, series_id) dataset, series_id, series_name AS name, units, frequency, category
          FROM indicators.ofr WHERE provider = 'ofr' ORDER BY dataset, series_id, date DESC) l
  JOIN (SELECT dataset, series_id, min(date) FILTER (WHERE value IS NOT NULL) AS first_date,
               max(date) FILTER (WHERE value IS NOT NULL) AS latest_date
          FROM indicators.ofr WHERE provider = 'ofr' GROUP BY dataset, series_id) d USING (dataset, series_id)
 ORDER BY series_id, dataset
"""

_HINT = "OFR series: use an id or name from ofr_series(), such as REPO-TRI_AR_OO-P or 'GCF Repo — Avg Rate, All'"


OfrSeries = _readers.IndicatorSeries
OfrValue = _readers.IndicatorValue


async def ofr_series() -> list[OfrSeries]:
    """Every stored OFR series by id, with its dataset, name, units, frequency and category. Source: Office of
    Financial Research, via indicators.ofr."""
    return await _readers._indicator_series(_SERIES_SQL)


async def ofr(series: str, start: dt.date, end: dt.date | None = None) -> list[OfrValue]:
    """One OFR series' values from start to end (default: today in US/Eastern), oldest first. Source: Office of
    Financial Research.

    series is a stored series' id or name from ofr_series(), such as "REPO-TRI_AR_OO-P" or "gcf repo — avg rate,
    all" (any case). Days with no value are left out. An unknown series, or a name two series share, raises
    ValueError.
    """
    return await _readers._indicator(_TABLE, ofr_series, _HINT, series, start, end)

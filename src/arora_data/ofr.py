"""US short-term funding markets and financial stress, from the OFR Short-term Funding Monitor and the OFR Financial
Stress Index. Source: Office of Financial Research (OFR), US Treasury, via indicators.ofr.

The series are the series that indicators.ofr holds. The main repo's ofr_* sources write them. Each call reads the
table. ofr_series() gives the dataset, name, units, frequency and category of each series id. These labels come from
the latest row of the series. The names are the OFR names. The ids are the OFR mnemonics, for example
"REPO-TRI_AR_OO-P" (tri-party repo average rate, overnight). The stress index ids start with OFR_FSI.

The category is the OFR dataset of the series:
- ofr/short_term_funding has repo (DVP, GCF and tri-party repo rates and volumes, daily) and mmf (money market fund
  holdings, monthly). It also has nypd (primary dealer statistics, weekly), fnyr (the New York Fed reference rates,
  with their percentiles and volumes, daily) and tyld (Treasury constant maturity yields, daily).
- ofr/financial_stress_index has the category fsi: the OFR Financial Stress Index and its 8 parts (5 categories, 3
  regions). It is daily from 2000-01-03, in units "index". A value of 0 is average stress. A value above 0 is more
  stress.

The functions give the rows as the table stores them, with these rules:
- Dollar series are in USD millions: the OFR dollar value divided by 1,000,000. 3951744.1 is approximately $3.95
  trillion. All other series have the OFR value and the OFR unit name in lower case ("percent": 3.91 is 3.91%).
- Each repo value has the business day that OFR gives it. The tri-party series come one business day after DVP and
  GCF, thus their latest day is one day behind. DVP was below zero on 94 days from 2020-03-24 to 2022-03-14. That
  is the OFR value.
- A NULL value is a day with no OFR value: no trades, or the day failed the OFR disclosure threshold. The functions
  do not give these days, and do not fill them. The DVP overnight rate has 96 such days, from 2018-05-07 to
  2025-02-06.
- A series with no value on any day has first_date and latest_date None. On 2026-11-25, 7 series had no value,
  for example NYPD-PD_SB_TOT-A.
- The main repo reads the full history of each dataset on each run. Thus the OFR revisions replace the earlier
  values.
- The table also holds the Hedge Fund Monitor (dataset ofr/hedge_fund_monitor). The functions do not give it until
  a person checks the terms of its SEC, CFTC, Fed and FICC sources.
- fnyr and tyld are the OFR copies. nyfed() gives the reference rates from the New York Fed. treasury() gives the
  yields from Treasury.
"""
from __future__ import annotations

import datetime as dt

from arora_data import _readers

# OFR asks for credit when a person copies its data. Use the OFR citation: Office of Financial Research, "OFR
# Short-term Funding Monitor," refreshed daily, https://www.financialresearch.gov/short-term-funding-monitor/
# (accessed <date>). Use the same form for the "OFR Financial Stress Index,"
# https://www.financialresearch.gov/financial-stress-index/. The OFR disclaimer: "This OFR monitor is presented solely
# for informative purposes and should not be relied upon for financial decisions; it is not intended to provide any
# investment or financial advice." The OFR repo rates are "not intended to be referenced in contracts."
# The OFR data (repo, mmf and the stress index) is public domain. Some data is not from OFR. nypd is New York Fed data,
# under the New York Fed terms of use. fnyr is under the New York Fed Reference Rates Terms of Use. tyld is Treasury
# data, public domain. arora_data has no affiliation with the OFR or the New York Fed, and they do not endorse it.
# This text stays in these comments and in the notebook text. The functions never give it in their rows.

_TABLE = "indicators.ofr"

# The labels of the latest row of each series, and the first and latest dates that have a value. A skip scan
# finds each (dataset, series_id) pair with one index probe, thus the query does not read all rows of the table.
# The query does not include the Hedge Fund Monitor. The docstring gives the reason.
_SERIES_SQL = """
WITH RECURSIVE s AS (
    (SELECT dataset, series_id FROM indicators.ofr WHERE provider = 'ofr' ORDER BY dataset, series_id LIMIT 1)
    UNION ALL
    SELECT n.dataset, n.series_id FROM s
     CROSS JOIN LATERAL (SELECT dataset, series_id FROM indicators.ofr o
                          WHERE o.provider = 'ofr' AND (o.dataset, o.series_id) > (s.dataset, s.series_id)
                          ORDER BY dataset, series_id LIMIT 1) n
)
SELECT s.series_id, s.dataset, l.series_name AS name, l.units, l.frequency, l.category,
       f.date AS first_date, z.date AS latest_date
  FROM s
 CROSS JOIN LATERAL (SELECT series_name, units, frequency, category FROM indicators.ofr o
                      WHERE o.provider = 'ofr' AND o.dataset = s.dataset AND o.series_id = s.series_id
                      ORDER BY date DESC LIMIT 1) l
  LEFT JOIN LATERAL (SELECT date FROM indicators.ofr o
                      WHERE o.provider = 'ofr' AND o.dataset = s.dataset AND o.series_id = s.series_id
                        AND value IS NOT NULL ORDER BY date LIMIT 1) f ON true
  LEFT JOIN LATERAL (SELECT date FROM indicators.ofr o
                      WHERE o.provider = 'ofr' AND o.dataset = s.dataset AND o.series_id = s.series_id
                        AND value IS NOT NULL ORDER BY date DESC LIMIT 1) z ON true
 WHERE s.dataset <> 'ofr/hedge_fund_monitor'
 ORDER BY s.series_id, s.dataset
"""

_HINT = "OFR series: use an id or name from ofr_series(), such as OFR_FSI or 'OFR Financial Stress Index'"


OfrSeries = _readers.IndicatorSeries
OfrValue = _readers.IndicatorValue


async def ofr_series() -> list[OfrSeries]:
    """Each OFR series by id, with its dataset, name, units, frequency and category. It does not include the Hedge
    Fund Monitor. Source: Office of Financial Research, via indicators.ofr."""
    return await _readers._indicator_series(_SERIES_SQL)


async def ofr(series: str, start: dt.date, end: dt.date | None = None) -> list[OfrValue]:
    """The values of one OFR series from start to end (default: today in US/Eastern), oldest first. Source: Office of
    Financial Research.

    series is the id or the name of a series from ofr_series(), for example "REPO-TRI_AR_OO-P" or "ofr financial
    stress index" (any case). The function does not give days with no value. An unknown series, or a name of two
    series, raises ValueError.
    """
    return await _readers._indicator(_TABLE, ofr_series, _HINT, series, start, end)

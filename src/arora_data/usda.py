"""US crop and livestock production, yields and prices, from the USDA National Agricultural Statistics Service (NASS).
Source: USDA NASS Quick Stats, via indicators.usda.

The series are the series that indicators.usda holds. The main repo's usda source writes them. Each call reads the
table, thus a new series needs no change here. usda_series() gives the dataset, name, units, frequency and category of
each series id. These labels come from the latest row of the series. The ids are the NASS "short_desc" text, for
example "CORN, GRAIN - PRICE RECEIVED, MEASURED IN $ / BU". The names are shorter, for example "Corn price received".

The datasets are one for each commodity: usda/corn, usda/soybeans, usda/wheat, usda/cotton, usda/cattle, usda/hogs
and usda/milk. usda_series(dataset=..., frequency=...) gives only the series of one dataset or one frequency.

The functions give the rows as the table stores them, with these rules:
- All the values are US national values, from the NASS surveys.
- All the series are annual, in the NASS calendar-year form (NASS reference period "YEAR"). A value has the first
  day of its year: 2025 is 2025-01-01. A price is the average of the calendar year, not of the marketing year.
- The values are in the NASS units, for example "$/BU", "$/CWT" (dollars per hundredweight), "$/LB", "BU",
  "BU/ACRE", "LB", "LB/ACRE", "480 LB BALES" and "HEAD".
- The calendar-year prices start in 1996, and the hog price in 1988. NASS has no calendar-year price before that.
- "HOGS - INVENTORY" has only 2 values (2024 and 2025), because NASS gives the calendar-year form only for those
  years.
- The values of the current year are NASS estimates. NASS revises them, and the main repo then writes over the earlier
  values. The table does not keep the earlier values.
- A NULL value is a year with no NASS value. The functions do not give these years, and do not fill them.
- The functions do not give the geo column.
- The main repo can add more series, datasets and frequencies (for example monthly prices). The functions read them
  with no change here, and usda_series(frequency=...) then keeps one frequency.
"""
from __future__ import annotations

import datetime as dt

from arora_data import _readers

# The NASS API terms ask each service that uses the API to show this notice: "This product uses the NASS API but is
# not endorsed or certified by NASS." (https://quickstats.nass.usda.gov/api). NASS asks for an acknowledgment of
# USDA-NASS as the source. Most NASS data is public domain. arora_data has no affiliation with USDA or NASS.
# This text stays in these comments and in the notebook text. The functions never give it in their rows.

_TABLE = "indicators.usda"

# The labels of the latest row of each series, and the first and latest dates that have a value. The table is
# small (1,570 rows on 2026-11-27), thus one pass over it is fast.
_SERIES_SQL = """
SELECT series_id, dataset, l.name, l.units, l.frequency, l.category, d.first_date, d.latest_date
  FROM (SELECT DISTINCT ON (dataset, series_id) dataset, series_id, series_name AS name, units, frequency, category
          FROM indicators.usda WHERE provider = 'usda' ORDER BY dataset, series_id, date DESC) l
  JOIN (SELECT dataset, series_id, min(date) FILTER (WHERE value IS NOT NULL) AS first_date,
               max(date) FILTER (WHERE value IS NOT NULL) AS latest_date
          FROM indicators.usda WHERE provider = 'usda' GROUP BY dataset, series_id) d USING (dataset, series_id)
 ORDER BY series_id, dataset
"""

_HINT = "USDA series: use an id or name from usda_series(), such as 'Corn price received'"


UsdaSeries = _readers.IndicatorSeries
UsdaValue = _readers.IndicatorValue


async def usda_series(dataset: str | None = None, frequency: str | None = None) -> list[UsdaSeries]:
    """Each USDA NASS series by id, with its dataset, name, units, frequency and category. Source: USDA NASS Quick
    Stats, via indicators.usda.

    dataset (for example "usda/corn") and frequency (now only "annual") keep only the series that have that value. A
    value that no stored series has raises ValueError.
    """
    stored = await _readers._indicator_series(_SERIES_SQL)
    picked = stored
    for field, want in (("dataset", dataset), ("frequency", frequency)):
        if want is None:
            continue
        # The values of all the series, thus a frequency that the dataset does not have gives [], not an error.
        known = sorted({getattr(s, field) for s in stored} - {None})
        if want not in known:
            raise ValueError(f"{want!r} is not a stored USDA {field}: use one of {', '.join(known)}")
        picked = [s for s in picked if getattr(s, field) == want]
    return picked


async def usda(series: str, start: dt.date, end: dt.date | None = None) -> list[UsdaValue]:
    """The values of one USDA NASS series from start to end (default: today in US/Eastern), oldest first. Source: USDA
    NASS Quick Stats.

    series is the id or the name of a series from usda_series(), for example "CORN, GRAIN - PRICE RECEIVED, MEASURED
    IN $ / BU" or "corn price received" (any case). An annual value has the first day of its year. The function does
    not give years with no value. An unknown series, or a name of two series, raises ValueError.
    """
    return await _readers._indicator(_TABLE, usda_series, _HINT, series, start, end)

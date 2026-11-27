"""US energy prices, production, petroleum stocks and natural gas storage, from the U.S. Energy Information
Administration (EIA). Source: EIA, via indicators.eia.

The series are the series that indicators.eia holds. The main repo's eia_* sources write them. Each call reads the
table, thus a new series needs no change here. eia_series() gives the dataset, name, units, frequency and category of
each series id. These labels come from the latest row of the series. The ids are the EIA series ids, for example
"RWTC" (WTI crude spot, Cushing) or "WCESTUS1" (US crude oil stocks excluding the SPR).

The datasets are the EIA routes that the main repo reads:
- monthly: eia/petroleum_spot (crude and product spot prices), eia/natgas (Henry Hub spot) and eia/crude_production.
- weekly: eia/weekly_petroleum_stocks, eia/weekly_refinery_utilization and eia/weekly_product_supplied (from the
  Weekly Petroleum Status Report), and eia/weekly_gas_storage (Lower 48 and its 5 regions).
eia_series(dataset=..., frequency=...) gives only the series of one dataset or one frequency.

The functions give the rows as the table stores them, with these rules:
- The values are in the EIA units, for example "$/BBL", "$/GAL", "$/MMBtu", "MBBL" (thousand barrels), "MBBL/D"
  (thousand barrels per day), "BCF" (billion cubic feet) and "%".
- A monthly value has the first day of its month: 2026-10 is 2026-10-01.
- A weekly value has the last day of its week, as EIA gives it.
- A NULL value is a period with no EIA value. The functions do not give these periods, and do not fill them.
- The main repo reads the full history on each run. Thus the EIA revisions replace the earlier values.
- The functions do not give the geo column.
- Other tables hold some EIA data. fred() gives the FRED copies, for example DCOILWTICO (daily WTI) and DHHNGSP
  (daily Henry Hub). commodities() gives the futures. eia() reads only indicators.eia.
"""
from __future__ import annotations

import datetime as dt

from arora_data import _readers

# EIA data is public domain (a US government work). EIA asks for an acknowledgment with the publication date, for
# example: "Source: U.S. Energy Information Administration (Oct 2008)". The EIA reuse page is
# https://www.eia.gov/about/copyrights_reuse.php. arora_data has no affiliation with EIA, and EIA does not endorse it.
# This text stays in these comments and in the notebook text. The functions never give it in their rows.

_TABLE = "indicators.eia"

# The labels of the latest row of each series, and the first and latest dates that have a value. The table is
# small (3,985 rows on 2026-11-26, approximately 25,000 with the weekly series), thus one pass over it is fast.
_SERIES_SQL = """
SELECT series_id, dataset, l.name, l.units, l.frequency, l.category, d.first_date, d.latest_date
  FROM (SELECT DISTINCT ON (dataset, series_id) dataset, series_id, series_name AS name, units, frequency, category
          FROM indicators.eia WHERE provider = 'eia' ORDER BY dataset, series_id, date DESC) l
  JOIN (SELECT dataset, series_id, min(date) FILTER (WHERE value IS NOT NULL) AS first_date,
               max(date) FILTER (WHERE value IS NOT NULL) AS latest_date
          FROM indicators.eia WHERE provider = 'eia' GROUP BY dataset, series_id) d USING (dataset, series_id)
 ORDER BY series_id, dataset
"""

_HINT = "EIA series: use an id or name from eia_series(), such as RWTC or 'WTI crude spot (Cushing)'"


EiaSeries = _readers.IndicatorSeries
EiaValue = _readers.IndicatorValue


async def eia_series(dataset: str | None = None, frequency: str | None = None) -> list[EiaSeries]:
    """Each EIA series by id, with its dataset, name, units, frequency and category. Source: U.S. Energy Information
    Administration, via indicators.eia.

    dataset (for example "eia/weekly_gas_storage") and frequency ("monthly" or "weekly") keep only the series that
    have that value. A value that no stored series has raises ValueError.
    """
    stored = await _readers._indicator_series(_SERIES_SQL)
    picked = stored
    for field, want in (("dataset", dataset), ("frequency", frequency)):
        if want is None:
            continue
        # The values of all the series, thus a frequency that the dataset does not have gives [], not an error.
        known = sorted({getattr(s, field) for s in stored} - {None})
        if want not in known:
            raise ValueError(f"{want!r} is not a stored EIA {field}: use one of {', '.join(known)}")
        picked = [s for s in picked if getattr(s, field) == want]
    return picked


async def eia(series: str, start: dt.date, end: dt.date | None = None) -> list[EiaValue]:
    """The values of one EIA series from start to end (default: today in US/Eastern), oldest first. Source: U.S.
    Energy Information Administration.

    series is the id or the name of a series from eia_series(), for example "RWTC" or "working gas in storage, lower
    48" (any case). The function does not give periods with no value. An unknown series, or a name of two series,
    raises ValueError.
    """
    return await _readers._indicator(_TABLE, eia_series, _HINT, series, start, end)

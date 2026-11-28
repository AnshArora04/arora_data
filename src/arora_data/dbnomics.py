"""International macro series (OECD, Eurostat, BIS, AMECO, World Bank and ECB data), from DBnomics. Source: DBnomics,
via indicators.dbnomics.

The series are the series that indicators.dbnomics holds. The main repo's dbnomics source writes them. Each call reads
the table, thus a series that the main repo adds or removes needs no change here. dbnomics_series() gives the dataset,
name, units, frequency, category, country and stale flag of each series id. These labels come from the latest row of
the series. The ids are the DBnomics series codes, for example "USA.M.LI.IX._Z.AA.IX._Z.H" (the OECD composite leading
indicator of the United States). The datasets are "<provider>/<dataset>", for example "OECD/DSD_STES@DF_CLI" or
"ECB/HICP". The names are the long DBnomics names.

The functions give the rows as the table stores them, with these rules:
- The values are in the units of the table, for example "percent", "index" or "percentage points".
- A value has the first day of its period: 2026-09 is 2026-09-01, and 2026-Q2 is 2026-04-01. A daily value has its day.
- A NULL value is a period with no value at DBnomics ("NA"). The functions do not give these periods, and do not fill
  them.
- The main repo reads the full history of each series on each run. Thus the revisions replace the earlier values, and
  the table does not keep the earlier values.
- The AMECO values from the current year on are forecasts of the European Commission, not outturns. Each AMECO release
  writes over them. Some are dated after today: give an end after today to get them.
- country is the area code in the series code, as the provider writes it. It is not ISO for every provider: for
  example "USA" (OECD), "DE" or "XM" (BIS, XM is the euro area), "EA20" (Eurostat, AMECO), "U2" or "I10" (ECB, the
  euro area) and "WLD" (World Bank, the world). It can be a group, for example "G7" or "OECD". For an ECB exchange
  rate it is the currency ("EUR"). A dataset that _AREA does not name gives None. The geo column of the table is NULL.
- stale is True when today (US/Eastern) is after the latest date with a value plus 2 periods of the series' frequency
  plus 120 days. The 120 days allow for the time from the end of a period to its publication. Many series stop at
  DBnomics when DBnomics stops its reads of a provider (on 2026-11-27: OECD, Eurostat, BIS and the World Bank). stale
  is None for a series with no value or with a frequency that is not daily, weekly, monthly, quarterly or annual.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
from typing import cast

from arora_data import _readers
from arora_data.dates import today_et

# DBnomics data keeps the licence of its original provider. DBnomics' own aggregated datasets are under the Open
# Database License (ODbL), which asks for attribution ("Retrieved via DBnomics") and is share-alike for a derived
# database that is made public (https://docs.db.nomics.world/legal-terms/). The terms of each provider:
# - OECD: CC BY 4.0 (OECD terms similar to CC BY 4.0 for content from before 2024-07-01), with attribution.
# - Eurostat: free reuse, commercial use included, with the source acknowledged.
# - BIS: free use when BIS is cited and the use does not mislead or imply endorsement. Commercial use is permitted
#   only when a product does not charge its users more because the BIS data is in it
#   (https://www.bis.org/terms_statistics.htm).
# - AMECO (European Commission): free reuse, commercial use included (Commission Decision 2011/833/EU).
# - World Bank WDI: CC BY 4.0.
# - ECB: free reuse, commercial use included, with the source quoted and the data not changed.
# arora_data has no affiliation with DBnomics or with these providers, and they do not endorse it. This text stays in
# these comments and in the notebook text. The functions never give it in their rows.

_TABLE = "indicators.dbnomics"

# The labels of the latest row of each series, and the first and latest dates that have a value. The table is
# small (36,602 rows on 2026-11-27), thus one pass over it is fast.
_SERIES_SQL = """
SELECT series_id, dataset, l.name, l.units, l.frequency, l.category, d.first_date, d.latest_date
  FROM (SELECT DISTINCT ON (dataset, series_id) dataset, series_id, series_name AS name, units, frequency, category
          FROM indicators.dbnomics WHERE provider = 'dbnomics' ORDER BY dataset, series_id, date DESC) l
  JOIN (SELECT dataset, series_id, min(date) FILTER (WHERE value IS NOT NULL) AS first_date,
               max(date) FILTER (WHERE value IS NOT NULL) AS latest_date
          FROM indicators.dbnomics WHERE provider = 'dbnomics' GROUP BY dataset, series_id) d USING (dataset, series_id)
 ORDER BY series_id, dataset
"""

_HINT = "DBnomics series: use an id or name from dbnomics_series(), such as USA.M.LI.IX._Z.AA.IX._Z.H"

# The place of the area in the series code, split at "." and "-", for a dataset or, else, for its provider. These
# are the dimension orders of the provider's datasets.
_AREA = {
    "OECD": 0,
    "AMECO": 0,
    "Eurostat": -1,
    "WB": -1,
    "BIS": 1,
    "BIS/WS_EER": -1,
    "ECB": 1,
    "ECB/MNA": 2,
    "ECB/EXR": 2,
}

# The length of one period: (months, days).
_PERIOD = {"daily": (0, 1), "weekly": (0, 7), "monthly": (1, 0), "quarterly": (3, 0), "annual": (12, 0)}
_PUBLICATION_DAYS = 120


class DbnomicsSeries(_readers.IndicatorSeries):
    """One stored DBnomics series, with its area code and whether its latest value is old."""

    country: str | None
    stale: bool | None


DbnomicsValue = _readers.IndicatorValue


def _country(dataset: str, series_id: str) -> str | None:
    place = _AREA.get(dataset, _AREA.get(dataset.partition("/")[0]))
    if place is None:
        return None
    parts = re.split(r"[.-]", series_id)
    return parts[place] if place < len(parts) else None


def _add_months(day: dt.date, months: int) -> dt.date:
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    return dt.date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def _stale(latest: dt.date | None, frequency: str | None, today: dt.date) -> bool | None:
    if latest is None or frequency not in _PERIOD:
        return None
    months, days = _PERIOD[frequency]
    deadline = _add_months(latest, 2 * months) + dt.timedelta(days=2 * days + _PUBLICATION_DAYS)
    return today > deadline


async def dbnomics_series(dataset: str | None = None, frequency: str | None = None) -> list[DbnomicsSeries]:
    """Each DBnomics series by id, with its dataset, name, units, frequency, category, country and stale flag. Source:
    DBnomics, via indicators.dbnomics.

    dataset (for example "ECB/HICP") and frequency (for example "monthly") keep only the series that have that value.
    A value that no stored series has raises ValueError. The module docstring gives the country and stale rules.
    """
    today = today_et()
    stored: list[_readers.IndicatorSeries] = [
        DbnomicsSeries(**s.model_dump(), country=_country(s.dataset, s.series_id),
                       stale=_stale(s.latest_date, s.frequency, today))
        for s in await _readers._indicator_series(_SERIES_SQL)]
    # _select gives back items it got, thus each is a DbnomicsSeries. No PEP 695 generic: requires-python is >=3.11.
    return cast(list[DbnomicsSeries], _readers._select(stored, "DBnomics", dataset, frequency))


async def dbnomics(series: str, start: dt.date, end: dt.date | None = None) -> list[DbnomicsValue]:
    """The values of one DBnomics series from start to end (default: today in US/Eastern), oldest first. Source:
    DBnomics, with the data of its original provider (OECD, Eurostat, BIS, European Commission, World Bank or ECB).

    series is the id or the name of a series from dbnomics_series(), for example "USA.M.LI.IX._Z.AA.IX._Z.H" (any
    case). The function does not give periods with no value. It does not check stale: dbnomics_series() gives it. An
    unknown series, or a name of two series, raises ValueError.
    """
    return await _readers._indicator(_TABLE, dbnomics_series, _HINT, series, start, end)

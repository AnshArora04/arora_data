"""Daily US Treasury par yields, in percent, by tenor. Source: FMP, via macro.treasury_rates.

The table holds one row per day with a column per tenor; treasury() serves one row per tenor per day, and a tenor
with no value that day is left out. Rows are served as stored:
- tenors start or pause with Treasury's own history: 1M from 2001-07-31, 2M from 2018-10-16, 20Y from 1993-10-01,
  and no 30Y between 2002-02-15 and 2006-02-09;
- the one weekend row, Sat 2001-03-31, is an FMP placeholder with an inverted curve (10Y 4.14 against 4.93 the day
  before) and the only 1M and 2M values before July 2001;
- 2010-10-11 (Columbus Day) has no values, so it never appears.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from arora_data import _readers
from arora_data._db import connection, fetch
from arora_data.catalog import _Row
from arora_data.dates import today_et

ALL = "ALL"

# Tenor: its column, in maturity order.
TENORS: dict[str, str] = {
    "1M": "month1", "2M": "month2", "3M": "month3", "6M": "month6",
    "1Y": "year1", "2Y": "year2", "3Y": "year3", "5Y": "year5", "7Y": "year7", "10Y": "year10", "20Y": "year20",
    "30Y": "year30",
}
_BY_COLUMN = {column.upper(): tenor for tenor, column in TENORS.items()}

_SQL = """
SELECT date, month1, month2, month3, month6, year1, year2, year3, year5, year7, year10, year20, year30
  FROM macro.treasury_rates
 WHERE date BETWEEN $1 AND $2
 ORDER BY date
"""


class TreasuryRate(_Row):
    """One tenor's par yield on one day, in percent (4.86 is 4.86%)."""

    tenor: str
    date: dt.date
    yield_pct: Decimal


async def _rows(start: dt.date, end: dt.date) -> list:
    async with connection() as conn:
        return await fetch(conn, _SQL, start, end)


def _resolve(tenor: str) -> str:
    key = "".join(tenor.split()).upper()
    if key in TENORS:
        return key
    if key in _BY_COLUMN:
        return _BY_COLUMN[key]
    raise ValueError(f"{tenor!r} is not a tenor: use one of {', '.join(TENORS)}")


async def treasury(start: dt.date, end: dt.date | None = None, tenor: str | None = None) -> list[TreasuryRate]:
    """Daily par yields from start to end (default: today in US/Eastern). Source: FMP.

    tenor is one of TENORS, such as "10Y" or "3M" (any case), or its column name, such as "year10". None or "ALL"
    gives every tenor, ordered by maturity then date; one tenor is ordered by date. A bad tenor raises ValueError.
    """
    end = end or today_et()
    _readers._check_range(start, end)
    if not isinstance(tenor, str | None):
        raise ValueError(f"{tenor!r} is not a tenor: use one of {', '.join(TENORS)}")
    picked = list(TENORS) if tenor is None or tenor.strip().upper() == ALL else [_resolve(tenor)]
    rows = await _rows(start, end)
    return [TreasuryRate(tenor=t, date=r["date"], yield_pct=r[TENORS[t]])
            for t in picked for r in rows if r[TENORS[t]] is not None]

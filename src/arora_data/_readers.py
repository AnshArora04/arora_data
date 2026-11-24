"""The two readers every series function calls, one for daily price bars and one for single values, the
id-or-name lookup fred(), bls(), bea() and nyfed() share, and the series list and read every indicators.* function shares.

Private: apps call the named series functions, never these. Table and column names come only
from the constants below; caller input reaches SQL only as $n parameters.
"""
from __future__ import annotations

import datetime as dt
from datetime import date
from decimal import Decimal

from arora_data._db import connection, fetch
from arora_data.catalog import SERIES, _Row
from arora_data.dates import today_et

# Price table: its extra column (the commodity tables have currency, the others vwap).
_PRICE_TABLES: dict[str, str] = {
    "commodities.prices": "currency",
    "crypto.prices": "vwap",
    "equity_indexes.prices": "vwap",
    "forex.prices": "vwap",
}

# Value table: the provider value its index starts with, or None outside indicators.*.
_VALUE_TABLES: dict[str, str | None] = {
    "macro.observations": None,
    "macro.economic_indicators": None,
    "indicators.bea": "bea",
    "indicators.bls": "bls",
    "indicators.census_asm": "census",
    "indicators.census_bds": "census",
    "indicators.census_govs": "census",
    "indicators.census_intltrade": "census",
    "indicators.dbnomics": "dbnomics",
    "indicators.eia": "eia",
    "indicators.ici": "ici",
    "indicators.nyfed": "nyfed",
    "indicators.ofr": "ofr",
    "indicators.treasury_fiscal": "treasury_fiscal",
    "indicators.usda": "usda",
}

# Tables that carry units and frequency on every row.
_LABELLED = {t for t in _VALUE_TABLES if t != "macro.economic_indicators"}


class PriceBar(_Row):
    date: dt.date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None
    vwap: Decimal | None
    currency: str | None


class Value(_Row):
    date: dt.date
    value: Decimal | None
    units: str | None
    frequency: str | None


def _check_range(start: date, end: date) -> None:
    if start > end:
        raise ValueError("start must be on or before end")


def _key(text: str) -> str:
    return " ".join(text.split()).lower()


def _resolve(series: str, stored: list, hint: str, id: str = "series_id", name: str = "name"):
    """The one stored item whose id, or else name, is series in any case and spacing. id and name are its
    attributes' names; hint ends the error."""
    key = _key(series) if isinstance(series, str) else None
    found = ([s for s in stored if getattr(s, id).lower() == key]
             or [s for s in stored if getattr(s, name) is not None and _key(getattr(s, name)) == key])
    if len(found) == 1:
        return found[0]
    if found:
        raise ValueError(f"{series!r} names {len(found)} series: use an id, one of {', '.join(getattr(s, id) for s in found)}")
    raise ValueError(f"{series!r} is not a stored {hint}")


def _filter(table: str, key: str, dataset: str | None) -> tuple[str, list[str]]:
    """WHERE clause and its arguments for one series, matching the start of the table's index."""
    if table not in _PRICE_TABLES and table not in _VALUE_TABLES:
        raise ValueError(f"{table} is not a series table")
    provider = _VALUE_TABLES.get(table)
    if (provider is None) != (dataset is None):
        raise ValueError(f"{table} {'needs' if provider else 'has no'} dataset")
    # The column names are constants from SERIES.
    if provider is None:
        return f"{SERIES[table][0]} = $1", [key]
    return "provider = $1 AND dataset = $2 AND series_id = $3", [provider, dataset, key]


def _in_range(where: str, n: int) -> str:
    return f"WHERE {where} AND date >= ${n + 1} AND date <= ${n + 2} ORDER BY date"


async def _read_prices(table: str, symbol: str, start: date, end: date) -> list[PriceBar]:
    """Daily bars for one symbol, oldest first."""
    if table not in _PRICE_TABLES:
        raise ValueError(f"{table} is not a price table")
    where, args = _filter(table, symbol, None)
    _check_range(start, end)
    extra = _PRICE_TABLES[table]
    vwap = "vwap" if extra == "vwap" else "NULL::numeric"
    currency = "currency::text" if extra == "currency" else "NULL::text"
    # table and the column names are constants from _PRICE_TABLES.
    sql = f"SELECT date, open, high, low, close, volume, {vwap} AS vwap, {currency} AS currency FROM {table} "
    async with connection() as conn:
        rows = await fetch(conn, sql + _in_range(where, len(args)), *args, start, end)
    return [PriceBar(**dict(r)) for r in rows]


async def _read_values(table: str, key: str, start: date, end: date, dataset: str | None = None) -> list[Value]:
    """One series' values, oldest first. indicators.* tables need dataset, because keys repeat across datasets."""
    if table not in _VALUE_TABLES:
        raise ValueError(f"{table} is not a value table")
    where, args = _filter(table, key, dataset)
    _check_range(start, end)
    labels = "units, frequency" if table in _LABELLED else "NULL::text AS units, NULL::text AS frequency"
    # table is a constant from _VALUE_TABLES.
    sql = f"SELECT date, value, {labels} FROM {table} "
    async with connection() as conn:
        rows = await fetch(conn, sql + _in_range(where, len(args)), *args, start, end)
    return [Value(**dict(r)) for r in rows]


async def _latest(table: str, key: str, dataset: str | None) -> tuple[date | None, str | None]:
    """The series' latest date and that row's frequency label (None outside labelled tables), via the index."""
    where, args = _filter(table, key, dataset)
    frequency = "frequency" if table in _LABELLED else "NULL::text"
    # table is a constant: _filter rejects anything else.
    sql = f"SELECT date, {frequency} AS frequency FROM {table} WHERE {where} ORDER BY date DESC LIMIT 1"
    async with connection() as conn:
        rows = await fetch(conn, sql, *args)
    return (rows[0]["date"], rows[0]["frequency"]) if rows else (None, None)


class IndicatorSeries(_Row):
    """One stored indicators.* series. The dates are its first and latest with a value."""

    series_id: str
    dataset: str
    name: str | None
    units: str | None
    frequency: str | None
    category: str | None
    first_date: dt.date | None
    latest_date: dt.date | None


class IndicatorValue(_Row):
    """One indicators.* series' value for one period, in its units."""

    series_id: str
    name: str | None
    date: dt.date
    value: Decimal
    units: str | None


async def _indicator_series(sql: str) -> list[IndicatorSeries]:
    """The series list one indicators.* module's constant sql returns."""
    async with connection() as conn:
        return [IndicatorSeries(**dict(r)) for r in await fetch(conn, sql)]


async def _indicator(table: str, list_series, hint: str, series: str, start: date, end: date | None) -> list[IndicatorValue]:
    """One indicators.* series' values, read in its own dataset, gaps left out. list_series() is that table's list."""
    end = end or today_et()
    _check_range(start, end)
    picked = _resolve(series, await list_series(), hint)
    values = await _read_values(table, picked.series_id, start, end, picked.dataset)
    return [IndicatorValue(series_id=picked.series_id, name=picked.name, date=v.date, value=v.value, units=picked.units)
            for v in values if v.value is not None]

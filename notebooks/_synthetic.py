"""Synthetic stand-ins for the FMP tables, so the FMP notebooks can be published without FMP's licensed data.

use() points forex(), commodities(), crypto(), index(), treasury() and treasury_curve() at made-up data, and the
notebooks' list_series() at the made-up symbols. Every price is a seeded random walk per symbol that stays near 100
(1 for a currency pair), so a value is the same on every call. Calendars are weekdays (forex also skips Jan 1 and Dec 25),
and every day for crypto. Treasury yields are a made-up Nelson-Siegel curve whose tenors start and pause on
Treasury's public dates. Nothing here is FMP data, and only the functions' inputs are replaced: their code runs
unchanged.

Start Jupyter with ARORA_REAL_DATA=1 to read prod instead. use() prints which data a run used, and
tests/test_notebooks_offline.py fails if a notebook is saved after a real-data run, so FMP data isn't committed.
"""
from __future__ import annotations

import datetime as dt
import math
import os
import random
import warnings
from bisect import bisect_left, bisect_right
from decimal import Decimal
from functools import lru_cache

from arora_data import _db, _readers, catalog, crypto, forex, index, sync, treasury
from arora_data.commodities import COMMODITIES
from arora_data.dates import today_et

D = dt.date
_FIRST: dict[str, D] = {"forex.prices": D(1999, 1, 4), "commodities.prices": D(1970, 1, 2),
                        "crypto.prices": D(2017, 1, 1), "equity_indexes.prices": D(1990, 1, 2)}
_EARLIER = {"crypto.prices": {"BTCUSD": D(2010, 7, 17)}, "equity_indexes.prices": {"^GSPC": D(1928, 1, 3),
                                                                                    "^DJI": D(1928, 1, 3)}}
_EUR_STYLE = {"EUR", "GBP", "AUD", "NZD"}  # quoted as XXXUSD, the others as USDXXX
_CURRENCIES = sorted({c for _, c in index.INDEXES.values() if c and c != "USD"}
                     | {"EUR", "GBP", "JPY", "CAD", "CHF", "INR", "BRL", "CNY", "KWD", "LBP"})
PAIRS = {f"{c}USD" if c in _EUR_STYLE else f"USD{c}" for c in _CURRENCIES}
_SYMBOLS = {"forex.prices": PAIRS, "commodities.prices": {s for s, _, _ in COMMODITIES.values()},
            "crypto.prices": {f"{c}USD" for c in crypto.NAMES}, "equity_indexes.prices": set(index.INDEXES)}


def _trades(table: str, day: D) -> bool:
    if table == "crypto.prices":
        return True
    if table == "forex.prices" and (day.month, day.day) in ((1, 1), (12, 25)):
        return False
    return day.weekday() < 5


def _days(table: str, first: D) -> list[D]:
    n = (today_et() - first).days + 1
    return [d for d in (first + dt.timedelta(i) for i in range(n)) if _trades(table, d)]


def _dec(x: float) -> Decimal:
    return Decimal(f"{x:.6f}")


@lru_cache(maxsize=None)
def _walk(table: str, symbol: str) -> tuple[list[D], list[tuple[float, float, float, float, int]]]:
    days = _days(table, _EARLIER.get(table, {}).get(symbol, _FIRST[table]))
    rng = random.Random(f"{table}/{symbol}")
    base, vol = (1.0, 0.005) if table == "forex.prices" else (100.0, 0.012)
    x, bars = 0.0, []  # x is log(level / base), pulled back toward 0 so prices stay near base
    for _ in days:
        open_ = base * math.exp(x + rng.gauss(0, vol / 4))
        x = x * 0.998 + rng.gauss(0, vol)
        level = base * math.exp(x)
        high = max(open_, level) * (1 + abs(rng.gauss(0, vol / 2)))
        low = min(open_, level) * (1 - abs(rng.gauss(0, vol / 2)))
        bars.append((open_, high, low, level, rng.randint(1_000, 9_999)))
    return days, bars


async def _read_prices(table: str, symbol: str, start: D, end: D) -> list[_readers.PriceBar]:
    _readers._check_range(start, end)
    if symbol not in _SYMBOLS[table]:
        return []
    days, bars = _walk(table, symbol)
    lo, hi = bisect_left(days, start), bisect_right(days, end)
    currency = ("USX" if symbol.endswith("USX") else "USD") if table == "commodities.prices" else None
    return [_readers.PriceBar(date=d, open=_dec(o), high=_dec(h), low=_dec(lw), close=_dec(c), volume=v,
                              vwap=None if currency else _dec((h + lw + c) / 3), currency=currency)
            for d, (o, h, lw, c, v) in zip(days[lo:hi], bars[lo:hi])]


@lru_cache(maxsize=None)
def _curve() -> tuple[list[D], list[dict]]:
    days = _days("equity_indexes.prices", D(1990, 1, 2))
    rng = random.Random("macro.treasury_rates")
    b0, b1, b2 = 5.0, -1.5, 1.0
    rows = []
    for d in days:
        b0 += 0.002 * (5.0 - b0) + rng.gauss(0, 0.03)
        b1 += 0.002 * (-1.5 - b1) + rng.gauss(0, 0.03)
        b2 += 0.002 * (1.0 - b2) + rng.gauss(0, 0.03)
        row = {"date": d}
        for tenor, column in treasury.TENORS.items():
            t = float(tenor[:-1]) / (12 if tenor.endswith("M") else 1)
            f1 = (1 - math.exp(-t / 2)) / (t / 2)
            y = max(0.01, b0 + b1 * f1 + b2 * (f1 - math.exp(-t / 2)))
            row[column] = Decimal(f"{y:.2f}")
        # Treasury's own history: these tenors start or pause on these dates, and 2010-10-11 has no values.
        for column, absent in [("month1", d < D(2001, 7, 31)), ("month2", d < D(2018, 10, 16)),
                               ("year20", d < D(1993, 10, 1)), ("year30", D(2002, 2, 15) < d < D(2006, 2, 9)),
                               *((c, d == D(2010, 10, 11)) for c in treasury.TENORS.values())]:
            if absent:
                row[column] = None
        rows.append(row)
    return days, rows


async def _treasury_rows(start: D, end: D) -> list[dict]:
    days, rows = _curve()
    return rows[bisect_left(days, start):bisect_right(days, end)]


def _list_series(schema: str, table: str) -> list[catalog.SeriesInfo]:
    name = f"{schema}.{table}"
    return [catalog.SeriesInfo(key=s, dataset=None, description=None, latest_date=_walk(name, s)[0][-1])
            for s in sorted(_SYMBOLS[name])]


SYNTHETIC = "Data: synthetic"
REAL = "Data: real, from prod. Don't commit these outputs: FMP's data can't be published."


def use() -> None:
    """Point the FMP functions at the synthetic data, unless ARORA_REAL_DATA=1, and print which data is in use.
    Warnings print without the file path they come from either way."""
    warnings.formatwarning = lambda message, category, *_, **__: f"{category.__name__}: {message}\n"
    if os.environ.get("ARORA_REAL_DATA") == "1":
        print(REAL)
        return

    async def stored(table):
        return set(_SYMBOLS[table])

    async def no_database():
        raise RuntimeError("synthetic data has no database: this reads something _synthetic.py doesn't replace")

    # Every connection goes through _db._pool, so a read not replaced below fails here instead of showing real data.
    _db._pool = no_database
    forex._stored = lambda: stored("forex.prices")
    crypto._symbols = lambda: stored("crypto.prices")
    index._symbols = lambda: stored("equity_indexes.prices")
    _readers._read_prices = _read_prices
    treasury._rows = _treasury_rows
    sync.list_series = _list_series
    print(SYNTHETIC)

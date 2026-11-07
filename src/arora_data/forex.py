"""Daily forex rates for any pair of the stored currencies. Source: FMP, via forex.prices.

A pair is read as stored, flipped when only its inverse is stored, or crossed through USD:
BASE/QUOTE = usd_per(BASE) / usd_per(QUOTE), where usd_per(X) is the XUSD rate, or 1 / USDX.
Rates are in the quote currency. SLL is the old Sierra Leone leone (the new SLE = SLL / 1000).
"""
from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal, localcontext
from itertools import combinations
from typing import Literal

from arora_data import _readers
from arora_data._db import connection, fetch
from arora_data.catalog import _Row
from arora_data.dates import today_et

ALL = "ALL"
USD = "USD"
_TABLE = "forex.prices"
# Significant digits kept in a computed rate. Stored rates carry 6 decimals, so more would be noise, and 28 made
# a column of tiny and huge crosses need over the 38 digits a Polars decimal holds.
SIG_DIGITS = 12

Route = Literal["stored", "inverted", "crossed"]

# The stored symbols, one index lookup per symbol (a skip scan) rather than grouping every row of the table.
_SYMBOLS_SQL = """
WITH RECURSIVE s AS (
    SELECT min(symbol) AS symbol FROM forex.prices
    UNION ALL
    SELECT (SELECT min(symbol) FROM forex.prices WHERE symbol > s.symbol) FROM s WHERE s.symbol IS NOT NULL
)
SELECT symbol FROM s WHERE symbol IS NOT NULL
"""


class FxBar(_Row):
    """One day of one pair. A crossed pair has open and close only: its legs peak at different moments, so their
    highs and lows can't give the cross's true range, and their tick volumes say nothing about the cross."""

    pair: str
    date: dt.date
    route: Route
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: int | None
    vwap: Decimal | None


async def _stored() -> set[str]:
    async with connection() as conn:
        return {r["symbol"] for r in await fetch(conn, _SYMBOLS_SQL)}


def _currencies(stored: set[str]) -> set[str]:
    """USD plus every currency with a stored USD pair, in either direction."""
    return {USD} | {s.replace(USD, "") for s in stored if USD in s}


def _leg(currency: str, stored: set[str]) -> tuple[str, bool]:
    """The stored USD pair for a currency, and whether it must be flipped to give USD per one unit."""
    return (currency + USD, False) if currency + USD in stored else (USD + currency, True)


def _plan(base: str, quote: str, stored: set[str]) -> tuple[Route, list[tuple[str, bool]]]:
    """How to get base/quote: its route, and the stored symbols it reads with whether each is flipped."""
    if base + quote in stored:
        return "stored", [(base + quote, False)]
    if quote + base in stored:
        return "inverted", [(quote + base, True)]
    return "crossed", [_leg(base, stored), _leg(quote, stored)]


def _usd_per(bars: list[_readers.PriceBar], flip: bool) -> dict[dt.date, tuple[Decimal, Decimal]]:
    """USD per one unit of a leg's currency at each day's open and close."""
    with localcontext() as ctx:
        ctx.prec = SIG_DIGITS
        return {b.date: (1 / b.open, 1 / b.close) if flip else (b.open, b.close) for b in bars}


def _bars(
    pair: str,
    route: Route,
    legs: list[tuple[str, bool]],
    read: dict[str, list[_readers.PriceBar]],
    usd: dict[str, dict[dt.date, tuple[Decimal, Decimal]]],
) -> list[FxBar]:
    """One pair's rows. usd holds _usd_per for every leg symbol a crossed pair uses."""
    if route == "stored":
        return [
            FxBar(pair=pair, date=b.date, route=route, open=b.open, high=b.high, low=b.low, close=b.close,
                  volume=b.volume, vwap=b.vwap)
            for b in read[legs[0][0]]
        ]
    with localcontext() as ctx:
        ctx.prec = SIG_DIGITS
        if route == "inverted":
            # Flipping swaps high and low. FMP's volume is a tick count, the same either way round.
            # 1/vwap is close to the inverse's VWAP, not equal: off by about (day's range / price) squared.
            return [
                FxBar(pair=pair, date=b.date, route=route, open=1 / b.open, high=1 / b.low, low=1 / b.high,
                      close=1 / b.close, volume=b.volume, vwap=None if b.vwap is None else 1 / b.vwap)
                for b in read[legs[0][0]]
            ]
        base_usd, quote_usd = (usd[s] for s, _ in legs)
        # A date where either USD pair has no bar is skipped: carrying the last rate forward would invent one.
        return [
            FxBar(pair=pair, date=d, route=route, open=base_usd[d][0] / quote_usd[d][0], high=None, low=None,
                  close=base_usd[d][1] / quote_usd[d][1], volume=None, vwap=None)
            for d in sorted(base_usd.keys() & quote_usd.keys())
        ]


async def forex(base: str, quote: str, start: dt.date, end: dt.date | None = None) -> list[FxBar]:
    """Daily base/quote rates from start to end (default: today in US/Eastern), oldest first. Source: FMP.

    base and quote are currency codes such as "EUR". A pair that isn't stored, either way round, is crossed
    through its two USD pairs, which gives open and close only; a currency with no stored USD pair (gold, silver, retired currencies) raises
    ValueError. base and quote both "ALL" returns every pair of stored currencies once: stored pairs the way
    they are stored, the rest crossed with the codes in alphabetical order. Each stored symbol is read once.
    """
    base, quote = base.upper(), quote.upper()
    end = end or today_et()
    _readers._check_range(start, end)
    if (base == ALL) != (quote == ALL):
        raise ValueError('base and quote must both be "ALL" or both be currencies')
    if base == quote != ALL:
        raise ValueError("base and quote must differ")
    stored = await _stored()
    currencies = _currencies(stored)
    if base == ALL:
        pairs = [(b, a) if b + a in stored else (a, b) for a, b in combinations(sorted(currencies), 2)]
    else:
        for c in (base, quote):
            if c not in currencies:
                raise ValueError(f"{c} has no stored USD pair, so {base}/{quote} can't be computed")
        pairs = [(base, quote)]
    plans = {b + q: _plan(b, q, stored) for b, q in pairs}
    symbols = sorted({s for _, legs in plans.values() for s, _ in legs})
    # The pool caps how many of these reads run at once.
    read = dict(zip(symbols, await asyncio.gather(*(_readers._read_prices(_TABLE, s, start, end) for s in symbols))))
    # Each leg's USD rates once, not once per pair: ALL crosses every currency with about 130 others.
    cross_legs = {leg for route, legs in plans.values() if route == "crossed" for leg in legs}
    usd = {s: _usd_per(read[s], flip) for s, flip in cross_legs}
    return [bar for pair, (route, legs) in plans.items() for bar in _bars(pair, route, legs, read, usd)]

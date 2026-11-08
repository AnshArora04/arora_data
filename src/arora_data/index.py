"""Daily market index levels, as stored or in any currency forex() can price. Source: FMP, via equity_indexes.prices.

The indexes are whatever equity_indexes.prices holds, read from the table on every call. currency names the currency
an index's members trade in (None for the volatility series). In another currency, a level is the stored level times
forex(currency, quote) that day, or the latest earlier rate when that day has none; quote_date names the rate's day.
Rows are served as stored:
- volume is unreliable: the volatility series, ^RUI, ^RUA, ^RLG, ^RLV, ^W5000, ^SPXEW and ^SP500TR are all zeros,
  ^MID is partly zeros, and ^OEX repeats ^GSPC's volume;
- weekend bars are either real sessions (^NSEI's Budget-day and Diwali sessions) or FMP placeholders at the Friday
  level with zero volume (the Russell indexes on 2025-04-26, ^TWII on 2026-09-20);
- gaps of over a week are market closures (Golden Week, Lunar New Year, Chuseok), not missing data.
"""
from __future__ import annotations

import asyncio
import bisect
import datetime as dt
from decimal import Context, Decimal
from typing import Literal

from arora_data import _readers, forex as _fx
from arora_data._db import connection, fetch
from arora_data.catalog import _Row
from arora_data.commodities import FX_LOOKBACK
from arora_data.dates import today_et

ALL = _fx.ALL
_TABLE = "equity_indexes.prices"

# The stored symbols, one index lookup per symbol (a skip scan) rather than grouping every row of the table.
_SYMBOLS_SQL = """
WITH RECURSIVE s AS (
    SELECT min(symbol) AS symbol FROM equity_indexes.prices
    UNION ALL
    SELECT (SELECT min(symbol) FROM equity_indexes.prices WHERE symbol > s.symbol) FROM s WHERE s.symbol IS NOT NULL
)
SELECT symbol FROM s WHERE symbol IS NOT NULL
"""

# Symbol: (name, currency its members trade in, None for volatility). The 55 the main repo ingests (INDEX-X,
# 2026-11-08), names and currencies from its index_universe.py. Any other stored index is served with both None.
INDEXES: dict[str, tuple[str, str | None]] = {
    "000001.SS": ("sse composite", "CNY"),
    "FTSEMIB.MI": ("ftse mib", "EUR"),
    "^AEX": ("aex", "EUR"),
    "^AXJO": ("s&p/asx 200", "AUD"),
    "^BVSP": ("ibovespa", "BRL"),
    "^DJI": ("dow jones industrial average", "USD"),
    "^FCHI": ("cac 40", "EUR"),
    "^FTSE": ("ftse 100", "GBP"),
    "^GDAXI": ("dax", "EUR"),
    "^GSPC": ("s&p 500", "USD"),
    "^GSPE": ("s&p 500 energy", "USD"),
    "^GSPTSE": ("s&p/tsx composite", "CAD"),
    "^GVZ": ("cboe gold volatility", None),
    "^HSI": ("hang seng", "HKD"),
    "^IBEX": ("ibex 35", "EUR"),
    "^IXIC": ("nasdaq composite", "USD"),
    "^KS11": ("kospi", "KRW"),
    "^MID": ("s&p midcap 400", "USD"),
    "^MOVE": ("ice bofa move", None),
    "^MXX": ("ipc mexico", "MXN"),
    "^N225": ("nikkei 225", "JPY"),
    "^NDX": ("nasdaq 100", "USD"),
    "^NSEI": ("nifty 50", "INR"),
    "^NYA": ("nyse composite", "USD"),
    "^OEX": ("s&p 100", "USD"),
    "^OVX": ("cboe crude oil volatility", None),
    "^RLG": ("russell 1000 growth", "USD"),
    "^RLV": ("russell 1000 value", "USD"),
    "^RUA": ("russell 3000", "USD"),
    "^RUI": ("russell 1000", "USD"),
    "^RUT": ("russell 2000", "USD"),
    "^RVX": ("cboe russell 2000 volatility", None),
    "^SP500-15": ("s&p 500 materials", "USD"),
    "^SP500-20": ("s&p 500 industrials", "USD"),
    "^SP500-25": ("s&p 500 consumer discretionary", "USD"),
    "^SP500-30": ("s&p 500 consumer staples", "USD"),
    "^SP500-35": ("s&p 500 health care", "USD"),
    "^SP500-40": ("s&p 500 financials", "USD"),
    "^SP500-45": ("s&p 500 information technology", "USD"),
    "^SP500-50": ("s&p 500 communication services", "USD"),
    "^SP500-55": ("s&p 500 utilities", "USD"),
    "^SP500-60": ("s&p 500 real estate", "USD"),
    "^SP500TR": ("s&p 500 total return", "USD"),
    "^SPXEW": ("s&p 500 equal weight", "USD"),
    "^SSMI": ("smi", "CHF"),
    "^STOXX": ("stoxx europe 600", "EUR"),
    "^STOXX50E": ("euro stoxx 50", "EUR"),
    "^TWII": ("tsec weighted", "TWD"),
    "^VIX": ("cboe volatility", None),
    "^VIX3M": ("cboe 3-month volatility", None),
    "^VIX6M": ("cboe 6-month volatility", None),
    "^VIX9D": ("cboe 9-day volatility", None),
    "^VVIX": ("cboe vix volatility", None),
    "^VXN": ("cboe nasdaq 100 volatility", None),
    "^W5000": ("wilshire 5000", "USD"),
}

# Other names a caller may use: alias -> symbol.
ALIASES: dict[str, str] = {
    "dow": "^DJI", "dow jones": "^DJI", "djia": "^DJI",
    "shanghai composite": "000001.SS",
    "asx 200": "^AXJO",
    "tsx": "^GSPTSE",
    "s&p 400": "^MID",
    "taiex": "^TWII",
}

# Symbols FMP lists that are the same series as a stored one.
MIRRORS: dict[str, str] = {"^SPX": "^GSPC"}
# Scaled copies of a stored index, not served: their level is the stored one divided by a fixed factor.
SCALED: dict[str, str] = {"^DJX": "^DJI", "^XSP": "^GSPC", "^NANOS": "^GSPC", "^MRUT": "^RUT"}

_BY_NAME = {name: symbol for symbol, (name, _) in INDEXES.items()} | ALIASES


class IndexBar(_Row):
    """One day of one index, as stored (route stored) or times a currency rate (route crossed: no high, low or VWAP)."""

    index: str
    name: str | None
    currency: str | None
    quote: str | None
    date: dt.date
    route: Literal["stored", "crossed"]
    quote_date: dt.date | None
    open: Decimal
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: int | None
    vwap: Decimal | None


async def _symbols() -> set[str]:
    async with connection() as conn:
        return {r["symbol"] for r in await fetch(conn, _SYMBOLS_SQL)}


def _resolve(base: str, stored: set[str]) -> str:
    """The symbol base names: a name or alias, a stored symbol, or a symbol without its caret, mirrors followed."""
    key = " ".join(base.split()).lower()
    if key in _BY_NAME:
        return _BY_NAME[key]
    symbol = key.upper()
    if symbol == ALL or symbol in stored:
        return symbol
    if "." not in symbol:  # an exchange-suffixed symbol (000001.SS, FTSEMIB.MI) has no caret
        symbol = "^" + symbol.removeprefix("^")
    return MIRRORS.get(symbol, symbol)


def _convert(symbol: str, quote: str | None, bars: list[_readers.PriceBar], fx: list[_fx.FxBar] | None) -> list[IndexBar]:
    """One index's rows in quote. fx is forex(currency, quote) oldest first, None when no conversion is needed."""
    name, currency = INDEXES.get(symbol, (None, None))
    same = {"index": symbol, "name": name, "currency": currency, "quote": quote}
    if fx is None:
        return [IndexBar(**same, date=b.date, route="stored", quote_date=None, open=b.open, high=b.high, low=b.low,
                         close=b.close, volume=b.volume, vwap=b.vwap) for b in bars]
    times = Context(prec=_fx.SIG_DIGITS).multiply
    days = [r.date for r in fx]
    rows = []
    for b in bars:
        i = bisect.bisect_right(days, b.date) - 1
        if i < 0:
            continue  # before the pair's first rate: nothing to carry
        r = fx[i]
        # A carried rate is the close of the last day the pair traded, for open and close alike.
        rate_open = r.open if r.date == b.date else r.close
        rows.append(IndexBar(**same, date=b.date, route="crossed", quote_date=r.date, open=times(b.open, rate_open),
                             high=None, low=None, close=times(b.close, r.close), volume=b.volume, vwap=None))
    return rows


async def index(base: str, start: dt.date, end: dt.date | None = None, quote: str | None = None) -> list[IndexBar]:
    """Daily levels of one index from start to end (default: today in US/Eastern), oldest first. Source: FMP.

    base is a stored symbol, with or without its caret, or a name or alias, such as "^GSPC", "gspc", "s&p 500" or
    "dow" (any case and spacing), or "ALL" for every stored index, ordered by symbol then date. ^SPX returns ^GSPC.
    The scaled copies (^DJX, ^XSP, ^NANOS, ^MRUT) and any other unstored symbol raise ValueError.
    quote is None (the stored level, with high, low and VWAP) or any currency forex() can price: the level is then
    multiplied by forex(currency, quote), carrying the latest earlier rate over days the pair has no bar, and an index
    already in quote is served as stored. Days before the pair's first rate are skipped. An index with no currency
    (the volatility series, any unnamed one) raises ValueError with a quote; "ALL" with a quote leaves them out.
    """
    quote = quote and quote.upper()
    end = end or today_et()
    _readers._check_range(start, end)
    if quote == ALL:
        raise ValueError('quote must be one currency: "ALL" is for base only')
    stored = await _symbols()
    symbol = _resolve(base, stored)
    if symbol in SCALED:
        raise ValueError(f"{symbol} is a scaled copy of {SCALED[symbol]} and is not stored: use {SCALED[symbol]}")
    if symbol != ALL and symbol not in stored:
        raise ValueError(f"{symbol} is not a stored index: use a symbol or name, such as ^GSPC, GSPC or s&p 500")
    picked = sorted(stored) if symbol == ALL else [symbol]
    currency = {s: INDEXES.get(s, (None, None))[1] for s in picked}
    if quote:
        if symbol != ALL and currency[symbol] is None:
            raise ValueError(f"{symbol} has no currency to convert from: leave quote out")
        picked = [s for s in picked if currency[s]]
    # forex first, so a bad quote raises before any index read starts; one read per currency, not per index.
    need = sorted({currency[s] for s in picked if quote and currency[s] != quote})
    fx = dict(zip(need, await asyncio.gather(*(_fx.forex(c, quote, start - FX_LOOKBACK, end) for c in need))))
    # The pool caps how many of these reads run at once.
    read = await asyncio.gather(*(_readers._read_prices(_TABLE, s, start, end) for s in picked))
    return [row for s, bars in zip(picked, read) for row in _convert(s, quote, bars, fx.get(currency[s]))]

"""Daily crypto prices in USD or any currency forex() can price. Source: FMP, via crypto.prices.

The coins are whatever crypto.prices holds as <CODE>USD, read from the table on every call. A non-USD price is the
USD price times forex("USD", quote) that day, or the latest earlier rate when that day has none (crypto trades every
day, forex not on weekends); quote_date names the rate's day. volume_usd is FMP's volume, in US dollars whatever the
quote. Rows are served as stored: DOGE's first two bars (2017-05-31, 06-01) are FMP placeholders at 0.0001.
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

ALL, USD = _fx.ALL, _fx.USD
_TABLE = "crypto.prices"

# The stored symbols, one index lookup per symbol (a skip scan) rather than grouping every row of the table.
_SYMBOLS_SQL = """
WITH RECURSIVE s AS (
    SELECT min(symbol) AS symbol FROM crypto.prices
    UNION ALL
    SELECT (SELECT min(symbol) FROM crypto.prices WHERE symbol > s.symbol) FROM s WHERE s.symbol IS NOT NULL
)
SELECT symbol FROM s WHERE symbol IS NOT NULL
"""

# Code: name, for the fixed top 15 the main repo ingests (CRYPTO-15, 2026-11-08). Any other stored coin is served by
# code, with name None.
NAMES: dict[str, str] = {
    "ADA": "cardano",
    "BNB": "bnb",
    "BTC": "bitcoin",
    "DOGE": "dogecoin",
    "ETH": "ethereum",
    "HYPE": "hyperliquid",
    "LEO": "unus sed leo",
    "LINK": "chainlink",
    "SOL": "solana",
    "TRX": "tron",
    "USDC": "usd coin",
    "USDT": "tether",
    "XMR": "monero",
    "XRP": "xrp",
    "ZEC": "zcash",
}

_BY_NAME = {name: code for code, name in NAMES.items()}

Route = Literal["stored", "crossed"]


class CryptoBar(_Row):
    """One day of one coin. A crossed bar has open and close only: the coin and the rate peak at different moments,
    so high, low and VWAP can't be converted. volume_usd stays in US dollars."""

    coin: str
    name: str | None
    quote: str
    date: dt.date
    route: Route
    quote_date: dt.date | None
    open: Decimal
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume_usd: int | None
    vwap: Decimal | None


async def _symbols() -> set[str]:
    async with connection() as conn:
        return {r["symbol"] for r in await fetch(conn, _SYMBOLS_SQL)}


def _convert(coin: str, quote: str, bars: list[_readers.PriceBar], fx: list[_fx.FxBar]) -> list[CryptoBar]:
    """One coin's rows in quote. fx is forex("USD", quote) oldest first, empty for USD."""
    same = {"coin": coin, "name": NAMES.get(coin), "quote": quote}
    if quote == USD:
        return [CryptoBar(**same, date=b.date, route="stored", quote_date=None, open=b.open, high=b.high, low=b.low,
                          close=b.close, volume_usd=b.volume, vwap=b.vwap) for b in bars]
    times = Context(prec=_fx.SIG_DIGITS).multiply
    days = [r.date for r in fx]
    rows = []
    for b in bars:
        i = bisect.bisect_right(days, b.date) - 1
        if i < 0:
            continue  # before the quote's first rate: nothing to carry
        r = fx[i]
        # A carried rate is the close of the last day the pair traded, for open and close alike.
        rate_open = r.open if r.date == b.date else r.close
        rows.append(CryptoBar(**same, date=b.date, route="crossed", quote_date=r.date, open=times(b.open, rate_open),
                              high=None, low=None, close=times(b.close, r.close), volume_usd=b.volume, vwap=None))
    return rows


async def crypto(base: str, start: dt.date, end: dt.date | None = None, quote: str = USD) -> list[CryptoBar]:
    """Daily prices of one coin from start to end (default: today in US/Eastern), oldest first. Source: FMP.

    base is a stored coin's code or one of NAMES, such as "BTC" or "bitcoin" (any case), or "ALL" for every stored
    coin, ordered by code then date. quote is "USD" (the stored price, with high, low and VWAP) or any currency
    forex() can price: the USD price is then multiplied by forex("USD", quote), carrying the latest earlier rate over
    days the pair has no bar, weekends included. Days before the pair's first rate are skipped. A bad code raises
    ValueError.
    """
    key = " ".join(base.split()).lower()
    base = _BY_NAME.get(key, key.upper())
    quote = quote.upper()
    end = end or today_et()
    _readers._check_range(start, end)
    if quote == ALL:
        raise ValueError('quote must be one currency: "ALL" is for base only')
    coins = {s.removesuffix(USD) for s in await _symbols() if s.endswith(USD)}
    if base != ALL and base not in coins:
        raise ValueError(f"{base} is not a stored coin: use a code or name, such as BTC or bitcoin")
    # forex first, so a bad quote raises before any coin read starts.
    fx = [] if quote == USD else await _fx.forex(USD, quote, start - FX_LOOKBACK, end)
    picked = sorted(coins) if base == ALL else [base]
    # The pool caps how many of these reads run at once.
    read = await asyncio.gather(*(_readers._read_prices(_TABLE, c + USD, start, end) for c in picked))
    return [row for c, bars in zip(picked, read) for row in _convert(c, quote, bars, fx)]

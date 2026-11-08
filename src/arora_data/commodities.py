"""Daily commodity prices in USD or any currency forex() can price. Source: FMP, via commodities.prices.

Each price is for one unit of the commodity (COMMODITIES, units from the CME, ICE and LME contract specs). Symbols
FMP quotes in US cents (currency USX) come back in dollars. A non-USD price is the USD price times
forex("USD", quote) that day, or the latest earlier rate when that day has none; quote_date names the rate's day.
"""
from __future__ import annotations

import asyncio
import bisect
import datetime as dt
from decimal import Context, Decimal
from typing import Literal

from arora_data import _readers, forex as _fx
from arora_data.catalog import _Row
from arora_data.dates import today_et

ALL, USD = _fx.ALL, _fx.USD
_TABLE = "commodities.prices"
# shortcut: the first days of a range carry a rate only if one falls this close before start; widen it if a currency
# ever goes longer without a bar.
FX_LOOKBACK = dt.timedelta(days=14)

# Root code: (stored symbol, what one price is for, name). Physical commodities only: the table's financial futures
# (ES, NQ, YM, RTY, ZB, ZN, ZF, ZT, ZQ, DX) and the micro gold and silver contracts (MGC, SIL), which close at the
# same price as GC and SI, are left out.
COMMODITIES: dict[str, tuple[str, str, str]] = {
    "BZ": ("BZUSD", "barrel", "brent crude"),
    "CL": ("CLUSD", "barrel", "wti crude"),
    "HO": ("HOUSD", "gallon", "heating oil"),
    "RB": ("RBUSD", "gallon", "rbob gasoline"),
    "NG": ("NGUSD", "MMBtu", "natural gas"),
    "GC": ("GCUSD", "troy oz", "gold"),
    "SI": ("SIUSD", "troy oz", "silver"),
    "PL": ("PLUSD", "troy oz", "platinum"),
    "PA": ("PAUSD", "troy oz", "palladium"),
    "HG": ("HGUSD", "lb", "copper"),
    "ALI": ("ALIUSD", "metric ton", "aluminum"),
    "ZC": ("ZCUSX", "bushel", "corn"),
    "ZS": ("ZSUSX", "bushel", "soybeans"),
    "ZO": ("ZOUSX", "bushel", "oats"),
    "KE": ("KEUSX", "bushel", "hard red winter wheat"),
    "ZL": ("ZLUSX", "lb", "soybean oil"),
    "ZM": ("ZMUSD", "short ton", "soybean meal"),
    "ZR": ("ZRUSD", "cwt", "rough rice"),
    "CC": ("CCUSD", "metric ton", "cocoa"),
    "KC": ("KCUSX", "lb", "coffee"),
    "SB": ("SBUSX", "lb", "sugar"),
    "CT": ("CTUSX", "lb", "cotton"),
    "OJ": ("OJUSX", "lb", "orange juice"),
    "LB": ("LBUSD", "1,000 board feet", "lumber"),
    "LE": ("LEUSX", "lb", "live cattle"),
    "GF": ("GFUSX", "lb", "feeder cattle"),
    "HE": ("HEUSX", "lb", "lean hogs"),
    "DC": ("DCUSD", "cwt", "class iii milk"),
}

# Other names a caller may use: alias -> root code. Words that could mean two commodities ("oil", "gas", "cattle")
# are left out. "crude" is WTI, as FMP names CLUSD "Crude Oil" and BZUSD "Brent Crude Oil".
ALIASES: dict[str, str] = {
    "crude": "CL", "crude oil": "CL", "wti": "CL",
    "brent": "BZ", "brent crude oil": "BZ",
    "natgas": "NG",
    "gasoline": "RB", "rbob": "RB",
    "aluminium": "ALI",
    "wheat": "KE", "kc wheat": "KE", "hrw wheat": "KE",
    "soybean": "ZS", "soyoil": "ZL", "soymeal": "ZM",
    "rice": "ZR",
    "sugar no. 11": "SB", "sugar 11": "SB",
    "hogs": "HE", "lean hog": "HE",
    "milk": "DC", "class 3 milk": "DC",
}

_BY_NAME = {name: code for code, (_, _, name) in COMMODITIES.items()} | ALIASES

Route = Literal["stored", "crossed"]


class CommodityBar(_Row):
    """One day of one commodity. A crossed bar has open and close only: the commodity and the rate peak at
    different moments, so high and low can't be converted. Volume counts contracts of the commodity, so it stays."""

    commodity: str
    name: str
    quote: str
    date: dt.date
    route: Route
    quote_date: dt.date | None
    open: Decimal
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: int | None
    unit: str


def _dollars(bar: _readers.PriceBar) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """The bar's open, high, low and close in dollars."""
    prices = (bar.open, bar.high, bar.low, bar.close)
    if bar.currency == USD:
        return prices
    if bar.currency != "USX":
        raise ValueError(f"unexpected currency {bar.currency!r} in {_TABLE}")
    return tuple(p / 100 for p in prices)


def _convert(commodity: str, quote: str, bars: list[_readers.PriceBar], fx: list[_fx.FxBar]) -> list[CommodityBar]:
    """One commodity's rows in quote. fx is forex("USD", quote) oldest first, empty for USD."""
    _, per, name = COMMODITIES[commodity]
    same = {"commodity": commodity, "name": name, "quote": quote, "unit": f"{quote} per {per}"}
    times = Context(prec=_fx.SIG_DIGITS).multiply
    days = [r.date for r in fx]
    rows = []
    for b in bars:
        open_, high, low, close = _dollars(b)
        if quote == USD:
            rows.append(CommodityBar(**same, date=b.date, route="stored", quote_date=None, open=open_, high=high,
                                     low=low, close=close, volume=b.volume))
            continue
        i = bisect.bisect_right(days, b.date) - 1
        if i < 0:
            continue  # before the quote's first rate: nothing to carry
        r = fx[i]
        # A carried rate is the close of the last day the pair traded, for open and close alike.
        rate_open = r.open if r.date == b.date else r.close
        rows.append(CommodityBar(**same, date=b.date, route="crossed", quote_date=r.date, open=times(open_, rate_open),
                                 high=None, low=None, close=times(close, r.close), volume=b.volume))
    return rows


async def commodities(base: str, start: dt.date, end: dt.date | None = None, quote: str = USD) -> list[CommodityBar]:
    """Daily prices of one commodity from start to end (default: today in US/Eastern), oldest first. Source: FMP.

    base is a root code, name or alias, such as "GC", "gold" or "crude" (any case), or "ALL" for every one of them,
    ordered by code then date. Each bar carries both.
    quote is "USD" (the stored price, with high and low) or any currency forex() can price: the USD price is then
    multiplied by forex("USD", quote), carrying the latest earlier rate over days the pair has no bar. Days before
    the pair's first rate are skipped. A bad code raises ValueError.
    """
    key = " ".join(base.split())
    base = _BY_NAME.get(key.lower(), key.upper())
    quote = quote.upper()
    end = end or today_et()
    _readers._check_range(start, end)
    if quote == ALL:
        raise ValueError('quote must be one currency: "ALL" is for base only')
    if base != ALL and base not in COMMODITIES:
        raise ValueError(f"{base} is not a served commodity: use a code, name or alias, such as GC, gold or crude")
    # forex first, so a bad quote raises before any commodity read starts.
    fx = [] if quote == USD else await _fx.forex(USD, quote, start - FX_LOOKBACK, end)
    roots = sorted(COMMODITIES) if base == ALL else [base]
    # The pool caps how many of these reads run at once.
    read = await asyncio.gather(*(_readers._read_prices(_TABLE, COMMODITIES[c][0], start, end) for c in roots))
    return [row for c, bars in zip(roots, read) for row in _convert(c, quote, bars, fx)]

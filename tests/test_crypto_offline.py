"""crypto() codes, names and currency conversion over weekends, with the database faked under the real forex()."""
import asyncio
from datetime import date
from decimal import Decimal

import pytest

from arora_data import _readers, crypto as cr, forex as fx

# Thursday Oct 1, then Friday to Monday: forex has no weekend bars, crypto does.
D0, FRI, SAT, SUN, MON = date(2026, 10, 1), date(2026, 11, 6), date(2026, 11, 7), date(2026, 11, 8), date(2026, 11, 9)


def bar(d, close, open_=None, high=None, low=None, vwap=None):
    return _readers.PriceBar(date=d, open=Decimal(open_ or close), high=Decimal(high or close),
                             low=Decimal(low or close), close=Decimal(close), volume=9,
                             vwap=None if vwap is None else Decimal(vwap), currency=None)


DATA = {
    "crypto.prices": {
        "BTCUSD": [bar(FRI, "70000", open_="60000", high="71000", low="59000", vwap="65000"),
                   bar(SAT, "70500"), bar(SUN, "71000"), bar(MON, "72000")],
        "ETHUSD": [bar(FRI, "2500")],
        "LTCUSD": [bar(FRI, "100")],  # stored but not in NAMES, as the dropped coins are until migration 138
        "LEOUSD": [bar(FRI, "9")],
        "DOGEUSD": [bar(D0, "0.1"), bar(FRI, "0.2")],
    },
    "forex.prices": {
        "EURUSD": [bar(FRI, "1.25", open_="1.2"), bar(MON, "1.6")],  # no weekend bars
        "USDJPY": [bar(FRI, "150", open_="140")],
    },
}


@pytest.fixture
def db(monkeypatch):
    reads = []

    async def fx_stored():
        return set(DATA["forex.prices"])

    async def symbols():
        return set(DATA["crypto.prices"]) | {"BTCEUR"}  # a non-USD symbol is not a coin

    async def read_prices(table, symbol, start, end):
        reads.append(symbol)
        return [b for b in DATA[table].get(symbol, []) if start <= b.date <= end]

    monkeypatch.setattr(fx, "_stored", fx_stored)
    monkeypatch.setattr(cr, "_symbols", symbols)
    monkeypatch.setattr(_readers, "_read_prices", read_prices)
    return reads


def run(base, quote="USD", start=FRI, end=MON):
    return asyncio.run(cr.crypto(base, start, end, quote))


def test_usd_comes_back_as_stored_without_reading_forex(db):
    rows = run("btc")
    assert [(r.coin, r.name, r.quote, r.date, r.route, r.quote_date) for r in rows] == [
        ("BTC", "bitcoin", "USD", d, "stored", None) for d in (FRI, SAT, SUN, MON)]
    assert (rows[0].open, rows[0].high, rows[0].low, rows[0].close, rows[0].volume_usd, rows[0].vwap) == (
        Decimal("60000"), Decimal("71000"), Decimal("59000"), Decimal("70000"), 9, Decimal("65000"))
    assert db == ["BTCUSD"]


def test_a_name_works_like_its_code_in_any_case_and_spacing(db):
    assert run("  Bitcoin ") == run("BTC") == run(" btc ")
    assert [(r.coin, r.name) for r in run("Unus  Sed LEO")] == [("LEO", "unus sed leo")]
    assert [(r.coin, r.name) for r in run("ltc")] == [("LTC", None)]  # stored, but no name


def test_names_cover_the_fixed_top_15_and_are_unique():
    assert set(cr.NAMES) == {"ADA", "BNB", "BTC", "DOGE", "ETH", "HYPE", "LEO", "LINK", "SOL", "TRX", "USDC", "USDT",
                             "XMR", "XRP", "ZEC"}
    names = list(cr.NAMES.values())
    assert len(set(names)) == len(names) == 15
    assert all(n == " ".join(n.split()).lower() for n in names)


def test_currency_price_is_usd_price_times_forex_carrying_the_weekend(db):
    rows = run("BTC", "eur")
    # EUR per USD = 1/EURUSD: Friday opens at 1/1.2 and closes at 0.8, the weekend carries 0.8, Monday is 0.625.
    assert [(r.date, r.route, r.quote_date, r.open, r.close) for r in rows] == [
        (FRI, "crossed", FRI, Decimal("50000"), Decimal("56000")),
        (SAT, "crossed", FRI, Decimal("56400"), Decimal("56400")),  # Friday's closing rate for open and close
        (SUN, "crossed", FRI, Decimal("56800"), Decimal("56800")),
        (MON, "crossed", MON, Decimal("45000"), Decimal("45000")),
    ]
    assert {(r.high, r.low, r.vwap, r.volume_usd, r.quote, r.name) for r in rows} == {
        (None, None, None, 9, "EUR", "bitcoin")}


def test_a_usd_first_pair_multiplies_as_stored(db):
    rows = run("BTC", "JPY")
    assert [(r.date, r.quote_date, r.open, r.close) for r in rows] == [
        (FRI, FRI, Decimal("8400000"), Decimal("10500000")),
        (SAT, FRI, Decimal("10575000"), Decimal("10575000")),
        (SUN, FRI, Decimal("10650000"), Decimal("10650000")),
        (MON, FRI, Decimal("10800000"), Decimal("10800000")),
    ]


def test_first_days_carry_a_rate_from_before_start(db):
    rows = run("BTC", "EUR", start=SUN)
    assert [(r.date, r.quote_date, r.close) for r in rows] == [(SUN, FRI, Decimal("56800")), (MON, MON, Decimal("45000"))]


def test_days_before_the_first_rate_are_skipped(db):
    assert [(r.date, r.close) for r in run("DOGE", "JPY", start=D0)] == [(FRI, Decimal("30"))]
    assert [(r.date, r.route) for r in run("DOGE", start=D0)] == [(D0, "stored"), (FRI, "stored")]


def test_all_reads_each_stored_coin_once_and_orders_by_code_then_date(db):
    rows = run("ALL", "EUR")
    assert db.count("EURUSD") == 1
    assert sorted(s for s in db if s != "EURUSD") == sorted(DATA["crypto.prices"])
    assert [(r.coin, r.date) for r in rows] == [
        ("BTC", FRI), ("BTC", SAT), ("BTC", SUN), ("BTC", MON), ("DOGE", FRI), ("ETH", FRI), ("LEO", FRI), ("LTC", FRI)]


@pytest.mark.parametrize("base, quote, match", [
    ("BTCUSD", "USD", "BTCUSD is not a stored coin"),
    ("BTCEUR", "USD", "BTCEUR is not a stored coin"),  # stored, but not a USD symbol
    ("xrp", "USD", "XRP is not a stored coin"),  # named, but not stored here
    ("litecoin", "USD", "LITECOIN is not a stored coin"),
    ("BTC", "ALL", '"ALL" is for base only'),
    ("BTC", "XAU", "XAU has no stored USD pair"),
    ("BTC", "ETH", "ETH has no stored USD pair"),  # a coin is not a quote currency
])
def test_bad_codes_rejected_before_any_coin_read(db, base, quote, match):
    with pytest.raises(ValueError, match=match):
        run(base, quote)
    assert db == []


def test_start_after_end_rejected(db):
    with pytest.raises(ValueError, match="start must be on or before end"):
        run("BTC", start=MON, end=FRI)
    assert db == []

"""forex() routing and math, with the database faked."""
import asyncio
from datetime import date
from decimal import Decimal

import pytest

from arora_data import _readers, forex as fx

D1, D2, D3 = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)


def bar(d, close, open_=None, high=None, low=None):
    c = Decimal(close)
    return _readers.PriceBar(date=d, open=Decimal(open_ or close), high=Decimal(high or close), low=Decimal(low or close),
                             close=c, volume=7, vwap=c, currency=None)


DATA = {
    "EURUSD": [bar(D1, "1.2"), bar(D2, "1.25"), bar(D3, "1.3")],
    "USDJPY": [bar(D1, "125"), bar(D3, "160")],  # no bar on D2
    "USDCAD": [bar(D1, "1.25", open_="1.2", high="1.6", low="1.0")],
    "EURJPY": [bar(D1, "180")],
}


@pytest.fixture
def db(monkeypatch):
    reads = []

    async def stored():
        return set(DATA)

    async def read_prices(table, symbol, start, end):
        assert table == "forex.prices"
        reads.append(symbol)
        return DATA[symbol]

    monkeypatch.setattr(fx, "_stored", stored)
    monkeypatch.setattr(_readers, "_read_prices", read_prices)
    return reads


def run(*args):
    return asyncio.run(fx.forex(*args, D1, D3))


def test_stored_pair_comes_back_whole(db):
    rows = run("usd", "cad")
    assert [(r.pair, r.route, r.open, r.high, r.low, r.close, r.volume, r.vwap) for r in rows] == [
        ("USDCAD", "stored", Decimal("1.2"), Decimal("1.6"), Decimal("1.0"), Decimal("1.25"), 7, Decimal("1.25"))
    ]
    assert db == ["USDCAD"]


def test_inverse_is_flipped_with_high_and_low_swapped(db):
    (row,) = run("CAD", "USD")
    assert (row.pair, row.route, row.open, row.high, row.low, row.close) == (
        "CADUSD", "inverted", Decimal("0.833333333333"), Decimal("1"), Decimal("0.625"), Decimal("0.8"))
    assert (row.volume, row.vwap) == (7, Decimal("0.8"))


def test_inverse_keeps_a_blank_vwap_blank(db):
    # Prod has a few forex rows with no vwap; flipping one must not divide by None.
    DATA["USDNOK"] = [bar(D1, "10").model_copy(update={"vwap": None})]
    try:
        (row,) = run("NOK", "USD")
    finally:
        del DATA["USDNOK"]
    assert (row.close, row.vwap) == (Decimal("0.1"), None)


def test_cross_flips_each_leg_to_usd_per_unit_and_skips_dates_a_leg_lacks(db):
    rows = run("JPY", "CAD")
    # usd_per(JPY) = 1/USDJPY, usd_per(CAD) = 1/USDCAD; only D1 has both legs.
    assert [(r.pair, r.date, r.route, r.close) for r in rows] == [("JPYCAD", D1, "crossed", Decimal("0.01"))]
    # Open crosses the same way: (1/125) / (1/1.2).
    assert rows[0].open == Decimal("0.0096")
    assert rows[0].high is rows[0].low is rows[0].volume is rows[0].vwap is None
    rows = run("EUR", "CAD")
    assert [r.close for r in rows] == [Decimal("1.2") * Decimal("1.25")]
    assert sorted(db) == ["EURUSD", "USDCAD", "USDCAD", "USDJPY"]


def test_cross_skips_a_date_only_one_leg_has(db):
    DATA["USDCHF"] = [bar(D2, "0.9"), bar(D3, "0.8")]
    try:
        rows = run("JPY", "CHF")
    finally:
        del DATA["USDCHF"]
    assert [(r.date, r.close) for r in rows] == [(D3, Decimal("0.005"))]


@pytest.mark.parametrize("base, quote", [("XAU", "USD"), ("USD", "XAU"), ("EUR", "XAU")])
def test_currency_without_usd_pair_is_named(db, base, quote):
    with pytest.raises(ValueError, match="XAU has no stored USD pair"):
        run(base, quote)
    assert db == []


@pytest.mark.parametrize("base, quote", [("ALL", "CAD"), ("EUR", "all"), ("CAD", "cad")])
def test_bad_pairs_rejected_before_connecting(monkeypatch, base, quote):
    def fail(*args):
        raise AssertionError("connected to the database")

    monkeypatch.setattr(fx, "_stored", fail)
    with pytest.raises(ValueError):
        run(base, quote)


def test_start_after_end_rejected(db):
    with pytest.raises(ValueError):
        asyncio.run(fx.forex("EUR", "USD", D3, D1))


def test_all_returns_each_pair_once_reading_each_symbol_once(db):
    rows = run("All", "ALL")
    routes = {r.pair: r.route for r in rows}
    assert routes == {
        "CADEUR": "crossed", "CADJPY": "crossed", "USDCAD": "stored",
        "EURJPY": "stored", "EURUSD": "stored", "USDJPY": "stored",
    }
    assert sorted(db) == sorted(DATA)

"""commodities() units, cents and currency conversion, with the database faked under the real forex()."""
import asyncio
from datetime import date
from decimal import Decimal

import pytest

from arora_data import _readers, commodities as cm, forex as fx

D0, D1, D2, D3 = date(2026, 10, 1), date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)


def bar(d, close, open_=None, high=None, low=None, currency="USD"):
    return _readers.PriceBar(date=d, open=Decimal(open_ or close), high=Decimal(high or close),
                             low=Decimal(low or close), close=Decimal(close), volume=7, vwap=None, currency=currency)


DATA = {
    "commodities.prices": {
        "GCUSD": [bar(D1, "2000", open_="1990", high="2100", low="1900"), bar(D2, "2010"), bar(D3, "2020")],
        "ZCUSX": [bar(D1, "441.5", open_="440", high="450", low="430", currency="USX")],
        "SIUSD": [bar(D0, "50"), bar(D1, "51")],
        "BZUSD": [bar(D1, "90")],
        "ALIUSD": [bar(D1, "3000")],  # after BZ in COMMODITIES, before it by code
        "CLUSD": [bar(D1, "90")],
        "KEUSX": [bar(D1, "632.5", currency="USX")],
    },
    "forex.prices": {
        "EURUSD": [bar(D1, "1.25", open_="1.2"), bar(D3, "1.6")],  # no bar on D2
        "USDJPY": [bar(D1, "150", open_="140")],
    },
}


@pytest.fixture
def db(monkeypatch):
    reads = []

    async def stored():
        return set(DATA["forex.prices"])

    async def read_prices(table, symbol, start, end):
        reads.append(symbol)
        return [b for b in DATA[table].get(symbol, []) if start <= b.date <= end]

    monkeypatch.setattr(fx, "_stored", stored)
    monkeypatch.setattr(_readers, "_read_prices", read_prices)
    return reads


def run(base, quote="USD", start=D1, end=D3):
    return asyncio.run(cm.commodities(base, start, end, quote))


def test_usd_comes_back_as_stored_without_reading_forex(db):
    rows = run("gc")
    assert [(r.commodity, r.quote, r.date, r.route, r.quote_date, r.unit) for r in rows] == [
        ("GC", "USD", d, "stored", None, "USD per troy oz") for d in (D1, D2, D3)]
    assert (rows[0].open, rows[0].high, rows[0].low, rows[0].close, rows[0].volume) == (
        Decimal("1990"), Decimal("2100"), Decimal("1900"), Decimal("2000"), 7)
    assert db == ["GCUSD"]


def test_a_name_works_like_its_code_in_any_case_and_spacing(db):
    assert run("  Gold ") == run("GC") == run("gold") == run(" gc ")
    assert {(r.commodity, r.name) for r in run("GOLD")} == {("GC", "gold")}
    assert [r.commodity for r in run("Corn", "EUR")] == ["ZC"]
    assert [r.name for r in run("ALL")][:2] == ["aluminum", "brent crude"]


def test_names_are_unique_and_never_a_code():
    names = [name for *_, name in cm.COMMODITIES.values()]
    assert len(set(names)) == len(names) == 28
    assert all(name == name.lower() and name.upper() not in cm.COMMODITIES for name in names)


def test_aliases_point_at_served_codes_and_never_shadow_a_name_or_code():
    names = {name for *_, name in cm.COMMODITIES.values()}
    assert set(cm.ALIASES.values()) <= set(cm.COMMODITIES)
    assert all(a == a.lower() and a not in names and a.upper() not in cm.COMMODITIES for a in cm.ALIASES)


def test_an_alias_returns_its_commodity_under_its_primary_name(db):
    assert [(r.commodity, r.name, r.close) for r in run(" Wheat ")] == [("KE", "hard red winter wheat", Decimal("6.325"))]
    assert [r.commodity for a in ("crude", "Crude Oil", "wti") for r in run(a)] == ["CL"] * 3  # WTI, not Brent
    assert {(r.commodity, r.name) for r in run("aluminium")} == {("ALI", "aluminum")}


def test_cents_become_dollars(db):
    (row,) = run("ZC")
    assert (row.open, row.high, row.low, row.close, row.unit) == (
        Decimal("4.4"), Decimal("4.5"), Decimal("4.3"), Decimal("4.415"), "USD per bushel")


def test_currency_price_is_usd_price_times_forex_carrying_a_missing_day(db):
    rows = run("GC", "eur")
    # EUR per USD = 1/EURUSD: D1 opens at 1/1.2 and closes at 0.8, D2 has no rate, D3 is 0.625.
    assert [(r.date, r.route, r.quote_date, r.open, r.close) for r in rows] == [
        (D1, "crossed", D1, Decimal("1658.33333333"), Decimal("1600")),
        (D2, "crossed", D1, Decimal("1608"), Decimal("1608")),  # D1's closing rate for open and close
        (D3, "crossed", D3, Decimal("1262.5"), Decimal("1262.5")),
    ]
    assert {(r.high, r.low, r.volume, r.unit, r.quote, r.name) for r in rows} == {
        (None, None, 7, "EUR per troy oz", "EUR", "gold")}


def test_a_usd_first_pair_multiplies_as_stored(db):
    rows = run("GC", "JPY")
    assert [(r.date, r.quote_date, r.open, r.close) for r in rows] == [
        (D1, D1, Decimal("278600"), Decimal("300000")),
        (D2, D1, Decimal("301500"), Decimal("301500")),
        (D3, D1, Decimal("303000"), Decimal("303000")),
    ]


def test_cents_are_converted_before_the_rate(db):
    (row,) = run("ZC", "EUR")
    assert (row.close, row.unit) == (Decimal("3.532"), "EUR per bushel")


def test_first_days_carry_a_rate_from_before_start(db):
    rows = run("GC", "EUR", start=D2)
    assert [(r.date, r.quote_date, r.close) for r in rows] == [(D2, D1, Decimal("1608")), (D3, D3, Decimal("1262.5"))]


def test_days_before_the_first_rate_are_skipped(db):
    rows = run("SI", "JPY", start=D0)
    assert [(r.date, r.close) for r in rows] == [(D1, Decimal("7650"))]
    assert [(r.date, r.route) for r in run("SI", start=D0)] == [(D0, "stored"), (D1, "stored")]


def test_all_reads_each_commodity_once_and_orders_by_code_then_date(db):
    rows = run("ALL", "EUR")
    assert db.count("EURUSD") == 1
    assert sorted(s for s in db if s != "EURUSD") == sorted(symbol for symbol, *_ in cm.COMMODITIES.values())
    assert [(r.commodity, r.date) for r in rows] == [
        ("ALI", D1), ("BZ", D1), ("CL", D1), ("GC", D1), ("GC", D2), ("GC", D3), ("KE", D1), ("SI", D1), ("ZC", D1)]


@pytest.mark.parametrize("base, quote, match", [
    ("ES", "USD", "ES is not a served commodity"),  # a financial future
    ("MGC", "USD", "MGC is not a served commodity"),  # the micro mirror of GC
    ("GCUSD", "USD", "GCUSD is not a served commodity"),
    ("oil", "USD", "OIL is not a served commodity"),  # WTI or Brent: deliberately not an alias
    ("GC", "ALL", '"ALL" is for base only'),
    ("GC", "XAU", "XAU has no stored USD pair"),
])
def test_bad_codes_rejected_before_any_commodity_read(db, base, quote, match):
    with pytest.raises(ValueError, match=match):
        run(base, quote)
    assert db == []


def test_start_after_end_rejected(db):
    with pytest.raises(ValueError, match="start must be on or before end"):
        run("GC", start=D3, end=D1)
    assert db == []


def test_an_unknown_stored_currency_raises(db, monkeypatch):
    monkeypatch.setitem(DATA["commodities.prices"], "GCUSD", [bar(D1, "2000", currency="EUR")])
    with pytest.raises(ValueError, match="unexpected currency 'EUR'"):
        run("GC")

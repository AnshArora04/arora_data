"""index() symbols, names, mirrors, ALL and currency conversion, with the database faked."""
import asyncio
from datetime import date, timedelta
from decimal import Decimal

import pytest

from arora_data import _readers, forex as fx, index as ix

D1, D2 = date(2026, 11, 5), date(2026, 11, 6)


def bar(d, close, volume=7):
    c = Decimal(close)
    return _readers.PriceBar(date=d, open=c - 1, high=c + 2, low=c - 3, close=c, volume=volume, vwap=c + 1,
                             currency=None)


def rate(d, open_, close):
    return fx.FxBar(pair="", date=d, route="stored", open=Decimal(open_), high=None, low=None, close=Decimal(close),
                    volume=None, vwap=None)


# forex(base, quote): USD/EUR has no D2 bar, so D2 carries D1's close.
FX = {("USD", "EUR"): [rate(D1, "0.9", "0.8")], ("CNY", "EUR"): [rate(D1, "0.12", "0.13")]}

DATA = {
    "^GSPC": [bar(D1, "7350"), bar(D2, "7376")],
    "^DJI": [bar(D1, "47000")],
    "^VIX": [bar(D1, "19", volume=0)],
    "000001.SS": [bar(D1, "3900")],
    "^ZZZ": [bar(D1, "1")],  # stored but not in INDEXES
}


@pytest.fixture
def db(monkeypatch):
    reads = []

    async def forex(base, quote, start, end):
        if (base, quote) not in FX:
            raise ValueError(f"no rate for {base}/{quote}")
        reads.append((base, quote, start, end))
        return [r for r in FX[(base, quote)] if start <= r.date <= end]

    async def symbols():
        return set(DATA)

    async def read_prices(table, symbol, start, end):
        assert table == "equity_indexes.prices"
        reads.append(symbol)
        return [b for b in DATA.get(symbol, []) if start <= b.date <= end]

    monkeypatch.setattr(ix, "_symbols", symbols)
    monkeypatch.setattr(fx, "forex", forex)
    monkeypatch.setattr(_readers, "_read_prices", read_prices)
    return reads


def run(base, start=D1, end=D2, quote=None):
    return asyncio.run(ix.index(base, start, end, quote))


def test_bars_come_back_as_stored_with_name_and_currency(db):
    rows = run("^GSPC")
    assert [(r.index, r.name, r.currency, r.date) for r in rows] == [
        ("^GSPC", "s&p 500", "USD", D1), ("^GSPC", "s&p 500", "USD", D2)]
    assert (rows[0].open, rows[0].high, rows[0].low, rows[0].close, rows[0].volume, rows[0].vwap) == (
        Decimal("7349"), Decimal("7352"), Decimal("7347"), Decimal("7350"), 7, Decimal("7351"))
    assert db == ["^GSPC"]


def test_volatility_has_no_currency_and_zero_volume_is_kept(db):
    assert [(r.currency, r.volume) for r in run("vix")] == [(None, 0)]


def test_the_caret_is_optional_and_case_and_spacing_do_not_matter(db):
    assert run("^GSPC") == run("gspc") == run(" ^gspc ") == run("S&P  500") == run("spx")
    assert [r.index for r in run("000001.ss")] == ["000001.SS"]  # no caret to add
    assert [r.index for r in run("Shanghai Composite")] == ["000001.SS"]


def test_an_alias_returns_its_index_under_its_name(db):
    assert [(r.index, r.name) for a in ("dow", "Dow Jones", "DJIA") for r in run(a)] == [
        ("^DJI", "dow jones industrial average")] * 3


def test_an_unnamed_stored_index_is_served_with_no_name(db):
    assert [(r.index, r.name, r.currency) for r in run("zzz")] == [("^ZZZ", None, None)]


def test_a_stored_symbol_with_no_caret_or_dot_is_served_as_is(db, monkeypatch):
    monkeypatch.setitem(DATA, "XYZ", [bar(D1, "5")])
    assert [r.index for r in run("xyz")] == ["XYZ"]


def test_names_and_aliases_are_unique_and_point_at_listed_symbols():
    names = [name for name, _ in ix.INDEXES.values()]
    assert len(ix.INDEXES) == len(set(names)) == 55
    assert all(n == " ".join(n.split()).lower() for n in [*names, *ix.ALIASES])
    assert set(ix.ALIASES.values()) <= set(ix.INDEXES) and not set(ix.ALIASES) & set(names)
    assert set(ix.MIRRORS.values()) | set(ix.SCALED.values()) <= set(ix.INDEXES)
    vol = {s for s, (_, cur) in ix.INDEXES.items() if cur is None}
    assert vol == {"^GVZ", "^MOVE", "^OVX", "^RVX", "^VIX", "^VIX3M", "^VIX6M", "^VIX9D", "^VVIX", "^VXN"}


def test_all_reads_each_stored_index_once_ordered_by_symbol_then_date(db):
    rows = run("all")
    assert sorted(db) == sorted(DATA)
    assert [(r.index, r.date) for r in rows] == [
        ("000001.SS", D1), ("^DJI", D1), ("^GSPC", D1), ("^GSPC", D2), ("^VIX", D1), ("^ZZZ", D1)]


@pytest.mark.parametrize("base, match", [
    ("^DJX", r"\^DJX is a scaled copy of \^DJI and is not stored: use \^DJI"),
    ("xsp", r"\^XSP is a scaled copy of \^GSPC"),
    ("nanos", r"\^NANOS is a scaled copy of \^GSPC"),
    ("^MRUT", r"\^MRUT is a scaled copy of \^RUT"),
    ("^NDX", r"\^NDX is not a stored index"),  # listed, but not stored here
    ("nasdaq 100", r"\^NDX is not a stored index"),
    ("000002.ss", r"^000002\.SS is not a stored index"),
    ("s&p", r"\^S&P is not a stored index"),
])
def test_bad_symbols_rejected_before_any_read(db, base, match):
    with pytest.raises(ValueError, match=match):
        run(base)
    assert db == []


def test_start_after_end_rejected(db):
    with pytest.raises(ValueError, match="start must be on or before end"):
        run("^GSPC", start=D2, end=D1)
    assert db == []


def test_a_quote_multiplies_by_the_days_rate_and_carries_the_last_close_over_days_with_none(db):
    rows = run("^GSPC", quote="eur")
    assert [(r.index, r.currency, r.quote, r.date, r.route, r.quote_date) for r in rows] == [
        ("^GSPC", "USD", "EUR", D1, "crossed", D1), ("^GSPC", "USD", "EUR", D2, "crossed", D1)]
    assert [(r.open, r.close) for r in rows] == [
        (Decimal("7349") * Decimal("0.9"), Decimal("7350") * Decimal("0.8")),
        (Decimal("7375") * Decimal("0.8"), Decimal("7376") * Decimal("0.8"))]
    assert [(r.high, r.low, r.vwap, r.volume) for r in rows] == [(None, None, None, 7)] * 2
    assert db == [("USD", "EUR", D1 - timedelta(days=14), D2), "^GSPC"]


def test_products_keep_twelve_significant_digits(db, monkeypatch):
    monkeypatch.setitem(FX, ("USD", "EUR"), [rate(D1, "0.123456789012", "0.123456789012")])
    assert run("^GSPC", D1, D1, quote="EUR")[0].close == Decimal("907.407399238")


def test_days_before_the_first_rate_are_skipped(db, monkeypatch):
    monkeypatch.setitem(FX, ("USD", "EUR"), [rate(D2, "0.9", "0.8")])
    assert [r.date for r in run("^GSPC", quote="EUR")] == [D2]


def test_an_index_already_in_the_quote_is_served_as_stored(db):
    rows = run("^GSPC", quote="usd")
    assert [(r.quote, r.route, r.quote_date, r.high, r.vwap) for r in rows] == [
        ("USD", "stored", None, Decimal("7352"), Decimal("7351")), ("USD", "stored", None, Decimal("7378"), Decimal("7377"))]
    assert db == ["^GSPC"]


def test_no_quote_is_the_stored_level(db):
    assert [(r.quote, r.route, r.quote_date) for r in run("^GSPC")] == [(None, "stored", None)] * 2


def test_all_with_a_quote_reads_each_currency_once_and_leaves_out_indexes_with_none(db):
    rows = run("ALL", quote="EUR")
    assert [(r.index, r.date) for r in rows] == [("000001.SS", D1), ("^DJI", D1), ("^GSPC", D1), ("^GSPC", D2)]
    assert sorted(x[:2] for x in db if isinstance(x, tuple)) == [("CNY", "EUR"), ("USD", "EUR")]
    assert rows[0].close == Decimal("3900") * Decimal("0.13")


@pytest.mark.parametrize("base, quote, match", [
    ("vix", "EUR", r"\^VIX has no currency to convert from"),
    ("zzz", "EUR", r"\^ZZZ has no currency to convert from"),
    ("^GSPC", "all", "quote must be one currency"),
    ("^GSPC", "XYZ", "no rate for USD/XYZ"),
])
def test_bad_quotes_rejected_before_any_index_read(db, base, quote, match):
    with pytest.raises(ValueError, match=match):
        run(base, quote=quote)
    assert all(isinstance(x, tuple) for x in db)

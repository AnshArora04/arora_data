"""fred() and fred_series(): lookup by id or name, gaps and labels, with the database faked."""
import asyncio
from datetime import date
from decimal import Decimal

import pytest

from arora_data import _readers, fred as fr

D1, D2, D3 = date(2026, 11, 6), date(2026, 11, 9), date(2026, 11, 10)


def series(series_id, name, units="Percent"):
    return fr.FredSeries(series_id=series_id, name=name, units=units, frequency="daily", category="Rates",
                         first_date=D1, latest_date=D3)


STORED = [
    series("DFF", "Effective Fed Funds Rate"),
    series("DFII10", "10-Year TIPS Real Yield"),
    series("TWIN1", "Same Name"),
    series("TWIN2", "Same Name"),
    series("PCE", "Personal Consumption Expenditures", "Billions of Dollars"),
    series("XPCE", "pce"),  # a name that is another series' id
]
VALUES = {
    "DFF": [(D1, "3.88"), (D2, "3.87"), (D3, "3.86")],
    # D2 is a holiday: FRED's "." gap, stored as NULL. The row's own units are older than the latest label.
    "DFII10": [(D1, "1.71"), (D2, None), (D3, "1.69")],
}


@pytest.fixture
def db(monkeypatch):
    reads = []

    async def stored():
        reads.append("series")
        return STORED

    async def read_values(table, key, start, end, dataset=None):
        reads.append((table, key, start, end, dataset))
        return [_readers.Value(date=d, value=None if v is None else Decimal(v), units="Old Units", frequency="daily")
                for d, v in VALUES.get(key, []) if start <= d <= end]

    monkeypatch.setattr(fr, "fred_series", stored)
    monkeypatch.setattr(fr._readers, "_read_values", read_values)
    return reads


def run(name, start=D1, end=D3):
    return asyncio.run(fr.fred(name, start, end))


def test_by_id_oldest_first_with_name_and_units(db):
    assert [(v.series_id, v.name, v.date, v.value, v.units) for v in run("DFF")] == [
        ("DFF", "Effective Fed Funds Rate", d, Decimal(x), "Percent") for d, x in VALUES["DFF"]]
    assert db == ["series", ("macro.observations", "DFF", D1, D3, None)]


def test_an_id_or_name_in_any_case_and_spacing_is_the_same_series(db):
    rows = run("DFF")
    assert run("dff") == run(" Dff ") == run("effective fed funds rate") == run("  EFFECTIVE   Fed funds RATE ") == rows


def test_an_id_reads_once_alongside_the_list_and_a_name_reads_again_by_its_id(db):
    run(" dff ")
    assert db == ["series", ("macro.observations", "DFF", D1, D3, None)]
    db.clear()
    run("effective  fed funds rate")
    assert db == ["series", ("macro.observations", "EFFECTIVE FED FUNDS RATE", D1, D3, None),
                  ("macro.observations", "DFF", D1, D3, None)]


def test_a_null_value_is_left_out_not_filled(db):
    assert [(v.date, v.value) for v in run("dfii10")] == [(D1, Decimal("1.71")), (D3, Decimal("1.69"))]


def test_units_are_the_latest_label_not_the_rows_own(db):
    assert {v.units for v in run("DFII10")} == {"Percent"}


def test_the_range_reaches_the_read_and_end_defaults_to_today_eastern(db, monkeypatch):
    monkeypatch.setattr(fr, "today_et", lambda: D2)
    assert [v.date for v in asyncio.run(fr.fred("DFF", D2))] == [D2]
    assert db[-1] == ("macro.observations", "DFF", D2, D2, None)


def test_an_id_wins_over_a_name_that_spells_it(db):
    run("pce")
    assert db[-1][1] == "PCE"
    assert _readers._resolve("pce", STORED, fr._HINT).series_id == "PCE"
    assert _readers._resolve("Personal consumption expenditures", STORED, fr._HINT).series_id == "PCE"


def test_a_name_two_series_share_raises_naming_both(db):
    with pytest.raises(ValueError, match="names 2 series: use an id, one of TWIN1, TWIN2$"):
        run("same name")
    assert _readers._resolve("twin2", STORED, fr._HINT).series_id == "TWIN2"
    assert len(db) == 2  # the list and the guessed read, nothing after


@pytest.mark.parametrize("bad", ["DGS10", "", "  ", "Effective Fed Funds", None, 5])
def test_an_unknown_series_raises(db, bad):
    with pytest.raises(ValueError, match="is not a stored FRED series: use an id or name from fred_series()"):
        run(bad)
    assert len(db) == 2  # the list and the guessed read, nothing after


def test_start_after_end_raises_before_any_read(db):
    with pytest.raises(ValueError, match="start must be on or before end"):
        run("DFF", D3, D1)
    assert db == []


def test_fred_series_is_the_stored_list(db):
    assert asyncio.run(fr.fred_series()) == STORED

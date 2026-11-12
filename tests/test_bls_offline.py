"""bls() and bls_series(): lookup by id or name, gaps and labels, with the database faked."""
import asyncio
from datetime import date
from decimal import Decimal

import pytest

from arora_data import _readers, bls as bl

M8, M9, M10, M11 = date(2025, 8, 1), date(2025, 9, 1), date(2025, 10, 1), date(2025, 11, 1)


def series(series_id, name, units="percent", dataset="bls/core"):
    return bl.BlsSeries(series_id=series_id, dataset=dataset, name=name, units=units, frequency="monthly",
                        category="Labor", first_date=M8, latest_date=M11)


STORED = [
    series("LNS14000000", "Unemployment rate (SA)"),
    series("CES0000000001", "Total nonfarm employment (SA)", "thousands of persons", "bls/other"),
]
VALUES = {
    # October 2025 was never published: stored as NULL. The rows' own units are older than the latest label.
    ("bls/core", "LNS14000000"): [(M8, "4.3"), (M9, "4.4"), (M10, None), (M11, "4.5")],
    ("bls/other", "CES0000000001"): [(M8, "159540"), (M9, "159659")],
}


@pytest.fixture
def db(monkeypatch):
    reads = []

    async def stored():
        reads.append("series")
        return STORED

    async def read_values(table, key, start, end, dataset=None):
        reads.append((table, key, start, end, dataset))
        return [_readers.Value(date=d, value=None if v is None else Decimal(v), units="Old Units", frequency="monthly")
                for d, v in VALUES.get((dataset, key), []) if start <= d <= end]

    monkeypatch.setattr(bl, "bls_series", stored)
    monkeypatch.setattr(bl._readers, "_read_values", read_values)
    return reads


def run(name, start=M8, end=M11):
    return asyncio.run(bl.bls(name, start, end))


def test_by_id_oldest_first_with_name_and_latest_units_and_the_null_month_left_out(db):
    assert [(v.series_id, v.name, v.date, v.value, v.units) for v in run("LNS14000000")] == [
        ("LNS14000000", "Unemployment rate (SA)", d, Decimal(x), "percent") for d, x in [(M8, "4.3"), (M9, "4.4"), (M11, "4.5")]]
    assert db == ["series", ("indicators.bls", "LNS14000000", M8, M11, "bls/core")]


def test_an_id_or_name_in_any_case_and_spacing_is_the_same_series_read_in_its_dataset(db):
    rows = run("CES0000000001")
    assert run(" ces0000000001 ") == run("total  NONFARM employment (sa)") == rows
    assert [(v.date, v.value, v.units) for v in rows] == [(M8, Decimal("159540"), "thousands of persons"),
                                                          (M9, Decimal("159659"), "thousands of persons")]
    assert {r[4] for r in db if r != "series"} == {"bls/other"}


def test_the_range_reaches_the_read_and_end_defaults_to_today_eastern(db, monkeypatch):
    monkeypatch.setattr(bl, "today_et", lambda: M9)
    assert [v.date for v in asyncio.run(bl.bls("LNS14000000", M9))] == [M9]
    assert db[-1] == ("indicators.bls", "LNS14000000", M9, M9, "bls/core")


@pytest.mark.parametrize("bad", ["CPIAUCSL", "", "Unemployment rate", None, 5])
def test_an_unknown_series_raises_before_any_read(db, bad):
    with pytest.raises(ValueError, match=r"is not a stored BLS series: use an id or name from bls_series\(\)"):
        run(bad)
    assert db == ["series"]


def test_start_after_end_raises_before_any_read(db):
    with pytest.raises(ValueError, match="start must be on or before end"):
        run("LNS14000000", M11, M8)
    assert db == []


def test_bls_series_is_the_stored_list(db):
    assert asyncio.run(bl.bls_series()) == STORED

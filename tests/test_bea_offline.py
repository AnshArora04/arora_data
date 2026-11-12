"""bea() and bea_series(): lookup by id or name, gaps and labels, with the database faked."""
import asyncio
from datetime import date
from decimal import Decimal

import pytest

from arora_data import _readers, bea as be

Q1, Q2, Q3, Q4 = date(2025, 1, 1), date(2025, 4, 1), date(2025, 7, 1), date(2025, 10, 1)


def series(series_id, name, dataset, units="USD millions"):
    return be.BeaSeries(series_id=series_id, dataset=dataset, name=name, units=units, frequency="quarterly",
                        category="National Accounts", first_date=Q1, latest_date=Q4)


STORED = [
    series("A191RC", "Gross domestic product", "bea/nipa_t10105"),
    series("A191RL", "Real GDP, percent change (annual rate)", "bea/nipa_t10101", "percent change, annual rate"),
]
VALUES = {
    # Q3 is a gap, stored as NULL. The rows' own units are older than the latest label.
    ("bea/nipa_t10105", "A191RC"): [(Q1, "29962047"), (Q2, "30353902"), (Q3, None), (Q4, "31098027")],
    ("bea/nipa_t10101", "A191RL"): [(Q1, "-0.5"), (Q2, "3.8")],
}


@pytest.fixture
def db(monkeypatch):
    reads = []

    async def stored():
        reads.append("series")
        return STORED

    async def read_values(table, key, start, end, dataset=None):
        reads.append((table, key, start, end, dataset))
        return [_readers.Value(date=d, value=None if v is None else Decimal(v), units="Old Units", frequency="quarterly")
                for d, v in VALUES.get((dataset, key), []) if start <= d <= end]

    monkeypatch.setattr(be, "bea_series", stored)
    monkeypatch.setattr(be._readers, "_read_values", read_values)
    return reads


def run(name, start=Q1, end=Q4):
    return asyncio.run(be.bea(name, start, end))


def test_by_id_oldest_first_with_name_and_latest_units_and_the_null_quarter_left_out(db):
    assert [(v.series_id, v.name, v.date, v.value, v.units) for v in run("A191RC")] == [
        ("A191RC", "Gross domestic product", d, Decimal(x), "USD millions")
        for d, x in [(Q1, "29962047"), (Q2, "30353902"), (Q4, "31098027")]]
    assert db == ["series", ("indicators.bea", "A191RC", Q1, Q4, "bea/nipa_t10105")]


def test_an_id_or_name_in_any_case_and_spacing_is_the_same_series_read_in_its_dataset(db):
    rows = run("A191RL")
    assert run(" a191rl ") == run("real gdp,  PERCENT change (annual rate)") == rows
    assert [(v.date, v.value, v.units) for v in rows] == [(Q1, Decimal("-0.5"), "percent change, annual rate"),
                                                          (Q2, Decimal("3.8"), "percent change, annual rate")]
    assert {r[4] for r in db if r != "series"} == {"bea/nipa_t10101"}


def test_the_range_reaches_the_read_and_end_defaults_to_today_eastern(db, monkeypatch):
    monkeypatch.setattr(be._readers, "today_et", lambda: Q2)
    assert [v.date for v in asyncio.run(be.bea("A191RC", Q2))] == [Q2]
    assert db[-1] == ("indicators.bea", "A191RC", Q2, Q2, "bea/nipa_t10105")


@pytest.mark.parametrize("bad", ["GDP", "", "Gross domestic", None, 5])
def test_an_unknown_series_raises_before_any_read(db, bad):
    with pytest.raises(ValueError, match=r"is not a stored BEA series: use an id or name from bea_series\(\)"):
        run(bad)
    assert db == ["series"]


def test_start_after_end_raises_before_any_read(db):
    with pytest.raises(ValueError, match="start must be on or before end"):
        run("A191RC", Q4, Q1)
    assert db == []


def test_bea_series_is_the_stored_list(db):
    assert asyncio.run(be.bea_series()) == STORED

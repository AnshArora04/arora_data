"""nyfed() and nyfed_series(): lookup by id or name, gaps and labels, with the database faked."""
import asyncio
from datetime import date
from decimal import Decimal

import pytest

from arora_data import _readers, nyfed as ny

D1, D2, D3, D4 = date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3), date(2026, 4, 6)
W1, W2 = date(2014, 5, 14), date(2014, 5, 21)


def series(series_id, name, units, dataset, frequency):
    return ny.NyfedSeries(series_id=series_id, dataset=dataset, name=name, units=units, frequency=frequency,
                          category="Money Markets — Rates", first_date=D1, latest_date=D4)


STORED = [
    series("sofr", "Secured Overnight Financing Rate (SOFR)", "percent", "nyfed/reference_rates", "daily"),
    series("frn", "SOMA floating-rate notes", "USD millions", "nyfed/soma_summary", "weekly"),
]
VALUES = {
    # Good Friday 2026-04-03 has no SOFR row; the rows' own units are older than the latest label.
    ("nyfed/reference_rates", "sofr"): [(D1, "4.35"), (D2, "4.33"), (D4, "4.31")],
    # The Fed held no floating-rate notes before 2014-05-21: stored as NULL.
    ("nyfed/soma_summary", "frn"): [(W1, None), (W2, "1250.5")],
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
                for d, v in VALUES.get((dataset, key), []) if start <= d <= end]

    monkeypatch.setattr(ny, "nyfed_series", stored)
    monkeypatch.setattr(ny._readers, "_read_values", read_values)
    return reads


def run(name, start=D1, end=D4):
    return asyncio.run(ny.nyfed(name, start, end))


def test_by_id_oldest_first_with_name_and_latest_units(db):
    assert [(v.series_id, v.name, v.date, v.value, v.units) for v in run("sofr")] == [
        ("sofr", "Secured Overnight Financing Rate (SOFR)", d, Decimal(x), "percent")
        for d, x in [(D1, "4.35"), (D2, "4.33"), (D4, "4.31")]]
    assert db == ["series", ("indicators.nyfed", "sofr", D1, D4, "nyfed/reference_rates")]


def test_an_id_or_name_in_any_case_and_spacing_is_the_same_series_read_in_its_dataset_with_nulls_left_out(db):
    rows = run("frn", W1, W2)
    assert run(" FRN ", W1, W2) == run("soma  FLOATING-rate notes", W1, W2) == rows
    assert [(v.date, v.value, v.units) for v in rows] == [(W2, Decimal("1250.5"), "USD millions")]
    assert {r[4] for r in db if r != "series"} == {"nyfed/soma_summary"}


def test_the_range_reaches_the_read_and_end_defaults_to_today_eastern(db, monkeypatch):
    monkeypatch.setattr(ny._readers, "today_et", lambda: D2)
    assert [v.date for v in asyncio.run(ny.nyfed("sofr", D2))] == [D2]
    assert db[-1] == ("indicators.nyfed", "sofr", D2, D2, "nyfed/reference_rates")


@pytest.mark.parametrize("bad", ["DFF", "", "Secured Overnight Financing Rate", None, 5])
def test_an_unknown_series_raises_before_any_read(db, bad):
    with pytest.raises(ValueError, match=r"is not a stored New York Fed series: use an id or name from nyfed_series\(\)"):
        run(bad)
    assert db == ["series"]


def test_start_after_end_raises_before_any_read(db):
    with pytest.raises(ValueError, match="start must be on or before end"):
        run("sofr", D4, D1)
    assert db == []

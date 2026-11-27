"""eia() and eia_series(): lookup by id or name, the dataset and frequency filters, and gaps, with the database
faked."""
import asyncio
import re
from datetime import date
from decimal import Decimal

import pytest

from arora_data import _readers, eia

# The monthly rows are prod rows of 2026-11-26. The weekly labels are the main repo's EIA-ADD labels (#312), and the
# dates are the dates of its dry run. The NULL weekly row is a test case.
SPOT, CRUDE = "eia/petroleum_spot", "eia/crude_production"
STOCKS, GAS = "eia/weekly_petroleum_stocks", "eia/weekly_gas_storage"


def series(series_id, dataset, name, units, frequency, category, first, latest):
    return eia.EiaSeries(series_id=series_id, dataset=dataset, name=name, units=units, frequency=frequency,
                         category=category, first_date=first, latest_date=latest)


STORED = [
    series("MCRFPUS2", CRUDE, "US crude oil field production", "MBBL/D", "monthly", "Energy — Petroleum",
           date(1920, 1, 1), date(2026, 8, 1)),
    series("NW2_EPG0_SWO_R31_BCF", GAS, "Working gas in storage, East region", "BCF", "weekly",
           "Energy — Natural Gas", date(2010, 1, 1), date(2026, 11, 20)),
    series("NW2_EPG0_SWO_R48_BCF", GAS, "Working gas in storage, Lower 48", "BCF", "weekly", "Energy — Natural Gas",
           date(2010, 1, 1), date(2026, 11, 20)),
    series("RBRTE", SPOT, "Brent crude spot (Europe)", "$/BBL", "monthly", "Energy — Petroleum",
           date(1987, 5, 1), date(2026, 10, 1)),
    series("RWTC", SPOT, "WTI crude spot (Cushing)", "$/BBL", "monthly", "Energy — Petroleum",
           date(1986, 1, 1), date(2026, 10, 1)),
    series("WCESTUS1", STOCKS, "US crude oil stocks excluding SPR", "MBBL", "weekly", "Energy — Petroleum",
           date(1982, 8, 20), date(2026, 11, 20)),
]
M1, M2, M3 = date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)
W1, W2, W3 = date(2026, 11, 6), date(2026, 11, 13), date(2026, 11, 20)
VALUES = {
    "RWTC": [(M1, "83.90"), (M2, "97.31"), (M3, "102.07")],
    "RBRTE": [(M1, "91.08"), (M2, "114.16"), (M3, "123.93")],
    "NW2_EPG0_SWO_R48_BCF": [(W1, "3900"), (W2, None), (W3, "3950")],
}


@pytest.fixture
def db(monkeypatch):
    reads = []

    async def stored(sql):
        reads.append("series")
        assert sql is eia._SERIES_SQL
        return STORED

    async def read_values(table, key, start, end, dataset=None):
        reads.append((table, key, start, end, dataset))
        return [_readers.Value(date=d, value=None if v is None else Decimal(v), units="Old Units", frequency="weekly")
                for d, v in VALUES.get(key, []) if start <= d <= end]

    monkeypatch.setattr(eia._readers, "_indicator_series", stored)
    monkeypatch.setattr(eia._readers, "_read_values", read_values)
    return reads


def run(name, start=M1, end=M3):
    return asyncio.run(eia.eia(name, start, end))


def ids(**filters):
    return [s.series_id for s in asyncio.run(eia.eia_series(**filters))]


def test_by_id_oldest_first_with_name_and_latest_units(db):
    assert [(v.series_id, v.name, v.date, v.value, v.units) for v in run("RWTC")] == [
        ("RWTC", "WTI crude spot (Cushing)", d, Decimal(x), "$/BBL") for d, x in VALUES["RWTC"]]
    assert db == ["series", ("indicators.eia", "RWTC", M1, M3, SPOT)]


def test_an_id_or_name_in_any_case_and_spacing_is_the_same_series_with_nulls_left_out(db):
    rows = run("NW2_EPG0_SWO_R48_BCF", W1, W3)
    assert run(" nw2_epg0_swo_r48_bcf ", W1, W3) == run("working gas in storage,  LOWER 48", W1, W3) == rows
    assert [(v.date, v.value) for v in rows] == [(W1, Decimal("3900")), (W3, Decimal("3950"))]
    assert db[-1] == ("indicators.eia", "NW2_EPG0_SWO_R48_BCF", W1, W3, GAS)


def test_the_range_reaches_the_read_and_end_defaults_to_today_eastern(db, monkeypatch):
    monkeypatch.setattr(eia._readers, "today_et", lambda: M2)
    assert [v.date for v in asyncio.run(eia.eia("RBRTE", M2))] == [M2]
    assert db[-1] == ("indicators.eia", "RBRTE", M2, M2, SPOT)


@pytest.mark.parametrize("bad", ["WTI", "", "RWTC1", "Brent", "Henry Hub", None, 5])
def test_an_unknown_series_raises_before_any_read(db, bad):
    with pytest.raises(ValueError, match=r"is not a stored EIA series: use an id or name from eia_series\(\)"):
        run(bad)
    assert db == ["series"]


def test_start_after_end_raises_before_any_read(db):
    with pytest.raises(ValueError, match="start must be on or before end"):
        run("RWTC", M3, M1)
    assert db == []


def test_eia_series_gives_every_stored_series_without_a_filter(db):
    assert ids() == [s.series_id for s in STORED]


def test_eia_series_keeps_one_dataset_or_one_frequency_or_both(db):
    assert ids(dataset=SPOT) == ["RBRTE", "RWTC"]
    assert ids(frequency="weekly") == ["NW2_EPG0_SWO_R31_BCF", "NW2_EPG0_SWO_R48_BCF", "WCESTUS1"]
    assert ids(dataset=GAS, frequency="weekly") == ["NW2_EPG0_SWO_R31_BCF", "NW2_EPG0_SWO_R48_BCF"]
    # Both values are stored, but no series has both: no error.
    assert ids(dataset=CRUDE, frequency="weekly") == []


@pytest.mark.parametrize("filters, message", [
    ({"dataset": "eia/natgas_spot"}, "'eia/natgas_spot' is not a stored EIA dataset: use one of " + ", ".join(
        sorted({s.dataset for s in STORED}))),
    ({"frequency": "Weekly"}, "'Weekly' is not a stored EIA frequency: use one of monthly, weekly"),
    ({"frequency": "daily", "dataset": SPOT}, "'daily' is not a stored EIA frequency"),
])
def test_eia_series_raises_for_a_value_no_stored_series_has(db, filters, message):
    with pytest.raises(ValueError, match="^" + re.escape(message)):
        asyncio.run(eia.eia_series(**filters))

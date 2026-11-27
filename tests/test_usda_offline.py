"""usda() and usda_series(): lookup by id or name, the dataset and frequency filters, and gaps, with the database
faked."""
import asyncio
import re
from datetime import date
from decimal import Decimal

import pytest

from arora_data import _readers, usda

# The labels and values are prod rows of 2026-11-27. The NULL corn price for 2024 (prod: 4.26) and the monthly
# series are test cases: prod has neither on that day.
CORN, HOGS = "usda/corn", "usda/hogs"
CORN_PRICE = "CORN, GRAIN - PRICE RECEIVED, MEASURED IN $ / BU"
CORN_YIELD = "CORN, GRAIN - YIELD, MEASURED IN BU / ACRE"
HOG_INVENTORY = "HOGS - INVENTORY"
CORN_MONTHLY = "CORN, GRAIN - PRICE RECEIVED, MEASURED IN $ / BU|MONTHLY"


def series(series_id, dataset, name, units, frequency, category, first, latest):
    return usda.UsdaSeries(series_id=series_id, dataset=dataset, name=name, units=units, frequency=frequency,
                           category=category, first_date=first, latest_date=latest)


STORED = [
    series(CORN_PRICE, CORN, "Corn price received", "$/BU", "annual", "Agriculture — Crops",
           date(1996, 1, 1), date(2025, 1, 1)),
    series(CORN_MONTHLY, CORN, "Corn price received, monthly", "$/BU", "monthly", "Agriculture — Crops",
           date(1908, 8, 1), date(2026, 10, 1)),
    series(CORN_YIELD, CORN, "Corn yield", "BU/ACRE", "annual", "Agriculture — Crops",
           date(1866, 1, 1), date(2026, 1, 1)),
    series(HOG_INVENTORY, HOGS, "Hog inventory", "HEAD", "annual", "Agriculture — Livestock",
           date(2024, 1, 1), date(2025, 1, 1)),
]
Y1, Y2, Y3 = date(2023, 1, 1), date(2024, 1, 1), date(2025, 1, 1)
VALUES = {
    CORN_PRICE: [(Y1, "5.95"), (Y2, None), (Y3, "4.29")],
    HOG_INVENTORY: [(Y2, "75075400"), (Y3, "75414700")],
}


@pytest.fixture
def db(monkeypatch):
    reads = []

    async def stored(sql):
        reads.append("series")
        assert sql is usda._SERIES_SQL
        return STORED

    async def read_values(table, key, start, end, dataset=None):
        reads.append((table, key, start, end, dataset))
        return [_readers.Value(date=d, value=None if v is None else Decimal(v), units="Old Units", frequency="annual")
                for d, v in VALUES.get(key, []) if start <= d <= end]

    monkeypatch.setattr(usda._readers, "_indicator_series", stored)
    monkeypatch.setattr(usda._readers, "_read_values", read_values)
    return reads


def run(name, start=Y1, end=Y3):
    return asyncio.run(usda.usda(name, start, end))


def ids(**filters):
    return [s.series_id for s in asyncio.run(usda.usda_series(**filters))]


def test_by_id_oldest_first_with_name_and_latest_units_and_nulls_left_out(db):
    assert [(v.series_id, v.name, v.date, v.value, v.units) for v in run(CORN_PRICE)] == [
        (CORN_PRICE, "Corn price received", Y1, Decimal("5.95"), "$/BU"),
        (CORN_PRICE, "Corn price received", Y3, Decimal("4.29"), "$/BU")]
    assert db == ["series", ("indicators.usda", CORN_PRICE, Y1, Y3, CORN)]


def test_an_id_or_name_in_any_case_and_spacing_is_the_same_series(db):
    rows = run(HOG_INVENTORY)
    assert run("  hogs - inventory ") == run("HOG   inventory") == rows
    assert [(v.date, v.value) for v in rows] == [(Y2, Decimal("75075400")), (Y3, Decimal("75414700"))]
    assert db[-1] == ("indicators.usda", HOG_INVENTORY, Y1, Y3, HOGS)


def test_the_range_reaches_the_read_and_end_defaults_to_today_eastern(db, monkeypatch):
    monkeypatch.setattr(usda._readers, "today_et", lambda: Y2)
    assert [v.date for v in asyncio.run(usda.usda(HOG_INVENTORY, Y2))] == [Y2]
    assert db[-1] == ("indicators.usda", HOG_INVENTORY, Y2, Y2, HOGS)


@pytest.mark.parametrize("bad", ["CORN", "", "Corn price", "corn, grain - price received", None, 5])
def test_an_unknown_series_raises_before_any_read(db, bad):
    with pytest.raises(ValueError, match=r"is not a stored USDA series: use an id or name from usda_series\(\)"):
        run(bad)
    assert db == ["series"]


def test_start_after_end_raises_before_any_read(db):
    with pytest.raises(ValueError, match="start must be on or before end"):
        run(CORN_PRICE, Y3, Y1)
    assert db == []


def test_usda_series_gives_every_stored_series_without_a_filter(db):
    assert ids() == [s.series_id for s in STORED]


def test_usda_series_keeps_one_dataset_or_one_frequency_or_both(db):
    assert ids(dataset=CORN) == [CORN_PRICE, CORN_MONTHLY, CORN_YIELD]
    assert ids(frequency="annual") == [CORN_PRICE, CORN_YIELD, HOG_INVENTORY]
    assert ids(dataset=CORN, frequency="monthly") == [CORN_MONTHLY]
    # Both values are stored, but no series has both: no error.
    assert ids(dataset=HOGS, frequency="monthly") == []


@pytest.mark.parametrize("filters, message", [
    ({"dataset": "usda/barley"}, "'usda/barley' is not a stored USDA dataset: use one of usda/corn, usda/hogs"),
    ({"frequency": "Annual"}, "'Annual' is not a stored USDA frequency: use one of annual, monthly"),
    ({"frequency": "daily", "dataset": CORN}, "'daily' is not a stored USDA frequency"),
])
def test_usda_series_raises_for_a_value_no_stored_series_has(db, filters, message):
    with pytest.raises(ValueError, match="^" + re.escape(message)):
        asyncio.run(usda.usda_series(**filters))

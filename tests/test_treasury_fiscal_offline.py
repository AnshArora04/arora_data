"""treasury_fiscal() and treasury_fiscal_series(): lookup by id or name, gaps and labels, with the database faked."""
import asyncio
from datetime import date
from decimal import Decimal

import pytest

from arora_data import _readers, treasury_fiscal as tf

D1, D2, D3 = date(2005, 3, 30), date(2005, 3, 31), date(2005, 4, 4)
DATASET = "treasury/debt_to_penny"


def series(series_id, name):
    return tf.TreasuryFiscalSeries(series_id=series_id, dataset=DATASET, name=name, units="USD millions",
                                   frequency="daily", category="Fiscal — Debt", first_date=D1, latest_date=D3)


STORED = [
    series("tot_pub_debt_out_amt", "Total Public Debt Outstanding"),
    series("debt_held_public_amt", "Debt Held by the Public"),
]
VALUES = {
    # The parts were split out of the total only at month end until 2005-04-04: other days are stored as NULL.
    "tot_pub_debt_out_amt": [(D1, "7776986.61"), (D2, "7776939.07"), (D3, "7786476.16")],
    "debt_held_public_amt": [(D1, None), (D2, "4592591.24"), (D3, "4600069.31")],
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

    monkeypatch.setattr(tf, "treasury_fiscal_series", stored)
    monkeypatch.setattr(tf._readers, "_read_values", read_values)
    return reads


def run(name, start=D1, end=D3):
    return asyncio.run(tf.treasury_fiscal(name, start, end))


def test_by_id_oldest_first_with_name_and_latest_units(db):
    assert [(v.series_id, v.name, v.date, v.value, v.units) for v in run("tot_pub_debt_out_amt")] == [
        ("tot_pub_debt_out_amt", "Total Public Debt Outstanding", d, Decimal(x), "USD millions")
        for d, x in [(D1, "7776986.61"), (D2, "7776939.07"), (D3, "7786476.16")]]
    assert db == ["series", ("indicators.treasury_fiscal", "tot_pub_debt_out_amt", D1, D3, DATASET)]


def test_an_id_or_name_in_any_case_and_spacing_is_the_same_series_with_nulls_left_out(db):
    rows = run("debt_held_public_amt")
    assert run(" DEBT_HELD_PUBLIC_AMT ") == run("debt held by  the PUBLIC") == rows
    assert [(v.date, v.value) for v in rows] == [(D2, Decimal("4592591.24")), (D3, Decimal("4600069.31"))]


def test_the_range_reaches_the_read_and_end_defaults_to_today_eastern(db, monkeypatch):
    monkeypatch.setattr(tf._readers, "today_et", lambda: D2)
    assert [v.date for v in asyncio.run(tf.treasury_fiscal("tot_pub_debt_out_amt", D2))] == [D2]
    assert db[-1] == ("indicators.treasury_fiscal", "tot_pub_debt_out_amt", D2, D2, DATASET)


@pytest.mark.parametrize("bad", ["GFDEBTN", "", "Total Public Debt", None, 5])
def test_an_unknown_series_raises_before_any_read(db, bad):
    with pytest.raises(ValueError, match=r"is not a stored US Treasury fiscal series: use an id or name from "
                                         r"treasury_fiscal_series\(\)"):
        run(bad)
    assert db == ["series"]


def test_start_after_end_raises_before_any_read(db):
    with pytest.raises(ValueError, match="start must be on or before end"):
        run("tot_pub_debt_out_amt", D3, D1)
    assert db == []

"""treasury() tenors, ALL and missing values, with the database faked."""
import asyncio
from datetime import date
from decimal import Decimal

import pytest

from arora_data import treasury as tr

D1, D2, D3 = date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)
COLUMNS = list(tr.TENORS.values())


def row(d, **values):
    return {"date": d, **{c: None for c in COLUMNS}, **{c: Decimal(v) for c, v in values.items()}}


ROWS = [
    row(D1, month3="4.21", year10="4.90", year30="5.28"),
    row(D2),  # a day with no values (2010-10-11 in prod)
    row(D3, month3="4.19", year10="4.86"),  # no 30Y that day
]


@pytest.fixture
def db(monkeypatch):
    reads = []

    async def rows(start, end):
        reads.append((start, end))
        return [r for r in ROWS if start <= r["date"] <= end]

    monkeypatch.setattr(tr, "_rows", rows)
    return reads


def run(tenor=None, start=D1, end=D3):
    return asyncio.run(tr.treasury(start, end, tenor))


def test_one_tenor_by_date_leaving_out_days_with_no_value(db):
    assert [(r.tenor, r.date, r.yield_pct) for r in run("10Y")] == [
        ("10Y", D1, Decimal("4.90")), ("10Y", D3, Decimal("4.86"))]
    assert [r.date for r in run("30Y")] == [D1]
    assert run("1M") == []
    assert db == [(D1, D3)] * 3


def test_no_tenor_or_all_gives_every_tenor_in_maturity_order_then_date(db):
    rows = run()
    assert [(r.tenor, r.date) for r in rows] == [("3M", D1), ("3M", D3), ("10Y", D1), ("10Y", D3), ("30Y", D1)]
    assert run("all") == run(" ALL ") == rows


def test_a_tenor_in_any_case_or_by_column_name(db):
    assert run("10y") == run(" 10Y ") == run("year10") == run("YEAR10") == run("10 y")


def test_tenors_are_in_maturity_order_and_columns_are_unique():
    assert list(tr.TENORS) == ["1M", "2M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]
    assert len(set(COLUMNS)) == 12


@pytest.mark.parametrize("tenor", ["10", "4M", "10 years", "yield10", ""])
def test_bad_tenor_rejected_before_any_read(db, tenor):
    with pytest.raises(ValueError, match="is not a tenor: use one of 1M, 2M, 3M"):
        run(tenor)
    assert db == []


def test_start_after_end_rejected(db):
    with pytest.raises(ValueError, match="start must be on or before end"):
        run(start=D3, end=D1)
    assert db == []

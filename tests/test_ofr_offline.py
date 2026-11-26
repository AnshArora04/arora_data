"""ofr() and ofr_series(): lookup by id or name, gaps and labels, with the database faked."""
import asyncio
from datetime import date
from decimal import Decimal

import pytest

from arora_data import _readers, ofr

# These rows are the rows of the OFR-FULL dry run (feature/ofr-full @ 64ea8d6), with the OFR names, units and datasets.
STFM, FSI = "ofr/short_term_funding", "ofr/financial_stress_index"


def series(series_id, dataset, name, units, category, first, latest):
    return ofr.OfrSeries(series_id=series_id, dataset=dataset, name=name, units=units, frequency="daily",
                         category=category, first_date=first, latest_date=latest)


STORED = [
    series("OFR_FSI", FSI, "OFR Financial Stress Index", "index", "fsi", date(2000, 1, 3), date(2000, 1, 5)),
    series("OFR_FSI_EQUITY_VALUATION", FSI, "OFR FSI: equity valuation", "index", "fsi",
           date(2000, 1, 3), date(2000, 1, 5)),
    series("REPO-DVP_AR_OO-P", STFM, "DVP Service Average Rate: Overnight/Open (Preliminary)", "percent", "repo",
           date(2018, 5, 8), date(2018, 5, 9)),
]
VALUES = {
    "OFR_FSI": [(date(2000, 1, 3), "2.14"), (date(2000, 1, 4), "2.421"), (date(2000, 1, 5), "2.297")],
    "OFR_FSI_EQUITY_VALUATION": [(date(2000, 1, 3), "-0.051"), (date(2000, 1, 4), "0.079"), (date(2000, 1, 5), "0.08")],
    # 2018-05-07 is the first DVP day. It has no value, thus the table has NULL.
    "REPO-DVP_AR_OO-P": [(date(2018, 5, 7), None), (date(2018, 5, 8), "1.69"), (date(2018, 5, 9), "1.70")],
}
D1, D3 = date(2000, 1, 3), date(2000, 1, 5)
R1, R2, R3 = date(2018, 5, 7), date(2018, 5, 8), date(2018, 5, 9)


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

    monkeypatch.setattr(ofr, "ofr_series", stored)
    monkeypatch.setattr(ofr._readers, "_read_values", read_values)
    return reads


def run(name, start=D1, end=D3):
    return asyncio.run(ofr.ofr(name, start, end))


def test_by_id_oldest_first_with_name_and_latest_units(db):
    assert [(v.series_id, v.name, v.date, v.value, v.units) for v in run("OFR_FSI")] == [
        ("OFR_FSI", "OFR Financial Stress Index", d, Decimal(x), "index")
        for d, x in [(D1, "2.14"), (date(2000, 1, 4), "2.421"), (D3, "2.297")]]
    assert db == ["series", ("indicators.ofr", "OFR_FSI", D1, D3, FSI)]


def test_an_id_or_name_in_any_case_and_spacing_is_the_same_series_with_nulls_left_out(db):
    rows = run("REPO-DVP_AR_OO-P", R1, R3)
    name = "dvp service average rate:  OVERNIGHT/open (preliminary)"
    assert run(" repo-dvp_ar_oo-p ", R1, R3) == run(name, R1, R3) == rows
    assert [(v.date, v.value) for v in rows] == [(R2, Decimal("1.69")), (R3, Decimal("1.70"))]
    assert db[-1] == ("indicators.ofr", "REPO-DVP_AR_OO-P", R1, R3, STFM)


def test_negative_values_are_kept(db):
    assert [v.value for v in run("ofr fsi: equity valuation")][0] == Decimal("-0.051")


def test_the_range_reaches_the_read_and_end_defaults_to_today_eastern(db, monkeypatch):
    monkeypatch.setattr(ofr._readers, "today_et", lambda: date(2000, 1, 4))
    assert [v.date for v in asyncio.run(ofr.ofr("OFR_FSI", date(2000, 1, 4)))] == [date(2000, 1, 4)]
    assert db[-1] == ("indicators.ofr", "OFR_FSI", date(2000, 1, 4), date(2000, 1, 4), FSI)


@pytest.mark.parametrize("bad", ["sofr", "", "OFR FSI", "DVP Repo — Avg Rate, Overnight", None, 5])
def test_an_unknown_series_raises_before_any_read(db, bad):
    with pytest.raises(ValueError, match=r"is not a stored OFR series: use an id or name from ofr_series\(\)"):
        run(bad)
    assert db == ["series"]


def test_start_after_end_raises_before_any_read(db):
    with pytest.raises(ValueError, match="start must be on or before end"):
        run("OFR_FSI", D3, D1)
    assert db == []

"""ofr() and ofr_series(): lookup by id or name, gaps and labels, with the database faked."""
import asyncio
from datetime import date
from decimal import Decimal

import pytest

from arora_data import _readers, ofr

D1, D2, D3 = date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)
DATASET = "ofr/short_term_funding"


def series(series_id, name, units):
    return ofr.OfrSeries(series_id=series_id, dataset=DATASET, name=name, units=units, frequency="daily",
                         category="Money Markets — Repo", first_date=D1, latest_date=D3)


STORED = [
    series("REPO-DVP_AR_OO-P", "DVP Repo — Avg Rate, Overnight", "percent"),
    series("REPO-TRI_TV_TOT-P", "Tri-Party Repo — Total Volume", "USD millions"),
]
VALUES = {
    # 2025-01-03 failed OFR's disclosure threshold, so DVP has no value that day: stored as NULL.
    "REPO-DVP_AR_OO-P": [(D1, "4.41"), (D2, None), (D3, "-0.08")],
    "REPO-TRI_TV_TOT-P": [(D1, "2101234.5"), (D2, "2098765.25"), (D3, "2110000")],
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

    monkeypatch.setattr(ofr, "ofr_series", stored)
    monkeypatch.setattr(ofr._readers, "_read_values", read_values)
    return reads


def run(name, start=D1, end=D3):
    return asyncio.run(ofr.ofr(name, start, end))


def test_by_id_oldest_first_with_name_and_latest_units(db):
    assert [(v.series_id, v.name, v.date, v.value, v.units) for v in run("REPO-TRI_TV_TOT-P")] == [
        ("REPO-TRI_TV_TOT-P", "Tri-Party Repo — Total Volume", d, Decimal(x), "USD millions")
        for d, x in [(D1, "2101234.5"), (D2, "2098765.25"), (D3, "2110000")]]
    assert db == ["series", ("indicators.ofr", "REPO-TRI_TV_TOT-P", D1, D3, DATASET)]


def test_an_id_or_name_in_any_case_and_spacing_is_the_same_series_with_nulls_left_out_and_negatives_kept(db):
    rows = run("REPO-DVP_AR_OO-P")
    assert run(" repo-dvp_ar_oo-p ") == run("dvp repo —  AVG rate, overnight") == rows
    assert [(v.date, v.value) for v in rows] == [(D1, Decimal("4.41")), (D3, Decimal("-0.08"))]


def test_the_range_reaches_the_read_and_end_defaults_to_today_eastern(db, monkeypatch):
    monkeypatch.setattr(ofr._readers, "today_et", lambda: D2)
    assert [v.date for v in asyncio.run(ofr.ofr("REPO-TRI_TV_TOT-P", D2))] == [D2]
    assert db[-1] == ("indicators.ofr", "REPO-TRI_TV_TOT-P", D2, D2, DATASET)


@pytest.mark.parametrize("bad", ["sofr", "", "DVP Repo", None, 5])
def test_an_unknown_series_raises_before_any_read(db, bad):
    with pytest.raises(ValueError, match=r"is not a stored OFR series: use an id or name from ofr_series\(\)"):
        run(bad)
    assert db == ["series"]


def test_start_after_end_raises_before_any_read(db):
    with pytest.raises(ValueError, match="start must be on or before end"):
        run("REPO-DVP_AR_OO-P", D3, D1)
    assert db == []

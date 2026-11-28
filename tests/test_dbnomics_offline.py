"""dbnomics() and dbnomics_series(): lookup by id or name, the filters, the country and stale rules, and gaps, with
the database faked."""
import asyncio
import re
from datetime import date
from decimal import Decimal

import pytest

from arora_data import _readers, dbnomics

# The ids, datasets and dates are prod values of 2026-11-27, and the names are shortened. The NULL value and the
# weekly series are test cases.
CLI, HICP, CBPOL, WDI = "OECD/DSD_STES@DF_CLI", "ECB/HICP", "BIS/WS_CBPOL", "WB/WDI"
US_CLI, EA_HICP, EA_RATE = "USA.M.LI.IX._Z.AA.IX._Z.H", "M.U2.N.000000.4D0.ANR", "M.XM"
TODAY = date(2026, 11, 27)


def series(series_id, dataset, name, units, frequency, category, first, latest):
    return _readers.IndicatorSeries(series_id=series_id, dataset=dataset, name=name, units=units, frequency=frequency,
                                    category=category, first_date=first, latest_date=latest)


STORED = [
    series("A-NY.GDP.MKTP.KD.ZG-WLD", WDI, "Annual – GDP growth (annual %) – World", "percent", "annual", "Growth",
           date(1961, 1, 1), date(2023, 1, 1)),
    series(EA_HICP, HICP, "Monthly – Euro area – HICP - Total – Annual rate of change", "percent", "monthly",
           "Inflation", date(1997, 1, 1), date(2026, 9, 1)),
    series(EA_RATE, CBPOL, " Central bank policy rates - Euro area - Monthly - End of period", "percent", "monthly",
           "Rates", date(1999, 1, 1), date(2025, 6, 1)),
    series(US_CLI, CLI, "United States – Monthly – Composite leading indicator (CLI)", "index", "monthly",
           "Leading Indicators", date(1955, 1, 1), date(2026, 5, 1)),
    series("X.W", "NEW/WEEKLY", None, None, "weekly", None, None, None),
]
M1, M2, M3 = date(2026, 3, 1), date(2026, 4, 1), date(2026, 5, 1)
VALUES = {US_CLI: [(M1, "100.21"), (M2, None), (M3, "100.35")]}


@pytest.fixture
def db(monkeypatch):
    reads = []

    async def stored(sql):
        reads.append("series")
        assert sql is dbnomics._SERIES_SQL
        return STORED

    async def read_values(table, key, start, end, dataset=None):
        reads.append((table, key, start, end, dataset))
        return [_readers.Value(date=d, value=None if v is None else Decimal(v), units="Old", frequency="monthly")
                for d, v in VALUES.get(key, []) if start <= d <= end]

    monkeypatch.setattr(dbnomics._readers, "_indicator_series", stored)
    monkeypatch.setattr(dbnomics._readers, "_read_values", read_values)
    monkeypatch.setattr(dbnomics, "today_et", lambda: TODAY)
    return reads


def run(name, start=M1, end=M3):
    return asyncio.run(dbnomics.dbnomics(name, start, end))


def listed(**filters):
    return asyncio.run(dbnomics.dbnomics_series(**filters))


def test_by_id_oldest_first_with_name_and_latest_units_and_nulls_left_out(db):
    assert [(v.series_id, v.date, v.value, v.units) for v in run(US_CLI)] == [
        (US_CLI, M1, Decimal("100.21"), "index"), (US_CLI, M3, Decimal("100.35"), "index")]
    assert db == ["series", ("indicators.dbnomics", US_CLI, M1, M3, CLI)]


def test_an_id_or_name_in_any_case_and_spacing_is_the_same_series(db):
    assert run(" usa.m.li.ix._z.aa.ix._z.h ") == run("UNITED STATES – Monthly –  Composite leading indicator (CLI)") \
        == run(US_CLI)


@pytest.mark.parametrize("bad", ["USA", "", "CLI", "M.U2", None, 5])
def test_an_unknown_series_raises_before_any_read(db, bad):
    with pytest.raises(ValueError, match=r"is not a stored DBnomics series: use an id or name from dbnomics_series\(\)"):
        run(bad)
    assert db == ["series"]


def test_start_after_end_raises_before_any_read(db):
    with pytest.raises(ValueError, match="start must be on or before end"):
        run(US_CLI, M3, M1)
    assert db == []


def test_dbnomics_series_gives_every_stored_series_with_its_country_and_stale_flag(db):
    assert [(s.series_id, s.country, s.stale) for s in listed()] == [
        ("A-NY.GDP.MKTP.KD.ZG-WLD", "WLD", True), (EA_HICP, "U2", False), (EA_RATE, "XM", True),
        (US_CLI, "USA", True), ("X.W", None, None)]
    assert [s.model_dump(exclude={"country", "stale"}) for s in listed()] == [s.model_dump() for s in STORED]


def test_dbnomics_series_keeps_one_dataset_or_one_frequency_or_both(db):
    assert [s.series_id for s in listed(dataset=HICP)] == [EA_HICP]
    assert [s.series_id for s in listed(frequency="monthly")] == [EA_HICP, EA_RATE, US_CLI]
    assert [s.series_id for s in listed(dataset=CLI, frequency="monthly")] == [US_CLI]
    assert listed(dataset=WDI, frequency="monthly") == []


@pytest.mark.parametrize("filters, message", [
    ({"dataset": "OECD"}, "'OECD' is not a stored DBnomics dataset: use one of BIS/WS_CBPOL, ECB/HICP, NEW/WEEKLY,"),
    ({"frequency": "Monthly"}, "'Monthly' is not a stored DBnomics frequency: use one of annual, monthly, weekly"),
])
def test_dbnomics_series_raises_for_a_value_no_stored_series_has(db, filters, message):
    with pytest.raises(ValueError, match="^" + re.escape(message)):
        listed(**filters)


@pytest.mark.parametrize("dataset, series_id, country", [
    (CLI, "G7.M.BCICP.IX._Z.AA.IX._Z.H", "G7"),
    ("AMECO/ZUTN", "EA20.1.0.0.0.ZUTN", "EA20"),
    ("Eurostat/ei_bssi_m_r2", "M.BS-ESI-I.SA.EA20", "EA20"),
    (WDI, "A-NY.GDP.PCAP.CD-CHN", "CHN"),
    ("BIS/WS_DSR", "Q.DE.P", "DE"),
    ("BIS/WS_EER", "M.R.B.XM", "XM"),
    ("ECB/FM", "D.U2.EUR.4F.KR.DFR.LEV", "U2"),
    ("ECB/MNA", "Q.Y.I10.W2.S1.S1.B.B1GQ._Z._Z._Z.EUR.LR.G1", "I10"),
    ("ECB/EXR", "M.E03.EUR.ERC0.A", "EUR"),
    ("ONS/MM23", "D7G7.M", None),
    ("BIS/WS_X", "M", None),
])
def test_country_is_the_area_code_at_its_place_in_the_series_code(dataset, series_id, country):
    assert dbnomics._country(dataset, series_id) == country


@pytest.mark.parametrize("frequency, latest, last_fresh_day", [
    ("monthly", date(2026, 5, 1), date(2026, 10, 29)),   # 2026-07-01 + 120 days
    ("quarterly", date(2026, 4, 1), date(2027, 1, 29)),  # 2026-10-01 + 120 days
    ("annual", date(2025, 1, 1), date(2027, 5, 1)),      # 2027-01-01 + 120 days
    ("daily", date(2026, 10, 31), date(2027, 3, 2)),     # 2026-11-02 + 120 days
    ("weekly", date(2026, 10, 2), date(2027, 2, 13)),    # 2026-10-16 + 120 days
    ("monthly", date(2025, 12, 31), date(2026, 6, 28)),  # 2026-02-28 + 120 days: the day is cut to the month end
])
def test_stale_is_true_from_the_day_after_2_periods_plus_120_days(frequency, latest, last_fresh_day):
    assert dbnomics._stale(latest, frequency, last_fresh_day) is False
    assert dbnomics._stale(latest, frequency, date.fromordinal(last_fresh_day.toordinal() + 1)) is True


def test_stale_is_none_without_a_value_or_a_known_frequency():
    assert dbnomics._stale(None, "monthly", TODAY) is None
    assert dbnomics._stale(date(2026, 1, 1), "semiannual", TODAY) is None
    assert dbnomics._stale(date(2026, 1, 1), None, TODAY) is None

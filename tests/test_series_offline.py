"""Series record and stale-check rules that need no database."""
import asyncio
import contextlib
from datetime import date

import pytest

from arora_data import _readers, series
from arora_data.series import MAX_AGE_DAYS, Series

TODAY = date(2026, 11, 3)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"table": "market.price_history", "key": "AAPL", "frequency": "daily"},
        {"table": "indicators.bls", "key": "X"},
        {"table": "macro.observations", "key": "GDP", "dataset": "x"},
        {"table": "forex.prices", "key": "USDCAD"},
        {"table": "macro.economic_indicators", "key": "GDP"},
        {"table": "forex.prices", "key": "USDCAD", "frequency": "hourly"},
    ],
)
def test_bad_records_rejected(kwargs):
    with pytest.raises(ValueError):
        Series(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"table": "forex.prices", "key": "USDCAD", "frequency": "daily"},
        {"table": "macro.observations", "key": "GDP"},
        {"table": "indicators.bls", "key": "X", "dataset": "y"},
        {"table": "macro.economic_indicators", "key": "GDP", "frequency": "quarterly"},
    ],
)
def test_good_records_accepted(kwargs):
    Series(**kwargs)


def fresh(monkeypatch, latest, label, record=None):
    async def fake_latest(table, key, dataset):
        return latest, label

    monkeypatch.setattr(_readers, "_latest", fake_latest)
    return asyncio.run(series.check_fresh(record or Series("macro.observations", "X"), today=TODAY))


@pytest.mark.parametrize(
    "age, stale",
    [(0, False), (MAX_AGE_DAYS["monthly"], False), (MAX_AGE_DAYS["monthly"] + 1, True), (-59, False)],
)
def test_stale_after_max_age(monkeypatch, age, stale):
    result = fresh(monkeypatch, date.fromordinal(TODAY.toordinal() - age), "monthly")
    assert result.stale is stale and result.age_days == age and result.max_age_days == MAX_AGE_DAYS["monthly"]


def test_empty_series_is_stale(monkeypatch):
    result = fresh(monkeypatch, None, None)
    assert result.stale and result.latest_date is None and result.age_days is None


def test_unknown_frequency_is_stale(monkeypatch):
    result = fresh(monkeypatch, TODAY, None)
    assert result.stale and result.frequency is None and result.max_age_days is None


def test_record_frequency_overrides_label(monkeypatch):
    record = Series("macro.observations", "AAA", frequency="monthly")
    result = fresh(monkeypatch, date(2026, 10, 1), "daily", record)
    assert result.frequency == "monthly" and not result.stale


def test_today_defaults_to_eastern(monkeypatch):
    monkeypatch.setattr(series, "today_et", lambda: TODAY)

    async def fake_latest(table, key, dataset):
        return TODAY, "daily"

    monkeypatch.setattr(_readers, "_latest", fake_latest)
    assert asyncio.run(series.check_fresh(Series("macro.observations", "X"))).age_days == 0


def test_read_picks_the_reader_for_the_table(monkeypatch):
    calls = []

    async def prices(*args):
        calls.append(("prices", args))

    async def values(*args):
        calls.append(("values", args))

    monkeypatch.setattr(_readers, "_read_prices", prices)
    monkeypatch.setattr(_readers, "_read_values", values)
    asyncio.run(series._read(Series("forex.prices", "USDCAD", frequency="daily"), TODAY, TODAY))
    asyncio.run(series._read(Series("indicators.bls", "X", dataset="d"), TODAY, TODAY))
    assert calls == [
        ("prices", ("forex.prices", "USDCAD", TODAY, TODAY)),
        ("values", ("indicators.bls", "X", TODAY, TODAY, "d")),
    ]


def test_latest_reads_one_row_newest_first(monkeypatch):
    seen = {}

    class Conn:
        @contextlib.asynccontextmanager
        async def transaction(self, readonly=False):
            assert readonly
            yield

        async def execute(self, sql):
            pass

        async def fetch(self, sql, *args):
            seen.update(sql=sql, args=args)
            return []

    @contextlib.asynccontextmanager
    async def fake_connection():
        yield Conn()

    monkeypatch.setattr(_readers, "connection", fake_connection)
    assert asyncio.run(_readers._latest("indicators.bls", "X", "d")) == (None, None)
    assert seen["args"] == ("bls", "d", "X")
    assert seen["sql"].endswith("WHERE provider = $1 AND dataset = $2 AND series_id = $3 ORDER BY date DESC LIMIT 1")
    assert "SELECT date, frequency AS frequency" in seen["sql"]

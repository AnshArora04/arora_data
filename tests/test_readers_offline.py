"""Reader guard rails that need no database."""
import asyncio
import contextlib
from datetime import date

import pytest

from arora_data import _readers, catalog

START, END = date(2026, 1, 1), date(2026, 1, 31)


class CaptureConn:
    def __init__(self):
        self.rows = 0
        self.sql = self.args = None

    @contextlib.asynccontextmanager
    async def transaction(self, readonly=False):
        assert readonly
        yield

    async def execute(self, sql):
        pass

    async def fetch(self, sql, *args):
        self.sql, self.args = sql, args
        return [{"date": START, "value": None, "units": None, "frequency": None}] * self.rows


@pytest.fixture
def conn(monkeypatch):
    conn = CaptureConn()

    @contextlib.asynccontextmanager
    async def fake_connection():
        yield conn

    monkeypatch.setattr(_readers, "connection", fake_connection)
    return conn


@pytest.fixture
def no_connect(monkeypatch):
    def fail():
        raise AssertionError("connected to the database")

    monkeypatch.setattr(_readers, "connection", fail)


def test_reader_tables_cover_every_series_table_once():
    assert set(_readers._PRICE_TABLES) | set(_readers._VALUE_TABLES) == set(catalog.SERIES)
    assert not set(_readers._PRICE_TABLES) & set(_readers._VALUE_TABLES)


@pytest.mark.parametrize(
    "call",
    [
        lambda: _readers._read_prices("market.price_history", "AAPL", START, END),
        lambda: _readers._read_prices("macro.observations", "DGS10", START, END),
        lambda: _readers._read_prices("forex.prices; DROP TABLE x", "EURUSD", START, END),
        lambda: _readers._read_prices("forex.prices", "EURUSD", END, START),
        lambda: _readers._read_values("forex.prices", "EURUSD", START, END),
        lambda: _readers._read_values("indicators.bls", "X", START, END),
        lambda: _readers._read_values("macro.observations", "DGS10", START, END, dataset="x"),
        lambda: _readers._read_values("macro.observations", "DGS10", END, START),
    ],
)
def test_bad_input_rejected_before_connecting(call, no_connect):
    with pytest.raises(ValueError):
        asyncio.run(call())


def test_prices_pass_values_as_parameters(conn):
    asyncio.run(_readers._read_prices("forex.prices", "USDCAD", START, END))
    assert "USDCAD" not in conn.sql and "2026" not in conn.sql
    assert conn.args == ("USDCAD", START, END)
    assert conn.sql.endswith("WHERE symbol = $1 AND date >= $2 AND date <= $3 ORDER BY date")
    assert "vwap AS vwap, NULL::text AS currency" in conn.sql


def test_commodity_prices_read_currency(conn):
    asyncio.run(_readers._read_prices("commodities.prices", "GCUSD", START, END))
    assert "NULL::numeric AS vwap, currency::text AS currency" in conn.sql


def test_indicator_values_filter_on_the_whole_index_prefix(conn):
    asyncio.run(_readers._read_values("indicators.census_intltrade", "X", START, END, dataset="exports"))
    assert conn.args == ("census", "exports", "X", START, END)
    assert conn.sql.startswith("SELECT date, value, units, frequency FROM indicators.census_intltrade ")
    assert conn.sql.endswith("WHERE provider = $1 AND dataset = $2 AND series_id = $3 AND date >= $4 AND date <= $5 ORDER BY date")


def test_macro_values(conn):
    asyncio.run(_readers._read_values("macro.observations", "GDP", START, END))
    assert conn.args == ("GDP", START, END)
    assert conn.sql.startswith("SELECT date, value, units, frequency FROM macro.observations ")
    assert "WHERE series_id = $1 AND date >= $2" in conn.sql


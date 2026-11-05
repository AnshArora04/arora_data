"""Tiny read-only checks against prod. Skipped when there is no .env."""
from datetime import timedelta
from decimal import Decimal

import pytest

from arora_data import _db, _readers, catalog, series, sync

pytestmark = pytest.mark.skipif(not _db.ENV_PATH.exists(), reason="no .env")

_INDEX_COLUMNS_SQL = """
SELECT ARRAY(SELECT a.attname::text FROM unnest(x.indkey) WITH ORDINALITY k(n, o)
               JOIN pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = k.n ORDER BY k.o) AS cols
  FROM pg_index x JOIN pg_class c ON c.oid = x.indrelid JOIN pg_namespace s ON s.oid = c.relnamespace
 WHERE s.nspname = $1 AND c.relname = $2 AND x.indisvalid AND x.indpred IS NULL
"""


async def _query(sql, *args):
    async with _db.connection() as conn:
        return await _db.fetch(conn, sql, *args)


def query(sql, *args):
    return sync._sync(_query)(sql, *args)


def live_tables(schema):
    return {r["name"] for r in query(catalog._LIST_TABLES_SQL, schema)}


def test_session_is_readonly_user_in_read_only_transaction():
    row = query("SELECT current_user::text AS who, current_setting('transaction_read_only') AS ro")[0]
    assert (row["who"], row["ro"]) == ("readonly_user", "on")


def test_every_selected_column_exists():
    selected = {(t, date_col) for t, (_, date_col) in catalog.TABLES.items() if date_col}
    for t, (key_col, name_col) in catalog.SERIES.items():
        selected |= {(t, key_col), (t, "date")}
        if name_col:
            selected.add((t, name_col))
    rows = query(
        "SELECT table_schema || '.' || table_name AS t, column_name::text AS c "
        "FROM information_schema.columns WHERE table_schema = ANY($1)",
        list(catalog.SCHEMAS),
    )
    assert selected - {(r["t"], r["c"]) for r in rows} == set()


def test_described_tables_match_prod():
    live = {f"{s}.{t}" for s in catalog.SCHEMAS for t in live_tables(s)}
    assert live ^ set(catalog.TABLES) == set()


@pytest.mark.parametrize("table", sorted(catalog.SERIES))
def test_series_table_returns_rows(table):
    # A table with row-level security and no policy returns 0 rows with no error (#201).
    # table is a SERIES constant.
    assert query(f"SELECT 1 FROM {table} LIMIT 1"), f"{table} returned no rows: empty or RLS-blocked"


@pytest.mark.parametrize("table", sorted(catalog.SERIES))
def test_series_reads_are_index_backed(table):
    # A series read filters on these columns, then a date range; an index must start with them in this order.
    schema, name = table.split(".")
    key_col = catalog.SERIES[table][0]
    filters = ["provider", "dataset", key_col] if schema == "indicators" else [key_col]
    indexes = [r["cols"] for r in query(_INDEX_COLUMNS_SQL, schema, name)]
    assert any(cols[: len(filters) + 1] == [*filters, "date"] for cols in indexes), indexes


async def _pick(table):
    # One real series in the table and its latest date, both read through the index.
    key_col = catalog.SERIES[table][0]
    provider = _readers._VALUE_TABLES.get(table)
    async with _db.connection() as conn:
        if provider:
            pick = f"SELECT dataset, series_id AS key FROM {table} WHERE provider = $1 ORDER BY dataset, series_id LIMIT 1"
            row = (await _db.fetch(conn, pick, provider))[0]
            latest_sql = f"SELECT max(date) AS d FROM {table} WHERE provider = $1 AND dataset = $2 AND series_id = $3"
            latest = (await _db.fetch(conn, latest_sql, provider, row["dataset"], row["key"]))[0]["d"]
        else:
            row = (await _db.fetch(conn, f"SELECT {key_col} AS key, NULL::text AS dataset FROM {table} ORDER BY 1 LIMIT 1"))[0]
            latest_sql = f"SELECT max(date) AS d FROM {table} WHERE {key_col} = $1"
            latest = (await _db.fetch(conn, latest_sql, row["key"]))[0]["d"]
    return row["key"], row["dataset"], latest


async def _read_latest_year(table):
    key, dataset, latest = await _pick(table)
    start = latest - timedelta(days=365)
    if table in _readers._PRICE_TABLES:
        rows = await _readers._read_prices(table, key, start, latest, limit=_db.MAX_LIMIT)
    else:
        rows = await _readers._read_values(table, key, start, latest, limit=_db.MAX_LIMIT, dataset=dataset)
    return rows, latest


@pytest.mark.parametrize("table", sorted(catalog.SERIES))
def test_reader_returns_a_real_series(table):
    rows, latest = sync._sync(_read_latest_year)(table)
    assert rows and rows[-1].date == latest
    assert [r.date for r in rows] == sorted({r.date for r in rows})
    model = _readers.PriceBar if table in _readers._PRICE_TABLES else _readers.Value
    assert all(type(r) is model for r in rows)
    sample = rows[-1].close if model is _readers.PriceBar else rows[-1].value
    assert sample is None or type(sample) is Decimal


@pytest.mark.parametrize("table", sorted(catalog.SERIES))
def test_check_fresh_matches_the_latest_row(table):
    key, dataset, latest = sync._sync(_pick)(table)
    frequency = None if table in _readers._LABELLED else "daily"
    result = sync.check_fresh(series.Series(table, key, dataset, frequency), today=latest)
    assert (result.latest_date, result.age_days) == (latest, 0)


def test_partition_children_are_hidden():
    names = live_tables("market")
    assert "price_history" in names
    assert not any(n.startswith("price_history_20") for n in names)


def test_list_schemas():
    assert {s.name for s in sync.list_schemas()} == set(catalog.SCHEMAS)


def test_list_tables():
    tables = {t.name: t for t in sync.list_tables("macro")}
    observations = tables["observations"]
    assert observations.description and observations.row_estimate
    assert observations.latest_date is not None


def test_list_columns():
    columns = {c.name: c for c in sync.list_columns("macro", "observations")}
    assert {"series_id", "date", "value", "series_name"} <= set(columns)
    assert columns["series_id"].nullable is False and columns["value"].nullable is True


def test_list_columns_unknown_table():
    with pytest.raises(ValueError):
        sync.list_columns("macro", "no_such_table")


def test_list_series():
    series = sync.list_series("macro", "observations")
    assert len(series) > 5
    assert [s.key for s in series] == sorted(s.key for s in series)
    assert all(s.latest_date for s in series) and any(s.description for s in series)
    assert all(s.dataset is None for s in series)


def test_list_series_raises_at_the_cap():
    with pytest.raises(_db.RowCapError):
        sync.list_series("macro", "observations", limit=5)


def test_list_series_keeps_datasets_apart():
    series = sync.list_series("indicators", "census_intltrade")
    pairs = [(s.key, s.dataset) for s in series]
    assert len(pairs) == len(set(pairs))
    assert len({s.key for s in series}) < len(pairs)

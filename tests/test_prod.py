"""Tiny read-only checks against prod. Skipped when there is no .env."""
from datetime import timedelta
from decimal import Context, Decimal

import pytest

from arora_data import _db, _readers, catalog, forex, series, sync
from arora_data.dates import today_et

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
        rows = await _readers._read_prices(table, key, start, latest)
    else:
        rows = await _readers._read_values(table, key, start, latest, dataset=dataset)
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


def test_list_series_keeps_datasets_apart():
    series = sync.list_series("indicators", "census_intltrade")
    pairs = [(s.key, s.dataset) for s in series]
    assert len(pairs) == len(set(pairs))
    assert len({s.key for s in series}) < len(pairs)


def _last_days(n):
    end = today_et()
    return end - timedelta(days=n), end


def test_forex_stored_pair_matches_the_table():
    start, end = _last_days(10)
    rows = sync.forex("USD", "CAD", start, end)
    bars = sync._sync(_readers._read_prices)("forex.prices", "USDCAD", start, end)
    assert rows and [(r.date, r.route, r.close, r.high) for r in rows] == [(b.date, "stored", b.close, b.high) for b in bars]


def test_forex_inverse_is_one_over_the_stored_pair():
    start, end = _last_days(10)
    usd_cad, cad_usd = sync.forex("USD", "CAD", start, end), sync.forex("CAD", "USD", start, end)
    flip = Context(prec=forex.SIG_DIGITS).divide
    assert cad_usd and [(r.date, r.route, r.close, r.high, r.volume, r.vwap) for r in cad_usd] == [
        (r.date, "inverted", flip(1, r.close), flip(1, r.low), r.volume, flip(1, r.vwap)) for r in usd_cad
    ]


async def _cross_without_stored(pair, start, end):
    stored = {s.key for s in await catalog.list_series("forex", "prices")} - {pair}
    route, legs = forex._plan(pair[:3], pair[3:], stored)
    read = {s: await _readers._read_prices("forex.prices", s, start, end) for s, _ in legs}
    usd = {s: forex._usd_per(read[s], flip) for s, flip in legs}
    return route, forex._bars(pair, route, legs, read, usd)


def test_forex_cross_tracks_the_stored_cross():
    # EURGBP is stored, so crossing it through EURUSD and GBPUSD must land close to it on every shared day.
    start, end = _last_days(365)
    route, crossed = sync._sync(_cross_without_stored)("EURGBP", start, end)
    stored = {r.date: r for r in sync.forex("EUR", "GBP", start, end)}
    shared = [r for r in crossed if r.date in stored]
    assert route == "crossed" and len(shared) > 200
    assert max(abs(r.close / stored[r.date].close - 1) for r in shared) < Decimal("0.01")
    assert max(abs(r.open / stored[r.date].open - 1) for r in shared) < Decimal("0.01")


def test_forex_crosses_two_new_usd_legs():
    rows = sync.forex("BRL", "INR", *_last_days(10))
    assert rows and {r.route for r in rows} == {"crossed"} and all(r.close > 0 for r in rows)


def test_forex_rejects_a_currency_with_no_usd_pair():
    with pytest.raises(ValueError, match="XAU has no stored USD pair"):
        sync.forex("XAU", "USD", *_last_days(10))


def test_forex_stored_symbols_match_the_series_list():
    assert sync._sync(forex._stored)() == {s.key for s in sync.list_series("forex", "prices")}


def test_forex_all_returns_every_pair_once():
    stored = {s.key for s in sync.list_series("forex", "prices")}
    rows = sync.forex("ALL", "ALL", *_last_days(7))
    pairs = {r.pair: r.route for r in rows}
    n = len(forex._currencies(stored))
    assert len(pairs) == n * (n - 1) // 2
    assert {p for p, route in pairs.items() if route == "stored"} == stored
    assert "inverted" not in pairs.values()

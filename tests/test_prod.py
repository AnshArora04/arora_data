"""Tiny read-only checks against prod. Skipped when there is no .env."""
from datetime import date, timedelta
from decimal import Context, Decimal

import pytest

from arora_data import _db, _readers, catalog, commodities, crypto, forex, index, series, sync, treasury, treasury_curve
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


@pytest.mark.parametrize("code, cents", [("GC", 1), ("ZC", 100)])
def test_commodities_usd_is_the_table_in_dollars(code, cents):
    start, end = _last_days(10)
    rows = sync.commodities(code, start, end)
    bars = sync._sync(_readers._read_prices)("commodities.prices", commodities.COMMODITIES[code][0], start, end)
    assert rows and [(r.date, r.route, r.open * cents, r.high * cents, r.low * cents, r.close * cents) for r in rows] == [
        (b.date, "stored", b.open, b.high, b.low, b.close) for b in bars]


def test_commodities_in_eur_are_usd_times_forex():
    start, end = _last_days(30)
    usd = {r.date: r.close for r in sync.commodities("GC", start, end)}
    rate = {r.date: r.close for r in sync.forex("USD", "EUR", start - commodities.FX_LOOKBACK, end)}
    eur = sync.commodities("GC", start, end, "eur")
    times = Context(prec=forex.SIG_DIGITS).multiply
    assert eur and [r.close for r in eur] == [times(usd[r.date], rate[r.quote_date]) for r in eur]
    assert all(r.quote_date <= r.date for r in eur) and sum(r.quote_date == r.date for r in eur) > len(eur) / 2


def test_commodities_all_serves_every_mapped_symbol():
    assert {s for s, *_ in commodities.COMMODITIES.values()} <= {s.key for s in sync.list_series("commodities", "prices")}
    assert {r.commodity for r in sync.commodities("ALL", *_last_days(10))} == set(commodities.COMMODITIES)


def test_crypto_usd_is_the_table():
    start, end = _last_days(10)
    rows = sync.crypto("bitcoin", start, end)
    bars = sync._sync(_readers._read_prices)("crypto.prices", "BTCUSD", start, end)
    assert rows and [(r.date, r.route, r.open, r.high, r.low, r.close, r.volume_usd, r.vwap) for r in rows] == [
        (b.date, "stored", b.open, b.high, b.low, b.close, b.volume, b.vwap) for b in bars]


def test_crypto_in_eur_is_usd_times_forex_carrying_weekends():
    start, end = _last_days(20)
    usd = {r.date: r.close for r in sync.crypto("ETH", start, end)}
    rate = {r.date: r.close for r in sync.forex("USD", "EUR", start - commodities.FX_LOOKBACK, end)}
    eur = sync.crypto("ETH", start, end, "eur")
    times = Context(prec=forex.SIG_DIGITS).multiply
    assert eur and [(r.close, r.quote_date) for r in eur] == [
        (times(usd[r.date], rate[r.quote_date]), max(d for d in rate if d <= r.date)) for r in eur]
    # FMP has Sunday forex bars (the market opens Sunday evening), so only some weekend days carry an earlier rate.
    assert any(r.quote_date < r.date for r in eur if r.date.weekday() >= 5)


def test_crypto_coins_are_the_stored_usd_symbols_and_every_name_is_stored():
    stored = {s.key for s in sync.list_series("crypto", "prices")}
    assert sync._sync(crypto._symbols)() == stored
    assert {c + "USD" for c in crypto.NAMES} <= stored
    assert {r.coin + "USD" for r in sync.crypto("ALL", *_last_days(10))} == stored


def test_index_is_the_table():
    start, end = _last_days(10)
    rows = sync.index("s&p 500", start, end)
    bars = sync._sync(_readers._read_prices)("equity_indexes.prices", "^GSPC", start, end)
    assert rows and [(r.index, r.date, r.open, r.high, r.low, r.close, r.volume, r.vwap) for r in rows] == [
        ("^GSPC", b.date, b.open, b.high, b.low, b.close, b.volume, b.vwap) for b in bars]
    assert sync.index("spx", start, end) == rows


def test_index_names_cover_exactly_the_stored_indexes():
    stored = {s.key for s in sync.list_series("equity_indexes", "prices")}
    assert sync._sync(index._symbols)() == stored == set(index.INDEXES)
    assert {r.index for r in sync.index("ALL", *_last_days(10))} == stored


def test_index_in_another_currency_is_the_level_times_forex():
    start, end = _last_days(20)
    level = {r.date: r.close for r in sync.index("^N225", start, end)}
    rate = {r.date: r.close for r in sync.forex("JPY", "EUR", start - commodities.FX_LOOKBACK, end)}
    eur = sync.index("nikkei 225", start, end, "eur")
    times = Context(prec=forex.SIG_DIGITS).multiply
    assert eur and [(r.date, r.close, r.quote_date) for r in eur] == [
        (d, times(level[d], rate[max(x for x in rate if x <= d)]), max(x for x in rate if x <= d)) for d in level]


def test_index_all_in_usd_leaves_out_the_volatility_series_and_keeps_usd_indexes_as_stored():
    start, end = _last_days(10)
    rows = sync.index("ALL", start, end, "USD")
    assert {r.index for r in rows} == {s for s, (_, cur) in index.INDEXES.items() if cur}
    gspc = [r for r in rows if r.index == "^GSPC"]
    assert [(r.date, r.high, r.close, r.route) for r in gspc] == [
        (r.date, r.high, r.close, "stored") for r in sync.index("^GSPC", start, end)]


def test_treasury_is_the_table():
    start, end = _last_days(10)
    stored = query("SELECT date, month3, year10, year30 FROM macro.treasury_rates WHERE date BETWEEN $1 AND $2 ORDER BY date",
                   start, end)
    assert stored and [(r.date, r.yield_pct) for r in sync.treasury(start, end, "10y")] == [
        (r["date"], r["year10"]) for r in stored if r["year10"] is not None]
    rows = sync.treasury(start, end)
    assert {(r.tenor, r.date): r.yield_pct for r in rows if r.tenor in ("3M", "30Y")} == {
        (t, r["date"]): r[c] for t, c in (("3M", "month3"), ("30Y", "year30")) for r in stored if r[c] is not None}


def test_treasury_tenors_are_the_table_columns_and_its_gaps_are_left_out():
    columns = {c.name for c in sync.list_columns("macro", "treasury_rates")} - {"date", "source", "ingested_at"}
    assert set(treasury.TENORS.values()) == columns
    # Columbus Day 2010-10-11 is stored with no values.
    assert [r.date for r in sync.treasury(date(2010, 10, 8), date(2010, 10, 12), "10Y")] == [
        date(2010, 10, 8), date(2010, 10, 12)]


@pytest.mark.filterwarnings("ignore::arora_data.treasury_curve.MethodWarning")
@pytest.mark.parametrize("method", [m for m in treasury_curve.METHODS if m != "nss"])
def test_treasury_curve_at_a_tenor_is_the_stored_yield(method):
    start, end = _last_days(30)
    stored = {r.date: r.yield_pct for r in sync.treasury(start, end, "10Y")}
    curve = sync.treasury_curve("10Y", start, end, method)
    assert stored and {p.date for p in curve} == set(stored)
    assert all(abs(p.par_yield - stored[p.date]) < Decimal("1e-8") and p.zero_rate > 0 for p in curve)


def test_treasury_curve_is_never_extrapolated_past_the_days_longest_tenor():
    # No 30Y was stored from 2002-02-19 to 2006-02-08.
    assert [p.date for p in sync.treasury_curve("25Y", date(2002, 2, 15), date(2006, 2, 9))] == [
        date(2002, 2, 15), date(2006, 2, 9)]
    assert len(sync.treasury_curve("20Y", date(2002, 2, 15), date(2006, 2, 9))) > 900


@pytest.mark.filterwarnings("ignore::arora_data.treasury_curve.MethodWarning")
def test_treasury_curve_nss_stays_near_the_stored_yields():
    start, end = _last_days(30)
    stored = {r.date: r.yield_pct for r in sync.treasury(start, end, "10Y")}
    curve = sync.treasury_curve("10Y", start, end, "nss")
    assert stored and {p.date for p in curve} == set(stored)
    assert all(abs(p.par_yield - stored[p.date]) < Decimal("0.3") for p in curve)  # 27 bp is the worst since 1990

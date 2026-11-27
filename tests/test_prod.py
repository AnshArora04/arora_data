"""Tiny read-only checks against prod. Skipped when there is no .env."""
from collections import Counter
from datetime import date, timedelta
from decimal import Context, Decimal

import pytest

from arora_data import _damodaran, _db, _readers, bea, bls, catalog, commodities, crypto, damodaran_editions, eia, forex, fred, index, nyfed, ofr, series, sync, treasury, treasury_curve, treasury_fiscal, usda
from arora_data.dates import EASTERN, today_et

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
    frequency = None if table in _readers._VALUE_TABLES else "daily"
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


_FRED_LATEST_SQL = """
SELECT DISTINCT ON (series_id) series_id, series_name AS name, units, frequency, category
  FROM macro.observations ORDER BY series_id, date DESC
"""
_FRED_DATES_SQL = """
SELECT series_id, min(date) AS first_date, max(date) AS latest_date
  FROM macro.observations WHERE value IS NOT NULL GROUP BY series_id
"""


def test_fred_series_is_every_series_with_its_latest_labels_and_value_dates():
    dates = {r["series_id"]: (r["first_date"], r["latest_date"]) for r in query(_FRED_DATES_SQL)}
    expected = [fred.FredSeries(**dict(r), first_date=dates[r["series_id"]][0], latest_date=dates[r["series_id"]][1])
                for r in query(_FRED_LATEST_SQL)]
    stored = sync.fred_series()
    assert len(stored) > 100 and stored == expected
    assert all(_readers._resolve(s.name, stored, fred._HINT) is s for s in stored)  # every name looks its series up


def test_fred_is_the_tables_values_with_its_gaps_left_out():
    start, end = date(2025, 9, 1), date(2025, 12, 31)
    rows = query("SELECT date, value FROM macro.observations WHERE series_id = $1 AND date BETWEEN $2 AND $3 ORDER BY date",
                 "DFII10", start, end)
    assert any(r["value"] is None for r in rows)  # Thanksgiving and other bond-market holidays
    served = sync.fred("10-year tips real yield", start, end)
    assert [(v.date, v.value) for v in served] == [(r["date"], r["value"]) for r in rows if r["value"] is not None]
    assert {(v.series_id, v.units) for v in served} == {("DFII10", "Percent")}
    assert sync.fred("dfii10", start, end) == served


_DAMODARAN_EDITIONS_SQL = """
SELECT edition_date, revision, file_name, first_seen_at, mature_erp, us_erp, vol_multiplier, cds_benchmark, editor_note
  FROM macro.damodaran_crp_editions
"""


def test_damodaran_editions_is_the_table_oldest_first_with_each_editions_newest_by_first_seen_flagged():
    rows = sorted(query(_DAMODARAN_EDITIONS_SQL), key=lambda r: (r["edition_date"], r["first_seen_at"], r["revision"]))
    newest = {}
    for r in rows:  # sorted, so the last one seen per edition wins
        newest[r["edition_date"]] = r["revision"]
    expected = [damodaran_editions.CrpEdition(**dict(r), latest=newest[r["edition_date"]] == r["revision"]) for r in rows]
    stored = sync.damodaran_editions()
    assert len(stored) >= 18 and stored == expected
    assert [(e.revision, e.latest) for e in stored if e.edition_date == date(2026, 1, 1)] == [
        (1, False), (2, False), (3, False), (4, True)]
    assert all(e.first_seen_at.tzinfo is EASTERN for e in stored)
    assert stored[-1].edition_date == date(2026, 7, 1) and stored[-1].first_seen_at.date() == date(2026, 11, 12)


def test_damodaran_editions_sql_flags_the_newest_by_first_seen_not_by_revision_number():
    # Prod's revision numbers follow first_seen_at, so feed the SQL a table where they don't.
    fake = """(VALUES (date '2026-01-01', 1::smallint, 'f', timestamptz '2026-02-17', 0.04, NULL::numeric, NULL::numeric,
                       NULL::text, NULL::text),
                      (date '2026-01-01', 2::smallint, 'f', timestamptz '2026-01-07', 0.04, NULL, NULL, NULL, NULL))
               AS t(edition_date, revision, file_name, first_seen_at, mature_erp, us_erp, vol_multiplier,
                    cds_benchmark, editor_note)"""
    sql = damodaran_editions._EDITIONS_SQL.replace("macro.damodaran_crp_editions", fake)
    assert sql != damodaran_editions._EDITIONS_SQL
    assert [(r["revision"], r["latest"]) for r in query(sql)] == [(2, False), (1, True)]


_DAMODARAN_VIEW_SQL = """
SELECT edition_date, revision, entity_code, published_name, entity_type, region, moodys_rating, basis, prs_score,
       default_spread, cds_spread_net, crp, cds_crp, erp, cds_erp
  FROM macro.damodaran_country_risk_latest_revision WHERE edition_date = $1
"""


def test_damodaran_rows_are_the_latest_revision_views_edition_in_force():
    rows = sync._sync(_damodaran._rows)
    latest = max(e.edition_date for e in sync.damodaran_editions())
    expected = sorted((_damodaran._CountryRisk(**dict(r)) for r in query(_DAMODARAN_VIEW_SQL, latest)),
                      key=lambda r: r.entity_code)
    served = rows(None, None, None)
    assert len(served) >= 178 and served == expected
    assert {"AE-AZ", "AE-RK", "AE-SH", "VCT"} <= {r.entity_code for r in served}  # the four FMP never carried
    january = rows(None, date(2026, 3, 31), None)
    assert {(r.edition_date, r.revision) for r in january} == {(date(2026, 1, 1), 4)}
    assert [r for r in january if r.entity_code == "KEN"][0].moodys_rating == "B3"  # the Feb 16 re-issue
    assert rows("usa", date(2026, 3, 31), 1)[0].erp == Decimal("0.0447")  # January as first posted


def test_country_risk_premium_is_the_views_premiums_with_the_us_unscaled():
    latest = max(e.edition_date for e in sync.damodaran_editions())
    view = sorted(query(_DAMODARAN_VIEW_SQL, latest), key=lambda r: r["entity_code"])
    served = sync.country_risk_premium()
    assert [(r.edition_date, r.entity_code, r.published_name, r.crp, r.cds_crp) for r in served] == [
        (r["edition_date"], r["entity_code"], r["published_name"], r["crp"], r["cds_crp"]) for r in view]
    usa = {r["entity_code"]: r for r in view}["USA"]
    assert usa["crp"] == usa["default_spread"]  # the US premium is its raw Aa1 spread
    assert sync.country_risk_premium("United States")[0].crp == usa["crp"]


_DAMODARAN_ERP_SQL = """
SELECT c.edition_date, c.entity_code, c.basis, c.crp, c.erp, c.cds_crp, c.cds_erp, e.mature_erp, e.us_erp
  FROM macro.damodaran_country_risk_latest_revision c JOIN macro.damodaran_crp_editions e USING (edition_date, revision)
"""


def test_equity_risk_premium_is_the_views_premiums_and_mature_plus_crp_but_for_two_published_exceptions():
    latest = max(e.edition_date for e in sync.damodaran_editions())
    view = sorted(query(_DAMODARAN_VIEW_SQL, latest), key=lambda r: r["entity_code"])
    erp = sync.equity_risk_premium
    assert [(r.edition_date, r.entity_code, r.published_name, r.erp, r.cds_erp) for r in erp()] == [
        (r["edition_date"], r["entity_code"], r["published_name"], r["erp"], r["cds_erp"]) for r in view]
    rows = query(_DAMODARAN_ERP_SQL)
    off = {(r["edition_date"], r["entity_code"]) for r in rows if r["erp"] != r["mature_erp"] + r["crp"]}
    us = {(r["edition_date"], "USA") for r in rows if r["entity_code"] == "USA" and r["us_erp"] is not None}
    prs_2017 = {(r["edition_date"], r["entity_code"]) for r in rows
                if r["edition_date"] == date(2017, 1, 5) and r["basis"] == "prs"}
    assert len(us) >= 3 and len(prs_2017) == 24 and off == us | prs_2017  # the docstring's two exceptions, no others
    assert all(r["erp"] == r["us_erp"] for r in rows if (r["edition_date"], r["entity_code"]) in us)
    assert all(r["cds_erp"] == r["mature_erp"] + r["cds_crp"] for r in rows if r["cds_erp"] is not None)
    assert erp("Guinea", date(2017, 1, 5))[0].erp == Decimal("0.2")


_DAMODARAN_SPREAD_SQL = """
SELECT c.edition_date, c.basis, c.default_spread, c.cds_spread_net, c.cds_crp, c.cds_erp, e.cds_benchmark
  FROM macro.damodaran_country_risk_latest_revision c JOIN macro.damodaran_crp_editions e USING (edition_date, revision)
"""


def test_sovereign_spread_is_the_views_spreads_as_its_docstring_describes():
    latest = max(e.edition_date for e in sync.damodaran_editions())
    view = sorted(query(_DAMODARAN_VIEW_SQL, latest), key=lambda r: r["entity_code"])
    served = sync.sovereign_spread()
    assert [(r.edition_date, r.entity_code, r.published_name, r.default_spread, r.cds_spread_net) for r in served] == [
        (r["edition_date"], r["entity_code"], r["published_name"], r["default_spread"], r["cds_spread_net"])
        for r in view]
    rows = query(_DAMODARAN_SPREAD_SQL)
    assert all(r["default_spread"] is not None for r in rows)
    assert all(r["cds_spread_net"] >= 0 for r in rows if r["cds_spread_net"] is not None)
    assert all((r["cds_spread_net"] is None) == (r["cds_crp"] is None) == (r["cds_erp"] is None) for r in rows)
    assert all(r["cds_spread_net"] is None for r in rows if r["basis"] == "prs")
    benchmark = {r["edition_date"]: r["cds_benchmark"] for r in rows}
    assert {b for d, b in benchmark.items() if d <= date(2025, 1, 1)} == {"US"}
    assert {b for d, b in benchmark.items() if d >= date(2025, 7, 1)} == {"Switzerland"}


def test_sovereign_rating_is_the_views_ratings_with_barbados_july_2026_as_his_file_has_it():
    latest = max(e.edition_date for e in sync.damodaran_editions())
    view = sorted(query(_DAMODARAN_VIEW_SQL, latest), key=lambda r: r["entity_code"])
    rating = sync.sovereign_rating
    assert [(r.edition_date, r.entity_code, r.basis, r.moodys_rating, r.prs_score) for r in rating()] == [
        (r["edition_date"], r["entity_code"], r["basis"], r["moodys_rating"], r["prs_score"]) for r in view]
    july, april = date(2026, 7, 1), date(2026, 4, 1)
    assert rating("Barbados", april)[0].moodys_rating == "B2"
    assert rating("BRB", july)[0].moodys_rating == "C"  # the docstring's known source issue
    rows = sync._sync(_damodaran._rows)
    brb, blr = rows("BRB", july, None)[0], rows("BLR", july, None)[0]
    assert (brb.default_spread, brb.crp, brb.erp) == (blr.default_spread, blr.crp, blr.erp) == (
        Decimal("0.1750000000"), Decimal("0.2720367171"), Decimal("0.3140367171"))
    assert rating("VEN", july)[0].moodys_rating == "Caa3"  # S&P's CCC-, Moody's withdrawn


_BLS_LATEST_SQL = """
SELECT DISTINCT ON (series_id) series_id, dataset, series_name AS name, units, frequency, category
  FROM indicators.bls ORDER BY series_id, date DESC
"""
_BLS_DATES_SQL = """
SELECT series_id, min(date) AS first_date, max(date) AS latest_date
  FROM indicators.bls WHERE value IS NOT NULL GROUP BY series_id
"""


def test_bls_series_is_every_series_with_its_latest_labels_and_value_dates():
    dates = {r["series_id"]: (r["first_date"], r["latest_date"]) for r in query(_BLS_DATES_SQL)}
    expected = [bls.BlsSeries(**dict(r), first_date=dates[r["series_id"]][0], latest_date=dates[r["series_id"]][1])
                for r in query(_BLS_LATEST_SQL)]
    stored = sync.bls_series()
    assert len(stored) >= 5 and stored == expected
    assert all(_readers._resolve(s.name, stored, bls._HINT) is s for s in stored)  # every name looks its series up


def test_bls_is_the_tables_values_with_its_gaps_left_out():
    start, end = date(2025, 6, 1), date(2025, 12, 31)
    rows = query("SELECT date, value FROM indicators.bls WHERE series_id = $1 AND date BETWEEN $2 AND $3 ORDER BY date",
                 "LNS14000000", start, end)
    assert any(r["value"] is None for r in rows)  # October 2025, never published
    served = sync.bls("unemployment rate (sa)", start, end)
    assert [(v.date, v.value) for v in served] == [(r["date"], r["value"]) for r in rows if r["value"] is not None]
    assert {(v.series_id, v.units) for v in served} == {("LNS14000000", "percent")}
    assert sync.bls("lns14000000", start, end) == served


_BEA_LATEST_SQL = """
SELECT DISTINCT ON (series_id) series_id, dataset, series_name AS name, units, frequency, category
  FROM indicators.bea ORDER BY series_id, date DESC
"""
_BEA_DATES_SQL = """
SELECT series_id, min(date) AS first_date, max(date) AS latest_date
  FROM indicators.bea WHERE value IS NOT NULL GROUP BY series_id
"""


def test_bea_series_is_every_series_with_its_latest_labels_and_value_dates():
    dates = {r["series_id"]: (r["first_date"], r["latest_date"]) for r in query(_BEA_DATES_SQL)}
    expected = [bea.BeaSeries(**dict(r), first_date=dates[r["series_id"]][0], latest_date=dates[r["series_id"]][1])
                for r in query(_BEA_LATEST_SQL)]
    stored = sync.bea_series()
    assert len(stored) >= 21 and stored == expected
    assert len({s.dataset for s in stored}) >= 6
    assert all(_readers._resolve(s.name, stored, bea._HINT) is s for s in stored)  # every name looks its series up


def test_bea_is_the_tables_values_in_their_own_dataset():
    start, end = date(2024, 1, 1), date(2025, 12, 31)
    rows = query("SELECT date, value FROM indicators.bea WHERE dataset = $1 AND series_id = $2 AND date BETWEEN $3 AND $4"
                 " ORDER BY date", "bea/nipa_t10101", "A191RL", start, end)
    served = sync.bea("real gdp, percent change (annual rate)", start, end)
    assert len(rows) == 8 and [(v.date, v.value) for v in served] == [(r["date"], r["value"]) for r in rows]
    assert {(v.series_id, v.units) for v in served} == {("A191RL", "percent change, annual rate")}
    assert sync.bea("a191rl", start, end) == served


_NYFED_LATEST_SQL = """
SELECT DISTINCT ON (series_id) series_id, dataset, series_name AS name, units, frequency, category
  FROM indicators.nyfed ORDER BY series_id, date DESC
"""
_NYFED_DATES_SQL = """
SELECT series_id, min(date) AS first_date, max(date) AS latest_date
  FROM indicators.nyfed WHERE value IS NOT NULL GROUP BY series_id
"""


def test_nyfed_series_is_every_series_with_its_latest_labels_and_value_dates():
    dates = {r["series_id"]: (r["first_date"], r["latest_date"]) for r in query(_NYFED_DATES_SQL)}
    expected = [nyfed.NyfedSeries(**dict(r), first_date=dates[r["series_id"]][0], latest_date=dates[r["series_id"]][1])
                for r in query(_NYFED_LATEST_SQL)]
    stored = sync.nyfed_series()
    assert len(stored) >= 13 and stored == expected
    assert {s.dataset for s in stored} == {"nyfed/reference_rates", "nyfed/soma_summary"}
    assert all(_readers._resolve(s.name, stored, nyfed._HINT) is s for s in stored)  # every name looks its series up


def test_nyfed_is_the_tables_values_with_its_gaps_left_out():
    start, end = date(2014, 4, 1), date(2014, 6, 30)
    rows = query("SELECT date, value FROM indicators.nyfed WHERE dataset = $1 AND series_id = $2 AND date BETWEEN $3 AND $4"
                 " ORDER BY date", "nyfed/soma_summary", "frn", start, end)
    assert any(r["value"] is None for r in rows)  # no floating-rate notes held before 2014-05-21
    served = sync.nyfed("soma floating-rate notes", start, end)
    assert [(v.date, v.value) for v in served] == [(r["date"], r["value"]) for r in rows if r["value"] is not None]
    assert served[0].date == date(2014, 5, 21)
    assert {(v.series_id, v.units) for v in served} == {("frn", "USD millions")}
    assert sync.nyfed("FRN", start, end) == served


def test_nyfed_sofr_skips_good_friday_and_effr_does_not():
    day = date(2026, 4, 3)
    assert [v.date for v in sync.nyfed("sofr", day, day)] == []
    assert [(v.date, v.units) for v in sync.nyfed("effr", day, day)] == [(day, "percent")]


_TREASURY_FISCAL_LATEST_SQL = """
SELECT DISTINCT ON (series_id) series_id, dataset, series_name AS name, units, frequency, category
  FROM indicators.treasury_fiscal ORDER BY series_id, date DESC
"""
_TREASURY_FISCAL_DATES_SQL = """
SELECT series_id, min(date) AS first_date, max(date) AS latest_date
  FROM indicators.treasury_fiscal WHERE value IS NOT NULL GROUP BY series_id
"""


def test_treasury_fiscal_series_is_every_series_with_its_latest_labels_and_value_dates():
    dates = {r["series_id"]: (r["first_date"], r["latest_date"]) for r in query(_TREASURY_FISCAL_DATES_SQL)}
    expected = [treasury_fiscal.TreasuryFiscalSeries(**dict(r), first_date=dates[r["series_id"]][0],
                                                     latest_date=dates[r["series_id"]][1])
                for r in query(_TREASURY_FISCAL_LATEST_SQL)]
    stored = sync.treasury_fiscal_series()
    assert len(stored) >= 3 and stored == expected
    assert {s.series_id: s.first_date for s in stored if s.series_id != "intragov_hold_amt"} == {
        "tot_pub_debt_out_amt": date(1993, 4, 1), "debt_held_public_amt": date(1997, 9, 30)}
    assert all(_readers._resolve(s.name, stored, treasury_fiscal._HINT) is s for s in stored)


def test_treasury_fiscal_is_the_tables_values_with_its_gaps_left_out():
    start, end = date(2005, 3, 1), date(2005, 4, 30)
    rows = query("SELECT date, value FROM indicators.treasury_fiscal WHERE series_id = $1 AND date BETWEEN $2 AND $3"
                 " ORDER BY date", "debt_held_public_amt", start, end)
    assert any(r["value"] is None for r in rows)  # split out of the total only at month end before 2005-04-04
    served = sync.treasury_fiscal("debt held by the public", start, end)
    assert [(v.date, v.value) for v in served] == [(r["date"], r["value"]) for r in rows if r["value"] is not None]
    assert [v.date for v in served][:2] == [date(2005, 3, 31), date(2005, 4, 4)]
    assert {(v.series_id, v.units) for v in served} == {("debt_held_public_amt", "USD millions")}
    assert sync.treasury_fiscal("DEBT_HELD_PUBLIC_AMT", start, end) == served


def test_treasury_fiscal_parts_add_up_to_the_total_except_treasurys_own_2025_08_04():
    rows = query("""
        SELECT t.date, p.value + i.value - t.value AS gap FROM indicators.treasury_fiscal t
          JOIN indicators.treasury_fiscal p ON p.date = t.date AND p.series_id = 'debt_held_public_amt'
          JOIN indicators.treasury_fiscal i ON i.date = t.date AND i.series_id = 'intragov_hold_amt'
         WHERE t.series_id = 'tot_pub_debt_out_amt' AND abs(p.value + i.value - t.value) >= 0.000001""")
    assert [(r["date"], r["gap"]) for r in rows] == [(date(2025, 8, 4), Decimal("10000"))]


_OFR_LATEST_SQL = """
SELECT DISTINCT ON (series_id) series_id, dataset, series_name AS name, units, frequency, category
  FROM indicators.ofr WHERE dataset <> 'ofr/hedge_fund_monitor' ORDER BY series_id, date DESC
"""
_OFR_DATES_SQL = """
SELECT series_id, min(date) AS first_date, max(date) AS latest_date
  FROM indicators.ofr WHERE value IS NOT NULL AND dataset <> 'ofr/hedge_fund_monitor' GROUP BY series_id
"""


def test_ofr_series_is_every_series_with_its_latest_labels_and_value_dates():
    dates = {r["series_id"]: (r["first_date"], r["latest_date"]) for r in query(_OFR_DATES_SQL)}
    # A series with no value on any day has no dates (7 on 2026-11-25, for example NYPD-PD_SB_TOT-A).
    expected = [ofr.OfrSeries(**dict(r), first_date=dates.get(r["series_id"], (None, None))[0],
                              latest_date=dates.get(r["series_id"], (None, None))[1])
                for r in query(_OFR_LATEST_SQL)]
    stored = sync.ofr_series()
    assert len(stored) >= 5 and stored == expected
    # DVP's first stored day, 2018-05-07, has no value.
    assert {s.series_id: s.first_date for s in stored}["REPO-DVP_AR_OO-P"] == date(2018, 5, 8)
    assert all(_readers._resolve(s.name, stored, ofr._HINT) is s for s in stored)
    # The table holds the Hedge Fund Monitor, but ofr_series() does not give it until a person checks its terms.
    assert {s.dataset for s in stored} <= {"ofr/short_term_funding", "ofr/financial_stress_index"}


def test_ofr_series_counts_are_the_full_ofr_set_without_the_hedge_fund_monitor():
    # OFR-FULL stores 948 series. ofr_series() gives 451 of them: not the 497 of the Hedge Fund Monitor.
    counts = Counter((s.dataset, s.category) for s in sync.ofr_series())
    assert counts == {("ofr/short_term_funding", "repo"): 164, ("ofr/short_term_funding", "mmf"): 42,
                      ("ofr/short_term_funding", "nypd"): 194, ("ofr/short_term_funding", "fnyr"): 30,
                      ("ofr/short_term_funding", "tyld"): 12, ("ofr/financial_stress_index", "fsi"): 9}
    stored = query("SELECT count(DISTINCT series_id) AS n FROM indicators.ofr WHERE dataset = 'ofr/hedge_fund_monitor'")
    assert stored[0]["n"] == 497


def test_ofr_is_the_tables_values_with_its_gaps_left_out():
    start, end = date(2025, 1, 1), date(2025, 2, 28)
    rows = query("SELECT date, value FROM indicators.ofr WHERE series_id = $1 AND date BETWEEN $2 AND $3"
                 " ORDER BY date", "REPO-DVP_AR_OO-P", start, end)
    assert any(r["value"] is None for r in rows)  # days that failed OFR's disclosure threshold
    # Use the name that the table has now. OFR-FULL changes it to the OFR name.
    name = {s.series_id: s.name for s in sync.ofr_series()}["REPO-DVP_AR_OO-P"]
    assert name
    served = sync.ofr(name.upper(), start, end)
    assert [(v.date, v.value) for v in served] == [(r["date"], r["value"]) for r in rows if r["value"] is not None]
    assert {(v.series_id, v.units) for v in served} == {("REPO-DVP_AR_OO-P", "percent")}
    assert sync.ofr("repo-dvp_ar_oo-p", start, end) == served


def test_ofr_dvp_gaps_and_negative_days_are_the_ones_the_docstring_names():
    rows = query("""
        SELECT count(*) FILTER (WHERE value IS NULL) AS nulls, min(date) FILTER (WHERE value IS NULL) AS first_null,
               max(date) FILTER (WHERE value IS NULL) AS last_null, count(*) FILTER (WHERE value < 0) AS negative,
               min(date) FILTER (WHERE value < 0) AS first_negative, max(date) FILTER (WHERE value < 0) AS last_negative
          FROM indicators.ofr WHERE series_id = 'REPO-DVP_AR_OO-P'""")
    assert dict(rows[0]) == {"nulls": 96, "first_null": date(2018, 5, 7), "last_null": date(2025, 2, 6),
                             "negative": 94, "first_negative": date(2020, 3, 24), "last_negative": date(2022, 3, 14)}
    month_ends = query("SELECT count(*) AS n FROM indicators.ofr WHERE series_id = 'MMF-MMF_TOT-M'"
                       " AND date <> (date_trunc('month', date) + interval '1 month - 1 day')::date")
    assert month_ends[0]["n"] == 0


_EIA_LATEST_SQL = """
SELECT DISTINCT ON (series_id) series_id, dataset, series_name AS name, units, frequency, category
  FROM indicators.eia ORDER BY series_id, date DESC
"""
_EIA_DATES_SQL = """
SELECT series_id, min(date) AS first_date, max(date) AS latest_date
  FROM indicators.eia WHERE value IS NOT NULL GROUP BY series_id
"""


def test_eia_series_is_every_series_with_its_latest_labels_and_value_dates():
    dates = {r["series_id"]: (r["first_date"], r["latest_date"]) for r in query(_EIA_DATES_SQL)}
    expected = [eia.EiaSeries(**dict(r), first_date=dates.get(r["series_id"], (None, None))[0],
                              latest_date=dates.get(r["series_id"], (None, None))[1])
                for r in query(_EIA_LATEST_SQL)]
    stored = sync.eia_series()
    assert stored == expected
    assert {"RWTC", "RBRTE", "EER_EPMRU_PF4_RGC_DPG", "EER_EPD2F_PF4_Y35NY_DPG", "EER_EPLLPA_PF4_Y44MB_DPG",
            "RNGWHHD", "MCRFPUS2"} <= {s.series_id for s in stored}
    assert all(s.name and _readers._resolve(s.name, stored, eia._HINT) is s for s in stored)


def test_eia_series_filters_give_the_series_the_table_holds_for_each_dataset_and_frequency():
    # The table can get new series (EIA-ADD added the weekly ones), thus the counts come from the table at run time.
    rows = query("SELECT dataset, frequency, count(DISTINCT series_id) AS n FROM indicators.eia GROUP BY 1, 2")
    assert Counter((s.dataset, s.frequency) for s in sync.eia_series()) == {
        (r["dataset"], r["frequency"]): r["n"] for r in rows}
    for r in rows:
        picked = sync.eia_series(dataset=r["dataset"], frequency=r["frequency"])
        assert len(picked) == r["n"] and {(s.dataset, s.frequency) for s in picked} == {(r["dataset"], r["frequency"])}
    for f in {r["frequency"] for r in rows}:
        assert len(sync.eia_series(frequency=f)) == sum(r["n"] for r in rows if r["frequency"] == f)
    with pytest.raises(ValueError, match="is not a stored EIA frequency"):
        sync.eia_series(frequency="daily")


def test_eia_is_the_tables_values():
    start, end = date(2020, 1, 1), date(2020, 12, 31)
    rows = query("SELECT date, value FROM indicators.eia WHERE series_id = 'RWTC' AND date BETWEEN $1 AND $2"
                 " ORDER BY date", start, end)
    served = sync.eia("WTI CRUDE SPOT (CUSHING)", start, end)
    assert len(rows) == 12
    assert [(v.date, v.value) for v in served] == [(r["date"], r["value"]) for r in rows if r["value"] is not None]
    assert {(v.series_id, v.units) for v in served} == {("RWTC", "$/BBL")}
    assert sync.eia("rwtc", start, end) == served


_USDA_LATEST_SQL = """
SELECT DISTINCT ON (series_id) series_id, dataset, series_name AS name, units, frequency, category
  FROM indicators.usda ORDER BY series_id, date DESC
"""
_USDA_DATES_SQL = """
SELECT series_id, min(date) AS first_date, max(date) AS latest_date
  FROM indicators.usda WHERE value IS NOT NULL GROUP BY series_id
"""


def test_usda_series_is_every_series_with_its_latest_labels_and_value_dates():
    dates = {r["series_id"]: (r["first_date"], r["latest_date"]) for r in query(_USDA_DATES_SQL)}
    expected = [usda.UsdaSeries(**dict(r), first_date=dates.get(r["series_id"], (None, None))[0],
                                latest_date=dates.get(r["series_id"], (None, None))[1])
                for r in query(_USDA_LATEST_SQL)]
    stored = sync.usda_series()
    assert stored == expected
    assert len(stored) >= 19 and {s.dataset for s in stored} >= {
        "usda/corn", "usda/soybeans", "usda/wheat", "usda/cotton", "usda/cattle", "usda/hogs", "usda/milk"}
    assert all(s.name and _readers._resolve(s.name, stored, usda._HINT) is s for s in stored)


def test_usda_series_filters_give_the_series_the_table_holds_for_each_dataset_and_frequency():
    # USDA-ADD can add series and frequencies, thus the counts come from the table at run time.
    rows = query("SELECT dataset, frequency, count(DISTINCT series_id) AS n FROM indicators.usda GROUP BY 1, 2")
    assert Counter((s.dataset, s.frequency) for s in sync.usda_series()) == {
        (r["dataset"], r["frequency"]): r["n"] for r in rows}
    for r in rows:
        picked = sync.usda_series(dataset=r["dataset"], frequency=r["frequency"])
        assert len(picked) == r["n"] and {(s.dataset, s.frequency) for s in picked} == {(r["dataset"], r["frequency"])}
    with pytest.raises(ValueError, match="is not a stored USDA frequency"):
        sync.usda_series(frequency="daily")


def test_usda_is_the_tables_values():
    start, end = date(2016, 1, 1), date(2025, 12, 31)
    rows = query("SELECT date, value FROM indicators.usda WHERE series_id = 'CORN, GRAIN - PRICE RECEIVED, MEASURED IN $ / BU'"
                 " AND date BETWEEN $1 AND $2 ORDER BY date", start, end)
    served = sync.usda("CORN PRICE RECEIVED", start, end)
    assert len(rows) == 10
    assert [(v.date, v.value) for v in served] == [(r["date"], r["value"]) for r in rows if r["value"] is not None]
    assert {(v.series_id, v.units) for v in served} == {("CORN, GRAIN - PRICE RECEIVED, MEASURED IN $ / BU", "$/BU")}
    assert sync.usda(" corn, grain - price received, measured in $ / bu", start, end) == served


def test_usda_annual_dates_and_first_years_are_the_ones_the_docstring_names():
    # The docstring names these facts. A USDA-ADD load that changes them must also change the docstring.
    annual = query("SELECT count(*) FILTER (WHERE frequency = 'annual' AND date <> date_trunc('year', date)::date)"
                   " AS not_jan1, count(*) FILTER (WHERE frequency IS NULL) AS no_frequency FROM indicators.usda")
    assert dict(annual[0]) == {"not_jan1": 0, "no_frequency": 0}
    first = {s.series_id: s.first_date for s in sync.usda_series(frequency="annual")}
    prices = {i: d for i, d in first.items() if "PRICE RECEIVED" in i}
    assert len(prices) == 7
    assert prices.pop("HOGS - PRICE RECEIVED, MEASURED IN $ / CWT") == date(1988, 1, 1)
    assert set(prices.values()) == {date(1996, 1, 1)}
    assert [(v.date.year, v.units) for v in sync.usda("HOGS - INVENTORY", date(1866, 1, 1))] == [
        (2024, "HEAD"), (2025, "HEAD")]

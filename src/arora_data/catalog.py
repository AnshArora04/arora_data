"""What is in the database: schemas, tables, columns and series.

Each table holds one vendor's data unless its description says otherwise. Six ownership and
holdings tables mix FMP history with recent SEC EDGAR rows (one source per period), and
panels.snapshots and feeds.events are shared factory tables with a provider column.
"""
from __future__ import annotations

from datetime import date

import asyncpg
from pydantic import BaseModel, ConfigDict

from arora_data._db import connection, fetch

# A MAX(date) with no index behind it reads the whole table, so bigger tables return no date.
# shortcut: 1M is an estimate, revisit once table sizes are measured on prod.
MAX_SCAN_ROWS = 1_000_000

SCHEMAS: dict[str, str] = {
    "commodities": "Daily commodity prices (open, high, low, close, volume).",
    "crypto": "Daily crypto prices for major pairs.",
    "equity_indexes": "Daily prices for major market indexes.",
    "estimates": "Analyst consensus estimates, grades, price targets and earnings surprises.",
    "events": "Market-wide calendars for earnings, dividends, splits and IPOs.",
    "feeds": "Dated events (SEC, FDA, NHTSA) and the tickers they mention.",
    "filings": "SEC filing metadata and industry classification.",
    "forex": "Daily currency-pair prices.",
    "fundamentals": "Financial statements, ratios, growth and revenue segments.",
    "gov": "Insider trades, beneficial ownership, and Senate and House trading.",
    "holdings": "ETF constituent holdings and mutual-fund N-PORT holdings.",
    "indicators": "Statistical time series, one table per provider.",
    "macro": "FRED macro series, plus FMP treasury rates, indicators and risk premium.",
    "market": "Daily stock prices, dividends, splits, valuations, and sector and industry data.",
    "news": "New York Times articles, their tags, and the tickers they mention.",
    "ownership": "13F institutional ownership: positions, superinvestors and holder analytics.",
    "panels": "Per-entity dated snapshots: CFTC Commitments of Traders and FINRA short volume.",
    "reference": "Ticker registry, company profiles, peers and other company reference data.",
    "transcripts": "Earnings-call transcript text.",
}

# "schema.table": (description, business-date column or None).
TABLES: dict[str, tuple[str, str | None]] = {
    "commodities.prices": ("FMP. Daily prices for all 40 FMP commodity and futures symbols.", "date"),
    "crypto.prices": ("FMP. Daily crypto prices.", "date"),
    "equity_indexes.prices": ("FMP. Daily market index prices.", "date"),
    "estimates.analyst_grades": ("FMP. Individual analyst grade changes.", "date"),
    "estimates.analyst_recommendations": ("Retired yfinance feed of buy, hold and sell counts.", None),
    "estimates.consensus_estimates": ("FMP. Consensus revenue, EBITDA and EPS estimates.", None),
    "estimates.earnings_surprises": ("FMP. Estimated against actual EPS and revenue.", "date"),
    "estimates.financial_ratings": ("FMP. Letter rating and component scores.", "date"),
    "estimates.grades_consensus": ("FMP. Current buy, hold and sell counts with the consensus.", "date"),
    "estimates.grades_historical": ("FMP. Buy, hold and sell counts over time.", "date"),
    "estimates.price_target_consensus": ("FMP. High, low, median and consensus price targets.", "date"),
    "estimates.price_target_summary": ("FMP. Analyst target counts and averages by lookback window.", "date"),
    "events.dividends_calendar": ("FMP. Dividend calendar; dates can be in the future.", "ex_date"),
    "events.earnings_calendar": ("FMP. Earnings calendar; dates can be in the future.", "date"),
    "events.ipos_calendar": ("FMP. IPO calendar; dates can be in the future.", "date"),
    "events.splits_calendar": ("FMP. Stock split calendar; dates can be in the future.", "date"),
    "feeds.event_tickers": ("Derived bridge from feed events to the tickers they mention.", None),
    "feeds.events": ("SEC, FDA and NHTSA dated events; the provider column gives the source.", "event_date"),
    "filings.filings": ("SEC EDGAR. Filing metadata by company and form type.", "period_of_report"),
    "filings.industry_classification": ("FMP. SIC codes and industry titles.", None),
    "forex.prices": ("FMP. Daily currency-pair prices.", "date"),
    "fundamentals.balance_sheets": ("FMP. Standardized balance sheets.", "period_end_date"),
    "fundamentals.balance_sheets_as_reported": ("FMP. Annual balance sheets as filed, in JSON.", "period_end_date"),
    "fundamentals.balance_sheets_as_reported_quarterly": ("FMP. Quarterly balance sheets as filed, in JSON.", "period_end_date"),
    "fundamentals.cash_flow_statements": ("FMP. Standardized cash flow statements.", "period_end_date"),
    "fundamentals.cash_flow_statements_as_reported": ("FMP. Annual cash flow statements as filed, in JSON.", "period_end_date"),
    "fundamentals.cash_flow_statements_as_reported_quarterly": ("FMP. Quarterly cash flow statements as filed, in JSON.", "period_end_date"),
    "fundamentals.financial_growth": ("FMP. Period-over-period growth rates.", "date"),
    "fundamentals.financial_ratios": ("FMP. Margin, liquidity and leverage ratios by period.", "date"),
    "fundamentals.financial_ratios_ttm": ("FMP. Trailing-twelve-month ratios.", "date"),
    "fundamentals.financial_reports_metadata": ("FMP. Links to each company's financial report files.", None),
    "fundamentals.financial_statements_full_as_reported": ("FMP. Annual full statements as filed, in JSON.", "period_end_date"),
    "fundamentals.financial_statements_full_as_reported_quarterly": ("FMP. Quarterly full statements as filed, in JSON.", "period_end_date"),
    "fundamentals.income_statements": ("FMP. Standardized income statements.", "period_end_date"),
    "fundamentals.income_statements_as_reported": ("FMP. Annual income statements as filed, in JSON.", "period_end_date"),
    "fundamentals.income_statements_as_reported_quarterly": ("FMP. Quarterly income statements as filed, in JSON.", "period_end_date"),
    "fundamentals.key_metrics": ("FMP. Valuation and operating metrics by period.", "date"),
    "fundamentals.key_metrics_ttm": ("FMP. Trailing-twelve-month valuation and operating metrics.", "date"),
    "fundamentals.owner_earnings": ("FMP. Owner earnings with growth and maintenance capex.", "date"),
    "fundamentals.revenue_segments": ("FMP. Revenue by business or geographic segment.", "date"),
    "gov.beneficial_ownership": ("FMP. Beneficial ownership filings.", None),
    "gov.house_trading": ("FMP. House member stock trades.", "transaction_date"),
    "gov.insider_trades": ("FMP. Company insider transactions.", "transaction_date"),
    "gov.senate_trading": ("FMP. Senator stock trades.", "transaction_date"),
    "holdings.etf_holdings": ("FMP and SEC EDGAR N-PORT. Latest holdings per ETF; FMP rows have no date.", "as_of_date"),
    "holdings.etf_holdings_history": ("SEC EDGAR N-PORT. Every quarter of ETF holdings, one row per filing line.", "as_of_date"),
    "holdings.etf_nport_filings": ("SEC EDGAR. Internal ledger of N-PORT filings seen per ETF.", None),
    "holdings.fund_disclosures": ("FMP history and SEC EDGAR N-PORT. Quarterly holdings for flagship mutual funds.", "report_date"),
    "indicators.bea": ("BEA. National accounts series.", "date"),
    "indicators.bls": ("BLS. Labor statistics series.", "date"),
    "indicators.census_asm": ("Census. Annual Survey of Manufactures series.", "date"),
    "indicators.census_bds": ("Census. Business Dynamics Statistics series.", "date"),
    "indicators.census_govs": ("Census. Government finance series.", "date"),
    "indicators.census_intltrade": ("Census. International trade series.", "date"),
    "indicators.dbnomics": ("DBnomics. AMECO, BIS, Eurostat, OECD and World Bank macro series; a few annual AMECO rows are dated next year.", "date"),
    "indicators.eia": ("EIA. Energy series.", "date"),
    "indicators.ici": ("ICI. Investment fund industry series.", "date"),
    "indicators.nyfed": ("New York Fed series.", "date"),
    "indicators.ofr": ("OFR. Financial stability series.", "date"),
    "indicators.treasury_fiscal": ("US Treasury. Fiscal data series.", "date"),
    "indicators.usda": ("USDA. Agriculture series.", "date"),
    "macro.damodaran_country_risk": ("Aswath Damodaran, NYU Stern. Country risk premiums by edition.", "edition_date"),
    "macro.damodaran_crp_editions": ("Aswath Damodaran, NYU Stern. Country risk premium editions.", "edition_date"),
    "macro.market_risk_premium": ("FMP. Equity and country risk premium by country.", "date"),
    "macro.observations": ("FRED. Macro time series.", "date"),
    "macro.treasury_rates": ("FMP. Daily treasury yields from 1 month to 30 years.", "date"),
    "market.dividends": ("FMP. Dividend history.", "ex_date"),
    "market.enterprise_values": ("FMP. Enterprise value and its components.", "date"),
    "market.industry_pe_history": ("FMP. Industry P/E over time.", "date"),
    "market.industry_pe_snapshots": ("FMP. Industry P/E snapshots.", "date"),
    "market.industry_performance_history": ("FMP. Industry average change over time.", "date"),
    "market.industry_performance_snapshots": ("FMP. Industry average change snapshots.", "date"),
    "market.price_history": ("FMP. Daily stock prices with adjusted close.", "date"),
    "market.price_history_dividend_adjusted": ("FMP. Daily stock prices, dividend adjusted.", "date"),
    "market.price_history_unadjusted": ("FMP. Daily stock prices, unadjusted.", "date"),
    "market.sector_pe_history": ("FMP. Sector P/E over time.", "date"),
    "market.sector_pe_snapshots": ("FMP. Sector P/E snapshots.", "date"),
    "market.sector_performance_history": ("FMP. Sector average change over time.", "date"),
    "market.sector_performance_snapshots": ("FMP. Sector average change snapshots.", "date"),
    "market.stock_splits": ("FMP. Stock split history.", "date"),
    "market.valuation_snapshots": ("FMP. Valuation ratios such as P/E, P/B and EV/EBITDA.", "date"),
    "news.article_keywords": ("New York Times. Tags on each article.", None),
    "news.article_tickers": ("Derived from New York Times tags: tickers each article mentions.", None),
    "news.articles": ("New York Times. Article metadata.", "pub_date"),
    "ownership.iha_backfill_progress": ("Internal ledger of the holder-analytics backfill.", None),
    "ownership.institutional_filer_performance": ("FMP history and SEC EDGAR. Superinvestor size, turnover and performance per quarter.", "date"),
    "ownership.institutional_holder_analytics": ("FMP history and SEC EDGAR. One institution's position in one security per quarter.", "date"),
    "ownership.institutional_holdings": ("FMP history and SEC EDGAR. Superinvestor 13F positions per quarter.", "date"),
    "ownership.institutional_positions_summary": ("FMP history and SEC EDGAR. Aggregate 13F ownership per symbol and quarter.", "date"),
    "panels.snapshots": ("CFTC Commitments of Traders and FINRA short volume; the provider column gives the source.", "as_of_date"),
    "reference.actively_trading_status": ("FMP. Whether each symbol is actively trading.", None),
    "reference.cap_tier_bands": ("Internal. Market-cap tier bands.", None),
    "reference.company_notes": ("FMP. Notes issued by each company.", None),
    "reference.company_profiles": ("FMP. One profile per company: name, sector, industry and identifiers.", None),
    "reference.cusip_ticker_map": ("OpenFIGI. CUSIP to ticker map.", None),
    "reference.data_providers": ("Internal. Registry of data providers and their licenses.", None),
    "reference.dcf_valuations": ("FMP. Discounted cash flow value against stock price.", "date"),
    "reference.delisted_companies": ("FMP. Delisted companies and their delisting dates, which can be in the future.", "delisted_date"),
    "reference.employee_count_history": ("FMP. Employee counts from filings.", "period_of_report"),
    "reference.etfs": ("FMP. List of ETFs.", None),
    "reference.executive_compensation": ("FMP. Executive pay by person and year.", None),
    "reference.financial_scores": ("FMP. Altman Z and Piotroski scores.", "date"),
    "reference.index_constituents": ("FMP. Index membership with start and removal dates.", "effective_date"),
    "reference.mergers_acquisitions": ("FMP. Merger and acquisition filings.", "transaction_date"),
    "reference.org_ticker_alias": ("Internal. Cache matching organization names to tickers.", None),
    "reference.outlier_revenue_floors": ("Internal. Revenue floors for data-quality checks.", None),
    "reference.promotion_candidates": ("Internal. Symbols being considered for the ticker universe.", None),
    "reference.sec_cusip_ticker_map": ("SEC EDGAR. CUSIP to ticker map from the 13F Securities List.", None),
    "reference.share_float": ("FMP. Free float and shares outstanding.", "date"),
    "reference.stock_list": ("FMP. List of stock symbols.", None),
    "reference.stock_peers": ("FMP. Peer companies for each ticker.", None),
    "reference.symbol_changes": ("FMP. Ticker symbol changes.", "change_date"),
    "reference.tickers": ("Internal. Registry of the tickers we track; no vendor source.", None),
    "transcripts.earnings_calls": ("FMP. Earnings-call transcript text.", None),
}

_INDICATOR_TABLES = (
    "bea", "bls", "census_asm", "census_bds", "census_govs", "census_intltrade", "dbnomics",
    "eia", "ici", "nyfed", "ofr", "treasury_fiscal", "usda",
)

# "schema.table": (series key column, name column or None).
SERIES: dict[str, tuple[str, str | None]] = {
    "macro.observations": ("series_id", "series_name"),
    "commodities.prices": ("symbol", None),
    "crypto.prices": ("symbol", None),
    "equity_indexes.prices": ("symbol", None),
    "forex.prices": ("symbol", None),
    **{f"indicators.{t}": ("series_id", "series_name") for t in _INDICATOR_TABLES},
}

_LIST_SCHEMAS_SQL = "SELECT nspname::text AS name FROM pg_namespace WHERE nspname = ANY($1) ORDER BY nspname"

# Partition children are hidden. A partitioned parent's own estimate is unreliable, so sum its children.
_LIST_TABLES_SQL = """
SELECT c.relname::text AS name,
       (CASE WHEN c.relkind = 'p' THEN (SELECT sum(greatest(k.reltuples, 0))
                                          FROM pg_inherits i JOIN pg_class k ON k.oid = i.inhrelid
                                         WHERE i.inhparent = c.oid)
             ELSE nullif(c.reltuples, -1) END)::bigint AS row_estimate,
       ARRAY(SELECT a.attname::text
               FROM pg_index x
               JOIN pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = x.indkey[0]
              WHERE x.indrelid = c.oid AND x.indisvalid AND x.indpred IS NULL) AS indexed_first
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = $1 AND c.relkind IN ('r', 'p') AND NOT c.relispartition
 ORDER BY c.relname
"""

_LIST_COLUMNS_SQL = """
SELECT a.attname::text AS name,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS nullable,
       col_description(c.oid, a.attnum) AS description
  FROM pg_attribute a
  JOIN pg_class c ON c.oid = a.attrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = $1 AND c.relname = $2
   AND c.relkind IN ('r', 'p') AND NOT c.relispartition
   AND a.attnum > 0 AND NOT a.attisdropped
 ORDER BY a.attnum
"""


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SchemaInfo(_Row):
    name: str
    description: str


class TableInfo(_Row):
    name: str
    description: str | None
    row_estimate: int | None
    latest_date: date | None


class ColumnInfo(_Row):
    name: str
    data_type: str
    nullable: bool
    description: str | None


class SeriesInfo(_Row):
    key: str
    dataset: str | None
    description: str | None
    latest_date: date | None


def _check_schema(schema: str) -> None:
    if schema not in SCHEMAS:
        raise ValueError(f"unknown schema: {schema!r}")


async def _latest_date(
    conn: asyncpg.Connection, table: str, date_col: str | None, row_estimate: int | None, indexed_first: list[str]
) -> date | None:
    if date_col is None:
        return None
    small = row_estimate is not None and row_estimate < MAX_SCAN_ROWS
    if date_col not in indexed_first and not small:
        return None
    try:
        # table and date_col are TABLES constants, never caller input.
        rows = await fetch(conn, f"SELECT max({date_col})::date AS latest FROM {table}")
    except asyncpg.QueryCanceledError:
        return None
    return rows[0]["latest"]


async def list_schemas() -> list[SchemaInfo]:
    """Data schemas that exist. Source: Postgres catalog, with descriptions held in this package."""
    async with connection() as conn:
        rows = await fetch(conn, _LIST_SCHEMAS_SQL, list(SCHEMAS))
    return [SchemaInfo(name=r["name"], description=SCHEMAS[r["name"]]) for r in rows]


async def list_tables(schema: str) -> list[TableInfo]:
    """Tables in a schema, with each one's description, estimated rows and most recent date.

    Source: Postgres catalog; each description names the table's vendor. latest_date is None
    when the table has no date column, is RLS-blocked or empty, or is over MAX_SCAN_ROWS with
    no index on its date, or the query times out. Dates can be in the future for events.*,
    indicators.dbnomics and reference.delisted_companies.
    """
    _check_schema(schema)
    tables = []
    async with connection() as conn:
        for r in await fetch(conn, _LIST_TABLES_SQL, schema):
            key = f"{schema}.{r['name']}"
            description, date_col = TABLES.get(key, (None, None))
            latest = await _latest_date(conn, key, date_col, r["row_estimate"], r["indexed_first"])
            tables.append(
                TableInfo(name=r["name"], description=description, row_estimate=r["row_estimate"], latest_date=latest)
            )
    return tables


async def list_columns(schema: str, table: str) -> list[ColumnInfo]:
    """Columns of a table. Source: Postgres catalog; most descriptions are None (few columns have comments)."""
    _check_schema(schema)
    async with connection() as conn:
        rows = await fetch(conn, _LIST_COLUMNS_SQL, schema, table)
    if not rows:
        raise ValueError(f"unknown table: {schema}.{table}")
    return [ColumnInfo(**dict(r)) for r in rows]


async def list_series(schema: str, table: str) -> list[SeriesInfo]:
    """Series in a time-series table, with each one's name and most recent date.

    Source: the table's vendor, per TABLES (FRED, FMP, BLS, BEA...). dataset is None outside
    indicators.*, where the same key can mean different series in different datasets.
    description is None for tables with no name column. Raises TimeoutError when the table is too big
    to group within the timeout.
    """
    key = f"{schema}.{table}"
    if key not in SERIES:
        raise ValueError(f"{key} has no series list")
    key_col, name_col = SERIES[key]
    name_sql = f"max({name_col})" if name_col else "NULL::text"
    dataset_sql = "dataset" if schema == "indicators" else "NULL::text"
    # key, key_col and name_col are SERIES constants: the lookup above rejects anything else.
    sql = (
        f"SELECT {key_col} AS key, {dataset_sql} AS dataset, {name_sql} AS description, "
        f"max(date)::date AS latest_date FROM {key} GROUP BY 1, 2 ORDER BY 1, 2"
    )
    # shortcut: this groups every row of the table, the one read here with no index bound. Fine while each
    # series table groups within the 5s timeout; switch to a skip scan (one index probe per series) once one doesn't.
    async with connection() as conn:
        try:
            rows = await fetch(conn, sql)
        except asyncpg.QueryCanceledError as err:
            raise TimeoutError(f"{key} is too big to list its series within the timeout") from err
    return [SeriesInfo(**dict(r)) for r in rows]

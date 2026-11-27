"""Blocking versions of the async functions, for notebooks and scripts. No SQL lives here."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from typing import ParamSpec, TypeVar

from arora_data import _db, bea as _bea, bls as _bls, catalog, commodities as _commodities, country_risk_premium as _country_risk_premium, crypto as _crypto, damodaran_editions as _damodaran_editions, eia as _eia, equity_risk_premium as _equity_risk_premium, forex as _forex, fred as _fred, index as _index, nyfed as _nyfed, ofr as _ofr, series, sovereign_rating as _sovereign_rating, sovereign_spread as _sovereign_spread, treasury as _treasury, treasury_curve as _treasury_curve, treasury_fiscal as _treasury_fiscal

P = ParamSpec("P")
T = TypeVar("T")


def _sync(fn: Callable[P, Awaitable[T]]) -> Callable[P, T]:
    async def run(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await fn(*args, **kwargs)
        finally:
            await _db.close_pool()

    @wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        # A worker thread, because asyncio.run raises inside Jupyter's already-running loop.
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, run(*args, **kwargs)).result()

    return wrapper


list_schemas = _sync(catalog.list_schemas)
list_tables = _sync(catalog.list_tables)
list_columns = _sync(catalog.list_columns)
list_series = _sync(catalog.list_series)
check_fresh = _sync(series.check_fresh)
forex = _sync(_forex.forex)
commodities = _sync(_commodities.commodities)
crypto = _sync(_crypto.crypto)
index = _sync(_index.index)
treasury = _sync(_treasury.treasury)
treasury_curve = _sync(_treasury_curve.treasury_curve)
fred = _sync(_fred.fred)
fred_series = _sync(_fred.fred_series)
bls = _sync(_bls.bls)
bls_series = _sync(_bls.bls_series)
bea = _sync(_bea.bea)
bea_series = _sync(_bea.bea_series)
nyfed = _sync(_nyfed.nyfed)
nyfed_series = _sync(_nyfed.nyfed_series)
treasury_fiscal = _sync(_treasury_fiscal.treasury_fiscal)
treasury_fiscal_series = _sync(_treasury_fiscal.treasury_fiscal_series)
ofr = _sync(_ofr.ofr)
ofr_series = _sync(_ofr.ofr_series)
eia = _sync(_eia.eia)
eia_series = _sync(_eia.eia_series)
damodaran_editions = _sync(_damodaran_editions.damodaran_editions)
sovereign_rating = _sync(_sovereign_rating.sovereign_rating)
sovereign_spread = _sync(_sovereign_spread.sovereign_spread)
country_risk_premium = _sync(_country_risk_premium.country_risk_premium)
equity_risk_premium = _sync(_equity_risk_premium.equity_risk_premium)

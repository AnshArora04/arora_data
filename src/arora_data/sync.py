"""Blocking versions of the async functions, for notebooks and scripts. No SQL lives here."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from typing import ParamSpec, TypeVar

from arora_data import _db, catalog, commodities as _commodities, crypto as _crypto, forex as _forex, fred as _fred, index as _index, series, treasury as _treasury, treasury_curve as _treasury_curve

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

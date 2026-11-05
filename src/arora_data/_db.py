"""The one way into the database: readonly_user, read-only transactions, bounded queries."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import asyncpg
from dotenv import dotenv_values

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
DEFAULT_LIMIT = 1000
MAX_LIMIT = 5000
# shortcut: 5 is a guess that keeps us small next to the ingest jobs, raise it once readonly_user's limit is measured.
POOL_SIZE = 5
CLOSE_TIMEOUT = 10  # seconds a graceful pool close may take before its connections are dropped

# One pool per event loop, because a pool only works on the loop that made it. Each value is a task, so callers that
# arrive together share one pool and one login attempt.
_pools: dict[asyncio.AbstractEventLoop, asyncio.Task[asyncpg.Pool]] = {}


class RowCapError(ValueError):
    """More rows matched than the limit allows."""


def _dsn() -> str:
    dsn = dotenv_values(ENV_PATH).get("ARORA_READONLY_DSN")
    if not dsn:
        raise RuntimeError(f"ARORA_READONLY_DSN is not set in {ENV_PATH}")
    # The pooler's username form is readonly_user.<project-ref>.
    username = urlsplit(dsn).username or ""
    if username.split(".")[0] != "readonly_user":
        raise RuntimeError("ARORA_READONLY_DSN must log in as readonly_user")
    return dsn


async def _pool() -> asyncpg.Pool:
    loop = asyncio.get_running_loop()
    # A caller that never ran close_pool leaves its ended loop's pool here; dropping it lets its socket close.
    for old in [lp for lp in list(_pools) if lp.is_closed()]:
        del _pools[old]
    task = _pools.get(loop)
    if task is None:
        # min_size=1 logs in once up front, so a bad password fails once rather than once per waiting caller.
        create = asyncpg.create_pool(_dsn(), statement_cache_size=0, min_size=1, max_size=POOL_SIZE)
        task = _pools[loop] = asyncio.ensure_future(create)
    try:
        # shield: cancelling one waiter must not cancel the login the others share.
        return await asyncio.shield(task)
    except BaseException:
        # Forget the pool only when the login itself failed, not when this waiter was cancelled.
        if task.done() and _pools.get(loop) is task:
            del _pools[loop]
        raise


@asynccontextmanager
async def connection() -> AsyncIterator[asyncpg.Connection]:
    """A pooled connection. At most POOL_SIZE are open per event loop; extra callers wait."""
    async with (await _pool()).acquire() as conn:
        yield conn


async def close_pool() -> None:
    """Close this event loop's pool. Call it before the loop ends; the sync wrappers do."""
    task = _pools.pop(asyncio.get_running_loop(), None)
    if task is None:
        return
    pool = await task
    try:
        # A graceful close waits on the server, so a dropped network would hang it with no end.
        await asyncio.wait_for(pool.close(), CLOSE_TIMEOUT)
    except asyncio.TimeoutError:
        pool.terminate()


async def fetch(conn: asyncpg.Connection, sql: str, *args: Any) -> list[asyncpg.Record]:
    """Run one query in its own read-only transaction, with dates cast in Eastern Time."""
    async with conn.transaction(readonly=True):
        await conn.execute("SET LOCAL statement_timeout = '5s'")
        await conn.execute("SET LOCAL TIME ZONE 'America/New_York'")
        return await conn.fetch(sql, *args)


def check_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


def check_cap(rows: list[asyncpg.Record], limit: int) -> list[asyncpg.Record]:
    """Raise rather than return a cut-off result. The query must ask for limit + 1 rows."""
    if len(rows) > limit:
        raise RowCapError(f"more than {limit} rows matched; narrow the range or raise limit (max {MAX_LIMIT})")
    return rows

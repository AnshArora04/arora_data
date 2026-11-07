"""Guard rails that need no database: every failure here means a bad query could reach prod."""
import asyncio
import contextlib
from datetime import date

import asyncpg
import pytest

from arora_data import _db, catalog, sync


class FakeConn:
    def __init__(self, error=None):
        self.queries = []
        self.readonly = None
        self.error = error

    def transaction(self, readonly=False):
        self.readonly = readonly
        conn = self

        class Tx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return Tx()

    async def execute(self, sql):
        self.queries.append(sql)

    async def fetch(self, sql, *args):
        self.queries.append(sql)
        if self.error:
            raise self.error
        return [{"latest": date(2026, 10, 30)}]


def write_env(tmp_path, monkeypatch, dsn):
    env = tmp_path / ".env"
    env.write_text(f"ARORA_READONLY_DSN={dsn}\n")
    monkeypatch.setattr(_db, "ENV_PATH", env)


@pytest.fixture
def no_connect(monkeypatch):
    def fail():
        raise AssertionError("connected to the database")

    monkeypatch.setattr(catalog, "connection", fail)


@pytest.mark.parametrize("user", ["postgres", "postgres.abc", "readonly_user_x", "other.readonly_user"])
def test_dsn_refuses_other_users_without_leaking_it(tmp_path, monkeypatch, user):
    write_env(tmp_path, monkeypatch, f"postgresql://{user}:s3cret@host:6543/postgres")
    with pytest.raises(RuntimeError) as err:
        _db._dsn()
    assert "s3cret" not in str(err.value) and user not in str(err.value)


@pytest.mark.parametrize("user", ["readonly_user", "readonly_user.abc"])
def test_dsn_accepts_readonly_user(tmp_path, monkeypatch, user):
    dsn = f"postgresql://{user}:pw@host:6543/postgres"
    write_env(tmp_path, monkeypatch, dsn)
    assert _db._dsn() == dsn


def test_dsn_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(_db, "ENV_PATH", tmp_path / ".env")
    with pytest.raises(RuntimeError, match="not set"):
        _db._dsn()


def test_fetch_is_read_only_with_timeout():
    conn = FakeConn()
    asyncio.run(_db.fetch(conn, "SELECT 1"))
    assert conn.readonly is True
    assert "SET LOCAL statement_timeout = '5s'" in conn.queries


@pytest.mark.parametrize(
    "call",
    [
        lambda: catalog.list_tables("ops"),
        lambda: catalog.list_columns("ops", "ingestion_log"),
        lambda: catalog.list_series("market", "price_history"),
        lambda: catalog.list_series("ownership", "institutional_holder_analytics"),
        lambda: catalog.list_series("macro; DROP TABLE x; --", "observations"),
    ],
)
def test_bad_input_rejected_before_connecting(call, no_connect):
    with pytest.raises(ValueError):
        asyncio.run(call())


@pytest.mark.parametrize(
    "date_col, estimate, indexed, queries",
    [
        (None, 10, [], False),
        ("date", 2_000_000, [], False),
        ("date", None, [], False),
        ("date", 2_000_000, ["date"], True),
        ("date", 10, [], True),
    ],
)
def test_latest_date_only_reads_when_cheap(date_col, estimate, indexed, queries):
    conn = FakeConn()
    latest = asyncio.run(catalog._latest_date(conn, "macro.observations", date_col, estimate, indexed))
    assert (latest == date(2026, 10, 30)) is queries
    assert bool(conn.queries) is queries


def test_latest_date_blank_on_timeout():
    conn = FakeConn(error=asyncpg.QueryCanceledError("timeout"))
    assert asyncio.run(catalog._latest_date(conn, "macro.observations", "date", 10, [])) is None


def test_list_series_timeout_names_the_table(monkeypatch):
    conn = FakeConn(error=asyncpg.QueryCanceledError("canceling statement due to statement timeout"))

    @contextlib.asynccontextmanager
    async def fake_connection():
        yield conn

    monkeypatch.setattr(catalog, "connection", fake_connection)
    with pytest.raises(TimeoutError, match="indicators.dbnomics"):
        asyncio.run(catalog.list_series("indicators", "dbnomics"))


class FakePool:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = self.terminated = False

    def __await__(self):
        yield from asyncio.sleep(0).__await__()
        return self

    async def close(self):
        self.closed = True

    def terminate(self):
        self.terminated = True


@pytest.fixture
def fake_pools(tmp_path, monkeypatch):
    write_env(tmp_path, monkeypatch, "postgresql://readonly_user:pw@host:6543/postgres")
    made = []

    def create_pool(dsn, **kwargs):
        made.append(FakePool(**kwargs))
        return made[-1]

    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    return made


def test_pool_is_capped_and_shared_by_callers_that_arrive_together(fake_pools):
    async def main():
        pools = await asyncio.gather(*(_db._pool() for _ in range(8)))
        await _db.close_pool()
        return pools

    pools = asyncio.run(main())
    assert len(fake_pools) == 1 and all(p is fake_pools[0] for p in pools)
    assert fake_pools[0].kwargs == {"statement_cache_size": 0, "min_size": 1, "max_size": _db.POOL_SIZE}
    assert fake_pools[0].closed and not _db._pools


def test_pools_of_ended_loops_are_dropped(fake_pools):
    # Two asyncio.run calls that never close their pool, like a script calling the async API directly.
    asyncio.run(_db._pool())
    asyncio.run(_db._pool())
    assert len(fake_pools) == 2 and len(_db._pools) == 1
    _db._pools.clear()


def test_cancelled_caller_does_not_cancel_the_shared_login(tmp_path, monkeypatch):
    write_env(tmp_path, monkeypatch, "postgresql://readonly_user:pw@host:6543/postgres")
    made = []

    async def slow_create_pool(dsn, **kwargs):
        await asyncio.sleep(0.05)
        made.append(FakePool(**kwargs))
        return made[-1]

    monkeypatch.setattr(asyncpg, "create_pool", slow_create_pool)

    async def main():
        other = asyncio.ensure_future(_db._pool())
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_db._pool(), 0.01)
        assert await other is made[0]
        assert len(made) == 1 and _db._pools
        await _db.close_pool()

    asyncio.run(main())
    assert not _db._pools


def test_close_gives_up_and_drops_connections_when_the_server_does_not_answer(fake_pools, monkeypatch):
    monkeypatch.setattr(_db, "CLOSE_TIMEOUT", 0.01)

    async def hangs():
        # Long next to the 0.01s timeout, short enough that a broken timeout fails the test instead of stalling it.
        await asyncio.sleep(2)

    async def main():
        pool = await _db._pool()
        pool.close = hangs
        await _db.close_pool()
        return pool

    pool = asyncio.run(main())
    assert pool.terminated and not _db._pools


def test_graceful_close_does_not_terminate(fake_pools):
    async def main():
        pool = await _db._pool()
        await _db.close_pool()
        return pool

    pool = asyncio.run(main())
    assert pool.closed and not pool.terminated


def test_failed_login_is_not_cached(tmp_path, monkeypatch):
    write_env(tmp_path, monkeypatch, "postgresql://readonly_user:pw@host:6543/postgres")
    calls = []

    async def create_pool(dsn, **kwargs):
        calls.append(1)
        raise OSError("refused")

    monkeypatch.setattr(asyncpg, "create_pool", create_pool)

    async def main():
        for _ in range(2):
            with pytest.raises(OSError):
                await _db._pool()

    asyncio.run(main())
    assert len(calls) == 2 and not _db._pools


def test_sync_closes_its_pool(fake_pools):
    async def uses_pool():
        return await _db._pool()

    pool = sync._sync(uses_pool)()
    assert pool.closed and not _db._pools


def test_constants_are_consistent():
    assert {t.split(".")[0] for t in catalog.TABLES} == set(catalog.SCHEMAS)
    assert set(catalog.SERIES) <= set(catalog.TABLES)
    assert len(catalog.SERIES) == 20
    assert "ops" not in catalog.SCHEMAS
    assert all(catalog.TABLES[t][1] == "date" for t in catalog.SERIES)


def test_sync_runs_inside_a_running_loop():
    async def from_notebook():
        async def fn(x):
            return x + 1

        return sync._sync(fn)(1)

    assert asyncio.run(from_notebook()) == 2

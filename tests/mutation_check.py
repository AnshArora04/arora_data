"""Break one source line at a time and confirm the tests notice. Run: python tests/mutation_check.py"""
import filecmp
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "arora_data"

# (file, line as written, its broken replacement)
MUTATIONS = [
    ("_db.py", 'if username.split(".")[0] != "readonly_user":', "if False:"),
    ("_db.py", "if not 1 <= limit <= MAX_LIMIT:", "if False:"),
    ("_db.py", "conn.transaction(readonly=True)", "conn.transaction()"),
    ("_db.py", """await conn.execute("SET LOCAL statement_timeout = '5s'")""", "pass"),
    ("catalog.py", "if date_col not in indexed_first and not small:", "if False:"),
    ("catalog.py", "except asyncpg.QueryCanceledError:", "except ZeroDivisionError:"),
    ("catalog.py", "if schema not in SCHEMAS:", "if False:"),
    ("catalog.py", "if key not in SERIES:", "if False:"),
    ("catalog.py", "    check_limit(limit)\n", "    pass\n"),
    ("sync.py", "return pool.submit(asyncio.run, run(*args, **kwargs)).result()", "return asyncio.run(run(*args, **kwargs))"),
    # Caught only by test_prod.py.
    ("catalog.py", '"macro.economic_indicators": ("indicator", None)', '"macro.economic_indicators": ("indicator_x", None)'),
    ("catalog.py", '"reference.etfs": ("FMP. List of ETFs.", None),\n', ""),
    ("catalog.py", "WHERE n.nspname = $1 AND c.relkind IN ('r', 'p') AND NOT c.relispartition\n ORDER BY c.relname", "WHERE n.nspname = $1 AND c.relkind IN ('r', 'p')\n ORDER BY c.relname"),
    ("catalog.py", "_LIST_SCHEMAS_SQL, list(SCHEMAS))", "_LIST_SCHEMAS_SQL, list(SCHEMAS)[:3])"),
    ("catalog.py", '"macro.observations": ("FRED. Macro time series.", "date")', '"macro.observations": ("FRED. Macro time series.", None)'),
    ("catalog.py", "NOT a.attnotnull AS nullable", "a.attnotnull AS nullable"),
    ("catalog.py", '    if not rows:\n        raise ValueError(f"unknown table: {schema}.{table}")\n', ""),
    ("catalog.py", '"macro.observations": ("series_id", "series_name")', '"macro.observations": ("series_id", None)'),
    ("catalog.py", 'dataset_sql = "dataset" if schema == "indicators" else "NULL::text"', 'dataset_sql = "NULL::text"'),
    ("frames.py", "date: pl.Date, Decimal: None}", "date: pl.Date, Decimal: pl.Float64}"),
    ("frames.py", ", schema=schema, infer_schema_length=None)", ", infer_schema_length=None)"),
    ("frames.py", ", schema=schema, infer_schema_length=None)", ", schema=schema, infer_schema_length=1)"),
    ("frames.py", "return df.cast({name: pl.Decimal(scale=0) for name, dtype in df.schema.items() if dtype == pl.Null})", "return df"),
    ("_db.py", "max_size=POOL_SIZE)", "max_size=50)"),
    ("_db.py", "statement_cache_size=0, min_size=1", "min_size=1"),
    ("_db.py", "min_size=1, max_size", "min_size=0, max_size"),
    ("_db.py", "task = _pools[loop] = asyncio.ensure_future(create)", "task = asyncio.ensure_future(create)"),
    ("_db.py", "            del _pools[loop]\n", "            pass\n"),
    ("_db.py", "return await asyncio.shield(task)", "return await task"),
    ("_db.py", "if task.done() and _pools.get(loop) is task:", "if _pools.get(loop) is task:"),
    ("_db.py", "await asyncio.wait_for(pool.close(), CLOSE_TIMEOUT)", "await pool.close()"),
    ("_db.py", "        pool.terminate()\n", "        pass\n"),
    ("_db.py", "task = _pools.pop(asyncio.get_running_loop(), None)", "task = _pools.get(asyncio.get_running_loop())"),
    ("_db.py", "if len(rows) > limit:", "if False:"),
    ("_db.py", "if lp.is_closed()]:", "if False]:"),
    ("catalog.py", "check_cap(await fetch(conn, sql, limit + 1), limit)", "check_cap(await fetch(conn, sql, limit), limit)"),
    ("catalog.py", "        except asyncpg.QueryCanceledError as err:\n", "        except ZeroDivisionError as err:\n"),
    ("sync.py", "            await _db.close_pool()\n", "            pass\n"),
    ("dates.py", 'EASTERN = ZoneInfo("America/New_York")', 'EASTERN = ZoneInfo("UTC")'),
    ("dates.py", "if now is not None and now.tzinfo is None:", "if False:"),
    ("_readers.py", "    if table not in _PRICE_TABLES:\n", "    if False:\n"),
    ("_readers.py", "    if table not in _VALUE_TABLES:\n", "    if False:\n"),
    ("_readers.py", "if (provider is None) != (dataset is None):", "if False:"),
    ("_readers.py", "    if start > end:\n", "    if False:\n"),
    ("_readers.py", "    check_limit(limit)\n", "    pass\n"),
    ("_readers.py", "start, end, limit + 1), limit)\n    return [PriceBar", "start, end, limit), limit)\n    return [PriceBar"),
    ("_readers.py", "start, end, limit + 1), limit)\n    return [Value", "start, end, limit), limit)\n    return [Value"),
    ("_readers.py", '"provider = $1 AND dataset = $2 AND series_id = $3"', '"dataset = $2 AND series_id = $3 AND $1::text IS NOT NULL"'),
    ("_readers.py", 'vwap = "vwap" if extra == "vwap" else "NULL::numeric"', 'vwap = "NULL::numeric"'),
    ("_readers.py", 'labels = "units, frequency" if table in _LABELLED', 'labels = "units, frequency" if False'),
    ("_readers.py", "AND date >= ${n + 1} AND date <= ${n + 2}", "AND ${n + 1}::date IS NOT NULL AND date <= ${n + 2}"),
    ("_readers.py", "date <= ${n + 2} ORDER BY date LIMIT", "date <= ${n + 2} ORDER BY date DESC LIMIT"),
    ("_readers.py", '"indicators.census_asm": "census",', '"indicators.census_asm": "census_asm",'),
    ("_readers.py", "ORDER BY date DESC LIMIT 1", "ORDER BY date LIMIT 1"),
    ("_readers.py", 'frequency = "frequency" if table in _LABELLED else "NULL::text"', 'frequency = "NULL::text"'),
    ("series.py", "        _readers._filter(self.table, self.key, self.dataset)\n", "        pass\n"),
    ("series.py", "if self.frequency is None and self.table not in _readers._LABELLED:", "if False:"),
    ("series.py", "if self.frequency is not None and self.frequency not in MAX_AGE_DAYS:", "if False:"),
    ("series.py", "frequency = series.frequency or label", "frequency = label or series.frequency"),
    ("series.py", "stale = age is None or max_age is None or age > max_age", "stale = age is None or max_age is None or age >= max_age"),
    ("series.py", "stale = age is None or max_age is None or", "stale = age is None or"),
    ("series.py", "    if series.table in _readers._PRICE_TABLES:\n", "    if False:\n"),
    ("series.py", "((today or today_et()) - latest)", "((today or date_today()) - latest)"),
    # Caught only by test_prod.py's index test.
    ("catalog.py", '"forex.prices": ("symbol", None)', '"forex.prices": ("date", None)'),
]


def tests_pass() -> bool:
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True)
    return run.returncode == 0


def main() -> int:
    if not tests_pass():
        print("tests fail before any mutation")
        return 1
    survived = 0
    for name, old, new in MUTATIONS:
        path = SRC / name
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / name
            shutil.copy2(path, snapshot)
            try:
                source = path.read_text()
                if source.count(old) != 1:
                    raise SystemExit(f"mutation target not unique or missing: {old!r}")
                path.write_text(source.replace(old, new))
                caught = not tests_pass()
            finally:
                shutil.copy2(snapshot, path)
            if not filecmp.cmp(snapshot, path, shallow=False):
                raise SystemExit(f"{name} was not restored byte for byte")
        print(f"{'caught  ' if caught else 'SURVIVED'} {name}: {old.strip()}")
        survived += not caught
    restored = tests_pass()
    print(f"restored run: {'green' if restored else 'FAILED'}")
    return 1 if survived or not restored else 0


if __name__ == "__main__":
    raise SystemExit(main())

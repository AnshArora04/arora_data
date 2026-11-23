"""The FMP notebooks are saved with synthetic outputs only: FMP's license doesn't allow publishing its data."""
import asyncio
import json
import re
import warnings
from datetime import date
from pathlib import Path

import pytest

from arora_data import _db, _readers, crypto, forex, fred, index, sync, treasury

NOTEBOOKS = Path(__file__).resolve().parents[1] / "notebooks"
# The FMP functions are the public ones whose docstring names FMP as the source; an FMP notebook calls one of them
# (not as a method, so values.index(x) doesn't count).
FMP_FUNCTIONS = sorted(n for n in dir(sync) if not n.startswith("_") and callable(getattr(sync, n))
                       and "Source: FMP" in (getattr(sync, n).__doc__ or ""))
_CALL = re.compile(r"(?<![\w.])(" + "|".join(FMP_FUNCTIONS) + r")\(")


def code(path):
    return [c for c in json.loads(path.read_text())["cells"] if c["cell_type"] == "code"]


FMP = sorted(p.name for p in NOTEBOOKS.glob("*.ipynb") if _CALL.search("\n".join("".join(c["source"]) for c in code(p))))


def test_every_fmp_function_has_its_notebook_checked():
    assert FMP_FUNCTIONS, "no function's docstring names FMP: the check below would test nothing"
    assert {f"function_{f}.ipynb" for f in FMP_FUNCTIONS} <= set(FMP)


def stdout(cell):
    return "".join("".join(o.get("text", "")) for o in cell.get("outputs", []) if o.get("name") == "stdout")


@pytest.mark.parametrize("name", FMP)
def test_fmp_notebook_is_saved_from_a_synthetic_run(name):
    cells = code(NOTEBOOKS / name)
    setup = [c for c in cells if "_synthetic.use()" in "".join(c["source"])]
    assert len(setup) == 1, "the setup cell that switches to synthetic data is missing"
    assert stdout(setup[0]) == "Data: synthetic\n", "saved after a real-data run: re-run without ARORA_REAL_DATA=1"
    assert cells[0] is setup[0], "the setup cell must run before any other code"
    # One fresh run from the top: a cell run on its own, without the setup cell, would read prod.
    assert [c["execution_count"] for c in cells] == list(range(1, len(cells) + 1)), "not one run from the top: re-run all"


def test_synthetic_mode_blocks_any_read_it_does_not_replace(monkeypatch):
    # use() patches these globally: setting each to itself first makes monkeypatch restore them afterwards.
    for obj, attr in [(_db, "_pool"), (forex, "_stored"), (crypto, "_symbols"), (index, "_symbols"),
                      (_readers, "_read_prices"), (treasury, "_rows"), (sync, "list_series"), (warnings, "formatwarning")]:
        monkeypatch.setattr(obj, attr, getattr(obj, attr))
    monkeypatch.delenv("ARORA_REAL_DATA", raising=False)
    monkeypatch.syspath_prepend(str(NOTEBOOKS))
    import _synthetic

    _synthetic.use()
    assert asyncio.run(forex.forex("USD", "CAD", date(2026, 1, 5), date(2026, 1, 5)))[0].route == "stored"
    with pytest.raises(RuntimeError, match="synthetic data has no database"):
        asyncio.run(fred.fred("DFF", date(2026, 1, 1)))  # FRED isn't replaced, so it must not reach prod

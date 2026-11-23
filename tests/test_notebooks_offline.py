"""The FMP notebooks are saved with synthetic outputs only: FMP's license doesn't allow publishing its data."""
import json
from pathlib import Path

import pytest

NOTEBOOKS = Path(__file__).resolve().parents[1] / "notebooks"
# The notebooks whose functions read FMP's tables. A new FMP function's notebook goes here too.
FMP = ["forex", "commodities", "crypto", "index", "treasury", "treasury_curve"]


def stdout(cell):
    return "".join("".join(o.get("text", "")) for o in cell.get("outputs", []) if o.get("name") == "stdout")


@pytest.mark.parametrize("name", FMP)
def test_fmp_notebook_is_saved_from_a_synthetic_run(name):
    cells = json.loads((NOTEBOOKS / f"function_{name}.ipynb").read_text())["cells"]
    setup = [c for c in cells if c["cell_type"] == "code" and "_synthetic.use()" in "".join(c["source"])]
    assert len(setup) == 1, "the setup cell that switches to synthetic data is missing"
    assert stdout(setup[0]) == "Data: synthetic\n", "saved after a real-data run: re-run without ARORA_REAL_DATA=1"
    code = [c for c in cells if c["cell_type"] == "code"]
    assert code[0] is setup[0], "the setup cell must run before any other code"

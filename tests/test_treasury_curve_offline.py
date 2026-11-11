"""treasury_curve() methods, nodes, gaps and input checks, with the database faked."""
import asyncio
import re
import warnings
from datetime import date
from decimal import Decimal, localcontext

import pytest

from arora_data import treasury as tr, treasury_curve as tc

D1, D2, D3 = date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)
COLUMNS = list(tr.TENORS.values())
METHODS = list(tc.METHODS)
REAL = {"month1": "3.90", "month2": "3.92", "month3": "3.95", "month6": "3.85", "year1": "3.70", "year2": "3.55",
        "year3": "3.55", "year5": "3.65", "year7": "3.85", "year10": "4.10", "year20": "4.65", "year30": "4.70"}


def row(d, **values):
    return {"date": d, **{c: None for c in COLUMNS}, **{c: Decimal(v) for c, v in values.items()}}


ROWS = [row(D1, **REAL), row(D2), row(D3, **{c: v for c, v in REAL.items() if c not in ("month1", "month2", "year30")})]


@pytest.fixture
def db(monkeypatch):
    reads, rows = [], list(ROWS)

    async def fake(start, end):
        reads.append((start, end))
        return [r for r in rows if start <= r["date"] <= end]

    monkeypatch.setattr(tr, "_rows", fake)
    return rows, reads


def run(maturity, start=D1, end=D3, method="flat_forward"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return asyncio.run(tc.treasury_curve(maturity, start, end, method))


def on(maturity, d=D1, method="flat_forward"):
    return next(p for p in run(maturity, method=method) if p.date == d)


@pytest.mark.parametrize("method", METHODS)
def test_a_flat_curve_is_flat_at_every_tenor_and_half_year(db, method):
    db[0][:] = [row(D1, **{c: "5" for c in COLUMNS})]
    for m in ["1M", "4M", "6M", "1Y", "1.5Y", "2Y", "4Y", "25Y", "30Y"]:
        p = on(m, method=method)
        assert abs(p.par_yield - 5) < Decimal("1e-9") and abs(p.zero_rate - 5) < Decimal("1e-9"), m
    for m in ["13M", "2.25Y"]:  # off the coupon dates, clean par drifts by under 1 bp
        p = on(m, method=method)
        assert abs(p.par_yield - 5) < Decimal("0.01") and abs(p.zero_rate - 5) < Decimal("0.01"), m


@pytest.mark.parametrize("method", METHODS)
def test_every_stored_tenor_comes_back_as_its_par_yield(db, method):
    for tenor, column in tr.TENORS.items():
        p = on(tenor, method=method)
        assert abs(p.par_yield - Decimal(REAL[column])) < Decimal("1e-8"), tenor
        assert (p.lower, p.upper) == (tenor, tenor)
        if tc._TENOR_YEARS[tenor] <= 1:  # bills are zero rates
            assert p.zero_rate == p.par_yield, tenor


def test_flat_forward_matches_a_hand_bootstrap(db):
    # 6M and 1Y at 4% are semiannual zeros; 2Y at 5% pays 2.5% every six months, with ln DF(1.5Y) halfway between
    # those of 1Y and 2Y, so u = sqrt(DF(2Y)) solves 1.025u^2 + 0.025 sqrt(DF(1Y)) u + 0.025 (DF(6M) + DF(1Y)) - 1 = 0.
    db[0][:] = [row(D1, month6="4", year1="4", year2="5")]
    with localcontext() as ctx:
        ctx.prec = 30
        df_half, df_1 = 1 / Decimal("1.02"), 1 / Decimal("1.02") ** 2
        a, b, c = Decimal("1.025"), Decimal("0.025") * df_1.sqrt(), Decimal("0.025") * (df_half + df_1) - 1
        u = (-b + (b * b - 4 * a * c).sqrt()) / (2 * a)
        zero_2 = 200 * (1 / u ** 2) ** (1 / Decimal(4)) - 200
        zero_15 = 200 * (1 / (df_1.sqrt() * u)) ** (1 / Decimal(3)) - 200
    assert abs(on("2Y").zero_rate - zero_2) < Decimal("1e-9")
    assert abs(on("18M").zero_rate - zero_15) < Decimal("1e-9")


@pytest.mark.parametrize("method", METHODS)
def test_discount_factors_fall_and_zero_rates_lie_near_the_curve(db, method):
    dfs = []
    for months in range(1, 361):
        p = on(f"{months}M", method=method)
        dfs.append((1 + p.zero_rate / 200) ** (-Decimal(months) / 6))
        assert Decimal("3.4") < p.zero_rate < Decimal("5.0"), months
    assert all(a > b for a, b in zip(dfs, dfs[1:]))


@pytest.mark.parametrize("method", ["flat_forward", "linear_par"])
def test_a_longer_tenor_never_moves_a_shorter_maturity(db, method):
    before = on("4Y", method=method), on("25Y", method=method)
    db[0][0] = {**db[0][0], "year20": Decimal("6.00")}
    assert on("4Y", method=method) == before[0]
    assert on("25Y", method=method).par_yield > before[1].par_yield


@pytest.mark.parametrize("method", METHODS)
def test_a_missing_tenor_is_skipped_never_filled_and_never_extrapolated_past(db, method):
    assert [p.date for p in run("25Y", method=method)] == [D1]
    assert [p.date for p in run("20Y", method=method)] == [D1, D3]
    assert [(p.lower, p.upper) for p in run("1M", method=method)] == [("1M", "1M"), (None, "3M")]
    assert [(p.lower, p.upper) for p in run("4Y", method=method)] == [("3Y", "5Y"), ("3Y", "5Y")]


@pytest.mark.parametrize("method", METHODS)
def test_below_the_first_tenor_the_rate_is_flat(db, method):
    m1, m2, m3 = on("1M", D3, method), on("2M", D3, method), on("3M", D3, method)
    assert m1.zero_rate == m2.zero_rate == m3.zero_rate == Decimal("3.95")
    assert m1.par_yield == m2.par_yield == Decimal("3.95")


def test_cubic_par_is_a_natural_spline_on_par_yields(db):
    # Through (1, 4), (2, 5), (3, 4) the inner second derivative m solves 4m = 6 * (-1 - 1), so m = -3, and halfway
    # along each segment the spline is the straight line plus m * (1/2)^3 / 6 - m * (1/2) / 6 = 4.5 + 0.1875.
    db[0][:] = [row(D1, year1="4", year2="5", year3="4")]
    assert on("18M", method="cubic_par").par_yield == on("30M", method="cubic_par").par_yield == Decimal("4.6875")


def test_cubic_par_is_smooth_at_every_inner_tenor_and_every_tenor_moves_it(db):
    eps = Decimal("0.000001")
    for tenor in list(tr.TENORS)[1:-1]:
        t = tc._TENOR_YEARS[tenor]
        left, mid, right = (on(f"{t + d}Y", method="cubic_par").par_yield for d in (-eps, 0, eps))
        assert abs((right - mid) - (mid - left)) / eps < Decimal("0.001"), tenor
    before = on("4Y", method="cubic_par")
    db[0][0] = {**db[0][0], "year20": Decimal("6.00")}
    assert on("4Y", method="cubic_par").par_yield != before.par_yield


def test_the_methods_differ_between_tenors_only(db):
    assert on("10Y").par_yield == on("10Y", method="linear_par").par_yield
    assert on("4Y", method="linear_par").par_yield == Decimal("3.6")
    assert abs(on("4Y").par_yield - on("4Y", method="linear_par").par_yield) > Decimal("0.01")


def test_maturity_in_any_case_or_spacing_is_named_as_written(db):
    assert run("4y") == run(" 4 Y ") == run("4Y")
    assert {p.maturity for p in run("2.5y")} == {"2.5Y"}
    assert run("18M") == [p.model_copy(update={"maturity": "18M"}) for p in run("1.5Y")]


@pytest.mark.parametrize("method", ["linear_par", "cubic_par"])
def test_a_method_with_a_warning_warns_on_every_call_and_flat_forward_does_not(db, method):
    with pytest.warns(tc.MethodWarning, match=re.escape(tc.METHODS[method][1])):
        asyncio.run(tc.treasury_curve("4Y", D1, D3, method))
    with warnings.catch_warnings(record=True) as caught:
        for _ in range(2):
            asyncio.run(tc.treasury_curve("4Y", D1, D3, method))
    assert [w.category for w in caught] == [tc.MethodWarning] * 2
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        asyncio.run(tc.treasury_curve("4Y", D1, D3))


@pytest.mark.parametrize("maturity", ["0M", "0.5M", "31Y", "30.5Y", "2W", "4", "Y", "", "4YY", "-1Y", 4, None])
def test_bad_maturity_rejected_before_any_read(db, maturity):
    with pytest.raises(ValueError, match="is not a maturity|is outside 1M to 30Y"):
        run(maturity)
    assert db[1] == []


def test_bad_method_or_start_after_end_rejected_before_any_read(db):
    with pytest.raises(ValueError, match="'cubic' is not a method: use one of flat_forward, linear_par, cubic_par$"):
        run("4Y", method="cubic")
    with pytest.raises(ValueError, match="start must be on or before end"):
        run("4Y", start=D3, end=D1)
    assert db[1] == []

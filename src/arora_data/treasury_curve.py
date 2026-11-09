"""US Treasury par yields and zero rates at any maturity from 1M to 30Y. Source: FMP, via macro.treasury_rates.

Each day's curve is built from that day's stored tenors only (see treasury()): a missing tenor is never filled, and a
maturity past the day's longest tenor gives no row for that day. Yields of 1Y and shorter are taken as semiannual zero
rates; longer tenors as par yields of bonds paying semiannual coupons. That bill convention is this module's
assumption, not Treasury's published one. Every rate served is in percent, compounded semiannually.

Methods:
- flat_forward (default): bootstrap discount factors tenor by tenor, linear in log discount factor between tenors, so
  the forward rate is flat between them and from today to the first tenor.
- linear_par: par yields linear in maturity between tenors (flat below the first), then zero rates bootstrapped from
  them every six months back from the maturity. Warns: its forward rates kink at each tenor.
"""
from __future__ import annotations

import datetime as dt
import re
import warnings
from bisect import bisect_left
from decimal import Context, Decimal, localcontext

from arora_data import _readers, treasury as _tr
from arora_data.catalog import _Row
from arora_data.dates import today_et
from arora_data.forex import SIG_DIGITS

DEFAULT = "flat_forward"
WARNINGS: dict[str, str | None] = {
    "flat_forward": None,
    "linear_par": "linear_par is not arbitrage-free: its forward rates kink at each tenor. flat_forward is the default.",
}
_PREC = SIG_DIGITS + 8  # working digits; results are rounded to SIG_DIGITS
_MATURITY = re.compile(r"(\d+(?:\.\d+)?)([MY])")
_HALF, _ONE, _TWO = Decimal("0.5"), Decimal(1), Decimal(2)


class CurvePoint(_Row):
    """One maturity's rates on one day, in percent. lower and upper are the stored tenors either side of the maturity
    (both the tenor itself when it is one; lower is None below the day's first tenor)."""

    maturity: str
    date: dt.date
    par_yield: Decimal
    zero_rate: Decimal
    lower: str | None
    upper: str


def _years(maturity: str) -> Decimal:
    match = _MATURITY.fullmatch(maturity)
    if not match:
        raise ValueError(f"{maturity!r} is not a maturity: use months or years, such as 18M, 4Y or 2.5Y")
    n = Decimal(match[1])
    with localcontext() as ctx:
        ctx.prec = _PREC
        return n / 12 if match[2] == "M" else n


_TENOR_YEARS = {t: _years(t) for t in _tr.TENORS}


def _bill_ln_df(t: Decimal, y: Decimal) -> Decimal:
    """Log discount factor of a semiannual zero rate y (a fraction) at t years."""
    return -2 * t * (_ONE + y / 2).ln()


def _zero(t: Decimal, ln_df: Decimal) -> Decimal:
    return 2 * ((-ln_df / (2 * t)).exp() - _ONE)


def _coupons(t: Decimal) -> tuple[list[Decimal], Decimal]:
    """Coupon times every six months back from t, oldest first, and the share of the first period already accrued."""
    times = [t]
    while times[-1] > _HALF:
        times.append(times[-1] - _HALF)
    return times[::-1], (_HALF - times[-1]) / _HALF


def _ln_df_at(times: list[Decimal], ln_dfs: list[Decimal], t: Decimal) -> Decimal:
    """Linear in log discount factor between known nodes; t must be within them."""
    i = bisect_left(times, t)
    if times[i] == t:
        return ln_dfs[i]
    w = (t - times[i - 1]) / (times[i] - times[i - 1])
    return ln_dfs[i - 1] + w * (ln_dfs[i] - ln_dfs[i - 1])


def _flat_forward(nodes: list[tuple[Decimal, Decimal]], t: Decimal) -> tuple[Decimal, Decimal]:
    """Par yield and zero rate (fractions) at t, bootstrapping the nodes (years, yield) up to the first at or past t."""
    times, ln_dfs = [Decimal(0)], [Decimal(0)]
    for node, y in nodes:
        if node <= 1:
            ln_df = _bill_ln_df(node, y)
        else:
            ln_df = _solve_par(times, ln_dfs, node, y)
        times.append(node)
        ln_dfs.append(ln_df)
        if node >= t:
            break
    ln_df = _ln_df_at(times, ln_dfs, t)
    if t <= 1:
        return _zero(t, ln_df), _zero(t, ln_df)
    pay, accrued = _coupons(t)
    annuity = sum(_ln_df_at(times, ln_dfs, s).exp() for s in pay)
    return 2 * (_ONE - ln_df.exp()) / (annuity - accrued), _zero(t, ln_df)


def _solve_par(times: list[Decimal], ln_dfs: list[Decimal], node: Decimal, y: Decimal) -> Decimal:
    """The log discount factor at node that prices a bond with coupon y (paid every six months) at par."""
    pay = [node - _HALF * k for k in range(int(node / _HALF))][::-1]  # nodes past 1Y are whole years
    last_t, last_ln = times[-1], ln_dfs[-1]
    fixed = sum(_ln_df_at(times, ln_dfs, s).exp() for s in pay if s <= last_t)
    weights = [(s - last_t) / (node - last_t) for s in pay if s > last_t]
    c = y / 2
    x = _bill_ln_df(node, y)  # start from a flat curve at y
    for _ in range(50):
        dfs = [(last_ln + w * (x - last_ln)).exp() for w in weights]
        price = c * (fixed + sum(dfs)) + dfs[-1] - _ONE
        slope = c * sum(w * df for w, df in zip(weights, dfs)) + dfs[-1]
        step = price / slope
        x -= step
        if abs(step) < Decimal(10) ** (4 - _PREC):
            return x
    raise ArithmeticError(f"no par solution at {node}Y for yield {y}")


def _linear_par(nodes: list[tuple[Decimal, Decimal]], t: Decimal) -> tuple[Decimal, Decimal]:
    """Par yield and zero rate (fractions) at t, with par yields linear between the nodes (years, yield)."""
    times = [n for n, _ in nodes]

    def par(s: Decimal) -> Decimal:
        i = bisect_left(times, s)
        if i == 0 or times[i] == s:
            return nodes[i][1]
        (t0, y0), (t1, y1) = nodes[i - 1], nodes[i]
        return y0 + (s - t0) / (t1 - t0) * (y1 - y0)

    pay, accrued = _coupons(t)
    dfs: list[Decimal] = []
    for s in pay:
        y = par(s)
        if s <= 1:
            dfs.append(_bill_ln_df(s, y).exp())
        else:
            dfs.append((_ONE - y / 2 * (sum(dfs) - accrued)) / (_ONE + y / 2))
    return par(t), _zero(t, dfs[-1].ln())


_METHODS = {"flat_forward": _flat_forward, "linear_par": _linear_par}


def _point(maturity: str, t: Decimal, day: dt.date, stored: dict[str, Decimal], method: str) -> CurvePoint | None:
    tenors = [k for k in _tr.TENORS if k in stored]
    if not tenors or _TENOR_YEARS[tenors[-1]] < t:
        return None
    lower = next((k for k in reversed(tenors) if _TENOR_YEARS[k] <= t), None)
    upper = next(k for k in tenors if _TENOR_YEARS[k] >= t)
    nodes = [(_TENOR_YEARS[k], stored[k] / 100) for k in tenors]
    with localcontext() as ctx:
        ctx.prec = _PREC
        par, zero = _METHODS[method](nodes, t)
    out = Context(prec=SIG_DIGITS).plus
    return CurvePoint(maturity=maturity, date=day, par_yield=out(par * 100), zero_rate=out(zero * 100),
                      lower=lower, upper=upper)


async def treasury_curve(maturity: str, start: dt.date, end: dt.date | None = None,
                         method: str = DEFAULT) -> list[CurvePoint]:
    """Par yield and zero rate at one maturity, per day from start to end (default: today in US/Eastern). Source: FMP.

    maturity is months or years from 1M to 30Y, such as "18M", "4Y" or "2.5Y" (any case). method is one of WARNINGS;
    a method with a warning there emits it as a UserWarning. A bad maturity or method raises ValueError.
    """
    end = end or today_et()
    _readers._check_range(start, end)
    key = "".join(maturity.split()).upper()
    t = _years(key)
    if not _TENOR_YEARS["1M"] <= t <= _TENOR_YEARS["30Y"]:
        raise ValueError(f"{maturity!r} is outside 1M to 30Y")
    if method not in _METHODS:
        raise ValueError(f"{method!r} is not a method: use one of {', '.join(_METHODS)}")
    if WARNINGS[method]:
        warnings.warn(WARNINGS[method], UserWarning, stacklevel=2)
    rows = await _tr._rows(start, end)
    points = (_point(key, t, r["date"], {k: r[c] for k, c in _tr.TENORS.items() if r[c] is not None}, method)
              for r in rows)
    return [p for p in points if p]

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
- cubic_par: par yields on a natural cubic spline through the tenors (flat below the first), then zero rates as in
  linear_par. Warns: it can overshoot between tenors, and every tenor moves the whole curve.
- monotone_convex: Hagan and West's monotone convex forwards (Applied Mathematical Finance, 2006; Treasury's method
  since 2021-12-06), in its basic form without the positivity amendment. Forwards are continuous and, where the
  tenors' average forwards rise or fall, keep doing so between them, from today to the last tenor. Each tenor moves
  its neighbours slightly, so every day's tenors are solved together. It works in float for speed, the one exception
  to Decimal here: about 15 digits inside, served rounded to 12, so the last digit can be one off an exact calculation.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import warnings
from bisect import bisect_left
from collections.abc import Callable
from decimal import Context, Decimal, localcontext

from arora_data import _readers, treasury as _tr
from arora_data.catalog import _Row
from arora_data.dates import today_et
from arora_data.forex import SIG_DIGITS

DEFAULT = "flat_forward"
_PREC = SIG_DIGITS + 8  # working digits; results are rounded to SIG_DIGITS
_MATURITY = re.compile(r"(\d+(?:\.\d+)?)([MY])")
_HALF, _ONE = Decimal("0.5"), Decimal(1)


class MethodWarning(UserWarning):
    """A method the research advises against; shown on every call, not once per session."""


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
    match = isinstance(maturity, str) and _MATURITY.fullmatch("".join(maturity.split()).upper())
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
    half: list[Decimal] = []  # discount factors at 0.5, 1, 1.5, ... years, as far as the curve is built
    for node, y in nodes:
        if node <= 1:
            ln_df = _bill_ln_df(node, y)
        else:
            while _HALF * (len(half) + 1) <= times[-1]:
                half.append(_ln_df_at(times, ln_dfs, _HALF * (len(half) + 1)).exp())
            ln_df, segment = _solve_par(times[-1], ln_dfs[-1], sum(half), node, y)
            half += segment
        times.append(node)
        ln_dfs.append(ln_df)
        if node >= t:
            break
    ln_df = _ln_df_at(times, ln_dfs, t)
    if t <= 1:
        return _zero(t, ln_df), _zero(t, ln_df)
    if t % _HALF == 0:  # on the six-month grid, every coupon's discount factor is already known
        n = int(t / _HALF)
        return 2 * (_ONE - half[n - 1]) / sum(half[:n]), _zero(t, ln_df)
    pay, accrued = _coupons(t)
    annuity = sum(_ln_df_at(times, ln_dfs, s).exp() for s in pay)
    return 2 * (_ONE - ln_df.exp()) / (annuity - accrued), _zero(t, ln_df)


def _segment(x: Decimal, last_ln: Decimal, span: Decimal, m: int) -> list[Decimal]:
    """Discount factors at the m six-monthly coupons back from a node with log discount factor x, oldest first.
    Linear in log discount factor, so each is the next one times the same ratio: two exps, not m."""
    dfs, ratio = [x.exp()], ((last_ln - x) * _HALF / span).exp()
    for _ in range(m - 1):
        dfs.append(dfs[-1] * ratio)
    return dfs[::-1]


def _solve_par(last_t: Decimal, last_ln: Decimal, fixed: Decimal, node: Decimal,
               y: Decimal) -> tuple[Decimal, list[Decimal]]:
    """The log discount factor at node that prices a bond with coupon y (paid every six months) at par, and the
    discount factors at its coupons after last_t. fixed is the sum of those at or before last_t."""
    span = node - last_t
    m = 0
    while node - _HALF * m > last_t:  # nodes past 1Y are whole years, so coupons fall every six months back
        m += 1
    weights = [1 - _HALF * j / span for j in range(m)][::-1]
    c = y / 2
    x = _bill_ln_df(node, y)  # start from a flat curve at y
    for _ in range(50):
        dfs = _segment(x, last_ln, span, m)
        price = c * (fixed + sum(dfs)) + dfs[-1] - _ONE
        slope = c * sum(w * df for w, df in zip(weights, dfs)) + dfs[-1]
        step = price / slope
        x -= step
        if abs(step) < Decimal(10) ** (4 - _PREC):
            return x, _segment(x, last_ln, span, m)
    raise ArithmeticError(f"no par solution at {node}Y for yield {y}")


def _g_integral(g0: float, g1: float, x: float) -> float:
    """The integral from 0 to x of Hagan and West's g, the forward less its interval average, which is g0 at the
    interval's start and g1 at its end (x runs 0 to 1). Its integral over the whole interval is 0."""
    if g0 == 0 or g1 == 0:  # the limit of every sector: g is 0 but at one end
        return 0.0
    if (g0 < 0 and -g0 / 2 <= g1 <= -2 * g0) or (g0 > 0 and -g0 / 2 >= g1 >= -2 * g0):  # (i)
        return g0 * (x - 2 * x ** 2 + x ** 3) + g1 * (x ** 3 - x ** 2)
    if (g0 < 0 and g1 > -2 * g0) or (g0 > 0 and g1 < -2 * g0):  # (ii) flat, then rising to g1
        eta = (g1 + 2 * g0) / (g1 - g0)
        return g0 * x + ((g1 - g0) * (x - eta) ** 3 / (3 * (1 - eta) ** 2) if x > eta else 0)
    if (g0 > 0 and 0 > g1 > -g0 / 2) or (g0 < 0 and 0 < g1 < -g0 / 2):  # (iii) falling from g0, then flat
        eta = 3 * g1 / (g1 - g0)
        return g1 * x + (g0 - g1) * (eta ** 3 - max(eta - x, 0) ** 3) / (3 * eta ** 2)
    eta, a = g1 / (g1 + g0), -g0 * g1 / (g0 + g1)  # (iv) g0 and g1 the same sign: down to a, then back up
    if x < eta:
        return a * x + (g0 - a) * (eta ** 3 - (eta - x) ** 3) / (3 * eta ** 2)
    return a * x + (g0 - a) * eta / 3 + (g1 - a) * (x - eta) ** 3 / (3 * (1 - eta) ** 2)


def _monotone_convex_curve(taus: list[float], ints: list[float]) -> Callable[[float], float]:
    """The integrated forward (minus the log discount factor) at any time up to the last node, through the nodes
    taus (starting at 0) with integrals ints (starting at 0)."""
    n = len(taus) - 1
    fd = [0.0] + [(ints[i] - ints[i - 1]) / (taus[i] - taus[i - 1]) for i in range(1, n + 1)]
    f = [fd[1]] * (n + 1)
    for i in range(1, n):
        f[i] = ((taus[i] - taus[i - 1]) * fd[i + 1] + (taus[i + 1] - taus[i]) * fd[i]) / (taus[i + 1] - taus[i - 1])
    if n > 1:
        f[0], f[n] = fd[1] - (f[1] - fd[1]) / 2, fd[n] - (f[n - 1] - fd[n]) / 2

    def integral(s: float) -> float:
        i = bisect_left(taus, s)
        if taus[i] == s:
            return ints[i]
        width = taus[i] - taus[i - 1]
        x = (s - taus[i - 1]) / width
        return ints[i - 1] + width * (fd[i] * x + _g_integral(f[i - 1] - fd[i], f[i] - fd[i], x))

    return integral


def _monotone_convex(nodes: list[tuple[Decimal, Decimal]], t: Decimal) -> tuple[Decimal, Decimal]:
    """Par yield and zero rate (fractions) at t on a monotone convex curve that prices every node's bond at par.
    In float: 9x faster than Decimal over the full history, and at most 1e-11 points off it (measured 2026-11-11)."""
    taus = [0.0] + [float(n) for n, _ in nodes]
    ys = [float(y) for _, y in nodes]
    ints = [0.0] + [2 * n * math.log1p(y / 2) for n, y in zip(taus[1:], ys)]  # exact for bills; bonds start flat
    bonds = [k for k in range(1, len(taus)) if taus[k] > 1]
    grid = [0.5 * j for j in range(1, round(taus[-1] / 0.5) + 1)] if bonds else []
    for _ in range(100):
        integral = _monotone_convex_curve(taus, ints)
        if not bonds:
            break
        dfs = [math.exp(-integral(s)) for s in grid]
        steps = []
        for k in bonds:
            c, last = ys[k - 1] / 2, round(taus[k] / 0.5)  # nodes past 1Y are whole years
            price = c * sum(dfs[:last]) + dfs[last - 1] - 1
            # How the price moves with ints[k] if the interval were flat forward: a Newton step that converges here.
            slope = -dfs[last - 1] - c * sum((s - taus[k - 1]) / (taus[k] - taus[k - 1]) * df
                                             for s, df in zip(grid[:last], dfs[:last]) if s > taus[k - 1])
            steps.append(price / slope)
        for k, step in zip(bonds, steps):
            ints[k] -= step
        if max(abs(step) for step in steps) < 1e-13:  # float's rounding keeps steps above 1e-15 on some days
            integral = _monotone_convex_curve(taus, ints)
            break
    else:
        raise ArithmeticError(f"monotone convex bootstrap did not settle for nodes {nodes}")
    x = float(t)
    zero = 2 * math.expm1(integral(x) / (2 * x))
    if x <= 1:
        return Decimal(repr(zero)), Decimal(repr(zero))
    pay = [x]
    while pay[-1] > 0.5:
        pay.append(pay[-1] - 0.5)
    accrued = (0.5 - pay[-1]) / 0.5
    annuity = sum(math.exp(-integral(s)) for s in pay)
    return Decimal(repr(2 * (1 - math.exp(-integral(x))) / (annuity - accrued))), Decimal(repr(zero))


def _linear_par(nodes: list[tuple[Decimal, Decimal]], t: Decimal) -> tuple[Decimal, Decimal]:
    """Par yield and zero rate (fractions) at t, with par yields linear between the nodes (years, yield)."""
    times = [n for n, _ in nodes]

    def par(s: Decimal) -> Decimal:
        i = bisect_left(times, s)
        if i == 0 or times[i] == s:
            return nodes[i][1]
        (t0, y0), (t1, y1) = nodes[i - 1], nodes[i]
        return y0 + (s - t0) / (t1 - t0) * (y1 - y0)

    return _from_par(par, t)


def _cubic_par(nodes: list[tuple[Decimal, Decimal]], t: Decimal) -> tuple[Decimal, Decimal]:
    """Par yield and zero rate (fractions) at t, with par yields on a natural cubic spline through the nodes."""
    xs, ys = [n for n, _ in nodes], [y for _, y in nodes]
    k, h = len(xs), [b - a for a, b in zip(xs, xs[1:])]
    # Second derivatives, zero at both ends; the inner ones solve a tridiagonal system (Thomas algorithm).
    m, diag, rhs = [Decimal(0)] * k, [], []
    for i in range(1, k - 1):
        b, d = 2 * (h[i - 1] + h[i]), 6 * ((ys[i + 1] - ys[i]) / h[i] - (ys[i] - ys[i - 1]) / h[i - 1])
        if diag:
            w = h[i - 1] / diag[-1]
            b, d = b - w * h[i - 1], d - w * rhs[-1]
        diag.append(b)
        rhs.append(d)
    for j in reversed(range(len(diag))):
        m[j + 1] = (rhs[j] - h[j + 1] * m[j + 2]) / diag[j]

    def par(s: Decimal) -> Decimal:
        if s <= xs[0]:
            return ys[0]
        i = bisect_left(xs, s) - 1
        u, v = xs[i + 1] - s, s - xs[i]
        return ((m[i] * u ** 3 + m[i + 1] * v ** 3) / (6 * h[i]) + (ys[i] / h[i] - m[i] * h[i] / 6) * u
                + (ys[i + 1] / h[i] - m[i + 1] * h[i] / 6) * v)

    return _from_par(par, t)


def _from_par(par: Callable[[Decimal], Decimal], t: Decimal) -> tuple[Decimal, Decimal]:
    """Par yield and zero rate (fractions) at t, bootstrapping discount factors every six months back from t."""
    pay, accrued = _coupons(t)
    dfs: list[Decimal] = []
    for s in pay:
        y = par(s)
        if s <= 1:
            dfs.append(_bill_ln_df(s, y).exp())
        else:
            dfs.append((_ONE - y / 2 * (sum(dfs) - accrued)) / (_ONE + y / 2))
    return par(t), _zero(t, dfs[-1].ln())


# Method: its function, and the warning it emits on every call (None for none).
METHODS: dict[str, tuple[Callable[[list[tuple[Decimal, Decimal]], Decimal], tuple[Decimal, Decimal]], str | None]] = {
    "flat_forward": (_flat_forward, None),
    "monotone_convex": (_monotone_convex, None),
    "linear_par": (_linear_par,
                   "linear_par is not arbitrage-free: its forward rates kink at each tenor. flat_forward is the default."),
    "cubic_par": (_cubic_par, "cubic_par can overshoot between tenors, and every tenor moves the whole curve. "
                              "flat_forward is the default."),
}


def _point(maturity: str, t: Decimal, day: dt.date, stored: dict[str, Decimal], method: Callable) -> CurvePoint | None:
    tenors = [k for k in _tr.TENORS if k in stored]
    if not tenors or _TENOR_YEARS[tenors[-1]] < t:
        return None
    lower = next((k for k in reversed(tenors) if _TENOR_YEARS[k] <= t), None)
    upper = next(k for k in tenors if _TENOR_YEARS[k] >= t)
    nodes = [(_TENOR_YEARS[k], stored[k] / 100) for k in tenors]
    with localcontext() as ctx:
        ctx.prec = _PREC
        par, zero = method(nodes, t)
    out = Context(prec=SIG_DIGITS).plus
    return CurvePoint(maturity=maturity, date=day, par_yield=out(par * 100), zero_rate=out(zero * 100),
                      lower=lower, upper=upper)


async def treasury_curve(maturity: str, start: dt.date, end: dt.date | None = None,
                         method: str = DEFAULT) -> list[CurvePoint]:
    """Par yield and zero rate at one maturity, per day from start to end (default: today in US/Eastern). Source: FMP.

    maturity is months or years from 1M to 30Y, such as "18M", "4Y" or "2.5Y" (any case). method is one of METHODS;
    a method with a warning there emits it as a MethodWarning on every call. A bad maturity or method raises ValueError.
    """
    end = end or today_et()
    _readers._check_range(start, end)
    t = _years(maturity)
    key = "".join(maturity.split()).upper()
    if not _TENOR_YEARS["1M"] <= t <= _TENOR_YEARS["30Y"]:
        raise ValueError(f"{maturity!r} is outside 1M to 30Y")
    if method not in METHODS:
        raise ValueError(f"{method!r} is not a method: use one of {', '.join(METHODS)}")
    fn, warning = METHODS[method]
    if warning:
        # No registry, so it is not deduplicated: warnings.warn shows a warning once per calling line.
        warnings.warn_explicit(warning, MethodWarning, __file__, 0)
    rows = await _tr._rows(start, end)
    points = (_point(key, t, r["date"], {k: r[c] for k, c in _tr.TENORS.items() if r[c] is not None}, fn)
              for r in rows)
    return [p for p in points if p]

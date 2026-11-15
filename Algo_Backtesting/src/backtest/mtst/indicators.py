"""Supertrend exactly as TradingView's ta.supertrend (Wilder / RMA ATR), plus an SMA-ATR variant (the classic
'Change ATR calculation method' = off). Every value at bar i uses bars <= i only."""
import numpy as np


def true_range(h, l, c) -> np.ndarray:
    pc = np.r_[np.nan, c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    tr[0] = h[0] - l[0]
    return tr


def atr(h, l, c, n: int, method: str = "rma") -> np.ndarray:
    """rma: Wilder ATR seeded with the SMA of the first n true ranges (TradingView ta.rma); sma: plain n-bar mean."""
    tr = true_range(h, l, c)
    out = np.full(len(tr), np.nan)
    if len(tr) < n:
        return out
    if method == "sma":
        cs = np.cumsum(np.r_[0.0, tr])
        out[n - 1:] = (cs[n:] - cs[:-n]) / n
        return out
    a = float(np.mean(tr[:n]))
    out[n - 1] = a
    k = (n - 1) / n
    for i in range(n, len(tr)):
        a = a * k + tr[i] / n
        out[i] = a
    return out


def supertrend(h, l, c, n: int, mult: float, method: str = "rma"):
    """Returns (line, dir, upper, lower). dir: +1 uptrend (line = lower band, 'buy mode'), -1 downtrend (line = upper
    band, 'sell mode'), 0 before the ATR exists. upper / lower = the final (trailing) bands.

    TradingView:  up = hl2 - m*atr; up := up > up[1] or close[1] < up[1] ? up : up[1]   (lower band only rises)
                  dn = hl2 + m*atr; dn := dn < dn[1] or close[1] > dn[1] ? dn : dn[1]   (upper band only falls)
                  first bar: down; then if line[1] == dn[1]: dir = close > dn ? up : down, else dir = close < up ? down : up
    """
    h, l, c = (np.asarray(x, float) for x in (h, l, c))
    m = len(c)
    a = atr(h, l, c, n, method)
    hl2 = (h + l) / 2
    ub0, lb0 = hl2 + mult * a, hl2 - mult * a
    line = np.full(m, np.nan)
    upper = np.full(m, np.nan)
    lower = np.full(m, np.nan)
    d = np.zeros(m, np.int8)
    s = n - 1
    if m <= s:
        return line, d, upper, lower
    ub, lb, dr = ub0[s], lb0[s], -1
    upper[s], lower[s], line[s], d[s] = ub, lb, ub, -1
    for i in range(s + 1, m):
        nub = ub0[i] if (ub0[i] < ub or c[i - 1] > ub) else ub
        nlb = lb0[i] if (lb0[i] > lb or c[i - 1] < lb) else lb
        if dr == -1:                       # previous line was the upper band
            dr = 1 if c[i] > nub else -1
        else:
            dr = -1 if c[i] < nlb else 1
        ub, lb = nub, nlb
        upper[i], lower[i] = ub, lb
        line[i] = lb if dr == 1 else ub
        d[i] = dr
    return line, d, upper, lower

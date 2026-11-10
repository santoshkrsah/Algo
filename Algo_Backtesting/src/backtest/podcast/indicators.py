"""Indicators. Every value at bar i uses bars <= i only (callers act on it at bar i+1 or later)."""
import numpy as np


def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    c = np.cumsum(np.insert(x.astype(float), 0, 0.0))
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def ema(x: np.ndarray, n: int) -> np.ndarray:
    """Exponential MA (alpha = 2/(n+1)), seeded with the SMA of the first n values; NaN before that."""
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    a = 2.0 / (n + 1)
    v = float(np.mean(x[:n]))
    out[n - 1] = v
    for i in range(n, len(x)):
        v = v + a * (x[i] - v)
        out[i] = v
    return out


def ma(kind: str, x: np.ndarray, n: int) -> np.ndarray:
    return ema(x, n) if kind == "EMA" else sma(x, n)


def true_range(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    pc = np.r_[np.nan, c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    tr[0] = h[0] - l[0]
    return tr


def atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int) -> np.ndarray:
    """Wilder ATR (points). Value at i includes bar i."""
    tr = true_range(h, l, c)
    out = np.full(len(tr), np.nan)
    if len(tr) < n:
        return out
    v = float(np.mean(tr[:n]))
    out[n - 1] = v
    for i in range(n, len(tr)):
        v = (v * (n - 1) + tr[i]) / n
        out[i] = v
    return out


def rolling_std(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1:i + 1]
        out[i] = np.std(w, ddof=1) if np.isfinite(w).all() else np.nan
    return out


def swings(h: np.ndarray, l: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Pivot highs / lows: bar p is a swing high if its high is above the highs of the n bars on each side (strictly),
    a swing low if its low is below the n lows on each side. A pivot is only KNOWN at bar p + n (after the n right-side
    bars closed). Returns (last_sh, last_sl): the most recent swing high / low level known at the close of each bar."""
    m = len(h)
    last_sh = np.full(m, np.nan)
    last_sl = np.full(m, np.nan)
    if m < 2 * n + 1:
        return last_sh, last_sl
    from numpy.lib.stride_tricks import sliding_window_view as sw
    wh = sw(h, 2 * n + 1)
    wl = sw(l, 2 * n + 1)
    ch, cl = wh[:, n], wl[:, n]
    others_h = np.delete(wh, n, axis=1).max(axis=1)
    others_l = np.delete(wl, n, axis=1).min(axis=1)
    is_sh = ch > others_h
    is_sl = cl < others_l
    piv = np.arange(n, m - n)                       # pivot bar index of each window
    known = piv + n                                 # bar at whose close it becomes known
    sh_at = np.full(m, np.nan)
    sl_at = np.full(m, np.nan)
    sh_at[known[is_sh]] = ch[is_sh]
    sl_at[known[is_sl]] = cl[is_sl]
    # forward fill
    idx = np.where(np.isfinite(sh_at), np.arange(m), 0)
    np.maximum.accumulate(idx, out=idx)
    last_sh = np.where(np.isfinite(sh_at[idx]), sh_at[idx], np.nan)
    idx = np.where(np.isfinite(sl_at), np.arange(m), 0)
    np.maximum.accumulate(idx, out=idx)
    last_sl = np.where(np.isfinite(sl_at[idx]), sl_at[idx], np.nan)
    return last_sh, last_sl


def pivots(ph: np.ndarray, pl: np.ndarray, pc: np.ndarray) -> dict:
    """Classic floor pivots from the previous session's high / low / close."""
    p = (ph + pl + pc) / 3.0
    return {"P": p, "R1": 2 * p - pl, "S1": 2 * p - ph, "R2": p + (ph - pl), "S2": p - (ph - pl)}

"""Module A signal engine: 1-hour Supertrend + moving average trend following, orders on NIFTY futures 1-minute bars.

Long entry: a 1-hour candle closes above BOTH the Supertrend line and the MA while the previous candle did not, and no
position is open -> buy at the next 1-minute futures open. The MA is ignored after entry. Exit 'close': a candle closes
below the Supertrend (direction flips) -> sell at the next 1-minute open. Exit 'touch': the stop is the Supertrend line
known at the previous candle's close; the first 1-minute candle of the signal chart touching it -> sell at the next
1-minute futures open. Short side mirrored (variant), size 50%; long and short never overlap (an exit and the opposite
entry can share the same fill time). A new entry always needs the entry condition to switch on again.
"""
import numpy as np

from backtest.podcast.indicators import ma as moving_average


def supertrend(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int, mult: float):
    """TradingView ta.supertrend: Wilder ATR(n); bands hl2 +- mult x ATR that only trail; direction flips when the close
    crosses the band. Returns (line, up) with up = True in an uptrend (line = lower band) else False (line = upper band)."""
    m = len(c)
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.r_[np.nan, c[:-1]]), np.abs(l - np.r_[np.nan, c[:-1]])))
    tr[0] = h[0] - l[0]
    atr = np.full(m, np.nan)
    if m < n:
        return np.full(m, np.nan), np.zeros(m, bool)
    a = float(np.mean(tr[:n]))
    atr[n - 1] = a
    for i in range(n, m):
        a = (a * (n - 1) + tr[i]) / n
        atr[i] = a
    hl2 = (h + l) / 2
    ub0, lb0 = hl2 + mult * atr, hl2 - mult * atr
    line = np.full(m, np.nan)
    up = np.zeros(m, bool)
    ub = lb = np.nan
    d = 1                                                   # TradingView: 1 = down, -1 = up
    st_prev = np.nan
    for i in range(n - 1, m):
        nub, nlb = ub0[i], lb0[i]
        if i > n - 1:
            nub = nub if (nub < ub or c[i - 1] > ub) else ub
            nlb = nlb if (nlb > lb or c[i - 1] < lb) else lb
            if st_prev == ub:
                d = -1 if c[i] > nub else 1
            else:
                d = 1 if c[i] < nlb else -1
        ub, lb = nub, nlb
        st_prev = lb if d == -1 else ub
        line[i] = st_prev
        up[i] = d == -1
    return line, up


def indicators(sig, atr_n: int, mult: float, ma_type: str, ma_len: int, cache: dict | None = None):
    cache = {} if cache is None else cache
    k1, k2 = (sig.name, "st", atr_n, mult), (sig.name, "ma", ma_type, ma_len)
    if k1 not in cache:
        cache[k1] = supertrend(sig.h, sig.l, sig.c, atr_n, mult)
    if k2 not in cache:
        cache[k2] = moving_average(ma_type, sig.c, ma_len)
    return cache[k1], cache[k2]


def _touch_fill(sig, k: int, stop: float, side: int, fut) -> int:
    """First 1-minute candle of signal candle k whose low (long) / high (short) reaches the stop -> futures index of the
    next 1-minute bar. -1 if candle k never touches."""
    src = sig.src
    a, z = sig.lo[k], sig.hi[k] + 1
    hit = (src.l[a:z] <= stop) if side > 0 else (src.h[a:z] >= stop)
    if not hit.any():
        return -1
    t = src.dt[a + int(np.argmax(hit))] + np.timedelta64(60, "s")
    i = int(np.searchsorted(fut.dt, t))
    return i if i < fut.n else -1


def run(sig, fut, line: np.ndarray, up: np.ndarray, ma: np.ndarray, sides: str = "long", exit_mode: str = "close",
        short_size: float = 0.5, entries: np.ndarray | None = None, entries_any: bool = False) -> list:
    """Trades on one signal chart. entries: optional forced entry candles (random control): enter long at those candles'
    fills instead of the rule's entry condition (exits unchanged); entries_any=False only takes the ones in an uptrend."""
    c = sig.c
    ok = np.isfinite(line) & np.isfinite(ma)
    cond_l = ok & (c > line) & (c > ma)
    cond_s = ok & (c < line) & (c < ma)
    trades = []
    pos, k_in, i_in = 0, -1, -1
    forced = set(entries.tolist()) if entries is not None else None
    for k in range(1, sig.n):
        if pos != 0:
            exit_i, why = -1, None
            if exit_mode == "touch" and k > k_in and np.isfinite(line[k - 1]):
                j = _touch_fill(sig, k, line[k - 1], pos, fut)
                if j >= 0:
                    exit_i, why = j, "touch"
            if exit_i < 0 and ((pos > 0 and not up[k]) or (pos < 0 and up[k])):
                exit_i, why = sig.fill[k], "close beyond Supertrend"
            if exit_i >= 0 or (exit_i < 0 and why is not None):
                if exit_i < 0:                                   # signal after the data ends: close at the last bar
                    exit_i, why = fut.n - 1, "end of data"
                trades.append(_trade(sig, fut, pos, k_in, i_in, k, exit_i, why, short_size))
                pos = 0
                if why == "touch":
                    continue                                     # no new entry from the candle that hit the stop
        if pos == 0 and sig.fill[k] >= 0:
            if forced is not None:
                if k in forced and (entries_any or up[k]):
                    pos, k_in, i_in = 1, k, sig.fill[k]
                continue
            if cond_l[k] and not cond_l[k - 1]:
                pos, k_in, i_in = 1, k, sig.fill[k]
            elif sides == "longshort" and cond_s[k] and not cond_s[k - 1]:
                pos, k_in, i_in = -1, k, sig.fill[k]
    if pos != 0:
        trades.append(_trade(sig, fut, pos, k_in, i_in, sig.n - 1, fut.n - 1, "end of data", short_size))
    return trades


def _trade(sig, fut, side: int, k_in: int, i_in: int, k_out: int, i_out: int, why: str, short_size: float) -> tuple:
    """(side, size, entry candle, exit candle, entry fill idx, exit fill idx, reason, rolls) - prices come from fut."""
    seg = fut.seg
    rolls = int(np.count_nonzero(seg[i_in + 1:i_out + 1] != seg[i_in:i_out])) if i_out > i_in else 0
    return (side, 1.0 if side > 0 else short_size, k_in, k_out, int(i_in), int(i_out), why, rolls)

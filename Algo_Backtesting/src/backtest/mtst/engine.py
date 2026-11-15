"""Signal engine + order simulation (one position at a time, NIFTY futures).

At every 1-hour candle close k (only information up to that close):
    hd[k]  = 1-hour Supertrend direction (+1 buy mode, -1 sell mode)
    dd[k]  = daily Supertrend direction of the LAST COMPLETED daily candle at that time (data.daily_index)
    aligned direction a[k] = hd[k] if hd[k] == dd[k] else 0
    E1 entry: a[k] != 0 and a[k] != a[k-1]            (alignment just became true, by a 1-hour flip OR a daily flip)
    E2 entry: hd[k] != hd[k-1] and hd[k] == dd[k]       (only 1-hour flips while the daily agrees)
    nofilter control: hd[k] != hd[k-1] -> enter hd[k] (no daily filter)
Exit (position side s):
    close        hd[k] == -s (candle closed beyond the 1-hour band = 1-hour flip); sl='daily': also close beyond the initial daily band
    touch        a 1-minute candle of k reaches the stop known before k (1-hour band at k-1, or the initial stop if tighter)
    close_daily  'close' + exit when the daily direction turns against the trade
Fills: 'next' = next 1-minute futures open after the close; 'close' = futures close of the candle's last minute;
       'defer' (last='defer') makes the part candle's signals fill at the next session's first bar.
Stop and another exit possible in the same candle -> the worse price. A gap through the stop -> that minute's open.
"""
import numpy as np
import pandas as pd

from backtest.mtst import indicators as ind
from backtest.mtst.data import PART, daily_index


class Prepared:
    """Indicators of one (chart, last-candle, ATR method, 1h / daily parameters) combination, cached by key."""

    def __init__(self, H, D, h_n, h_mult, d_n, d_mult, method):
        self.H, self.D = H, D
        self.h_line, self.hd, self.h_up, self.h_lo = ind.supertrend(H.h, H.l, H.c, h_n, h_mult, method)
        dl, ddir, _, _ = ind.supertrend(D.h, D.l, D.c, d_n, d_mult, method)
        di = daily_index(H, D)
        ok = di >= 0
        self.di = di
        self.dline = np.where(ok, dl[np.clip(di, 0, None)], np.nan)
        self.dd = np.where(ok, ddir[np.clip(di, 0, None)], 0).astype(np.int8)
        self.d_line_all, self.d_dir_all = dl, ddir


_CACHE: dict = {}


def prepare(M, Hs: dict, Ds: dict, p: dict) -> Prepared:
    key = (p["chart"], p["last"], p["atr"], p["h_n"], p["h_mult"], p["d_n"], p["d_mult"])
    if key not in _CACHE:
        if len(_CACHE) > 600:
            _CACHE.clear()
        _CACHE[key] = Prepared(Hs[(p["chart"], p["last"])], Ds[p["chart"]], p["h_n"], p["h_mult"], p["d_n"], p["d_mult"], p["atr"])
    return _CACHE[key]


def events(P: Prepared, p: dict) -> np.ndarray:
    """Entry direction per candle (+1 / -1 / 0) from the entry rule (before the sides filter)."""
    hd, dd = P.hd.astype(np.int64), P.dd.astype(np.int64)
    prev_h = np.r_[0, hd[:-1]]
    if p["sides"] == "nofilter":
        return np.where((hd != 0) & (prev_h != 0) & (hd != prev_h), hd, 0)
    a = np.where((hd != 0) & (hd == dd), hd, 0)
    if p["entry"] == "E2":
        return np.where((prev_h != 0) & (hd != prev_h) & (a != 0), a, 0)
    prev_a = np.r_[0, a[:-1]]
    return np.where((a != 0) & (a != prev_a), a, 0)


def run(M, P: Prepared, p: dict, start: str, forced: dict | None = None) -> pd.DataFrame:
    """Simulate trades. forced = {candle: direction} replaces the rule's entries (random control; exits unchanged)."""
    H, fut = P.H, M["fut"]
    src = H.src
    n = H.n
    hd, dd, hline, dline = P.hd, P.dd, P.h_line, P.dline
    ev = events(P, p)
    if p["sides"] == "long":
        ev = np.where(ev > 0, ev, 0)
    elif p["sides"] == "short":
        ev = np.where(ev < 0, ev, 0)
    ok_day = H.day >= np.datetime64(start, "D")
    spot_chart = H.chart == "spot"
    fo, fc_, fdt = fut.o, fut.c, fut.dt

    def fill_at(k):
        """(futures index, adjusted price) of an order on candle k's close."""
        use_next = p["fill"] == "next" or (p["last"] == "defer" and H.bucket[k] == PART)
        if not use_next and H.fill_close[k] >= 0:
            j = int(H.fill_close[k])
            return j, float(fc_[j])
        j = int(H.fill_next[k])
        return (j, float(fo[j])) if j >= 0 else (-1, np.nan)

    def basis(k):
        if not spot_chart or H.fill_close[k] < 0:
            return 0.0
        return float(fc_[H.fill_close[k]] - H.c[k])

    def touch(k, stop, s):
        """First minute of candle k reaching the stop -> (futures index, adjusted price) or None."""
        a, z = int(H.lo[k]), int(H.hi[k]) + 1
        hit = (src.l[a:z] <= stop) if s > 0 else (src.h[a:z] >= stop)
        if not hit.any():
            return None
        j = a + int(np.argmax(hit))
        lvl = min(stop, src.o[j]) if s > 0 else max(stop, src.o[j])     # gap through the stop -> the open (worse)
        if not spot_chart:
            return j, float(lvl)
        jf = int(np.searchsorted(fdt, src.dt[j]))
        if jf >= fut.n:
            return None
        return jf, float(lvl + (fo[jf] - src.o[j]))                      # spot level -> futures with that minute's basis

    rows = []
    pos, k_in, i_in, px_in, stop0, sdist = 0, -1, -1, np.nan, np.nan, np.nan
    for k in range(1, n):
        if pos != 0:
            s = pos
            close_exit = None
            if hd[k] == -s:
                close_exit = "1h flip (close)"
            elif p["sl"] == "daily" and ((s > 0 and H.c[k] < stop0) or (s < 0 and H.c[k] > stop0)):
                close_exit = "initial daily stop (close)"
            elif p["exit"] == "close_daily" and dd[k] == -s:
                close_exit = "daily flip against"
            t_hit = None
            if p["exit"] == "touch" and k > k_in:
                trail = hline[k - 1] if hd[k - 1] == s else np.nan
                stop = stop0 if not np.isfinite(trail) else (max(trail, stop0) if s > 0 else min(trail, stop0))
                t_hit = touch(k, stop, s)
            if t_hit and close_exit == "1h flip (close)":
                close_exit = None          # a close beyond the band passed through it first: the touch IS that exit (order known)
            if close_exit or t_hit:
                cands = []
                if t_hit:
                    cands.append((t_hit[0], t_hit[1], "touch band (intrabar)"))
                if close_exit:             # a different exit in the same candle (daily stop / daily flip): order unknown -> worse
                    j, px = fill_at(k)
                    if j >= 0:
                        cands.append((j, px, close_exit))
                if cands:
                    j, px, why = min(cands, key=lambda x: s * x[1])           # the worse price for the position
                    if len(cands) == 2 and why != cands[0][2]:
                        why += " (worse than the touch)"
                    rows.append((s, k_in, k, i_in, px_in, j, px, why, stop0, sdist))
                    pos = 0
        if pos == 0 and ok_day[k]:
            if forced is not None:
                s = forced.get(k, 0)
            else:
                s = int(ev[k])
            if s != 0:
                j, px = fill_at(k)
                if j >= 0:
                    lvl = hline[k] if (p["sl"] == "1h" or forced is not None) else dline[k]
                    if not np.isfinite(lvl):
                        continue
                    pos, k_in, i_in, px_in = s, k, j, px
                    stop0 = float(lvl)
                    sdist = abs(float(H.c[k]) - stop0)          # signal-chart points (video: 24,153 - 23,913 = 240)
    if pos != 0:
        j = fut.n - 1
        rows.append((pos, k_in, n - 1, i_in, px_in, j, float(fc_[j]), "end of data", stop0, sdist))
    cols = ["side", "k_in", "k_out", "i_in", "entry_px", "i_out", "exit_px", "reason", "stop_lvl", "stop_dist"]
    df = pd.DataFrame(rows, columns=cols)
    if not len(df):
        return df
    i_in, i_out = df["i_in"].to_numpy(), df["i_out"].to_numpy()
    seg = fut.seg
    df["rolls"] = [int(np.count_nonzero(seg[a + 1:b + 1] != seg[a:b])) if b > a else 0 for a, b in zip(i_in, i_out)]
    df["sig_in"] = H.end[df["k_in"].to_numpy()]
    df["sig_out"] = H.end[df["k_out"].to_numpy()]
    df["entry_dt"] = fdt[i_in]
    df["exit_dt"] = fdt[i_out]
    df["entry_raw"] = df["entry_px"] - fut.off[i_in]
    df["exit_raw"] = df["exit_px"] - fut.off[i_out]
    df["sig_close"] = H.c[df["k_in"].to_numpy()]
    df["h_line_in"] = hline[df["k_in"].to_numpy()]
    df["d_line_in"] = dline[df["k_in"].to_numpy()]
    df["size"] = 1.0
    df["gross_pts"] = df["side"] * (df["exit_px"] - df["entry_px"])
    df["days_held"] = (df["exit_dt"] - df["entry_dt"]).dt.total_seconds() / 86400
    return df

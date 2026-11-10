"""Order simulation shared by the modules: fills with slippage, stop / target walking (stop first, gap fills), rolls.

A trade (dict) holds prices in the bar series' ADJUSTED space (futures are back-adjusted; real price = price - off):
    inst, kind, side (+1 long / -1 short), sig_i (signal bar), entry_i, entry_px (fill incl. slippage), entry_raw,
    stop_px, target_px (nan = none), risk_pts (entry - stop per unit, > 0; for exits without a stop: the sizing risk),
    legs [(bar, fill px incl. slippage, fraction, reason)], rolls [(bar whose close is the roll, real old px, real new px,
    fraction still open)], roll_slip (points per unit paid on rolls), slip_pts (all slippage per unit).
Rules: fills at the next bar's open (or a stop-entry level), slippage on every fill (buy +, sell -), stop and target in
the same bar -> stop; a bar opening beyond the stop fills at that open; a target fills at the target price (never better).
"""
import numpy as np
import pandas as pd

from backtest.podcast.config import CONFIG
from backtest.podcast.data import Bars

INF = np.inf


def slip_array(b: Bars, mult: float = 1.0) -> np.ndarray:
    """Slippage in points for a fill at each bar (index futures: fixed points; stocks: % of the real price)."""
    s = CONFIG["slippage"]
    name = {"NIFTY50": "NIFTY"}.get(b.name, b.name)              # index series priced like its futures
    if b.kind in ("fut", "index") and name in s:
        return np.full(b.n, s[name] * mult)
    return s["stock_pct"] / 100.0 * (b.c - b.off) * mult


def walk(o, h, l, start: int, end: int, side: int, stop: float, target: float):
    """First bar j in [start, end) where the stop or the target is touched; the stop wins when both are. Entry was at
    o[start], so a gap through the stop only counts from start+1. Returns (j, level, reason) or (-1, nan, None)."""
    j0 = start
    tgt = target if np.isfinite(target) else (INF if side > 0 else -INF)
    while j0 < end:
        j1 = min(end, j0 + 256)
        if side > 0:
            sh = l[j0:j1] <= stop
            th = h[j0:j1] >= tgt
        else:
            sh = h[j0:j1] >= stop
            th = l[j0:j1] <= tgt
        hit = sh | th
        if hit.any():
            k = int(np.argmax(hit))
            j = j0 + k
            if sh[k]:
                gap = j > start and ((o[j] <= stop) if side > 0 else (o[j] >= stop))
                return j, (o[j] if gap else stop), ("stop gap" if gap else "stop")
            return j, tgt, "target"
        j0 = j1
    return -1, np.nan, None


def rolls_between(b: Bars, e: int, x: int, frac: float = 1.0) -> list:
    """Contract rolls passed while holding from bar e to bar x: [(bar j-1 whose close is the roll, real old, real new, frac)]."""
    if x <= e or b.kind not in ("fut", "sfut"):
        return []
    seg = b.seg
    js = np.nonzero(seg[e + 1:x + 1] != seg[e:x])[0] + e + 1
    return [(int(j - 1), float(b.c[j - 1] - b.off[j - 1]), float(b.c[j - 1] - b.off[j]), frac) for j in js]


def new_trade(b: Bars, side: int, sig_i: int, e: int, entry_level: float, slip: np.ndarray, stop: float, target: float,
              risk_pts: float | None = None, **extra) -> dict:
    """Entry fill = level + side x slippage. stop / target are levels in the same (adjusted) space."""
    px = entry_level + side * slip[e]
    t = {"inst": b.name, "kind": b.kind, "side": side, "sig_i": sig_i, "entry_i": e, "entry_px": px, "entry_raw": px - b.off[e],
         "stop_px": stop, "target_px": target, "risk_pts": abs(px - stop) if risk_pts is None else risk_pts,
         "legs": [], "rolls": [], "roll_slip": 0.0, "slip_pts": slip[e]}
    t.update(extra)
    return t


OPEN_FILLS = {"stop gap", "opposite cross", "15:15 exit", "random hold", "time exit", "back inside"}


def close_leg(t: dict, b: Bars, j: int, level: float, frac: float, reason: str, slip: np.ndarray, when: str | None = None):
    """Exit `frac` of the position at bar j at `level` (fill = level - side x slippage). when: 'open' = filled at the
    bar's open (realised at its start), 'bar' = during / at the end of the bar (realised at its end). Default from the
    reason (gap stops, 15:15 / cross / time exits fill at the open)."""
    when = when or ("open" if reason in OPEN_FILLS else "bar")
    t["legs"].append((j, level - t["side"] * slip[j], frac, reason, when))
    t["slip_pts"] += slip[j] * frac


def add_rolls(t: dict, b: Bars, e: int, x: int, frac: float, slip: np.ndarray):
    for r in rolls_between(b, e, x, frac):
        t["rolls"].append(r)
        t["roll_slip"] += 2 * slip[r[0]] * frac
        t["slip_pts"] += 2 * slip[r[0]] * frac


def frame(trades: list, b_by_inst: dict) -> pd.DataFrame:
    """List of trade dicts -> DataFrame with exit columns, P&L points per unit (incl. slippage) and gross R."""
    if not trades:
        return pd.DataFrame()
    rows = []
    for t in trades:
        b = b_by_inst[t["inst"]]
        legs = t["legs"]
        pnl = sum(f * t["side"] * (px - t["entry_px"]) for _, px, f, _, _ in legs) - t["roll_slip"]
        xi = legs[-1][0]
        reasons = [lg[3] for lg in legs]
        bar_end = np.timedelta64(b.bar_minutes * 60 - 1, "s")
        rows.append({**{k: v for k, v in t.items() if k not in ("legs", "rolls")},
                     "entry_dt": b.dt[t["entry_i"]], "exit_i": xi, "exit_dt": b.dt[xi], "exit_px": legs[-1][1],
                     "exit_raw": legs[-1][1] - b.off[xi], "reason": reasons[0] if len(set(reasons)) == 1 else " / ".join(reasons),
                     "legs": [(int(j), float(px), float(px - b.off[j]), float(f), r, b.dt[j] if w == "open" else b.dt[j] + bar_end) for j, px, f, r, w in legs],
                     "rolls": t["rolls"], "n_rolls": len(t["rolls"]), "pnl_pts": pnl, "R_gross": pnl / t["risk_pts"] if t["risk_pts"] > 0 else np.nan,
                     "bars": xi - t["entry_i"] + 1})
    df = pd.DataFrame(rows)
    return df.sort_values(["entry_dt", "inst"], kind="mergesort").reset_index(drop=True)


# --------------------------------------------------------------------------------------------- Module B (MA crossover)
def run_b(b: Bars, fast: np.ndarray, slow: np.ndarray, version: int, exit_mode: str, stop_pct: float, target_pct: float,
          slip: np.ndarray, eod_min: int, cross_risk_pct: float) -> list:
    """Moving-average crossover on one instrument.
    version 1: enter only on a cross (fast crosses slow); 2: when flat, enter in the direction fast vs slow (re-entry
    after a stop / target). Signal at a bar's close, fill at the next bar's open.
    exit_mode: 'sltp' stop / target % of the entry price; 'sltp_eod' same plus exit at the 15:15 candle's open (same day,
    no entry on the 15:15 candle); 'cross' exit and reverse at the next open after the opposite cross (no stop: sized as
    if the stop were cross_risk_pct %)."""
    o, h, l, c = b.o, b.h, b.l, b.c
    d = fast - slow
    n = b.n
    trades = []
    i = 1
    eod = exit_mode == "sltp_eod"
    while i < n - 1:
        di, dp = d[i], d[i - 1]
        if not (np.isfinite(di) and np.isfinite(dp)):
            i += 1
            continue
        if version == 1:
            sig = 1 if (di > 0 >= dp) else (-1 if (di < 0 <= dp) else 0)
        else:
            sig = 1 if di > 0 else (-1 if di < 0 else 0)
        e = i + 1
        if sig == 0 or (eod and b.minute[e] >= eod_min):
            i += 1
            continue
        side = sig
        px = o[e] + side * slip[e]
        raw = px - b.off[e]
        if exit_mode == "cross":
            t = new_trade(b, side, i, e, o[e], slip, np.nan, np.nan, risk_pts=raw * cross_risk_pct / 100.0)
            flip = np.nonzero(side * d[e:n - 1] < 0)[0]
            if len(flip):
                k = e + int(flip[0])
                add_rolls(t, b, e, k + 1, 1.0, slip)
                close_leg(t, b, k + 1, o[k + 1], 1.0, "opposite cross", slip)
                trades.append(t)
                i = k                                   # reverse: the cross at k is the next signal
            else:
                add_rolls(t, b, e, n - 1, 1.0, slip)
                close_leg(t, b, n - 1, c[n - 1], 1.0, "end of data", slip)
                trades.append(t)
                break
            continue
        stop = px - side * raw * stop_pct / 100.0
        target = px + side * raw * target_pct / 100.0
        t = new_trade(b, side, i, e, o[e], slip, stop, target)
        end = n
        eod_bar = -1
        if eod:
            day_last = b.last[b.di[e]]
            cand = np.nonzero(b.minute[e:day_last + 1] >= eod_min)[0]
            eod_bar = e + int(cand[0]) if len(cand) else -1
            end = eod_bar if eod_bar >= 0 else day_last + 1
        j, lvl, why = walk(o, h, l, e, end, side, stop, target)
        if j < 0:
            if eod:
                j, lvl, why = (eod_bar, o[eod_bar], "15:15 exit") if eod_bar >= 0 else (end - 1, c[end - 1], "day close")
            else:
                j, lvl, why = n - 1, c[n - 1], "end of data"
        add_rolls(t, b, e, j, 1.0, slip)
        close_leg(t, b, j, lvl, 1.0, why, slip)
        trades.append(t)
        i = j if j > i else i + 1
    return trades

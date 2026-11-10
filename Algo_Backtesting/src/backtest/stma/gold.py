"""Optional extension: Module A (long-only, close exit) on MCX GOLD futures (1 kg, price per 10 g; 1 point = Rs 100 / lot).

Continuous series from Fyers GOLD_F1 / GOLD_F2 1m: Fyers switch day = the day the next-month (F2) volume collapses
(< 1/4 of its last-5-day high, >= 25 days apart, confirmed by F1 opening near F2's previous close); the next contract
is used for the 3 trading days before the switch; back-adjusted (difference) so roll gaps are never P&L.
1-hour candles from 09:00 (MCX session 09:00-23:30; last part candle 23:00-23:30).
Costs per order: Rs 20 brokerage, CTT 0.01% on sells, MCX 0.0021%, SEBI 0.0001%, stamp 0.002% on buys, GST 18%.
"""
import numpy as np
import pandas as pd

from backtest.podcast import data as pdata
from backtest.stma import engine
from backtest.stma.bars import Signal

UNIT = 100           # Rs per 1-point move per lot
SLIP = 10.0          # points (Rs per 10 g) per side
OPEN = 540


def _read(n):
    df = pdata.read(n, "1m")
    m = df["datetime"].dt.hour * 60 + df["datetime"].dt.minute
    return df[(m >= OPEN) & (m <= 23 * 60 + 29) & (df["close"] > 0)].reset_index(drop=True)


def switch_days(f1: pd.DataFrame, f2: pd.DataFrame) -> list:
    d1 = f1.groupby(f1["datetime"].dt.normalize()).agg(o=("open", "first"), c=("close", "last"))
    d2 = f2.groupby(f2["datetime"].dt.normalize()).agg(c=("close", "last"), v=("volume", "sum"))
    j = d1.join(d2, rsuffix="2", how="inner")
    peak = j["v"].shift().rolling(5, min_periods=1).max()
    cand = j.index[(j["v"] < peak / 4)]
    out = []
    for d in cand:
        i = j.index.get_loc(d)
        if i == 0 or (out and (d - out[-1]).days < 25):
            continue
        prev = j.iloc[i - 1]
        if abs(j.loc[d, "o"] - prev["c2"]) < abs(j.loc[d, "o"] - prev["c"]):
            out.append(d)
    return out


def continuous(k: int = 3) -> tuple[pd.DataFrame, list]:
    f1, f2 = _read("GOLD_F1"), _read("GOLD_F2")
    sw = switch_days(f1, f2)
    days = pd.DatetimeIndex(sorted(set(f1["datetime"].dt.normalize())))
    src = pd.Series(1, index=days)
    seg = pd.Series(0, index=days)
    for n, w in enumerate(sw):
        p = days.get_loc(w)
        src.iloc[max(0, p - k):p] = 2
        seg.iloc[max(0, p - k):] = n + 1
    d1, d2 = f1["datetime"].dt.normalize(), f2["datetime"].dt.normalize()
    df = pd.concat([f1[(d1.map(src) == 1).to_numpy()], f2[(d2.map(src) == 2).to_numpy()]]).sort_values("datetime").reset_index(drop=True)
    day = df["datetime"].dt.normalize()
    df["seg"] = day.map(seg).astype(int).to_numpy()
    l1, l2 = f1.groupby(d1)["close"].last(), f2.groupby(d2)["close"].last()
    gaps = {}
    for s_ in range(1, len(sw) + 1):
        first = seg.index[seg == s_][0]
        prev = days[days.get_loc(first) - 1]
        gaps[s_] = float(l2.get(prev, np.nan) - l1.get(prev, np.nan)) if prev in l1.index and prev in l2.index else 0.0
    off, acc = {}, 0.0
    for s_ in sorted(set(df["seg"]), reverse=True):
        off[s_] = acc
        acc += gaps.get(s_, 0.0)
    df["off"] = df["seg"].map(off).astype(float)
    for c in ("open", "high", "low", "close"):
        df[c] = df[c] + df["off"]
    return df, sw


def hourly(b) -> Signal:
    bucket = (b.minute.astype(np.int64) - OPEN) // 60
    key = b.di.astype(np.int64) * 16 + bucket
    brk = np.r_[0, np.nonzero(np.diff(key))[0] + 1]
    hi_idx = np.r_[brk[1:] - 1, b.n - 1]
    bk = bucket[brk]
    day0 = b.dt[brk].astype("datetime64[D]").astype("datetime64[ns]")
    start = day0 + ((OPEN + 60 * bk) * 60_000_000_000).astype("timedelta64[ns]")
    end = day0 + (np.minimum(OPEN + 60 * (bk + 1), 23 * 60 + 30) * 60_000_000_000).astype("timedelta64[ns]")
    fill = np.searchsorted(b.dt, end)
    fill = np.where(fill < b.n, fill, -1)
    return Signal("GOLD", start, end, b.o[brk], np.maximum.reduceat(b.h, brk), np.minimum.reduceat(b.l, brk), b.c[hi_idx], bk, brk, hi_idx, fill, b)


def _cost(value, buy):
    brk, exch, sebi = 20.0, 0.000021 * value, 0.000001 * value
    return brk + (0.0 if buy else 0.0001 * value) + exch + sebi + (0.00002 * value if buy else 0.0) + 0.18 * (brk + exch + sebi)


def run_gold(grid: dict, split_frac: float = 0.6) -> dict:
    df, sw = continuous()
    b = pdata._from_frame(df, "GOLD", "fut", "1m")
    sig = hourly(b)
    split = pd.Timestamp(b.days[int(len(b.days) * split_frac)])
    rows, cache = [], {}
    for a in grid["atr_n"]:
        for m in grid["mult"]:
            for t in grid["ma_type"]:
                for n in grid["ma_len"]:
                    (line, up), ma = engine.indicators(sig, a, m, t, n, cache)
                    tr = engine.run(sig, b, line, up, ma, "long", "close")
                    pts = []
                    for (side, size, ki, ko, i0, i1, why, rolls) in tr:
                        e, x = b.o[i0], b.o[i1]
                        raw_e, raw_x = (e - b.off[i0]) * UNIT, (x - b.off[i1]) * UNIT
                        cost = (_cost(raw_e, True) + _cost(raw_x, False) + rolls * (_cost(raw_e, False) + _cost(raw_e, True))) / UNIT
                        pts.append((pd.Timestamp(b.dt[i0]), x - e - SLIP * (2 + 2 * rolls) - cost, x - e))
                    p = pd.DataFrame(pts, columns=["entry", "net", "gross"])
                    row = {"atr_n": a, "mult": m, "ma_type": t, "ma_len": n}
                    for part, q in (("is", p[p.entry < split]), ("oos", p[p.entry >= split]), ("full", p)):
                        w, l_ = q.net[q.net > 0].sum(), -q.net[q.net <= 0].sum()
                        row.update({f"{part}_trades": len(q), f"{part}_net": float(q.net.sum()), f"{part}_pf": float(w / l_) if l_ > 0 else np.nan})
                    rows.append(row)
    g = pd.DataFrame(rows)
    return {"grid": g, "split": split, "switches": len(sw), "first": str(b.days[0]), "last": str(b.days[-1])}

"""1-hour signal candles + the 1-minute NIFTY futures series every order fills on.

    fut, spot = bars.load()                      # podcast data layer: continuous back-adjusted futures 1m, spot 1m
    sig = bars.hourly(spot, "L1", fut)           # L1 spot incl. the 15:15-15:30 part candle, L2 without it, L3 futures chart

Candles are 09:15-anchored: 09:15, 10:15, 11:15, 12:15, 13:15, 14:15 (full hours) and 15:15-15:29 (part candle).
A candle's signal is known at its end (start + 60 min; 15:30 for the part candle); the order fills at the first futures
1-minute bar starting at or after that time (the NEXT 1-minute bar's open).
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest.podcast import data as pdata
from backtest.stma.config import CONFIG

OPEN = 555                  # 09:15 as minute of the day
PART = 6                    # bucket number of the 15:15-15:30 part candle


@dataclass
class Signal:
    name: str
    start: np.ndarray       # candle start (datetime64[ns])
    end: np.ndarray         # time the candle's close is known
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    bucket: np.ndarray      # 0..6 (6 = part candle)
    lo: np.ndarray          # first / last index of the candle's 1-minute bars in the signal chart's 1m series
    hi: np.ndarray
    fill: np.ndarray        # futures 1m index of the next 1-minute bar (order fill); -1 = after the data ends
    src: object             # the 1m Bars of the signal chart (spot or futures)

    @property
    def n(self) -> int:
        return len(self.c)


def load(start: str | None = None, end: str | None = None):
    start = start or CONFIG["data"]["start"]
    end = end or CONFIG["data"]["end"]
    fut = pdata.bars("NIFTY", "1m", start=start, end=end)
    spot = pdata.bars("NIFTY50", "1m", kind="index", start=start, end=end)
    return fut, spot


def hourly(b, name: str, fut) -> Signal:
    """1-hour candles from 1-minute Bars b (spot for L1 / L2, futures for L3)."""
    bucket = (b.minute.astype(np.int64) - OPEN) // 60
    key = b.di.astype(np.int64) * 8 + bucket
    brk = np.r_[0, np.nonzero(np.diff(key))[0] + 1]
    hi_idx = np.r_[brk[1:] - 1, b.n - 1]
    o = b.o[brk]
    h = np.maximum.reduceat(b.h, brk)
    l = np.minimum.reduceat(b.l, brk)
    c = b.c[hi_idx]
    bk = bucket[brk]
    day0 = b.dt[brk].astype("datetime64[D]").astype("datetime64[ns]")
    start = day0 + ((OPEN + 60 * bk) * 60_000_000_000).astype("timedelta64[ns]")
    end_min = np.where(bk >= PART, 930, OPEN + 60 * (bk + 1))
    end = day0 + (end_min * 60_000_000_000).astype("timedelta64[ns]")
    keep = np.ones(len(brk), bool) if name != "L2" else bk < PART
    fill = np.searchsorted(fut.dt, end[keep])
    fill = np.where(fill < fut.n, fill, -1)
    return Signal(name, start[keep], end[keep], o[keep], h[keep], l[keep], c[keep], bk[keep], brk[keep], hi_idx[keep], fill, b)


def signals(fut, spot) -> dict:
    return {"L1": hourly(spot, "L1", fut), "L2": hourly(spot, "L2", fut), "L3": hourly(fut, "L3", fut)}


def lot_size(days: np.ndarray) -> np.ndarray:
    """NIFTY futures lot size in force on each day (CONFIG lot_sizes)."""
    days = np.asarray(days, "datetime64[D]")
    out = np.zeros(len(days), dtype=np.int64)
    for d0, lot in CONFIG["lot_sizes"]:
        out[days >= np.datetime64(d0, "D")] = lot
    return out


def closing_auction_start(spot) -> tuple[pd.Timestamp | None, pd.DataFrame]:
    """When the NIFTY spot index started to freeze in the last minutes and jump at 15:29 (closing auction session):
    per day, share of 15:20-15:28 one-minute candles with zero range, and the 15:29 move. Start = first month where most
    days are frozen. Returns (start day or None, monthly table)."""
    m = spot.minute
    sel = (m >= 920) & (m <= 928)
    df = pd.DataFrame({"day": spot.days[spot.di[sel]], "flat": (spot.h[sel] - spot.l[sel]) == 0})
    per_day = df.groupby("day")["flat"].mean()
    last = (m == 929)
    jump = pd.Series(np.abs(spot.c[last] - spot.o[last]) / spot.o[last] * 100, index=spot.days[spot.di[last]])
    both = pd.DataFrame({"flat_share": per_day, "jump_1529_pct": jump})
    mon = both.groupby(pd.to_datetime(both.index).to_period("M")).mean()
    frozen = mon[mon["flat_share"] > 0.6]
    start = None
    if len(frozen):
        first_month = frozen.index[0]
        days = per_day[(pd.to_datetime(per_day.index).to_period("M") == first_month) & (per_day > 0.6)]
        start = pd.Timestamp(days.index[0]) if len(days) else first_month.to_timestamp()
    return start, mon

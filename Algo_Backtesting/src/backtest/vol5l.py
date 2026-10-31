"""Vol 5L: first 15-minute candle (9:15-9:30) green with volume >= 5 lakh -> BUY at its close.
Target 7%, stop-loss 3%; at +2% the SL moves to the buy price and then trails 2% below the highest price (never down).

Two steps:
    prepare()   reads every day file once (in parallel) and keeps the qualifying stocks with their 1-minute candles
    simulate()  replays the top stocks of each day minute by minute for one set of settings
"""
import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from backtest.config import EQUITY
from backtest.data import COLUMNS, trading_days

T_ENTRY = 9 * 60 + 30       # entry at the close of the 9:15 15m candle = 9:30
T_SQUARE = 15 * 60 + 15     # square-off: open of the 15:15 one-minute candle
MIN_VOL = 500_000           # first 15m candle volume (shares)
TARGET = 0.07
SL_PCT = 0.03
BE_TRIGGER = 0.02           # at +2% the SL moves to the buy price ...
TRAIL_GAP = 0.02            # ... and then trails 2% below the highest price
EXPOSURE = 175_000          # Rs 35,000 margin x 5 leverage per trade
CAPITAL = 70_000
MAX_TRADES = 2
KEEP = 5                    # per day: top 5 by volume, by turnover and by volume with price >= CHECK_PRICE (for the checks)
CHECK_PRICE = 100
COST_PCT = 0.001            # 0.10% of the trade value
WORKERS = 6


@dataclass
class Cand:
    day: str
    symbol: str
    vol: float            # first 15m candle volume
    c_open: float
    c_high: float
    c_low: float
    entry: float          # close of the first 15m candle
    times: np.ndarray     # minute of day for each 1m bar, 9:30 .. 15:15
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray


def read_day(day: str) -> tuple[list[Cand], int]:
    """Qualifying stocks of one day (see KEEP) and how many stocks qualified in total."""
    df = pd.read_csv(EQUITY / f"{day}.csv", usecols=COLUMNS)
    first = df[(df.timeframe == "15m") & (df.datetime == f"{day} 09:15:00")].set_index("symbol")
    q = first[(first.volume >= MIN_VOL) & (first.close > first.open)].sort_values("volume", ascending=False)
    by_turn = q.assign(t=q.volume * q.close).sort_values("t", ascending=False)
    keep = set(q.head(KEEP).index) | set(by_turn.head(KEEP).index) | set(q[q.close >= CHECK_PRICE].head(KEEP).index)
    top = q[q.index.isin(keep)]
    m1 = df[(df.timeframe == "1m") & df.symbol.isin(top.index)].copy()
    m1["m"] = m1.datetime.str[11:13].astype(int) * 60 + m1.datetime.str[14:16].astype(int)
    m1 = m1[(m1.m >= T_ENTRY) & (m1.m <= T_SQUARE)].sort_values(["symbol", "m"])
    bars = dict(tuple(m1.groupby("symbol")))
    out = []
    for sym, r in top.iterrows():
        b = bars.get(sym)
        if b is None or b.empty:
            continue
        out.append(Cand(day, sym, float(r.volume), float(r.open), float(r.high), float(r.low), float(r.close),
                        b.m.to_numpy(), b.open.to_numpy(float), b.high.to_numpy(float), b.low.to_numpy(float),
                        b.close.to_numpy(float)))
    return out, len(q)


def prepare(start: str, end: str, on_day: Callable[[int, int, int], None] | None = None) -> tuple[list[Cand], dict]:
    days = trading_days(start, end)
    cands, qualified = [], {}
    with ProcessPoolExecutor(WORKERS) as ex:
        jobs = {ex.submit(read_day, d): d for d in days}
        for i, f in enumerate(as_completed(jobs), 1):
            cs, n = f.result()
            cands += cs
            qualified[jobs[f]] = n
            if on_day:
                on_day(i, len(days), len(cands))
    return sorted(cands, key=lambda c: (c.day, -c.vol)), qualified


def simulate_trade(c: Cand, slip: float, target: float | None = TARGET, be: float | None = BE_TRIGGER,
                   gap: float | None = TRAIL_GAP) -> dict | None:
    """Replay one BUY. Stops are set from candles that already closed, so they apply from the next candle.
    target=None: no target. be=None: no breakeven / trailing. gap=None: breakeven only, no trailing."""
    e = c.entry
    qty = math.floor(EXPOSURE / e)
    if qty == 0:
        return None
    init, tgt = e * (1 - SL_PCT), np.inf if target is None else e * (1 + target)
    best = np.maximum.accumulate(np.concatenate(([e], c.h[:-1])))   # highest price of the closed candles
    if be is None:
        sl = np.full(len(best), init)
    else:
        moved = e if gap is None else np.maximum(e, best * (1 - gap))
        sl = np.maximum(np.where(best >= e * (1 + be), moved, -np.inf), init)   # only ever goes up
    n = len(c.o) - 1 if c.times[-1] == T_SQUARE else len(c.o)                    # 15:15 bar = exit bar, not checked
    hit_sl, hit_tg = c.l[:n] <= sl[:n], c.h[:n] >= tgt
    k_sl = int(hit_sl.argmax()) if hit_sl.any() else 10**6
    k_tg = int(hit_tg.argmax()) if hit_tg.any() else 10**6
    if k_sl <= k_tg and k_sl < 10**6:               # SL and target in the same candle: SL first
        k = k_sl
        raw = min(c.o[k], sl[k])
        why = "initial SL" if sl[k] == init else "breakeven SL" if sl[k] == e else "trailing SL"
    elif k_tg < 10**6:
        k = k_tg
        raw = max(c.o[k], tgt)
        why = "target 7%" if target == TARGET else "target"
    elif n < len(c.o):
        k, raw, why = n, float(c.o[n]), "15:15 square-off"
    else:
        k, raw, why = n - 1, float(c.c[n - 1]), "last candle close"
    buy_px, sell_px = e * (1 + slip), raw * (1 - slip)
    gross = qty * (sell_px - buy_px)
    cost = COST_PCT * qty * e
    t = int(c.times[k])
    return {"date": c.day, "symbol": c.symbol, "side": "BUY", "qty": qty, "entry": round(e, 2), "exit": round(float(raw), 2),
            "exit_time": f"{t // 60:02d}:{t % 60:02d}", "hold_hours": round((t - T_ENTRY) / 60, 3), "exit_reason": why,
            "max_gain_pct": round((float(c.h[:k + 1].max()) / e - 1) * 100, 2),
            "gross_pnl": round(gross, 2), "costs": round(cost, 2), "net_pnl": round(gross - cost, 2),
            "first_vol_lakh": round(c.vol / 1e5, 1), "first_candle_pct": round((c.entry / c.c_open - 1) * 100, 2)}


def simulate(cands: list[Cand], slip: float, max_trades: int = MAX_TRADES, by_turnover: bool = False, min_price: float = 0,
             **kw) -> pd.DataFrame:
    """Top max_trades per day by signal volume (or by turnover = volume x close). Checks only: by_turnover, min_price."""
    by_day: dict[str, list[Cand]] = {}
    for c in cands:
        by_day.setdefault(c.day, []).append(c)
    rows = []
    for day in sorted(by_day):
        n = 0
        for c in sorted(by_day[day], key=lambda c: -c.vol * (c.entry if by_turnover else 1)):
            if c.entry < min_price:
                continue
            r = simulate_trade(c, slip, **kw)
            if r:
                rows.append(r)
                n += 1
            if n == max_trades:
                break
    return pd.DataFrame(rows)

"""VMU Equity V3 (see strategies/VMU_EQUITY_V3_RULES.md): opening-volume spike, top-2 by range, 1-minute simulation.

Two steps so that many parameter combinations stay fast:
    prepare()   reads each day once and keeps only the stocks that pass the volume/turnover/gap filters
    simulate()  replays those stocks minute by minute (vectorised per trade) for one parameter combination
"""
import math
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from backtest.config import DATA, EQUITY
from backtest.data import COLUMNS, trading_days

NIFTY_CSV = DATA / "index" / "NIFTY50_1m.csv"
FUT_DIR = DATA / "fno" / "1m" / "futures"
T0 = pd.Timedelta(hours=9, minutes=15)
T_SIG_END = pd.Timedelta(hours=9, minutes=25)
T_SQUARE = pd.Timedelta(hours=15, minutes=15)

EXPOSURE = 175_000          # Rs 35,000 margin x 5 leverage per trade
CAPITAL = 70_000
MAX_TRADES = 2
MIN_TURNOVER = 5e7          # Rs 5 crore, 9:15-9:25
GAP_LIMIT = 1.5             # trade only when Nifty gap is within +-1.5%
COST_PCT = 0.001            # 0.10% of the trade value
TRAIL_START = 0.02          # trailing begins at +2% profit
VOL_MULTS = (5, 7, 10)
SL_MODES = ("fixed3", "fixed4", "candle")
BE_TRIGGERS = (0.01, 0.015, 0.02)
TRAIL_GAPS = (0.01, 0.015, 0.02)
SLIPPAGES = (0.0, 0.003, 0.005)


@dataclass
class Cand:
    day: str
    symbol: str
    ratio: float          # signal volume / average of the previous 3 days
    range_pct: float
    side: int             # +1 buy, -1 sell
    entry: float          # open of the 9:25 candle
    sig_high: float
    sig_low: float
    times: np.ndarray     # minute of day for each bar, 9:25 .. 15:15
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray


def fno_first_dates() -> dict[str, str]:
    """Underlying -> first date its futures have data. Futures start 1 Jan 2026 = in F&O the whole period.
    Later start = joined F&O then (approximation of the point-in-time list; only current contracts are stored)."""
    out = {}
    for f in FUT_DIR.glob("*FUT.csv"):
        with open(f) as fh:
            fh.readline()
            line = fh.readline()
        if line:
            out[f.stem.removesuffix("26NOVFUT")] = line[:10]
    return out


def _nifty_gaps() -> dict[str, float]:
    n = pd.read_csv(NIFTY_CSV, parse_dates=["datetime"])
    n["d"] = n["datetime"].dt.strftime("%Y-%m-%d")
    g = n.groupby("d")
    o, c = g["open"].first(), g["close"].last()
    return ((o - c.shift(1)) / c.shift(1) * 100).dropna().to_dict()


def prepare(start: str, end: str, on_day: Callable[[int, int, int], None] | None = None) -> list[Cand]:
    all_days = trading_days()
    days = trading_days(start, end)
    first = all_days.index(days[0])
    warm = all_days[first - 3:first]
    gaps, fno = _nifty_gaps(), fno_first_dates()
    vol_hist: dict[str, pd.Series] = {}
    cands: list[Cand] = []

    for i, day in enumerate(warm + days):
        df = pd.read_csv(EQUITY / f"{day}.csv", usecols=COLUMNS, parse_dates=["datetime"])
        df = df[df["timeframe"] == "1m"]
        tod = df["datetime"] - pd.Timestamp(day)
        sig = df[(tod >= T0) & (tod < T_SIG_END)]
        g = sig.sort_values("datetime").groupby("symbol")
        vol = g["volume"].sum()
        vol_hist[day] = vol
        if day in warm:
            continue
        prev = [vol_hist[d] for d in all_days[all_days.index(day) - 3:all_days.index(day)]]
        gap = gaps.get(day)
        if gap is None or abs(gap) > GAP_LIMIT or len(prev) < 3:
            if on_day:
                on_day(i - 2, len(days), len(cands))
            continue
        avg3 = pd.concat(prev, axis=1).mean(axis=1, skipna=False)
        stat = pd.DataFrame({
            "open": g["open"].first(), "close": g["close"].last(), "high": g["high"].max(), "low": g["low"].min(),
            "vol": vol, "turn": sig.assign(t=(sig.high + sig.low + sig.close) / 3 * sig.volume).groupby("symbol")["t"].sum(),
            "avg3": avg3})
        stat = stat.dropna()
        stat = stat[(stat.avg3 > 0) & (stat.vol >= VOL_MULTS[0] * stat.avg3) & (stat.turn >= MIN_TURNOVER)
                    & (stat.close != stat.open)]
        stat = stat[[not (s in fno and day >= fno[s]) for s in stat.index]]
        if len(stat):
            m1 = df[df["symbol"].isin(stat.index) & (tod >= T_SIG_END) & (tod <= T_SQUARE)]
            for sym, b in m1.groupby("symbol"):
                b = b.sort_values("datetime")
                if b["datetime"].iloc[0] != pd.Timestamp(day) + T_SIG_END:
                    continue
                r = stat.loc[sym]
                cands.append(Cand(
                    day, sym, r.vol / r.avg3, (r.high - r.low) / r.open * 100, 1 if r.close > r.open else -1,
                    float(b["open"].iloc[0]), float(r.high), float(r.low),
                    ((b["datetime"] - pd.Timestamp(day)).dt.total_seconds() / 60).to_numpy(),
                    b["open"].to_numpy(float), b["high"].to_numpy(float), b["low"].to_numpy(float)))
        if on_day:
            on_day(i - 2, len(days), len(cands))
    return cands


def simulate_trade(c: Cand, sl_mode: str, be: float | None, gap: float | None, slip: float) -> dict:
    """Replay one trade. Stops are decided from candles that already closed, so they apply from the next candle.
    be=None turns breakeven off, gap=None turns trailing off (VMU V2: fixed SL only)."""
    be = np.inf if be is None else be
    trail_start = np.inf if gap is None else TRAIL_START
    gap = 0.0 if gap is None else gap
    s, e = c.side, c.entry
    if sl_mode == "candle":
        init = max(c.sig_low, e * 0.96) if s == 1 else min(c.sig_high, e * 1.04)
    else:
        pct = 0.03 if sl_mode == "fixed3" else 0.04
        init = e * (1 - s * pct)
    if s == 1:
        best = np.maximum.accumulate(np.concatenate(([e], c.h[:-1])))
        be_sl = np.where(best >= e * (1 + be), e, -np.inf)
        tr_sl = np.where(best >= e * (1 + trail_start), best * (1 - gap), -np.inf)
        sl = np.maximum(np.maximum(be_sl, tr_sl), init)
        hit = c.l[:-1] <= sl[:-1]
    else:
        best = np.minimum.accumulate(np.concatenate(([e], c.l[:-1])))
        be_sl = np.where(best <= e * (1 - be), e, np.inf)
        tr_sl = np.where(best <= e * (1 - trail_start), best * (1 + gap), np.inf)
        sl = np.minimum(np.minimum(be_sl, tr_sl), init)
        hit = c.h[:-1] >= sl[:-1]
    if hit.any():
        k = int(hit.argmax())
        raw = min(c.o[k], sl[k]) if s == 1 else max(c.o[k], sl[k])
        if sl[k] == init:
            why = "initial SL"
        elif sl[k] == tr_sl[k]:
            why = "trailing SL"
        else:
            why = "breakeven SL"
    else:
        k, raw, why = len(c.o) - 1, float(c.o[-1]), "15:15 square-off"
    qty = math.floor(EXPOSURE / e)
    buy_px, sell_px = (e * (1 + slip), raw * (1 - slip)) if s == 1 else (raw * (1 + slip), e * (1 - slip))
    gross = qty * (sell_px - buy_px)
    cost = COST_PCT * qty * e
    return {"date": c.day, "symbol": c.symbol, "side": "BUY" if s == 1 else "SELL", "qty": qty,
            "entry": round(e, 2), "exit": round(raw, 2), "exit_time": f"{int(c.times[k]) // 60:02d}:{int(c.times[k]) % 60:02d}",
            "hold_hours": round((c.times[k] - 565) / 60, 3), "exit_reason": why, "initial_sl": round(float(init), 2),
            "gross_pnl": round(gross, 2), "costs": round(cost, 2), "net_pnl": round(gross - cost, 2),
            "range_pct": round(c.range_pct, 2), "vol_ratio": round(c.ratio, 1)}


def simulate(cands: list[Cand], vol_mult: float, sl_mode: str, be: float | None, gap: float | None, slip: float) -> pd.DataFrame:
    by_day: dict[str, list[Cand]] = {}
    for c in cands:
        if c.ratio >= vol_mult:
            by_day.setdefault(c.day, []).append(c)
    rows = []
    for day in sorted(by_day):
        top = sorted(by_day[day], key=lambda c: -c.range_pct)[:MAX_TRADES]
        rows += [simulate_trade(c, sl_mode, be, gap, slip) for c in top]
    return pd.DataFrame(rows)


def summary(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"trades": 0}
    w, l = t.loc[t.net_pnl > 0, "net_pnl"], t.loc[t.net_pnl <= 0, "net_pnl"]
    cum = t.sort_values(["date", "symbol"]).net_pnl.cumsum()
    return {
        "trades": len(t), "win_pct": round((t.net_pnl > 0).mean() * 100, 1), "net": round(t.net_pnl.sum()),
        "profit_factor": round(w.sum() / -l.sum(), 2) if l.sum() < 0 else None,
        "max_dd": round((cum - cum.cummax().clip(lower=0)).min()), "median_hold_h": round(t.hold_hours.median(), 2),
        "avg_loss": round(l.mean()) if len(l) else 0, "worst_trade": round(t.net_pnl.min()),
        "sl_pct": round(t.exit_reason.eq("initial SL").mean() * 100, 1),
    }


EXPECTED = {"trades": 200, "win_pct": 67.5, "net": 570_000, "avg_loss": -3020, "worst_trade": -7497,
            "median_hold_h": 3.8, "sl_pct": 23.5}


def match_score(sm: dict) -> float:
    """Average relative distance from the expected result (lower = closer)."""
    if sm.get("trades", 0) == 0:
        return 9.9
    return float(np.mean([abs(sm[k] - v) / abs(v) for k, v in EXPECTED.items()]))

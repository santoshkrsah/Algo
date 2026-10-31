"""VMU ORB V4: opening-range breakout on volume-spike stocks, 1-minute simulation, train/test split.

    prepare()   reads each day once and keeps the stocks that pass the day-level filters (both opening-range lengths)
    sim_path()  replays one stock for one (max SL, partial R) setting -> fills without costs
    run()       picks the top 2 per day for one combination, applies slippage, costs and the daily loss limit
"""
import math
from collections import deque
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from backtest.config import DATA, EQUITY
from backtest.data import COLUMNS, trading_days

NIFTY_CSV = DATA / "index" / "NIFTY50_1m.csv"
BANDS_DIR = DATA / "reference" / "price_bands"
OPEN_MIN = 555               # 9:15 in minutes from midnight
ENTRY_END = 660              # breakout must happen on a candle starting before 11:00
SQUARE_MIN = 910             # 15:10 square-off at that candle's open

CAPITAL = 70_000
EXPOSURE = 175_000           # Rs 35,000 margin x 5 MIS leverage
MAX_TRADES = 2
DAY_LOSS_LIMIT = -6_000
MIN_TURNOVER = 5e7           # Rs 5 crore in the opening range
PRICE_MIN, PRICE_MAX = 50, 5_000
RANGE_MIN, RANGE_MAX = 1.0, 5.0
CIRCUIT_GAP = 0.03
GAP_LIMIT = 1.5
BREAK_BUF = 0.0005           # entry 0.05% beyond the opening range
COST_PCT = 0.001
AVG_DAYS = 5

VOL_MULTS = (3, 5, 7)
MAX_SLS = (0.02, 0.025, 0.03)
PARTIALS = (1.5, 2.0, 3.0)
OR_MINS = (15, 30)
SLIPPAGES = {"0%": 0.0, "0.3%": 0.003, "0.5%": 0.005}
SELECT_SLIP = "0.3%"         # the train-period choice is made at this slippage
MIN_TRAIN_TRADES = 100


@dataclass
class Cand:
    day: str
    symbol: str
    or_min: int
    side: int                # +1 buy breakout (Nifty up at OR end), -1 sell breakout
    ratio: float
    or_high: float
    or_low: float
    t: np.ndarray            # minute of day of every candle from 9:15 to 15:10
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray


def _nifty_days() -> dict[str, dict]:
    n = pd.read_csv(NIFTY_CSV)
    n["d"] = n.datetime.str[:10]
    n["m"] = n.datetime.str[11:13].astype(int) * 60 + n.datetime.str[14:16].astype(int)
    g = n.groupby("d")
    first, last = g.first(), g.last()
    out = {}
    prev = None
    for d in first.index:
        out[d] = {"open": float(first.loc[d, "open"]), "prev_close": prev}
        prev = float(last.loc[d, "close"])
    at = {}
    for L in OR_MINS:
        # Nifty "at 9:30" (or 9:45) = close of the last candle inside the opening range
        s = n[n.m == OPEN_MIN + L - 1].set_index("d")["close"]
        at[L] = s.to_dict()
    for d, v in out.items():
        v["at"] = {L: at[L].get(d) for L in OR_MINS}
    return out


def _bands(day: str, cache: dict) -> pd.DataFrame | None:
    """NSE price band list for the day (latest earlier file if that day's is missing). Index = symbol."""
    files = cache.setdefault("_files", sorted(p.stem for p in BANDS_DIR.glob("????-??-??.csv")))
    usable = [f for f in files if f <= day]
    if not usable:
        return None
    f = usable[-1]
    if f not in cache:
        b = pd.read_csv(BANDS_DIR / f"{f}.csv", dtype=str).rename(columns=str.strip)
        b = b[b.Series.str.strip() == "EQ"].drop_duplicates("Symbol").set_index("Symbol")
        cache.clear()
        cache["_files"] = files
        cache[f] = b
    return cache[f]


def universe(b: pd.DataFrame) -> pd.Series:
    """Symbol -> band fraction, for stocks tradable under the rules that day:
    EQ series, has a price band (No Band = in F&O), band not 2% and not under GSM (proxy for MIS allowed)."""
    band = b.Band.str.strip()
    gsm = b.Remarks.fillna("").str.upper().str.contains("GSM")
    ok = (band != "No Band") & (band != "2") & ~gsm
    return pd.to_numeric(band[ok], errors="coerce").dropna() / 100


def prepare(start: str, end: str, on_day: Callable[[int, int, int], None] | None = None) -> tuple[list[Cand], dict]:
    all_days = trading_days()
    days = trading_days(start, end)
    i0 = all_days.index(days[0])
    warm = all_days[i0 - AVG_DAYS:i0]
    nifty = _nifty_days()
    hist = {L: deque(maxlen=AVG_DAYS) for L in OR_MINS}
    prev_close: pd.Series | None = None
    band_cache: dict = {}
    cands: list[Cand] = []
    skipped = {"no_nifty": 0, "gap": 0, "flat_nifty": 0, "no_bands": 0}

    for i, day in enumerate(warm + days):
        df = pd.read_csv(EQUITY / f"{day}.csv", usecols=COLUMNS)
        df = df[df.timeframe == "1m"]
        df["m"] = df.datetime.str[11:13].astype(int) * 60 + df.datetime.str[14:16].astype(int)
        df = df[(df.m >= OPEN_MIN) & (df.m <= SQUARE_MIN)].sort_values(["symbol", "m"])
        df["tp"] = (df.high + df.low + df.close) / 3 * df.volume
        ors = {}
        for L in OR_MINS:
            w = df[df.m < OPEN_MIN + L]
            g = w.groupby("symbol")
            ors[L] = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last(),
                                   "vol": g.volume.sum(), "turn": g.tp.sum()})
        day_close = df.groupby("symbol").close.last()
        if day in warm:
            for L in OR_MINS:
                hist[L].append(ors[L].vol)
            prev_close = day_close
            continue

        nf = nifty.get(day)
        b = _bands(day, band_cache)
        reason = None
        if nf is None or nf["prev_close"] is None:
            reason = "no_nifty"
        elif abs((nf["open"] - nf["prev_close"]) / nf["prev_close"] * 100) > GAP_LIMIT:
            reason = "gap"
        elif b is None:
            reason = "no_bands"
        if reason is None:
            uni = universe(b)
            for L in OR_MINS:
                at = nf["at"].get(L)
                side = 0 if at is None else (1 if at > nf["open"] else -1 if at < nf["open"] else 0)
                if side == 0 or len(hist[L]) < AVG_DAYS:
                    continue
                s = ors[L].copy()
                s["avg"] = pd.concat(list(hist[L]), axis=1).mean(axis=1, skipna=False)
                s["pc"] = prev_close
                s["band"] = uni
                s = s.dropna()
                s["ratio"] = s.vol / s.avg
                s["rng"] = (s.high - s.low) / s.open * 100
                uc, lc = s.pc * (1 + s.band), s.pc * (1 - s.band)
                s = s[(s.avg > 0) & (s.ratio >= min(VOL_MULTS)) & (s.turn >= MIN_TURNOVER)
                      & s.close.between(PRICE_MIN, PRICE_MAX) & s.rng.between(RANGE_MIN, RANGE_MAX)
                      & ((uc - s.close) / s.close >= CIRCUIT_GAP) & ((s.close - lc) / s.close >= CIRCUIT_GAP)]
                if s.empty:
                    continue
                bars = df[df.symbol.isin(s.index)]
                for sym, bb in bars.groupby("symbol"):
                    r = s.loc[sym]
                    cands.append(Cand(day, sym, L, side, float(r.ratio), float(r.high), float(r.low), bb.m.to_numpy(),
                                      bb.open.to_numpy(float), bb.high.to_numpy(float), bb.low.to_numpy(float)))
            if all(nf["at"].get(L) == nf["open"] for L in OR_MINS):
                skipped["flat_nifty"] += 1
        else:
            skipped[reason] += 1
        for L in OR_MINS:
            hist[L].append(ors[L].vol)
        prev_close = day_close
        if on_day:
            on_day(i - len(warm) + 1, len(days), len(cands))
    return cands, skipped


def sim_path(c: Cand, max_sl: float, partial_r: float) -> dict | None:
    """One trade without costs/slippage. Fills are (minute, price, share of qty). None = no breakout before 11:00.
    Stops move after a candle closes and apply from the next one; a candle touching both SL and target counts as SL."""
    s = c.side
    trig = c.or_high * (1 + BREAK_BUF) if s == 1 else c.or_low * (1 - BREAK_BUF)
    t, o, h, l = c.t, c.o, c.h, c.l
    n = len(t)
    start = int(np.searchsorted(t, OPEN_MIN + c.or_min))
    k = None
    for i in range(start, n):
        if t[i] >= ENTRY_END:
            break
        if (s == 1 and h[i] >= trig) or (s == -1 and l[i] <= trig):
            k = i
            break
    if k is None:
        return None
    e = max(o[k], trig) if s == 1 else min(o[k], trig)
    sl = max(c.or_low, e * (1 - max_sl)) if s == 1 else min(c.or_high, e * (1 + max_sl))
    risk = abs(e - sl)
    target = e + s * partial_r * risk
    be_level = e + s * risk
    qty = math.floor(EXPOSURE / e)
    half = qty // 2
    fills, left, kind, partial = [], qty, "initial SL", None
    bucket = (t - OPEN_MIN) // 15
    for i in range(k, n):
        if t[i] >= SQUARE_MIN:
            fills.append((int(t[i]), float(o[i]), left))
            return _done(c, e, qty, t[k], fills, "15:10 square-off", partial)
        gap_hit = i > k and ((s == 1 and o[i] <= sl) or (s == -1 and o[i] >= sl))
        if gap_hit or (s == 1 and l[i] <= sl) or (s == -1 and h[i] >= sl):
            fills.append((int(t[i]), float(o[i]) if gap_hit else float(sl), left))
            return _done(c, e, qty, t[k], fills, kind, partial)
        if partial is None and half > 0 and ((s == 1 and h[i] >= target) or (s == -1 and l[i] <= target)):
            px = float(target) if i == k else (max(o[i], target) if s == 1 else min(o[i], target))
            fills.append((int(t[i]), px, half))
            left -= half
            partial = px
        # end of candle i: move the stop for the next candle
        if (s == 1 and h[i] >= be_level) or (s == -1 and l[i] <= be_level) or partial is not None:
            if (s == 1 and e > sl) or (s == -1 and e < sl):
                sl, kind = e, "breakeven SL"
        if partial is not None:
            done_b = (t[i] + 1 - OPEN_MIN) // 15          # 15-min candles completed by the end of this minute
            m = (bucket >= done_b - 2) & (bucket < done_b) & (np.arange(n) <= i)
            if done_b >= 2 and m.any():
                tr = l[m].min() if s == 1 else h[m].max()
                if (s == 1 and tr > sl) or (s == -1 and tr < sl):
                    sl, kind = float(tr), "trailing SL"
    fills.append((int(t[-1]), float(o[-1]), left))     # no 15:10 candle: last candle available
    return _done(c, e, qty, t[k], fills, "15:10 square-off", partial)


def _done(c, e, qty, t_entry, fills, why, partial):
    return {"c": c, "entry": float(e), "qty": qty, "t_entry": int(t_entry), "fills": fills, "why": why, "partial": partial}


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def price_trade(p: dict, slip: float) -> dict:
    c, s, e, qty = p["c"], p["c"].side, p["entry"], p["qty"]
    e_fill = e * (1 + s * slip)
    gross = sum(s * (px * (1 - s * slip) - e_fill) * q for _, px, q in p["fills"])
    cost = COST_PCT * qty * e
    t_exit = p["fills"][-1][0]
    avg_exit = sum(px * q for _, px, q in p["fills"]) / qty
    return {"date": c.day, "symbol": c.symbol, "side": "BUY" if s == 1 else "SELL", "qty": qty,
            "entry_time": _hm(p["t_entry"]), "entry": round(e, 2), "exit_time": _hm(t_exit), "exit": round(avg_exit, 2),
            "exit_reason": p["why"], "partial": "" if p["partial"] is None else round(p["partial"], 2),
            "gross_pnl": round(gross, 2), "costs": round(cost, 2), "net_pnl": round(gross - cost, 2),
            "hold_hours": round((t_exit - p["t_entry"]) / 60, 3), "vol_ratio": round(c.ratio, 1), "or_min": c.or_min,
            "_t_exit": t_exit}


class Runner:
    """Caches the price paths so every combination and slippage reuses them."""

    def __init__(self, cands: list[Cand]):
        self.by_day: dict[tuple[str, int], list[Cand]] = {}
        for c in cands:
            self.by_day.setdefault((c.day, c.or_min), []).append(c)
        self.cache: dict = {}

    def path(self, c: Cand, max_sl: float, partial_r: float):
        key = (c.day, c.symbol, c.or_min, max_sl, partial_r)
        if key not in self.cache:
            self.cache[key] = sim_path(c, max_sl, partial_r)
        return self.cache[key]

    def run(self, vol_mult: float, max_sl: float, partial_r: float, or_min: int, slip: float, days: tuple[str, str]) -> pd.DataFrame:
        rows = []
        for (day, L), cs in sorted(self.by_day.items()):
            if L != or_min or not days[0] <= day <= days[1]:
                continue
            top = sorted((c for c in cs if c.ratio >= vol_mult), key=lambda c: -c.ratio)[:MAX_TRADES]
            trades = [price_trade(p, slip) for p in (self.path(c, max_sl, partial_r) for c in top) if p]
            trades.sort(key=lambda r: r["entry_time"])
            for j, r in enumerate(trades):
                closed = sum(x["net_pnl"] for x in trades[:j] if x["_t_exit"] < int(r["entry_time"][:2]) * 60 + int(r["entry_time"][3:]))
                if closed <= DAY_LOSS_LIMIT:
                    continue
                rows.append(r)
        t = pd.DataFrame(rows)
        return t.drop(columns="_t_exit") if len(t) else t


def summary(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"trades": 0, "win_pct": 0, "net": 0, "profit_factor": None, "max_dd": 0, "avg_trade": 0}
    w, l = t.loc[t.net_pnl > 0, "net_pnl"], t.loc[t.net_pnl <= 0, "net_pnl"]
    cum = t.sort_values(["date", "entry_time"]).net_pnl.cumsum()
    return {"trades": len(t), "win_pct": round((t.net_pnl > 0).mean() * 100, 1), "net": round(t.net_pnl.sum()),
            "profit_factor": round(w.sum() / -l.sum(), 2) if l.sum() < 0 else None,
            "max_dd": round((cum - cum.cummax().clip(lower=0)).min()), "avg_trade": round(t.net_pnl.mean())}

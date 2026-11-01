"""Option R3 momentum on real 1-minute option candles (Fyers; only the live Nov 2026 stock options, ~Oct 2026).

Setups (CALL options, R3 from the option's own previous-day H/L/C):
    A30 / A60       any 30-min / 1-hour candle closes above R3 -> buy at its close, hold overnight (BTST)
    A30_last        only the last 30-min candle of the day (15:15-15:30 when candles start at 9:15)
    B15             first 15-min candle (9:15-9:30) closes above R3 -> intraday
    B15x2           first or second 15-min candle
    combo           yesterday A30_last + today opens above yesterday's close + today B15
Exits: BTST -> next day 10:30 (time) or SL 30% / target 1:2 till next day 15:15; intraday -> SL 30% / target 1:2 or 15:15.
"""
import re

import numpy as np
import pandas as pd

from backtest.config import FNO
from backtest.opt_r3 import PER_TRADE, pnl, r3

OPT = FNO / "1m" / "options"
FUT = FNO / "1m" / "futures"
INDEX = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX")
PAT = re.compile(r"^([A-Z0-9&-]+?)26NOV([\d.]+)(CE|PE)$")
M0, M_END = 555, 930                      # 9:15 .. 15:30
SL, RR = 0.30, 2


def load_contracts() -> dict:
    """symbol -> list of (strike, 1m DataFrame with day, m) for its CE contracts."""
    out = {}
    for f in OPT.glob("*26NOV*CE.csv"):
        m = PAT.match(f.stem)
        if not m or m[1] in INDEX:
            continue
        d = pd.read_csv(f)
        if d.empty:
            continue
        d["day"] = d.datetime.str[:10]
        d["m"] = d.datetime.str[11:13].astype(int) * 60 + d.datetime.str[14:16].astype(int)
        d = d[(d.m >= M0) & (d.m < M_END)]
        out.setdefault(m[1], []).append((float(m[2]), d))
    return out


def spot_close(symbol: str) -> pd.Series:
    f = FUT / f"{symbol}26NOVFUT.csv"
    if not f.exists():
        return pd.Series(dtype=float)
    d = pd.read_csv(f, usecols=["datetime", "close"])
    d = d[d.datetime >= "2026-09-01"]
    return d.groupby(d.datetime.str[:10]).close.last()


def candles(d: pd.DataFrame, size: int) -> pd.DataFrame:
    b = d.assign(bar=(d.m - M0) // size)
    g = b.groupby(["day", "bar"])
    out = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), vol=("volume", "sum")).reset_index()
    out["end"] = np.minimum(M0 + (out.bar + 1) * size, M_END)  # minute when the candle closes (last one is 15:15-15:30)
    return out


def _path_exit(d: pd.DataFrame, days: list[str], day: str, m_in: int, entry: float, exit_day: str, exit_m: int, sl: bool):
    """Walk 1m bars after entry until (exit_day, exit_m). sl=True: SL 30% / target 1:2 checked on every bar (SL first)."""
    p = d[((d.day == day) & (d.m >= m_in)) | ((d.day > day) & (d.day <= exit_day))]
    p = p[(p.day < exit_day) | (p.m < exit_m)]
    stop, tgt = entry * (1 - SL), entry * (1 + SL * RR)
    if sl:
        for r in p.itertuples():
            if r.low <= stop:
                return min(r.open, stop), "SL", r.day, r.m
            if r.high >= tgt:
                return max(r.open, tgt), "target", r.day, r.m
    last = d[(d.day == exit_day) & (d.m <= exit_m)]
    if last.empty:
        return None
    return float(last.close.iloc[-1]), "time", exit_day, int(last.m.iloc[-1])


def run(contracts: dict, pivot: str) -> pd.DataFrame:
    rows = []
    for sym, lst in contracts.items():
        spot = spot_close(sym)
        strikes = sorted(k for k, _ in lst)
        if spot.empty or len(strikes) < 3:
            continue
        for K, d in lst:
            daily = d.groupby("day").agg(high=("high", "max"), low=("low", "min"), close=("close", "last"), vol=("volume", "sum"))
            days = list(daily.index)
            c15, c30, c60 = candles(d, 15), candles(d, 30), candles(d, 60)
            for i, day in enumerate(days[1:], 1):
                prev = days[i - 1]
                if day not in spot.index or prev not in spot.index or daily.vol[prev] <= 0 or daily.vol[day] <= 0:
                    continue
                atm = min(strikes, key=lambda s: abs(s - spot[prev]))
                k = strikes.index(K) - strikes.index(atm)
                if not 0 <= k <= 2:
                    continue
                lvl = r3(daily.high[prev], daily.low[prev], daily.close[prev], pivot)
                nxt = days[i + 1] if i + 1 < len(days) else None
                base = dict(symbol=sym, strike=K, k=k, date=day, r3=round(lvl, 2), prev_close=daily.close[prev])
                for name, cs, last_only in [("A30", c30, False), ("A60", c60, False), ("A30_last", c30, True)]:
                    x = cs[(cs.day == day) & (cs.vol > 0)]
                    if last_only:
                        x = x[x.end == M_END]
                    hit = x[x.close > lvl]
                    if hit.empty or nxt is None:
                        continue
                    h = hit.iloc[0]
                    for ex, sl, em in [("next 10:30", False, 630), ("SL30/T1:2 till next 15:15", True, 915)]:
                        r = _path_exit(d, days, day, int(h.end), float(h.close), nxt, em, sl)
                        if r:
                            rows.append({**base, "setup": name, "exit_rule": ex, "entry_time": f"{int(h.end) // 60:02d}:{int(h.end) % 60:02d}",
                                         "entry": float(h.close), "exit": r[0], "exit_reason": r[1], "exit_day": r[2]})
                first = c15[(c15.day == day)].sort_values("bar")
                opened_up = not d[d.day == day].empty and float(d[d.day == day].open.iloc[0]) > daily.close[prev]
                yday = c30[(c30.day == prev) & (c30.end == M_END)]
                yday_sig = False
                if i >= 2 and not yday.empty:
                    pp = days[i - 2]
                    yday_sig = float(yday.close.iloc[0]) > r3(daily.high[pp], daily.low[pp], daily.close[pp], pivot)
                for name, nbars, extra in [("B15", 1, True), ("B15x2", 2, True), ("combo", 1, yday_sig and opened_up)]:
                    hit = first[(first.bar < nbars) & (first.close > lvl) & (first.vol > 0)]
                    if hit.empty or not extra:
                        continue
                    h = hit.iloc[0]
                    for ex, sl in [("15:15 (time)", False), ("SL30/T1:2 ya 15:15", True)]:
                        r = _path_exit(d, days, day, int(h.end), float(h.close), day, 915, sl)
                        if r:
                            rows.append({**base, "setup": name, "exit_rule": ex, "entry_time": f"{int(h.end) // 60:02d}:{int(h.end) % 60:02d}",
                                         "entry": float(h.close), "exit": r[0], "exit_reason": r[1], "exit_day": r[2]})
    t = pd.DataFrame(rows)
    if t.empty:
        return t
    g, c = pnl(t.entry.to_numpy(), t.exit.to_numpy(), 0.01)
    t["net_pnl"] = np.round(g - c, 2)
    t["ret_pct"] = np.round(t.net_pnl / PER_TRADE * 100, 2)
    t["pivot"] = pivot
    return t

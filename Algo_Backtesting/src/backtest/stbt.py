"""Positional option selling: 9:15-11:15 range breakout on an index, sell 1-strike ITM option (PE on a break up, CE on a
break down) with an OTM hedge, SL on the underlying, one re-entry, exit next day 9:35 (STBT / BTST).

Option prices are MODELLED (Black-76 on the forward, IV = k x 20-day realised volatility) because real 1-minute option
prices exist only for the Nov 2026 contracts. calibrate() picks k from those real prices.

    under = load_under("NIFTY");  k = calibrate("NIFTY", under)[0];  trades = simulate(under, Params(iv_mult=k))
"""
import math
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from backtest.config import DATA

OPT_DIR = DATA / "fno" / "1m" / "options"
SOURCES = {"NIFTY": DATA / "index" / "NIFTY50_1m.csv",                       # spot
           "BANKNIFTY": DATA / "fno" / "1m" / "futures" / "BANKNIFTY26NOVFUT.csv",   # continuous front-month futures
           "SENSEX": DATA / "fno" / "1m" / "futures" / "SENSEX26NOVFUT.csv"}
STEP = {"NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100}
SPOT = {"NIFTY": True, "BANKNIFTY": False, "SENSEX": False}
R, Q = 0.065, 0.012          # interest rate, index dividend yield (spot only)
IV_MIN, IV_MAX = 0.08, 0.80
M0, M_RANGE_END, M_LAST_ENTRY, M_NEXT_EXIT = 555, 675, 915, 575    # 9:15, 11:15, 15:15, 9:35 (minute of day)
MONTHS = "123456789OND"


@dataclass(frozen=True)
class Params:
    iv_mult: float = 1.0
    range_end: int = M_RANGE_END    # range = 9:15 .. range_end, breakout watched from range_end to 15:15
    sl_und: float = 0.005           # SL: underlying moves 0.5% against the trade
    sl_prem: float | None = None    # instead: SL when the sold option is up by this share (0.5 = 50% of premium)
    itm_steps: int = 1              # sold strike: this many strikes in the money
    hedge_pct: float | None = 0.02  # hedge: buy same-type option this far OTM (None = no hedge)
    reentry: bool = True
    exit_next: bool = True          # False: exit 15:15 same day
    risk: float = 2000.0            # Rs at risk per trade (qty = risk / loss at SL)
    capital: float = 200_000.0
    month_stop: float | None = 0.03  # stop new trades for the month after a loss of this share of capital
    slip: float = 0.0               # slippage, share of premium, each fill


# ---------------------------------------------------------------- data
def load_under(name: str) -> pd.DataFrame:
    d = pd.read_csv(SOURCES[name], usecols=["datetime", "open", "high", "low", "close"])
    d["day"] = d.datetime.str[:10]
    d["m"] = d.datetime.str[11:13].astype(int) * 60 + d.datetime.str[14:16].astype(int)
    return d[(d.m >= M0) & (d.m < 930)].sort_values("datetime").reset_index(drop=True)


def realised_vol(under: pd.DataFrame) -> pd.Series:
    """day -> annualised close-to-close volatility of the 20 trading days BEFORE that day."""
    c = under.groupby("day").close.last()
    r = np.log(c).diff()
    return (r.rolling(20).std() * math.sqrt(252)).shift(1)


def expiries(name: str, days: list[str]) -> list[date]:
    """Expiry dates: NIFTY weekly (Thu, Tue from Sep 2025), SENSEX weekly (Thu), BANKNIFTY monthly (last Tue).
    An expiry on a non-trading day (inside the data) moves to the previous trading day."""
    tset = set(days)
    first, last = date.fromisoformat(days[0]) - timedelta(days=7), date.fromisoformat(days[-1]) + timedelta(days=70)
    out, d = [], first
    while d <= last:
        if name == "BANKNIFTY":
            nxt = d + timedelta(days=7)
            ok = d.weekday() == 1 and nxt.month != d.month
        elif name == "NIFTY":
            ok = d.weekday() == (1 if d >= date(2025, 9, 1) else 3)
        else:
            ok = d.weekday() == 3
        if ok:
            e = d
            while e.isoformat() <= days[-1] and e.isoformat() not in tset and e > first:
                e -= timedelta(days=1)
            out.append(e)
        d += timedelta(days=1)
    return sorted(set(out))


# ---------------------------------------------------------------- option model
def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def price(name: str, und: float, K: float, cp: str, T: float, vol: float) -> float:
    """Black-76 on the forward. Spot index: forward = S x e^((r-q)T); futures: forward = futures price."""
    if T <= 0:
        return max(0.0, und - K) if cp == "CE" else max(0.0, K - und)
    F = und * math.exp((R - Q) * T) if SPOT[name] else und
    sv = vol * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sv * sv) / sv
    d2 = d1 - sv
    df = math.exp(-R * T)
    if cp == "CE":
        return df * (F * _ncdf(d1) - K * _ncdf(d2))
    return df * (K * _ncdf(-d2) - F * _ncdf(-d1))


def years(day: str, minute: int, exp: date) -> float:
    t = datetime.fromisoformat(day) + timedelta(minutes=minute)
    return max(0.0, (datetime.combine(exp, datetime.min.time()) + timedelta(hours=15, minutes=30) - t).total_seconds() / (365 * 86400))


# ---------------------------------------------------------------- calibration on real option prices
def _opt_files(name: str):
    """(path, expiry, strike, CE/PE) for every stored real option contract of this index."""
    pat_w = re.compile(rf"^{name}(\d\d)([1-9OND])(\d\d)(\d+)(CE|PE)$")
    pat_m = re.compile(rf"^{name}(\d\d)([A-Z]{{3}})(\d+)(CE|PE)$")
    for f in OPT_DIR.glob(f"{name}26*.csv"):
        s = f.stem
        if (m := pat_w.match(s)):
            exp = date(2000 + int(m[1]), MONTHS.index(m[2]) + 1, int(m[3]))
            yield f, exp, float(m[4]), m[5]
        elif (m := pat_m.match(s)):
            mon = datetime.strptime(m[2], "%b").month
            y = 2000 + int(m[1])
            d = date(y + (mon == 12), mon % 12 + 1, 1) - timedelta(days=1)     # last day of month
            wd = 3 if name == "SENSEX" else 1
            while d.weekday() != wd:
                d -= timedelta(days=1)
            yield f, d, float(m[3]), m[4]


def calibrate(name: str, under: pd.DataFrame, grid=np.arange(0.6, 2.51, 0.05)) -> tuple[float, dict]:
    """IV multiplier k that makes the model closest to the real stored option prices (median absolute % error).
    Samples: 11:30, 13:30, 15:00 candles, real price >= Rs 5, at least 1 day to expiry."""
    rv = realised_vol(under)
    u = under.set_index(["day", "m"]).close
    rows = []
    for f, exp, K, cp in _opt_files(name):
        o = pd.read_csv(f, usecols=["datetime", "close"])
        o["day"], o["m"] = o.datetime.str[:10], o.datetime.str[11:13].astype(int) * 60 + o.datetime.str[14:16].astype(int)
        for r in o[o.m.isin([690, 810, 900])].itertuples():
            T = years(r.day, r.m, exp)
            if r.close < 5 or T < 1 / 365 or (r.day, r.m) not in u.index or pd.isna(rv.get(r.day)):
                continue
            S = float(u[(r.day, r.m)])
            itm = (S > K) if cp == "CE" else (S < K)
            rows.append((S, K, cp, T, float(rv[r.day]), float(r.close), itm))
    if not rows:
        return 1.0, {"n": 0}
    S, K, cp, T, vol, real, itm = map(np.array, zip(*rows))
    best = None
    for k in grid:
        iv = np.clip(vol * k, IV_MIN, IV_MAX)
        model = np.array([price(name, s, kk, c, t, v) for s, kk, c, t, v in zip(S, K, cp, T, iv)])
        err = (model - real) / real
        score = float(np.median(np.abs(err)))
        if best is None or score < best[0]:
            best = (score, float(k), err)
    score, k, err = best
    return k, {"n": len(rows), "k": round(k, 2), "med_abs_err": round(score * 100, 1), "med_err": round(float(np.median(err)) * 100, 1),
               "itm_med_abs_err": round(float(np.median(np.abs(err[itm]))) * 100, 1) if itm.any() else None,
               "n_itm": int(itm.sum()), "rv_avg": round(float(np.mean(vol)) * 100, 1)}


# ---------------------------------------------------------------- strategy
def _strikes(name: str, S: float, side: int, p: Params) -> tuple[str, float, float | None]:
    """side +1 = break up -> sell ITM PE (strike above spot); -1 = break down -> sell ITM CE (strike below)."""
    st = STEP[name]
    if side == 1:
        K = (math.floor(S / st) + p.itm_steps) * st
        H = None if p.hedge_pct is None else round(S * (1 - p.hedge_pct) / st) * st
        return "PE", K, H
    K = (math.ceil(S / st) - p.itm_steps) * st
    H = None if p.hedge_pct is None else round(S * (1 + p.hedge_pct) / st) * st
    return "CE", K, H


def _costs(qty: float, sells: float, buys: float, orders: int) -> float:
    """Fyers/NSE option charges on premium turnover (sells / buys = Rs premium value)."""
    brokerage = 20.0 * orders
    exchange = 0.0003503 * (sells + buys)
    sebi = 0.000001 * (sells + buys)
    gst = 0.18 * (brokerage + exchange + sebi)
    return brokerage + exchange + sebi + gst + 0.001 * sells + 0.00003 * buys


def _sl_level(name, E, side, cp, K, T, iv, p: Params) -> float:
    if p.sl_prem is None:
        return E * (1 - side * p.sl_und)
    target = price(name, E, K, cp, T, iv) * (1 + p.sl_prem)     # underlying level where the sold option is up sl_prem
    lo, hi = (E * 0.8, E) if side == 1 else (E, E * 1.2)
    for _ in range(60):
        mid = (lo + hi) / 2
        worse = price(name, mid, K, cp, T, iv) >= target
        if side == 1:
            lo, hi = (mid, hi) if worse else (lo, mid)
        else:
            lo, hi = (lo, mid) if worse else (mid, hi)
    return (lo + hi) / 2


def _trade(name, p: Params, iv, day, m_in, E, side, exp, path, reentry: bool) -> dict:
    """path = list of (day, minute, open, high, low, close) after entry up to the exit bar (last item = exit bar)."""
    cp, K, H = _strikes(name, E, side, p)
    T0 = years(day, m_in, exp)
    sl = _sl_level(name, E, side, cp, K, T0, iv, p)
    s0, h0 = price(name, E, K, cp, T0, iv), (price(name, E, H, cp, T0, iv) if H else 0.0)
    loss_unit = (price(name, sl, K, cp, T0, iv) - s0) - ((price(name, sl, H, cp, T0, iv) - h0) if H else 0.0)
    qty = p.risk / loss_unit if loss_unit > 0 else 0.0
    x_day, x_m, X, why, k_hit = None, None, None, None, None
    for i, (d, m, o, hi, lo, c) in enumerate(path[:-1]):
        if (side == 1 and lo <= sl) or (side == -1 and hi >= sl):
            X = min(o, sl) if side == 1 else max(o, sl)
            x_day, x_m, why, k_hit = d, m, "SL", i
            break
    if X is None:
        d, m, o = path[-1][:3]
        x_day, x_m, X, why = d, m, o, "agle din 9:35 exit" if p.exit_next else "15:15 exit"
    T1 = years(x_day, x_m, exp)
    s1, h1 = price(name, X, K, cp, T1, iv), (price(name, X, H, cp, T1, iv) if H else 0.0)
    sl_ = p.slip
    sell_s, buy_s = s0 * (1 - sl_), s1 * (1 + sl_)
    buy_h, sell_h = h0 * (1 + sl_), h1 * (1 - sl_)
    gross = qty * ((sell_s - buy_s) + ((sell_h - buy_h) if H else 0.0))
    cost = _costs(qty, qty * (sell_s + (sell_h if H else 0)), qty * (buy_s + (buy_h if H else 0)), 4 if H else 2)
    return {"date": day, "symbol": name, "side": f"SELL {cp}", "strike": K, "hedge": H or "-", "expiry": exp.isoformat(),
            "entry_time": f"{m_in // 60:02d}:{m_in % 60:02d}", "und_entry": round(E, 2), "und_sl": round(sl, 2),
            "exit_date": x_day, "exit_time": f"{x_m // 60:02d}:{x_m % 60:02d}", "und_exit": round(X, 2),
            "iv_pct": round(iv * 100, 1), "opt_entry": round(s0, 2), "opt_exit": round(s1, 2),
            "hedge_entry": round(h0, 2), "hedge_exit": round(h1, 2), "qty": round(qty, 1),
            "exit_reason": why, "reentry": "haan" if reentry else "nahi",
            "gross_pnl": round(gross, 2), "costs": round(cost, 2), "net_pnl": round(gross - cost, 2),
            "_hit_index": k_hit}


def simulate(name: str, under: pd.DataFrame, p: Params, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    rv = realised_vol(under)
    days = sorted(under.day.unique())
    exps = expiries(name, days)
    by_day = {d: g for d, g in under.groupby("day")}
    rows, month, month_pnl = [], None, 0.0
    for i, day in enumerate(days[:-1]):
        if (start and day < start) or (end and day > end) or pd.isna(rv.get(day)):
            continue
        if day[:7] != month:
            month, month_pnl = day[:7], 0.0
        stopped = lambda: p.month_stop is not None and month_pnl <= -p.month_stop * p.capital  # noqa: E731
        if stopped():
            continue
        g, nxt = by_day[day], by_day[days[i + 1]]
        rng = g[g.m < p.range_end]
        if rng.empty or rng.m.iloc[0] != M0:
            continue
        hi, lo = rng.high.max(), rng.low.min()
        watch = g[(g.m >= p.range_end) & (g.m < M_LAST_ENTRY)]
        brk = watch[(watch.close > hi) | (watch.close < lo)]
        if brk.empty:
            continue
        b = brk.iloc[0]
        side = 1 if b.close > hi else -1
        exp = next(e for e in exps if e.isoformat() > day)
        iv = float(np.clip(p.iv_mult * rv[day], IV_MIN, IV_MAX))
        bars = [(day, r.m, r.open, r.high, r.low, r.close) for r in g[g.m > b.m].itertuples()]
        if p.exit_next:
            nb = [(days[i + 1], r.m, r.open, r.high, r.low, r.close) for r in nxt[nxt.m <= M_NEXT_EXIT].itertuples()]
            if not nb or nb[-1][1] != M_NEXT_EXIT:
                continue
            path = bars + nb
        else:
            path = [x for x in bars if x[1] <= M_LAST_ENTRY]
            if not path or path[-1][1] != M_LAST_ENTRY:
                continue
        t = _trade(name, p, iv, day, int(b.m) + 1, float(b.close), side, exp, path, False)
        rows.append(t)
        month_pnl += t["net_pnl"]
        k = t.pop("_hit_index")
        if not p.reentry or k is None or path[k][0] != day or stopped():
            continue
        E0 = float(b.close)
        for j in range(k + 1, len(path)):
            d, m, o, h_, l_, c = path[j]
            if d != day or m >= M_LAST_ENTRY:
                break
            if (side == 1 and c >= E0) or (side == -1 and c <= E0):
                t2 = _trade(name, p, iv, day, m + 1, c, side, exp, path[j + 1:], True)
                t2.pop("_hit_index")
                rows.append(t2)
                month_pnl += t2["net_pnl"]
                break
    return pd.DataFrame(rows)


def with_params(base: Params, **kw) -> Params:
    return replace(base, **kw)

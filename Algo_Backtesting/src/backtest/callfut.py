"""Intraday call + future hedge on NSE stock F&O: at 9:15 BUY 2 lots of the delta-0.50 CALL + SELL 1 lot of the same stock's
future (same, current-month expiry; net delta ~0, i.e. 2 x CALL - FUT ~ 1 CALL + 1 PUT = long straddle), square off the same day.

Prices: entry = the day's real NSE bhavcopy OPEN of the option and the future, exit = the real CLOSE. Exits inside the day
(target / stop / 12:00) need the option price minute by minute; expired stock options have no 1-minute data, so it is
modelled with Black-76 on the real 1-minute future, IV moving in a straight line from the IV at the open to the IV at the close.

    d = load("2026-04-24", "2026-10-28")
    c = candidates(d, Params())                      # every stock-day: chosen strike, real open / close, model path exits
    t = pick(c, Params())                            # the portfolio: max 5 stocks a day within the capital
"""
import math
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from backtest.config import FNO

FUT_COLS = {"TradDt": "date", "TckrSymb": "symbol", "FininstrmActlXpryDt": "expiry", "OpnPric": "open", "HghPric": "high", "LwPric": "low",
            "ClsPric": "close", "TtlNbOfTxsExctd": "trades", "NewBrdLotQty": "lot"}
OPT_COLS = {"TradDt": "date", "TckrSymb": "symbol", "XpryDt": "expiry", "StrkPric": "strike", "OptnTp": "type", "OpnPric": "open",
            "HghPric": "high", "LwPric": "low", "ClsPric": "close", "TtlNbOfTxsExctd": "trades"}
OPEN_MIN, CLOSE_MIN, LAST_CHECK = 0, 360, 359          # minutes after 9:15: close = 15:15 (NSE close = 15:00-15:30 average)
# exit variants: (key, label, kind, arg). "close" = real close; "tpsl" = target / stop on the position P&L as a share of the
# premium paid, checked every minute on the model price till 15:14, else real close; "time" = model price at that minute.
EXITS = [("close", "Din ka close (asli)", "close", None), ("t10", "Target 10% / SL 10%", "tpsl", (0.10, 0.10)),
         ("t20", "Target 20% / SL 15%", "tpsl", (0.20, 0.15)), ("noon", "12 baje exit", "time", 165)]


@dataclass(frozen=True)
class Params:
    delta: float = 0.50            # CALL whose delta (yesterday's IV, today's future open) is closest to this
    call_lots: int = 2
    fut_lots: int = 1
    put_lots: int = 0              # check only: 1 CE + 1 PE (same strike) = call_lots 1, fut_lots 0, put_lots 1
    series: str = "near"           # "near" = current-month expiry, "next" = next month (real open / close only)
    max_off: float = 0.10          # skip the stock that day if no strike is within delta +/- 0.10
    min_trades: int = 50           # the strike had this many trades yesterday
    min_fut_trades: int = 100      # the future had this many trades yesterday
    top: int | None = 5            # stocks a day (most traded strike yesterday first); None = every stock
    capital: float = 1_000_000.0
    slip_opt: float = 0.005        # slippage, share of the option premium, every fill
    slip_fut: float = 0.0005       # slippage, share of the future price, every fill
    stt_opt: float = 0.0015        # STT on option sell premium (Budget 2026, from 1 Apr 2026; was 0.1%)
    stt_fut: float = 0.0005        # STT on future sell value (Budget 2026; was 0.02%)
    margin: float = 0.20           # future margin, share of the contract value (hedge benefit ignored)
    rate: float = 0.06             # interest rate in Black-76


def with_params(base: Params, **kw) -> Params:
    return replace(base, **kw)


# ---------------------------------------------------------------- Black-76
def ncdf(x):
    """Normal CDF (Abramowitz-Stegun 7.1.26, error < 1.5e-7)."""
    x = np.asarray(x, float)
    z = np.abs(x) / math.sqrt(2)
    t = 1 / (1 + 0.3275911 * z)
    erf = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * np.exp(-z * z)
    return 0.5 * (1 + np.sign(x) * erf)


def b76(F, K, T, vol, r, cp="CE"):
    """(price, delta) on the future."""
    F, K, T, vol = (np.asarray(x, float) for x in (F, K, T, vol))
    sv = vol * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sv * sv) / sv
    df = np.exp(-r * T)
    if cp == "CE":
        return df * (F * ncdf(d1) - K * ncdf(d1 - sv)), df * ncdf(d1)
    return df * (K * ncdf(sv - d1) - F * ncdf(-d1)), -df * ncdf(-d1)


def implied_vol(price, F, K, T, r, cp="CE"):
    """Vectorised bisection; NaN when the price is outside the model range (below intrinsic, stale close)."""
    price, F, K, T = np.broadcast_arrays(*(np.asarray(x, float) for x in (price, F, K, T)))
    lo, hi = np.full(price.shape, 0.01), np.full(price.shape, 4.0)
    for _ in range(50):
        mid = (lo + hi) / 2
        up = b76(F, K, T, mid, r, cp)[0] < price
        lo, hi = np.where(up, mid, lo), np.where(up, hi, mid)
    iv = (lo + hi) / 2
    return np.where((iv > 0.011) & (iv < 3.99), iv, np.nan)


def years(day: str, expiry: str, minute: float) -> float:
    """Time from <day> 9:15 + minute to expiry 15:30, in years."""
    return ((pd.Timestamp(expiry) - pd.Timestamp(day)).days * 1440 + (375 - minute)) / (365 * 1440)


# ---------------------------------------------------------------- data
def load(start: str, end: str) -> dict:
    days = [p.stem for p in sorted((FNO / "bhavcopy").glob("*.csv")) if start <= p.stem <= end]
    fut = pd.concat([pd.read_csv(FNO / "bhavcopy" / f"{d}.csv", usecols=list(FUT_COLS) + ["FinInstrmTp"]) for d in days], ignore_index=True)
    fut = fut[fut.FinInstrmTp == "STF"].rename(columns=FUT_COLS)[list(FUT_COLS.values())]
    opt = pd.concat([pd.read_csv(FNO / "bhavcopy_opt" / f"{d}.csv", usecols=list(OPT_COLS)) for d in days], ignore_index=True).rename(columns=OPT_COLS)
    n = fut.groupby("expiry").symbol.nunique()                   # monthly expiries (a one-off date for a single stock is not one)
    return {"fut": fut, "opt": opt, "days": days, "expiries": sorted(n[n >= 100].index)}


def minute_futures(symbols, start: str, end: str) -> dict:
    """{symbol: DataFrame(date, minute, open, close)} from the continuous current-month 1-minute future (rolls after expiry)."""
    out = {}
    for s in symbols:
        f = FNO / "1m" / "futures" / f"{s}26NOVFUT.csv"
        if not f.exists():
            continue
        m = pd.read_csv(f)
        m = m[(m.datetime >= start) & (m.datetime < end + " 99")]
        dt = pd.to_datetime(m.datetime)
        m = m.assign(date=m.datetime.str[:10], minute=(dt.dt.hour * 60 + dt.dt.minute - 555).values)
        out[s] = {d: g for d, g in m[(m.minute >= 0) & (m.minute < 375)][["date", "minute", "open", "close"]].groupby("date")}
    return out


# ---------------------------------------------------------------- candidates (every stock-day)
def candidates(d: dict, p: Params, first: str, last: str, minutes: dict | None = None) -> pd.DataFrame:
    fut, opt, days = d["fut"], d["opt"], d["days"]
    exps = d["expiries"]
    rows = []
    for i in range(1, len(days)):
        day, prev = days[i], days[i - 1]
        if not (first <= day <= last) or day in exps:            # no trade on an expiry day (physical settlement, roll)
            continue
        ex_list = [e for e in exps if e > day]
        exp = ex_list[0] if p.series == "near" else ex_list[1]
        ft = fut[(fut.date == day) & (fut.expiry == exp)][["symbol", "open", "high", "low", "close", "trades", "lot"]]
        fp = fut[(fut.date == prev) & (fut.expiry == exp)][["symbol", "close", "trades"]].rename(columns={"close": "f_prev", "trades": "f_prev_trades"})
        f = ft.merge(fp, on="symbol")
        f = f[(f.f_prev_trades >= p.min_fut_trades) & (f.open > 0)]
        o_prev = opt[(opt.date == prev) & (opt.expiry == exp) & (opt.type == "CE") & (opt.trades >= p.min_trades) & (opt.close > 0.05)]
        c = o_prev[["symbol", "strike", "close", "trades"]].rename(columns={"close": "c_prev", "trades": "k_prev_trades"}).merge(f, on="symbol")
        if c.empty:
            continue
        iv_prev = implied_vol(c.c_prev.values, c.f_prev.values, c.strike.values, years(prev, exp, CLOSE_MIN), p.rate)
        dl = b76(c.open.values, c.strike.values, years(day, exp, OPEN_MIN), np.nan_to_num(iv_prev, nan=0.3), p.rate)[1]
        c = c.assign(iv_prev=iv_prev, delta=np.where(np.isnan(iv_prev), np.nan, dl)).dropna(subset=["delta"])
        c = c.loc[(c.delta - p.delta).abs().groupby(c.symbol).idxmin()]
        c = c[(c.delta - p.delta).abs() <= p.max_off]
        today = opt[(opt.date == day) & (opt.expiry == exp)]
        ce = today[today.type == "CE"][["symbol", "strike", "open", "high", "low", "close", "trades"]].add_prefix("c_").rename(columns={"c_symbol": "symbol", "c_strike": "strike"})
        c = c.merge(ce, on=["symbol", "strike"])
        if p.put_lots:
            pe = today[today.type == "PE"][["symbol", "strike", "open", "close", "trades"]].add_prefix("p_").rename(columns={"p_symbol": "symbol", "p_strike": "strike"})
            c = c.merge(pe, on=["symbol", "strike"])
            c = c[(c.p_trades > 0) & (c.p_open > 0)]
        c = c[(c.c_trades > 0) & (c.c_open > 0)]                  # the option must have traded today, else no fill
        c = c.assign(date=day, expiry=exp, dte=(pd.Timestamp(exp) - pd.Timestamp(day)).days)
        rows.append(c)
    c = pd.concat(rows, ignore_index=True).rename(columns={"open": "f_open", "high": "f_high", "low": "f_low", "close": "f_close", "trades": "f_trades"})
    c["iv_open"] = implied_vol(c.c_open.values, c.f_open.values, c.strike.values, [years(a, b, OPEN_MIN) for a, b in zip(c.date, c.expiry)], p.rate)
    c["iv_close"] = implied_vol(c.c_close.values, c.f_close.values, c.strike.values, [years(a, b, CLOSE_MIN) for a, b in zip(c.date, c.expiry)], p.rate)
    c["premium"] = p.call_lots * c.lot * c.c_open + (p.put_lots * c.lot * c.p_open if p.put_lots else 0)
    c["margin"] = p.fut_lots * p.margin * c.lot * c.f_open
    c["blocked"] = c.premium + c.margin
    c["net_delta"] = p.call_lots * c.delta - p.fut_lots - (p.put_lots * (1 - c.delta) if p.put_lots else 0)
    c = c.sort_values(["date", "k_prev_trades"], ascending=[True, False]).reset_index(drop=True)
    return exits(c, p, minutes)


# ---------------------------------------------------------------- exits
def _costs(opt_buy, opt_sell, fut_buy, fut_sell, orders, p: Params) -> dict:
    """Fyers/NSE charges on Rs turnover. Options: exchange 0.03503% of premium, futures 0.00173%; stamp 0.003% / 0.002% on buys."""
    brokerage = 20.0 * orders
    exch = 0.0003503 * (opt_buy + opt_sell) + 0.0000173 * (fut_buy + fut_sell) + 0.000001 * (opt_buy + opt_sell + fut_buy + fut_sell)
    gst = 0.18 * (brokerage + exch)
    stt = p.stt_opt * opt_sell + p.stt_fut * fut_sell
    stamp = 0.00003 * opt_buy + 0.00002 * fut_buy
    return {"brokerage": brokerage, "stt": stt, "exch": exch, "stamp": stamp, "gst": gst, "total": brokerage + exch + gst + stt + stamp}


def model_path(r, g: pd.DataFrame, p: Params):
    """Minute-by-minute (minute, future, model call) for one stock-day; IV from the open's IV to the close's IV in a straight line."""
    mins = g.minute.values.astype(float)
    F = g.close.values.astype(float)
    a, b = r.iv_open, r.iv_close if not np.isnan(r.iv_close) else r.iv_open
    iv = a + (b - a) * np.clip(mins / CLOSE_MIN, 0, 1)
    T = ((pd.Timestamp(r.expiry) - pd.Timestamp(r.date)).days * 1440 + (375 - mins)) / (365 * 1440)
    return mins, F, b76(F, r.strike, T, iv, p.rate)[0]


def exits(c: pd.DataFrame, p: Params, minutes: dict | None) -> pd.DataFrame:
    so, sf = p.slip_opt, p.slip_fut
    ql = c.lot.values
    c_in, f_in = c.c_open.values * (1 + so), c.f_open.values * (1 - sf)
    pu_in = c.p_open.values * (1 + so) if p.put_lots else np.zeros(len(c))
    orders = 2 * ((p.call_lots > 0) + (p.put_lots > 0) + (p.fut_lots > 0))
    prem = p.call_lots * ql * c_in + p.put_lots * ql * pu_in

    def book(key, c_out, f_out, pu_out, why, minute):
        gross = p.call_lots * ql * (c_out * (1 - so) - c_in) + p.put_lots * ql * (pu_out * (1 - so) - pu_in) - p.fut_lots * ql * (f_out * (1 + sf) - f_in)
        cs = _costs(p.call_lots * ql * c_in + p.put_lots * ql * pu_in, p.call_lots * ql * c_out * (1 - so) + p.put_lots * ql * pu_out * (1 - so),
                    p.fut_lots * ql * f_out * (1 + sf), p.fut_lots * ql * f_in, orders, p)
        c[f"{key}_call"], c[f"{key}_fut"], c[f"{key}_why"], c[f"{key}_min"] = c_out, f_out, why, minute
        c[f"{key}_gross"], c[f"{key}_costs"], c[f"{key}_net"] = gross, cs["total"], gross - cs["total"]

    pu_close = c.p_close.values if p.put_lots else np.zeros(len(c))
    book("close", c.c_close.values, c.f_close.values, pu_close, np.array(["Din ka close"] * len(c), dtype=object), np.full(len(c), CLOSE_MIN))
    c["model_ok"] = False
    if minutes is None or p.put_lots or p.series != "near":
        return c
    res = {k: [] for k, *_ in EXITS[1:]}
    mhi, mlo = np.full(len(c), np.nan), np.full(len(c), np.nan)
    for j, r in enumerate(c.itertuples()):
        g = minutes.get(r.symbol, {}).get(r.date)
        if g is None or len(g) < 300 or np.isnan(r.iv_open) or abs(g.open.iloc[0] / r.f_open - 1) > 0.003:
            for k in res:
                res[k].append((r.c_close, r.f_close, "Din ka close", CLOSE_MIN))
            continue
        c.at[r.Index, "model_ok"] = True
        mins, F, C = model_path(r, g, p)
        mhi[j], mlo[j] = C.max(), C.min()
        pnl = p.call_lots * ql[j] * (C * (1 - so) - c_in[j]) - p.fut_lots * ql[j] * (F * (1 + sf) - f_in[j])
        for key, label, kind, arg in EXITS[1:]:
            if kind == "time":
                k = int(np.searchsorted(mins, arg, side="right")) - 1
                res[key].append((C[k], F[k], f"{label}", mins[k]))
                continue
            tp, sl = arg
            live = (mins >= 1) & (mins <= LAST_CHECK)
            hit_t = np.where(live & (pnl >= tp * prem[j]))[0]
            hit_s = np.where(live & (pnl <= -sl * prem[j]))[0]
            kt, ks = (hit_t[0] if len(hit_t) else 10 ** 6), (hit_s[0] if len(hit_s) else 10 ** 6)
            if kt == ks == 10 ** 6:
                res[key].append((r.c_close, r.f_close, "Din ka close", CLOSE_MIN))
            elif ks <= kt:
                res[key].append((C[ks], F[ks], f"SL {int(sl * 100)}%", mins[ks]))
            else:
                res[key].append((C[kt], F[kt], f"Target {int(tp * 100)}%", mins[kt]))
    c["m_high"], c["m_low"] = mhi, mlo
    for key, v in res.items():
        a = np.array(v, dtype=object)
        book(key, a[:, 0].astype(float), a[:, 1].astype(float), np.zeros(len(c)), a[:, 2], a[:, 3].astype(float))
    return c


# ---------------------------------------------------------------- portfolio
def pick(c: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Per day: most traded strike (yesterday) first, max p.top stocks and money blocked within the capital."""
    if p.top is None:
        return c.copy()
    keep = []
    for _, g in c.groupby("date", sort=True):
        used, n = 0.0, 0
        for i, b in zip(g.index, g.blocked):
            if n >= p.top:
                break
            if used + b <= p.capital:
                keep.append(i)
                used += b
                n += 1
    return c.loc[keep].copy()


def stats(net: pd.Series, dates: pd.Series, capital: float) -> dict:
    if net.empty:
        return {"trades": 0, "net": 0.0, "win_pct": None, "profit_factor": None, "max_dd": 0.0, "avg": None, "ret_pct": 0.0}
    daily = net.groupby(dates.values).sum().sort_index()
    cum = daily.cumsum()
    dd = float((cum - cum.cummax().clip(lower=0)).min())
    w, l = net[net > 0], net[net <= 0]
    return {"trades": int(len(net)), "net": float(net.sum()), "ret_pct": float(net.sum() / capital * 100), "win_pct": float((net > 0).mean() * 100),
            "avg": float(net.mean()), "profit_factor": float(w.sum() / -l.sum()) if l.sum() < 0 else None, "max_dd": dd}

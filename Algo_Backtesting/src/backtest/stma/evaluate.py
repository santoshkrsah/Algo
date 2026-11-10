"""Costs, points, metrics and equity for Module A.

Per trade (per unit of NIFTY; shorts scaled by their 50% size):
    gross points = side x (exit fill - entry fill)            fills = next 1-minute futures open (back-adjusted)
    slippage     = slip x (2 + 2 x rolls)                      every fill, both legs of every roll
    charges      = Rs per lot from the order values / lot size of that date   (futures or synthetic model)
    net points   = gross - slippage - charges
Equity in Rs: lots = floor(equity x leverage x dd_factor x size / (futures price x lot)) at each entry (compounding).
"""
import math

import numpy as np
import pandas as pd

from backtest.stma.bars import lot_size
from backtest.stma.config import CONFIG


def _stt(kind: str, days: np.ndarray, mode: str) -> np.ndarray:
    tbl = CONFIG["costs"][kind]["stt_sell_pct"]
    if mode == "current":
        return np.full(len(days), tbl[-1][1] / 100.0)
    out = np.zeros(len(days))
    for d0, r in tbl:
        out[np.asarray(days, "datetime64[D]") >= np.datetime64(d0, "D")] = r / 100.0
    return out


def _order(kind: str, value: np.ndarray, buy: np.ndarray, days: np.ndarray, mode: str) -> np.ndarray:
    """Rs charges for orders of `value` Rs (futures notional or option premium x lot)."""
    m = CONFIG["costs"][kind]
    brk = CONFIG["costs"]["brokerage"]
    stt = np.where(buy, 0.0, _stt(kind, days, mode) * value)
    exch = m["exch_pct"] / 100 * value
    sebi = m["sebi_pct"] / 100 * value
    stamp = np.where(buy, m["stamp_buy_pct"] / 100 * value, 0.0)
    gst = m["gst_pct"] / 100 * (brk + exch + sebi)
    return brk + stt + exch + sebi + stamp + gst


def atm_premium(spot: np.ndarray, vix: np.ndarray, days: float) -> np.ndarray:
    """Black-Scholes ATM call (= put, zero rates) with India VIX as the implied volatility: S x (2N(d) - 1), d = s sqrt(T)/2."""
    s = np.asarray(vix, float) / 100.0 * math.sqrt(days / 365.0)
    nd = 0.5 * (1 + np.vectorize(math.erf)(s / 2 / math.sqrt(2)))
    return np.asarray(spot, float) * (2 * nd - 1)


def trades_frame(tr: list, sig, fut, vix_by_day: pd.Series | None = None) -> pd.DataFrame:
    """Engine tuples -> one row per trade with prices, dates, rolls (and roll prices) and gross points per unit."""
    if not tr:
        return pd.DataFrame()
    a = np.array([(t[0], t[1], t[2], t[3], t[4], t[5], t[7]) for t in tr], dtype=float)
    side, size, k_in, k_out, i_in, i_out, rolls = (a[:, j] for j in range(7))
    i_in, i_out, k_in, k_out = i_in.astype(int), i_out.astype(int), k_in.astype(int), k_out.astype(int)
    df = pd.DataFrame({"side": side.astype(int), "size": size, "sig_in": sig.end[k_in], "sig_out": sig.end[k_out],
                       "entry_dt": fut.dt[i_in], "exit_dt": fut.dt[i_out], "entry_px": fut.o[i_in], "exit_px": fut.o[i_out],
                       "entry_raw": fut.o[i_in] - fut.off[i_in], "exit_raw": fut.o[i_out] - fut.off[i_out], "rolls": rolls.astype(int),
                       "reason": [t[6] for t in tr], "i_in": i_in, "i_out": i_out})
    df["gross_pts"] = df["side"] * (df["exit_px"] - df["entry_px"])
    df["days_held"] = (df["exit_dt"] - df["entry_dt"]).dt.total_seconds() / 86400
    df["lot"] = lot_size(df["entry_dt"].to_numpy("datetime64[D]"))
    if vix_by_day is not None:
        d = df["entry_dt"].dt.normalize()
        df["vix"] = d.map(vix_by_day).ffill().bfill().to_numpy()
    return df


def apply_costs(df: pd.DataFrame, fut, instrument: str = "F", slip: float | None = None, rates: str | None = None) -> pd.DataFrame:
    """Adds slip_pts, cost_rs_lot, cost_pts, net_pts (per unit, size-scaled) for futures (F) or synthetic futures (S)."""
    if not len(df):
        return df
    slip = CONFIG["slippage_main"] if slip is None else slip
    rates = rates or CONFIG["rates_mode"]
    df = df.copy()
    lot = df["lot"].to_numpy(float)
    long = df["side"].to_numpy() > 0
    d_in = df["entry_dt"].to_numpy("datetime64[D]")
    d_out = df["exit_dt"].to_numpy("datetime64[D]")
    if instrument == "F":
        v_in, v_out = df["entry_raw"].to_numpy() * lot, df["exit_raw"].to_numpy() * lot
        c = _order("fut", v_in, long, d_in, rates) + _order("fut", v_out, ~long, d_out, rates)
        # rolls: close old + open new near the roll (same notional), per roll
        roll_v = df["entry_raw"].to_numpy() * lot
        c += df["rolls"].to_numpy() * (_order("fut", roll_v, ~long, d_in, rates) + _order("fut", roll_v, long, d_in, rates))
    else:
        days = CONFIG["costs"]["synthetic_days"]
        p_in = atm_premium(df["entry_raw"], df.get("vix", pd.Series(15.0, index=df.index)), days) * lot
        p_out = atm_premium(df["exit_raw"], df.get("vix", pd.Series(15.0, index=df.index)), days) * lot
        t = np.ones(len(df), bool)
        f = np.zeros(len(df), bool)
        # long synthetic: entry buy CE + sell PE; exit sell CE + buy PE (short synthetic mirrored: same order types)
        c = (_order("opt", p_in, t, d_in, rates) + _order("opt", p_in, f, d_in, rates)
             + _order("opt", p_out, f, d_out, rates) + _order("opt", p_out, t, d_out, rates))
        c += df["rolls"].to_numpy() * 2 * (_order("opt", p_in, t, d_in, rates) + _order("opt", p_in, f, d_in, rates))
    sz = df["size"].to_numpy()
    df["slip_pts"] = slip * (2 + 2 * df["rolls"].to_numpy()) * sz
    df["cost_rs_lot"] = c
    df["cost_pts"] = c / lot * sz
    df["gross_u"] = df["gross_pts"] * sz
    df["net_pts"] = df["gross_u"] - df["slip_pts"] - df["cost_pts"]
    return df


def stats(df: pd.DataFrame, years: float | None = None) -> dict:
    """Trade metrics on net points per unit."""
    n = len(df)
    if not n:
        return {"trades": 0}
    x = df["net_pts"].to_numpy()
    w, ls = x[x > 0], x[x <= 0]
    gl = -ls.sum()
    k = max(1, math.ceil(0.05 * n))
    tot = x.sum()
    span = years or max((df["exit_dt"].max() - df["entry_dt"].min()).days / 365.25, 1e-9)
    out = {"trades": n, "trades_per_year": n / span, "net_pts": float(tot), "pts_per_year": float(tot / span), "gross_pts": float(df["gross_u"].sum()),
           "cost_pts": float(df["cost_pts"].sum()), "slip_pts": float(df["slip_pts"].sum()), "cost_pts_per_year": float((df["cost_pts"] + df["slip_pts"]).sum() / span),
           "win_pct": len(w) / n * 100, "avg_win": float(w.mean()) if len(w) else 0.0, "avg_loss": float(ls.mean()) if len(ls) else 0.0,
           "pf": float(w.sum() / gl) if gl > 0 else float("inf"), "exp_pts": float(x.mean()),
           "best5_share": float(np.sort(x)[::-1][:k].sum() / tot * 100) if tot > 0 else float("nan"),
           "best": float(x.max()), "worst": float(x.min()), "avg_days": float(df["days_held"].mean()),
           "long_n": int((df["side"] > 0).sum()), "long_pts": float(df.loc[df["side"] > 0, "net_pts"].sum()),
           "short_n": int((df["side"] < 0).sum()), "short_pts": float(df.loc[df["side"] < 0, "net_pts"].sum())}
    out["rr"] = out["avg_win"] / abs(out["avg_loss"]) if out["avg_loss"] < 0 else float("nan")
    return out


def daily_points(df: pd.DataFrame, fut) -> pd.Series:
    """Net points per unit per trading day (open positions marked at each day's last futures close; slippage and
    charges booked on the exit day)."""
    pnl = np.zeros(len(fut.days))
    last_close = fut.c[fut.last]
    for r in df.itertuples(index=False):
        de, dx = int(fut.di[r.i_in]), int(fut.di[r.i_out])
        s = r.side * r.size
        if de == dx:
            pnl[de] += s * (r.exit_px - r.entry_px)
        else:
            marks = np.r_[r.entry_px, last_close[de:dx]]
            pnl[de:dx] += s * np.diff(marks)
            pnl[dx] += s * (r.exit_px - last_close[dx - 1])
        pnl[dx] -= r.slip_pts + r.cost_pts
    return pd.Series(pnl, index=pd.DatetimeIndex(fut.days))


def curve_stats(day: pd.Series, base: float = 0.0) -> dict:
    """Max drawdown and time drawdown of a daily P&L series (points or Rs); base = starting equity (0 for points)."""
    eq = base + day.cumsum().to_numpy()
    curve = np.r_[base, eq]
    peak = np.maximum.accumulate(curve)
    dd = peak - curve
    high = curve[1:] >= peak[1:] - 1e-9
    best, run = (0, None, None), None
    for i, h in enumerate(high):
        if h:
            if run is not None and i - run > best[0]:
                best = (i - run, run, i)
            run = None
        elif run is None:
            run = i
    if run is not None and len(high) - run > best[0]:
        best = (len(high) - run, run, len(high) - 1)
    idx = day.index
    n, a, b = best
    out = {"max_dd": float(dd.max()), "time_dd_days": int(n),
           "time_dd_months": float((idx[b] - idx[max(a - 1, 0)]).days / 30.44) if a is not None else 0.0,
           "time_dd_from": str(idx[max(a - 1, 0)].date()) if a is not None else None, "time_dd_to": str(idx[b].date()) if a is not None else None}
    if base > 0:
        out["max_dd_pct"] = float((dd / peak).max() * 100)
        yrs = (idx[-1] - idx[0]).days / 365.25
        out["end"] = float(eq[-1])
        out["cagr_pct"] = ((eq[-1] / base) ** (1 / yrs) - 1) * 100 if eq[-1] > 0 and yrs > 0 else -100.0
        below = np.nonzero(eq <= 0)[0]
        out["ruin_date"] = str(idx[below[0]].date()) if len(below) else None
    return out


def rupee_equity(df: pd.DataFrame, fut, leverage: float, dd_rule: bool, capital: float | None = None) -> tuple[pd.DataFrame, pd.Series]:
    """Compounding Rs equity: lots at each entry from the equity then, the leverage and the drawdown rule. Returns
    (trades with lots / pnl_rs, daily Rs P&L)."""
    capital = capital or CONFIG["capital"]
    df = df.sort_values("entry_dt").reset_index(drop=True).copy()
    eq, peak = capital, capital
    lots_l, pnl_l, fac_l = [], [], []
    steps = CONFIG["dd_rule"]["steps"]
    for r in df.itertuples(index=False):
        fac = 1.0
        if dd_rule:
            dd = (peak - eq) / peak * 100 if peak > 0 else 0.0
            for lim, f in steps:
                if dd > lim:
                    fac = f
        notional = r.entry_raw * r.lot
        lots = max(0, math.floor(eq * leverage * fac * r.size / notional)) if eq > 0 else 0
        units = lots * r.lot
        pnl = units * r.side * (r.exit_px - r.entry_px) - units * r.slip_pts / r.size - lots * r.cost_rs_lot
        eq += pnl
        peak = max(peak, eq)
        lots_l.append(lots)
        pnl_l.append(pnl)
        fac_l.append(fac)
    df["lots"], df["pnl_rs"], df["dd_factor"] = lots_l, pnl_l, fac_l
    day = np.zeros(len(fut.days))
    last_close = fut.c[fut.last]
    for r in df.itertuples(index=False):
        if r.lots == 0:
            continue
        u = r.lots * r.lot * r.side
        de, dx = int(fut.di[r.i_in]), int(fut.di[r.i_out])
        if de == dx:
            day[de] += r.pnl_rs
            continue
        marks = np.r_[r.entry_px, last_close[de:dx]]
        day[de:dx] += u * np.diff(marks)
        cost_rs = u * (r.exit_px - r.entry_px) - r.pnl_rs            # slippage + charges of the trade in Rs
        day[dx] += u * (r.exit_px - last_close[dx - 1]) - cost_rs
    return df, pd.Series(day, index=pd.DatetimeIndex(fut.days))


def buy_hold(fut, leverage: float, capital: float | None = None, slip: float | None = None) -> dict:
    """Long NIFTY futures all the time: points per unit (rolls pay 2 orders + 2 slippages) and Rs with lots re-sized at
    every roll to the same leverage."""
    capital = capital or CONFIG["capital"]
    slip = CONFIG["slippage_main"] if slip is None else slip
    seg = fut.seg
    starts = np.r_[0, np.nonzero(np.diff(seg))[0] + 1]
    ends = np.r_[starts[1:] - 1, fut.n - 1]
    pts_day = np.zeros(len(fut.days))
    last_close = fut.c[fut.last]
    pts_total, eq = 0.0, capital
    rs_day = np.zeros(len(fut.days))
    for a, z in zip(starts, ends):
        lot = int(lot_size(np.array([fut.dt[a]], "datetime64[D]"))[0])
        raw = fut.o[a] - fut.off[a]
        lots = max(0, math.floor(eq * leverage / (raw * lot)))
        c = _order("fut", np.array([raw * lot]), np.array([True]), np.array([fut.dt[a]], "datetime64[D]"), CONFIG["rates_mode"])[0]
        c += _order("fut", np.array([raw * lot]), np.array([False]), np.array([fut.dt[z]], "datetime64[D]"), CONFIG["rates_mode"])[0]
        de, dz = int(fut.di[a]), int(fut.di[z])
        marks = np.r_[fut.o[a], last_close[de:dz + 1]]
        mv = np.diff(marks)
        cost_pts = 2 * slip + c / lot
        pts_day[de:dz + 1] += mv
        pts_day[dz] -= cost_pts
        rs_day[de:dz + 1] += lots * lot * mv
        rs_day[dz] -= lots * (2 * slip * lot + c)
        eq += lots * lot * (marks[-1] - marks[0]) - lots * (2 * slip * lot + c)
        pts_total += marks[-1] - marks[0] - cost_pts
    idx = pd.DatetimeIndex(fut.days)
    return {"pts_day": pd.Series(pts_day, index=idx), "rs_day": pd.Series(rs_day, index=idx), "net_pts": pts_total}


def yearly(df: pd.DataFrame, pts_day: pd.Series, rs_day: pd.Series | None = None, capital: float | None = None) -> pd.DataFrame:
    capital = capital or CONFIG["capital"]
    rows = []
    for y in sorted(set(pts_day.index.year)):
        t = df[df["exit_dt"].dt.year == y]
        s = stats(t, 1.0) if len(t) else {"trades": 0}
        row = {"year": y, "net_pts": float(pts_day[pts_day.index.year == y].sum()), "trades": s.get("trades", 0), "win_pct": s.get("win_pct"),
               "pf": s.get("pf"), "best": s.get("best"), "cost_pts": s.get("cost_pts", 0) + s.get("slip_pts", 0)}
        if rs_day is not None:
            start_eq = capital + rs_day[rs_day.index.year < y].sum()
            row["net_rs"] = float(rs_day[rs_day.index.year == y].sum())
            row["return_pct"] = row["net_rs"] / start_eq * 100 if start_eq > 0 else None
        rows.append(row)
    return pd.DataFrame(rows)

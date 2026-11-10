"""Money layer: sizing, charges, daily loss cap, drawdown kill switch, daily mark-to-market equity.

    acc = portfolio.apply(trades, bars, risk_pct=0.25, kill=False)    # every candidate trade, with taken / skip_reason
    eq  = portfolio.daily_equity(acc[acc.taken], bars, days)          # Rs P&L per trading day (open positions marked at the close)
"""
import heapq

import numpy as np
import pandas as pd

from backtest.podcast import costs
from backtest.podcast.config import CONFIG


def size(trades: pd.DataFrame, risk_pct: float, capital: float) -> np.ndarray:
    """Units (shares / index units) so that the stop loses risk_pct % of capital; one position <= max_notional_x x capital."""
    risk_rs = capital * risk_pct / 100.0
    by_risk = risk_rs / trades["risk_pts"].to_numpy(float)
    by_cap = CONFIG["max_notional_x"] * capital / trades["entry_raw"].to_numpy(float)
    u = np.floor(np.minimum(by_risk, by_cap))
    return np.where(np.isfinite(u), u, 0.0)


def charges(trades: pd.DataFrame, units: np.ndarray) -> pd.DataFrame:
    """Charges per trade (Rs) for its orders: entry, every exit leg, two orders per roll."""
    ti, raw, frac, buy, day, kind = [], [], [], [], [], []
    for k, (side, er, edt, legs, rolls, kd) in enumerate(zip(trades["side"], trades["entry_raw"], trades["entry_dt"], trades["legs"], trades["rolls"], trades["kind"])):
        ti.append(k); raw.append(er); frac.append(1.0); buy.append(side > 0); day.append(edt); kind.append(kd)
        for (_, _, lraw, f, _, ldt) in legs:
            ti.append(k); raw.append(lraw); frac.append(f); buy.append(side < 0); day.append(ldt); kind.append(kd)
        for (_, rold, rnew, f) in rolls:
            ti += [k, k]; raw += [rold, rnew]; frac += [f, f]; buy += [side < 0, side > 0]; day += [edt, edt]; kind += [kd, kd]
    ti = np.asarray(ti)
    val = units[ti] * np.asarray(raw, float) * np.asarray(frac, float)
    buy = np.asarray(buy)
    days = pd.to_datetime(pd.Series(day)).to_numpy("datetime64[D]")
    kind = np.asarray(kind)
    out = {k: np.zeros(len(trades)) for k in ("brokerage", "stt", "exchange", "stamp", "gst", "total")}
    for kd in np.unique(kind):
        m = kind == kd
        c = costs.order_costs(kd, val[m], buy[m], days[m])
        for k in out:
            out[k] += np.bincount(ti[m], weights=c[k], minlength=len(trades))
    return pd.DataFrame(out, index=trades.index)


def apply(trades: pd.DataFrame, risk_pct: float | None = None, capital: float | None = None, kill: bool = False,
          daily_cap: bool = True) -> pd.DataFrame:
    """Size every candidate trade, charge it, then walk them in entry-time order:
    - daily loss cap: realised P&L of the day (closed trades + partial exits) <= -cap -> skip new trades that day
    - kill switch (kill=True): realised equity >= dd_kill_pct below its peak -> no new trades for dd_restart_days
      trading days, then restart with the peak reset to the equity at that time.
    Returns the trades with units, charges, gross / net Rs, R, taken (bool) and skip_reason."""
    if trades is None or not len(trades):
        return pd.DataFrame()
    capital = capital or CONFIG["capital"]
    risk_pct = CONFIG["risk_pct"] if risk_pct is None else risk_pct
    t = trades.copy()
    u = size(t, risk_pct, capital)
    t["units"] = u
    ch = charges(t, u)
    for k in ch:
        t[f"c_{k}"] = ch[k].to_numpy()
    t["fees"] = t["c_total"]
    t["gross"] = u * t["pnl_pts"].to_numpy()
    t["slippage"] = u * t["slip_pts"].to_numpy()
    t["net"] = t["gross"] - t["fees"]
    t["risk_rs"] = u * t["risk_pts"].to_numpy()
    t["R"] = np.where(t["risk_rs"] > 0, t["net"] / t["risk_rs"], np.nan)
    t["notional"] = u * t["entry_raw"].to_numpy()
    t["taken"] = u >= 1
    t["skip_reason"] = np.where(u >= 1, "", "size < 1 unit")
    cap_rs = capital * CONFIG["daily_loss_cap_pct"] / 100.0
    dd_lim = CONFIG["dd_kill_pct"] / 100.0
    restart_n = CONFIG["dd_restart_days"]
    order = np.argsort(t["entry_dt"].to_numpy(), kind="mergesort")
    edt = t["entry_dt"].to_numpy("datetime64[ns]")
    eday = edt.astype("datetime64[D]")
    cal = np.unique(np.concatenate([eday, t["exit_dt"].to_numpy("datetime64[ns]").astype("datetime64[D]")]))
    taken = t["taken"].to_numpy().copy()
    reason = t["skip_reason"].to_numpy().astype(object)
    net = t["net"].to_numpy()
    fees = t["fees"].to_numpy()
    side = t["side"].to_numpy()
    epx = t["entry_px"].to_numpy()
    legs = t["legs"].to_numpy()
    roll_slip = t["roll_slip"].to_numpy() if "roll_slip" in t else np.zeros(len(t))
    heap = []                                   # (time, seq, Rs) realisation events of taken trades
    seq = 0
    day_real: dict = {}
    equity, peak = capital, capital
    killed_until = None
    kill_events = []
    for k in order:
        now = edt[k]
        while heap and heap[0][0] <= now:
            tm, _, rs = heapq.heappop(heap)
            d = tm.astype("datetime64[D]")
            day_real[d] = day_real.get(d, 0.0) + rs
            equity += rs
            if equity > peak:
                peak = equity
            if kill and killed_until is None and peak - equity >= dd_lim * peak:
                i = int(np.searchsorted(cal, d))
                killed_until = cal[min(i + restart_n, len(cal) - 1)] if i + restart_n < len(cal) else np.datetime64("2100-01-01")
                kill_events.append((str(d), float(equity), float(peak)))
        if not taken[k]:
            continue
        if killed_until is not None:
            if eday[k] >= killed_until:
                killed_until = None
                peak = equity
            else:
                taken[k] = False
                reason[k] = "kill switch (5% drawdown)"
                continue
        if daily_cap and day_real.get(eday[k], 0.0) <= -cap_rs:
            taken[k] = False
            reason[k] = "daily loss cap"
            continue
        # realisation events: each leg its share of gross, charges and roll slippage pro rata
        u_k = u[k]
        lg = legs[k]
        tot_f = sum(x[3] for x in lg) or 1.0
        for (_, px, _, f, _, ldt) in lg:
            rs = u_k * f * side[k] * (px - epx[k]) - (fees[k] + u_k * roll_slip[k]) * f / tot_f
            heapq.heappush(heap, (np.datetime64(ldt, "ns"), seq, rs))
            seq += 1
    t["taken"] = taken
    t["skip_reason"] = reason
    t.attrs["kill_events"] = kill_events
    t.attrs["risk_pct"] = risk_pct
    return t


def daily_equity(acc: pd.DataFrame, bars: dict, days: np.ndarray) -> pd.Series:
    """Rs P&L per trading day of the taken trades; open positions are marked at each day's last close (adjusted prices,
    so roll gaps never count). Charges and roll slippage are booked on the exit day. Index = days (datetime64[D])."""
    days = np.asarray(days, "datetime64[D]")
    pnl = np.zeros(len(days))
    if acc is None or not len(acc):
        return pd.Series(pnl, index=pd.DatetimeIndex(days))
    pos = {d: i for i, d in enumerate(days)}
    for r in acc.itertuples(index=False):
        b = bars[r.inst]
        u, s = r.units, r.side
        de, dx = int(b.di[r.entry_i]), int(b.di[r.exit_i])
        if de == dx:
            k = pos.get(b.days[de])
            if k is not None:
                pnl[k] += r.net
            continue
        closes = b.c[b.last[de:dx]]                     # day-end closes from entry day to the day before exit
        marks = np.r_[r.entry_px, closes]
        steps = s * u * np.diff(marks)
        for off_d, v in enumerate(steps):
            k = pos.get(b.days[de + off_d])
            if k is not None:
                pnl[k] += v
        last_leg = r.legs[-1]
        k = pos.get(b.days[dx])
        if k is not None:
            # exit day: move from the last mark to the exit fills (partial legs weighted), minus charges and roll slippage
            moved = sum(f * s * (px - closes[-1]) for (_, px, _, f, _, _) in r.legs)
            pnl[k] += u * moved - r.fees - u * r.roll_slip
    return pd.Series(pnl, index=pd.DatetimeIndex(days))


def net_R(trades: pd.DataFrame, risk_pct: float | None = None, capital: float | None = None) -> np.ndarray:
    """Fast path for the random control: net R per trade (sizing + charges, no daily cap / kill switch walk)."""
    if trades is None or not len(trades):
        return np.array([])
    capital = capital or CONFIG["capital"]
    risk_pct = CONFIG["risk_pct"] if risk_pct is None else risk_pct
    u = size(trades, risk_pct, capital)
    fees = charges(trades, u)["total"].to_numpy()
    net = u * trades["pnl_pts"].to_numpy() - fees
    risk = u * trades["risk_pts"].to_numpy()
    ok = u >= 1
    return net[ok] / risk[ok]

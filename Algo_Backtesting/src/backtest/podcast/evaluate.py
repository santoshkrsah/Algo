"""Evaluation: every metric together, year / month tables, plateau vs overfit on a parameter grid, random-entry control.

Plateau rule (per grid cell, in-sample): the cell AND every neighbour (one step up / down in each numeric parameter,
other settings unchanged) must have profit factor > 1 after costs -> PLATEAU. Cell profitable but a neighbour not ->
OVERFIT (works at one point only). Cell not profitable -> LOSS. plateau_score = lowest PF among the cell and its
neighbours; the chosen setting is the in-sample cell with the best plateau_score (never the single best cell).
"""
import math

import numpy as np
import pandas as pd

from backtest.podcast.config import CONFIG


def trade_stats(t: pd.DataFrame) -> dict:
    """Trade metrics on taken trades (net = after slippage and charges)."""
    n = len(t)
    if not n:
        return {"trades": 0}
    net = t["net"].to_numpy()
    w, ls = net[net > 0], net[net <= 0]
    gp, gl = w.sum(), -ls.sum()
    k = max(1, math.ceil(0.05 * n))
    top = np.sort(net)[::-1][:k].sum()
    tot = net.sum()
    r = t["R"].to_numpy()
    out = {"trades": n, "net": float(tot), "win_pct": len(w) / n * 100, "avg_win": float(w.mean()) if len(w) else 0.0,
           "avg_loss": float(ls.mean()) if len(ls) else 0.0, "pf": float(gp / gl) if gl > 0 else float("inf"),
           "exp_rs": float(net.mean()), "exp_R": float(np.nanmean(r)) if np.isfinite(r).any() else np.nan,
           "fees": float(t["fees"].sum()), "slippage": float(t["slippage"].sum()), "gross": float(t["gross"].sum()),
           "best5_share": float(top / tot * 100) if tot > 0 else np.nan,
           "long_n": int((t["side"] > 0).sum()), "long_net": float(t.loc[t["side"] > 0, "net"].sum()),
           "short_n": int((t["side"] < 0).sum()), "short_net": float(t.loc[t["side"] < 0, "net"].sum())}
    out["rr"] = out["avg_win"] / abs(out["avg_loss"]) if out["avg_loss"] < 0 else np.nan
    rs = t["R"].dropna()
    out["t_stat"] = float(rs.mean() / rs.std(ddof=1) * math.sqrt(len(rs))) if len(rs) > 2 and rs.std(ddof=1) > 0 else np.nan
    return out


def equity_stats(day_pnl: pd.Series, capital: float) -> dict:
    """Return, CAGR, max drawdown (% of peak equity and Rs), time drawdown (longest stretch between equity highs)."""
    if day_pnl is None or not len(day_pnl):
        return {}
    eq = capital + day_pnl.cumsum()
    curve = np.r_[capital, eq.to_numpy()]
    peak = np.maximum.accumulate(curve)
    dd_rs = peak - curve
    dd_pct = dd_rs / peak * 100
    days = eq.index
    span = (days[-1] - days[0]).days or 1
    end = eq.iloc[-1]
    # time drawdown: longest run of days below the previous equity high
    high = curve[1:] >= peak[1:] - 1e-9
    longest, start_i, run_start = 0, None, None
    best = (0, None, None)
    for i, hi in enumerate(high):
        if hi:
            if run_start is not None and i - run_start > best[0]:
                best = (i - run_start, run_start, i)
            run_start = None
        elif run_start is None:
            run_start = i
    if run_start is not None and len(high) - run_start > best[0]:
        best = (len(high) - run_start, run_start, len(high) - 1)
    td_days, a, b = best
    td_months = ((days[b] - days[max(a - 1, 0)]).days / 30.44) if a is not None else 0.0
    sd = day_pnl.std(ddof=1)
    below = np.nonzero(eq.to_numpy() <= 0)[0]
    return {"max_dd_cap_pct": float(dd_rs.max() / capital * 100), "min_equity": float(eq.min()),
            "ruin_date": str(days[below[0]].date()) if len(below) else None,
            "total_return_pct": (end - capital) / capital * 100, "cagr_pct": ((end / capital) ** (365.25 / span) - 1) * 100 if end > 0 else -100.0,
            "max_dd_pct": float(dd_pct.max()), "max_dd_rs": float(dd_rs.max()), "time_dd_days": int(td_days), "time_dd_months": float(td_months),
            "time_dd_from": str(days[max(a - 1, 0)].date()) if a is not None else None, "time_dd_to": str(days[b].date()) if a is not None else None,
            "sharpe": float(day_pnl.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0, "end_equity": float(end)}


def yearly(t: pd.DataFrame, day_pnl: pd.Series, capital: float) -> pd.DataFrame:
    rows = []
    yrs = sorted(set(day_pnl.index.year))
    for y in yrs:
        d = day_pnl[day_pnl.index.year == y]
        tt = t[pd.to_datetime(t["exit_dt"]).dt.year == y]
        e = equity_stats(d, capital)
        s = trade_stats(tt)
        rows.append({"year": y, "net": float(d.sum()), "return_pct": float(d.sum() / capital * 100), "trades": s.get("trades", 0),
                     "win_pct": s.get("win_pct", np.nan), "pf": s.get("pf", np.nan), "max_dd_pct": e.get("max_dd_pct", np.nan)})
    y = pd.DataFrame(rows)
    if len(y):
        med = y["net"].abs().median()
        y["flag"] = np.where((y["net"] < 0) & (y["net"].abs() > CONFIG["big_loss_year_x"] * med), "BIG LOSS YEAR", "")
    return y


def monthly(t: pd.DataFrame, day_pnl: pd.Series, capital: float) -> pd.DataFrame:
    m = day_pnl.groupby(day_pnl.index.to_period("M")).sum()
    tt = t.assign(m=pd.to_datetime(t["exit_dt"]).dt.to_period("M"))
    g = tt.groupby("m")["net"]
    out = pd.DataFrame({"month": m.index.astype(str), "net": m.to_numpy(), "return_pct": m.to_numpy() / capital * 100})
    out["trades"] = out["month"].map(g.size().rename(index=str)).fillna(0).astype(int)
    out["win_pct"] = out["month"].map(g.apply(lambda x: (x > 0).mean() * 100).rename(index=str))
    return out


def split_day(days: np.ndarray) -> pd.Timestamp:
    """First out-of-sample day: CONFIG is_frac of the trading days are in-sample."""
    days = np.asarray(days, "datetime64[D]")
    return pd.Timestamp(days[int(len(days) * CONFIG["is_frac"])])


def plateau(grid: pd.DataFrame, dims: list[str], fixed: list[str], value: str = "is_pf", min_trades: int | None = None) -> pd.DataFrame:
    """Adds status (PLATEAU / OVERFIT / LOSS / FEW TRADES), plateau_score (lowest `value` over the cell and its
    neighbours) and n_neighbours. dims = numeric grid parameters (neighbours = one step in one of them); fixed = the
    categorical settings that must match."""
    g = grid.copy()
    mt = CONFIG["min_is_trades"] if min_trades is None else min_trades
    levels = {d: sorted(g[d].unique()) for d in dims}
    pos = {d: {v: i for i, v in enumerate(levels[d])} for d in dims}
    key = {tuple(r[c] for c in fixed + dims): i for i, r in g.iterrows()}
    score, status, nn = [], [], []
    for i, r in g.iterrows():
        vals = [r[value]]
        nb = 0
        for d in dims:
            for step in (-1, 1):
                j = pos[d][r[d]] + step
                if 0 <= j < len(levels[d]):
                    k = tuple(r[c] for c in fixed) + tuple(levels[x][j] if x == d else r[x] for x in dims)
                    if k in key:
                        vals.append(g.loc[key[k], value])
                        nb += 1
        v = np.array(vals, float)
        v = np.where(np.isfinite(v), v, 0.0)
        score.append(float(v.min()))
        nn.append(nb)
        if r.get("is_trades", mt) < mt:
            status.append("FEW TRADES")
        elif not (r[value] > 1.0):
            status.append("LOSS")
        elif (v > 1.0).all():
            status.append("PLATEAU")
        else:
            status.append("OVERFIT")
    g["plateau_score"] = score
    g["status"] = status
    g["n_neighbours"] = nn
    return g


def choose(grid: pd.DataFrame) -> pd.Series:
    """In-sample choice: best plateau_score among cells with enough trades (PLATEAU preferred), ties -> more trades."""
    ok = grid[grid["status"] != "FEW TRADES"]
    if not len(ok):
        ok = grid
    pl = ok[ok["status"] == "PLATEAU"]
    pool = pl if len(pl) else ok
    return pool.sort_values(["plateau_score", "is_trades"], ascending=False).iloc[0]


def pvalue(rule: float, sims: np.ndarray) -> float:
    sims = np.asarray(sims, float)
    sims = sims[np.isfinite(sims)]
    return float((np.sum(sims >= rule) + 1) / (len(sims) + 1)) if len(sims) else np.nan


def split_stats(acc: pd.DataFrame, split: pd.Timestamp) -> dict:
    """Trade stats in-sample / out-of-sample / full for taken trades (by entry time)."""
    t = acc[acc["taken"]] if "taken" in acc else acc
    e = pd.to_datetime(t["entry_dt"])
    return {"is": trade_stats(t[e < split]), "oos": trade_stats(t[e >= split]), "full": trade_stats(t)}

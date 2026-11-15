"""Metrics, plateau test, regimes, random control for the multi-timeframe Supertrend backtest.

Point metrics (per 1 unit of NIFTY futures, after charges + slippage) reuse backtest.stma.evaluate (same definitions as
report 25): win rate, avg win / loss, PF, expectancy, trades / year, best-5% share, max drawdown and time drawdown on the
daily mark-to-market curve. Rupee metrics come from the sized account (money.size / money.daily_rs).
"""
import numpy as np
import pandas as pd

from backtest.mtst import engine, money
from backtest.mtst.config import CONFIG
from backtest.stma import evaluate as sev

stats = sev.stats
curve_stats = sev.curve_stats
daily_points = sev.daily_points


def split_stats(df, split, span_is, span_oos) -> dict:
    if not len(df):
        z = {"trades": 0}
        return {"is": z, "oos": z, "full": z}
    e = df["entry_dt"]
    a, b = df[e < split], df[e >= split]
    return {"is": stats(a, span_is) if len(a) else {"trades": 0}, "oos": stats(b, span_oos) if len(b) else {"trades": 0},
            "full": stats(df, span_is + span_oos)}


def rupee_stats(sized: pd.DataFrame, fut, capital=None) -> tuple[dict, pd.Series]:
    capital = capital or CONFIG["capital"]
    rs_day = money.daily_rs(sized, fut)
    cs = curve_stats(rs_day, capital)
    t = sized[sized["lots"] > 0]
    out = {"net_rs": float(rs_day.sum()), **cs, "trades_taken": int(len(t)), "skipped": int((sized["lots"] == 0).sum()),
           "capped": int(sized["capped"].sum()), "dd_half": int(sized["dd_half"].sum()),
           "avg_lots": float(t["lots"].mean()) if len(t) else 0.0, "max_lots": int(t["lots"].max()) if len(t) else 0,
           "avg_risk_pct": float((t["risk_rs"] / t["equity_in"]).mean() * 100) if len(t) else 0.0,
           "max_risk_pct": float((t["risk_rs"] / t["equity_in"]).max() * 100) if len(t) else 0.0,
           "avg_notional": float((t["lots"] * t["lot"] * t["entry_raw"]).mean()) if len(t) else 0.0,
           "peak_notional": float((t["lots"] * t["lot"] * t["entry_raw"]).max()) if len(t) else 0.0,
           "peak_margin": float((t["lots"] * CONFIG["margin_per_lot"]).max()) if len(t) else 0.0}
    w, l = t.loc[t.pnl_rs > 0, "pnl_rs"], t.loc[t.pnl_rs <= 0, "pnl_rs"]
    out.update({"win_pct_rs": len(w) / len(t) * 100 if len(t) else np.nan, "pf_rs": float(w.sum() / -l.sum()) if l.sum() < 0 else np.inf,
                "avg_win_rs": float(w.mean()) if len(w) else 0.0, "avg_loss_rs": float(l.mean()) if len(l) else 0.0,
                "exp_rs": float(t["pnl_rs"].mean()) if len(t) else 0.0})
    return out, rs_day


# ------------------------------------------------------------------------------------------------------ plateau
DIMS = ["h_n", "h_mult", "d_n", "d_mult"]


def plateau(g: pd.DataFrame, value: str = "is_pf") -> pd.DataFrame:
    """PLATEAU = the cell and EVERY neighbour one grid step away (in any of the 4 dimensions) have fit PF > 1.
    OVERFIT = the cell is profitable but at least one neighbour is not. plateau_score = lowest fit PF among them."""
    g = g.copy().reset_index(drop=True)
    lv = {d: sorted(g[d].unique()) for d in DIMS}
    pos = {d: {v: i for i, v in enumerate(lv[d])} for d in DIMS}
    key = {tuple(r[d] for d in DIMS): i for i, r in g.iterrows()}
    sc, st, nb_ok = [], [], []
    for _, r in g.iterrows():
        vals = []
        for d in DIMS:
            for s in (-1, 1):
                j = pos[d][r[d]] + s
                if 0 <= j < len(lv[d]):
                    k = tuple(lv[x][j] if x == d else r[x] for x in DIMS)
                    if k in key:
                        vals.append(g.loc[key[k], value])
        nb = np.nan_to_num(np.array(vals, float), nan=0.0, posinf=9.99)
        me = float(np.nan_to_num(r[value], nan=0.0, posinf=9.99))
        sc.append(float(min([me] + nb.tolist())))
        nb_ok.append(f"{int((nb > 1).sum())}/{len(nb)}")
        if r["is_trades"] < CONFIG["min_is_trades"]:
            st.append("FEW TRADES")
        elif not me > 1:
            st.append("LOSS")
        elif (nb > 1).all():
            st.append("PLATEAU")
        else:
            st.append("OVERFIT")
    g["plateau_score"], g["status"], g["neighbours_ok"] = sc, st, nb_ok
    return g


# ------------------------------------------------------------------------------------------------------ regimes
def regimes(df: pd.DataFrame, P, vix: pd.Series) -> pd.DataFrame:
    """Label each trade: daily-trend stability (daily Supertrend flips in the last N completed daily candles before the
    entry) and India VIX (previous day's close, so no look-ahead)."""
    R = CONFIG["regime"]
    dd = P.d_dir_all.astype(int)
    flips = np.r_[0, (np.diff(dd) != 0) & (dd[1:] != 0) & (dd[:-1] != 0)].astype(int)
    cum = np.cumsum(flips)
    di = P.di[df["k_in"].to_numpy()]
    lb = R["flip_lookback"]
    nfl = cum[di] - cum[np.clip(di - lb, 0, None)]
    df = df.copy()
    df["daily_flips"] = nfl
    df["trend_regime"] = np.where(nfl <= R["trend_max_flips"], "trending", np.where(nfl >= R["chop_min_flips"], "choppy", "mixed"))
    v = vix.sort_index()
    vprev = v.shift(1)
    d = pd.DatetimeIndex(df["entry_dt"]).normalize()
    vals = vprev.reindex(d, method="ffill").to_numpy()
    df["vix_prev"] = vals
    a, b = R["vix_cuts"]
    df["vix_regime"] = np.where(vals < a, f"VIX < {a:g}", np.where(vals <= b, f"VIX {a:g}-{b:g}", f"VIX > {b:g}"))
    return df


def regime_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in ("trend_regime", "vix_regime"):
        for k, g in df.groupby(col):
            s = stats(g, None)
            rows.append({"by": "daily trend stability" if col == "trend_regime" else "India VIX", "regime": k, "trades": s["trades"],
                         "net_pts": s["net_pts"], "pf": s["pf"], "win_pct": s["win_pct"], "exp_pts": s["exp_pts"],
                         "net_rs": float(g["pnl_rs"].sum()) if "pnl_rs" in g else np.nan})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------------ random control
def random_control(M, P, p, start, n_rule: int, rule_mean: float, kind: str, sims: int, rng, slip=None) -> dict:
    """Random entry candles (kind 'trend': direction = the 1-hour Supertrend direction then; 'coin': random direction),
    the SAME exit rule, the same number of trades. p-value = share of runs whose mean net points / trade >= the rule's."""
    H = P.H
    elig = np.nonzero((H.day >= np.datetime64(start, "D")) & (H.fill_next >= 0) & np.isfinite(P.h_line) & (P.hd != 0))[0]
    means, totals = np.full(sims, np.nan), np.full(sims, np.nan)
    for s in range(sims):
        take = n_rule * 3
        perm = rng.permutation(elig)
        while True:
            ks = np.sort(perm[:take])
            dirs = P.hd[ks].astype(int) if kind == "trend" else rng.choice([-1, 1], size=len(ks))
            tr = engine.run(M, P, p, start, forced=dict(zip(ks.tolist(), dirs.tolist())))
            if len(tr) >= n_rule or take >= len(perm):
                break
            take = min(len(perm), take * 2)
        if len(tr) > n_rule:
            tr = tr.iloc[np.sort(rng.choice(len(tr), size=n_rule, replace=False))]
        tr = money.apply_costs(tr, M["fut"], slip)
        means[s], totals[s] = tr["net_pts"].mean(), tr["net_pts"].sum()
    ok = np.isfinite(means)
    return {"kind": kind, "sims_mean": means, "sims_total": totals, "mean": float(np.nanmean(means)), "p95": float(np.nanpercentile(means, 95)),
            "p": float((np.sum(means[ok] >= rule_mean) + 1) / (ok.sum() + 1)), "rule_mean": rule_mean, "n_rule": n_rule}

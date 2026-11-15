"""Day loop (loads every day's 1m candles once, runs all stock strategies + variants on it), NIFTY momentum, money.

    C = collect(out)          # candidate trades of every strategy / variant (cached in out/candidates.pkl)
    B = books(C)              # {(strat, var, slip): trade table}
"""
import pickle
import time

import numpy as np
import pandas as pd

from backtest import fast
from backtest.groww5 import data, money, signals
from backtest.groww5.config import CONFIG, cfg

# variant name -> config overrides. "base" = the rules in GROWW5_INTRADAY_RULES.md (fixed before any run, not tuned)
VARIANTS = {
    "scalp": {"base": {}, "no_psar": {"scalp": {"use_psar": False}}, "hold3": {"scalp": {"hold": 3}}},
    "zones": {"base": {}, "pullback": {"zones": {"brk_mode": "pullback"}}, "no_touch": {"zones": {"use_touch": False}}, "tf5": {"zones": {"tf": 5}}},
    "news": {"base": {}, "gap3": {"news": {"gap_pct": 3.0}}, "no_color": {"news": {"need_color": False}}},
    "momo": {"base": {}, "long_only": {"momo": {"sides": "long"}}},
}
VAR_LABEL = {"base": "Base (video rules + hamare default)", "no_psar": "PSAR filter ke bina", "hold3": "3 min hold (5 ki jagah)",
             "pullback": "Breakout: pullback ke baad entry", "no_touch": "Touch rule ke bina (har zone >= 2 touch)", "tf5": "5-min candle (15 ki jagah)",
             "gap3": "Gap >= 3%", "no_color": "Pehli candle ka rang na dekho", "long_only": "Sirf long (video jaisa)"}


def collect(out, days=None, syms=None, log=print) -> pd.DataFrame:
    """Stock by stock (all its 1m days at once), then NIFTY. days / syms given = quick test, nothing cached."""
    p = out / "candidates.pkl"
    test = days is not None or syms is not None
    if not test and p.exists():
        return pd.read_pickle(p)
    syms = syms or data.universe()
    d0, d1 = (days[0], days[-1]) if days else (CONFIG["data"]["start"], CONFIG["data"]["end"])
    t0 = time.time()
    Z = data.zone_daily(syms)
    zt = signals.zone_table(Z, CONFIG["zones"], d0)
    zt = zt[zt["date"] <= d1]
    zt_by = {s: g for s, g in zt.groupby("symbol")}
    log(f"zones ready: {len(zt):,} stock-days, {time.time() - t0:.0f}s")
    P = fast.add_features(fast.panel(CONFIG["data"]["start"], d1, columns=["open", "high", "low", "close", "volume", "value",
                                                                             "o5_open", "o5_high", "o5_low", "o5_close", "o5_vol", "n_bars"]))
    P["date"] = pd.to_datetime(P["date"]).dt.strftime("%Y-%m-%d")
    P = P[P["symbol"].isin(syms) & (P["date"] >= d0)]
    p_by = {s: g for s, g in P.groupby("symbol")}
    cs = {k: cfg(**v) for k, v in VARIANTS["scalp"].items()}
    cz = {k: cfg(**v) for k, v in VARIANTS["zones"].items()}
    cn = {k: cfg(**v) for k, v in VARIANTS["news"].items()}
    rows, short = [], []
    for n, sym in enumerate(syms):
        df = fast.symbol_1m(sym, d0, d1)
        if df.empty:
            continue
        G = data.grid(df, sym)
        ok = np.isfinite(G.c).sum(1) >= 300                  # Muhurat / special / broken sessions out
        short += [(sym, d) for d in G.days[~ok]]
        G = data.Grid(G.syms[ok], G.days[ok], G.o[ok], G.h[ok], G.l[ok], G.c[ok], G.v[ok])
        G = signals.ffill_grid(G)
        ps = signals.psar(G.h, G.l, *CONFIG["scalp"]["psar"])
        for v, c in cs.items():
            rows += signals.scalp(G, ps, c["scalp"], v)
        zd = zt_by.get(sym)
        for v, c in cz.items():
            rows += signals.zones(G, zd, c["zones"], v)
        pd_ = p_by.get(sym)
        if pd_ is not None:
            for v, c in cn.items():
                rows += signals.news(G, pd_, c["news"], v)
        if n % 10 == 0:
            log(f"stock {n + 1}/{len(syms)} {sym}: {len(rows):,} candidates, {time.time() - t0:.0f}s")
    if not test or days is not None:
        F = data.nifty()
        if test:
            F = F.window(d0, d1)
        for v, ov in VARIANTS["momo"].items():
            rows += signals.momo(F, cfg(**ov)["momo"], v)
    C = pd.DataFrame(rows)
    C.attrs["short_rows"] = short
    if not test:
        C.to_pickle(p)
        with open(out / "short_rows.pkl", "wb") as f:
            pickle.dump(short, f)
    return C


def books(C: pd.DataFrame) -> dict:
    """Trade tables for every strategy x variant at normal and 2x slippage."""
    out = {}
    for strat in ("scalp", "momo", "brk", "rev", "news"):
        fam = {"brk": "zones", "rev": "zones"}.get(strat, strat)
        for var in VARIANTS[fam]:
            if strat == "rev" and var == "pullback":
                continue
            c = C[(C["strat"] == strat) & (C["var"] == var)]
            for slip in (1.0, CONFIG["stress"]):
                out[(strat, var, slip)] = money.book(c, strat, slip)
    return out

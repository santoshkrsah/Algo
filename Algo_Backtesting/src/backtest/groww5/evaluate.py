"""Numbers for the report: stats per period / year / month / side / exit / stock, variants, stress, verdict."""
import numpy as np
import pandas as pd

from backtest.groww5.config import CONFIG, STRATS

CAP = CONFIG["money"]["capital"]


def periods(strat: str):
    d = CONFIG["data"]
    end = d["end"]
    ps = [("Build (DEV) 2021-10 - 2022", d["start"], d["dev_end"]), ("Validation 2023", "2023-01-01", d["val_end"]),
          ("Latest / FINAL 2024 - 2026", "2024-01-01", end), ("Poora 2021-10 - 2026", d["start"], end)]
    if strat == "momo":
        ps = [("Purana 2018-02 - 2021-09", d["fut_start"], "2021-09-30")] + ps + [("Poora 2018 - 2026 (sirf NIFTY)", d["fut_start"], end)]
    return ps


def daily(t: pd.DataFrame, days) -> pd.Series:
    s = t.groupby("date")["net"].sum() if len(t) else pd.Series(dtype=float)
    return s.reindex(days, fill_value=0.0)


def max_dd(pnl: pd.Series) -> float:
    eq = pnl.cumsum().to_numpy()
    if not len(eq):
        return 0.0
    peak = np.maximum.accumulate(np.r_[0.0, eq])[1:]
    return float((peak - eq).max())


def stats(t: pd.DataFrame, days) -> dict:
    n = len(t)
    net = t["net"].to_numpy() if n else np.array([])
    w, l_ = net[net > 0], net[net <= 0]
    d = daily(t, days)
    sd = d.std()
    years = max(len(days) / 248, 1e-9)
    gross0 = float(t["gross0"].sum()) if n else 0.0
    cost = float((t["slip_rs"] + t["charges"]).sum()) if n else 0.0
    return {
        "trades": n, "per_year": n / years, "win": float(np.mean(net > 0) * 100) if n else None, "net": float(net.sum()),
        "ret_pct": float(net.sum() / CAP * 100), "cagr_simple": float(net.sum() / CAP * 100 / years),
        "pf": float(w.sum() / -l_.sum()) if len(l_) and l_.sum() < 0 else None, "avg": float(net.mean()) if n else None,
        "avg_win": float(w.mean()) if len(w) else None, "avg_loss": float(l_.mean()) if len(l_) else None,
        "avg_R": float(t["R"].mean()) if n else None, "max_dd": max_dd(d), "dd_pct": max_dd(d) / CAP * 100,
        "sharpe": float(d.mean() / sd * np.sqrt(248)) if sd > 0 else None,
        "tstat": float(net.mean() / (net.std(ddof=1) / np.sqrt(n))) if n > 2 and net.std() > 0 else None,
        "gross0": gross0, "slip": float(t["slip_rs"].sum()) if n else 0.0, "charges": float(t["charges"].sum()) if n else 0.0,
        "cost_per_trade": cost / n if n else None, "gross_per_trade": gross0 / n if n else None,
        "best": float(net.max()) if n else None, "worst": float(net.min()) if n else None,
        "days_traded": int(t["date"].nunique()) if n else 0,
    }


def cut(t: pd.DataFrame, a, b):
    return t[(t["date"] >= a) & (t["date"] <= b)]


def days_in(all_days, a, b):
    return [d for d in all_days if a <= d <= b]


def verdict(strat, by_period: dict, stress_full: dict) -> dict:
    keys = [p[0] for p in periods(strat)]
    full = by_period[[k for k in keys if k.startswith("Poora 2021")][0]]
    val, lat = by_period["Validation 2023"], by_period["Latest / FINAL 2024 - 2026"]
    checks = [
        ["Poore period me net profit", full["net"] > 0, f"Rs {full['net']:,.0f}"],
        ["Validation 2023 me profit", val["net"] > 0, f"Rs {val['net']:,.0f}"],
        ["Latest 2024-2026 me profit", lat["net"] > 0, f"Rs {lat['net']:,.0f}"],
        ["Latest profit factor >= 1.2", (lat["pf"] or 0) >= 1.2, f"{lat['pf'] or 0:.2f}"],
        ["2x slippage pe bhi profit", stress_full["net"] > 0, f"Rs {stress_full['net']:,.0f}"],
        ["Kam se kam 100 trade", full["trades"] >= 100, f"{full['trades']}"],
        ["Average gross trade >= 2x average kharcha", (full["gross_per_trade"] or 0) >= 2 * (full["cost_per_trade"] or 1e9),
         f"gross Rs {full['gross_per_trade'] or 0:,.0f} vs kharcha Rs {full['cost_per_trade'] or 0:,.0f}"],
    ]
    ok = all(c[1] for c in checks)
    return {"pass": ok, "checks": checks}


def breakdown(t: pd.DataFrame, key) -> list:
    rows = []
    if not len(t):
        return rows
    for k, g in t.groupby(key, sort=True):
        n = g["net"].to_numpy()
        w, l_ = n[n > 0].sum(), -n[n <= 0].sum()
        rows.append([str(k), len(g), float(np.mean(n > 0) * 100), float(n.sum()), float(n.sum() / CAP * 100), float(n.mean()),
                     float(w / l_) if l_ > 0 else None])
    return rows


def evaluate(B: dict, stock_days: list, fut_days: list) -> dict:
    res = {}
    for strat in STRATS:
        base, stress = B[(strat, "base", 1.0)], B[(strat, "base", CONFIG["stress"])]
        all_days = fut_days if strat == "momo" else stock_days
        bp, bps = {}, {}
        for name, a, b in periods(strat):
            dd = days_in(all_days, a, b)
            bp[name] = stats(cut(base, a, b), dd)
            bps[name] = stats(cut(stress, a, b), dd)
        full_key = [p[0] for p in periods(strat) if p[0].startswith("Poora 2021")][0]
        v = verdict(strat, bp, bps[full_key])
        yr = base.assign(y=base["date"].str[:4])
        mo = base.assign(m=base["date"].str[:7])
        side = base.assign(s=np.where(base["side"] > 0, "LONG", "SHORT"))
        var_rows = []
        fam = {"brk": "zones", "rev": "zones"}.get(strat, strat)
        from backtest.groww5.run import VARIANTS, VAR_LABEL
        for var in VARIANTS[fam]:
            if (strat, var, 1.0) not in B:
                continue
            tv, ts = B[(strat, var, 1.0)], B[(strat, var, CONFIG["stress"])]
            row = {"var": var, "label": VAR_LABEL[var]}
            for name, a, b in periods(strat):
                s = stats(cut(tv, a, b), days_in(all_days, a, b))
                row[name] = {"trades": s["trades"], "net": s["net"], "pf": s["pf"], "win": s["win"]}
            row["stress_net"] = stats(cut(ts, all_days[0], all_days[-1]), all_days)["net"]
            var_rows.append(row)
        res[strat] = {"periods": bp, "stress": bps, "verdict": v, "years": breakdown(yr, "y"), "months": breakdown(mo, "m"),
                      "sides": breakdown(side, "s"), "exits": breakdown(base, "reason"), "stocks": breakdown(base, "sym"), "variants": var_rows,
                      "full_key": full_key}
    return res

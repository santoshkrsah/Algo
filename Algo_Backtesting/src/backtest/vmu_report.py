"""Interactive HTML report for the VMU Equity V3 parameter study (same look as the other backtest reports)."""
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.report import ASSETS
from backtest.vmu import CAPITAL, EXPECTED


def _runs(flags: pd.Series) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def totals(t: pd.DataFrame) -> dict:
    t = t.sort_values(["date", "symbol"]).reset_index(drop=True)
    daily = t.groupby("date").net_pnl.sum()
    month = t.groupby(t.date.str[:7]).net_pnl.sum()
    cum = t.net_pnl.cumsum()
    dd = (cum - cum.cummax().clip(lower=0)).min()
    w, l = t.loc[t.net_pnl > 0, "net_pnl"], t.loc[t.net_pnl <= 0, "net_pnl"]
    return {
        "net": t.net_pnl.sum(), "ret_pct": t.net_pnl.sum() / CAPITAL * 100, "avg_month": month.mean(),
        "plus_months": f"{int((month > 0).sum())} of {len(month)}", "trades": len(t), "win_pct": (t.net_pnl > 0).mean() * 100,
        "avg_trade": t.net_pnl.mean(), "avg_win": w.mean(), "avg_loss": l.mean(),
        "win_loss_ratio": w.mean() / -l.mean() if len(l) else None, "profit_factor": w.sum() / -l.sum() if l.sum() < 0 else None,
        "max_dd": dd, "best_trade": t.net_pnl.max(), "worst_trade": t.net_pnl.min(),
        "best_day": daily.max(), "worst_day": daily.min(),
        "win_streak": _runs(t.net_pnl > 0), "loss_streak": _runs(t.net_pnl <= 0),
        "sl_pct": t.exit_reason.eq("initial SL").mean() * 100, "any_sl_pct": t.exit_reason.str.contains("SL").mean() * 100,
        "median_hold": t.hold_hours.median(), "trade_days": int(daily.size), "net_over_dd": t.net_pnl.sum() / -dd if dd < 0 else None,
    }


def write_html(path, t: pd.DataFrame, grid: pd.DataFrame, best: dict, rules: list[str], period: str):
    t = t.sort_values(["date", "symbol"]).reset_index(drop=True)
    month = t.groupby(t.date.str[:7]).agg(net=("net_pnl", "sum"), trades=("net_pnl", "size"), win=("net_pnl", lambda x: (x > 0).mean() * 100))
    daily = t.groupby("date").net_pnl.sum()
    cum = daily.cumsum()
    cols = ["date", "symbol", "side", "qty", "entry", "exit", "exit_time", "exit_reason", "net_pnl"]
    g = grid.copy()
    g["best"] = (g.index == best["idx"])
    gcols = ["vol_mult", "sl_mode", "be", "trail", "slip", "trades", "win_pct", "net", "profit_factor", "max_dd", "median_hold_h",
             "avg_loss", "worst_trade", "sl_pct", "score", "best"]
    data = {
        "period": period, "rules": rules, "capital": CAPITAL, "expected": EXPECTED, "best": {k: (v.item() if hasattr(v, "item") else v) for k, v in best.items() if k != "idx"},
        "totals": {k: (None if v is None or (isinstance(v, float) and not np.isfinite(v)) else v) for k, v in totals(t).items()},
        "months": [[m, round(r.net), round(r.net / CAPITAL * 100, 2), int(r.trades), round(r.win, 1)] for m, r in month.iterrows()],
        "daily": [[d, round(float(daily[d])), round(float(cum[d]))] for d in daily.index],
        "grid_cols": gcols, "grid": g[gcols].astype(object).where(g[gcols].notna(), None).values.tolist(),
        "trades": t[cols].astype(object).values.tolist(), "trade_cols": cols, "generated": datetime.now().strftime("%d %b %Y %H:%M"),
    }
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = (ASSETS / "vmu_template.html").read_text(encoding="utf-8").replace("__CSS__", css)
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else str(o)).replace("</", "<\\/")
    Path(path).write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")


def _clean(v):
    v = v.item() if hasattr(v, "item") else v
    return None if v is None or (isinstance(v, float) and not np.isfinite(v)) else v


def _dd(pnl: pd.Series) -> float:
    cum = pnl.cumsum()
    return float((cum - cum.cummax().clip(lower=0)).min()) if len(cum) else 0.0


def run_data(t: pd.DataFrame) -> dict:
    """Everything the V2 dashboard shows for one slippage run."""
    t = t.sort_values(["date", "symbol"]).reset_index(drop=True)
    tot = totals(t)
    cum = t.net_pnl.cumsum()
    peak = cum.cummax().clip(lower=0)
    tot["max_dd_pct"] = float(((cum - peak) / (CAPITAL + peak)).min() * 100)
    month = [[m, round(g.net_pnl.sum()), round(g.net_pnl.sum() / CAPITAL * 100, 2), len(g), round((g.net_pnl > 0).mean() * 100, 1),
              round(_dd(g.net_pnl)), round(g.exit_reason.eq("initial SL").mean() * 100, 1)] for m, g in t.groupby(t.date.str[:7])]
    daily = t.groupby("date").net_pnl.sum()
    stocks = t.groupby("symbol").agg(n=("net_pnl", "size"), w=("net_pnl", lambda x: (x > 0).mean() * 100), net=("net_pnl", "sum"),
                                     best=("net_pnl", "max"), worst=("net_pnl", "min"), last=("date", "max"))
    cols = ["date", "symbol", "side", "qty", "entry", "exit", "exit_time", "exit_reason", "gross_pnl", "costs", "net_pnl"]
    return {
        "totals": {k: _clean(v) for k, v in tot.items()}, "months": month,
        "daily": [[d, round(float(v)), round(float(c))] for (d, v), c in zip(daily.items(), daily.cumsum())],
        "stocks": [[s, int(r.n), round(r.w, 1), round(r.net), round(r.best), round(r.worst), r["last"]] for s, r in stocks.iterrows()],
        "trades": t[cols].astype(object).values.tolist(),
    }


def write_html_v2(path, runs: dict[str, pd.DataFrame], expected: dict, rules: list[str], notes: list[str], checks: list[list], period: str):
    """VMU V2 dashboard: one tab set, a switch between the slippage runs (keys like "0%")."""
    data = {"period": period, "rules": rules, "notes": notes, "checks": checks, "capital": CAPITAL, "expected": expected,
            "runs": {k: run_data(t) for k, t in runs.items()}, "generated": datetime.now().strftime("%d %b %Y %H:%M")}
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = (ASSETS / "vmu_v2_template.html").read_text(encoding="utf-8").replace("__CSS__", css)
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"), default=_clean).replace("</", "<\\/")
    Path(path).write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")

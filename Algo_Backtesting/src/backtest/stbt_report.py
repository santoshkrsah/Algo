"""Interactive HTML report (simple Hinglish) for the positional option selling backtest: switch by index and by slippage."""
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtest.report import ASSETS
from backtest.vmu_report import _clean, _dd, _runs

TRADE_COLS = ["date", "side", "strike", "hedge", "expiry", "entry_time", "und_entry", "und_sl", "exit_date", "exit_time", "und_exit",
              "iv_pct", "opt_entry", "opt_exit", "qty", "exit_reason", "reentry", "gross_pnl", "costs", "net_pnl"]


def totals(t: pd.DataFrame, capital: float) -> dict:
    t = t.sort_values(["date", "entry_time"]).reset_index(drop=True)
    daily = t.groupby("date").net_pnl.sum()
    month = t.groupby(t.date.str[:7]).net_pnl.sum()
    cum = t.net_pnl.cumsum()
    peak = cum.cummax().clip(lower=0)
    dd = float((cum - peak).min())
    w, l = t.loc[t.net_pnl > 0, "net_pnl"], t.loc[t.net_pnl <= 0, "net_pnl"]
    side = t.groupby("side").net_pnl.agg(["size", "sum"])
    return {
        "net": t.net_pnl.sum(), "ret_pct": t.net_pnl.sum() / capital * 100, "avg_month": month.mean(),
        "plus_months": f"{int((month > 0).sum())} / {len(month)}", "trades": len(t), "win_pct": (t.net_pnl > 0).mean() * 100,
        "avg_trade": t.net_pnl.mean(), "avg_win": w.mean(), "avg_loss": l.mean(),
        "win_loss_ratio": w.mean() / -l.mean() if len(l) and len(w) else None,
        "profit_factor": w.sum() / -l.sum() if l.sum() < 0 else None,
        "max_dd": dd, "max_dd_pct": float(((cum - peak) / (capital + peak)).min() * 100),
        "best_trade": t.net_pnl.max(), "worst_trade": t.net_pnl.min(), "best_day": daily.max(), "worst_day": daily.min(),
        "win_streak": _runs(t.net_pnl > 0), "loss_streak": _runs(t.net_pnl <= 0),
        "sl_pct": t.exit_reason.eq("SL").mean() * 100, "trade_days": int(daily.size),
        "net_over_dd": t.net_pnl.sum() / -dd if dd < 0 else None, "reentries": int((t.reentry == "haan").sum()),
        "pe": [int(side.loc["SELL PE", "size"]) if "SELL PE" in side.index else 0, float(side["sum"].get("SELL PE", 0.0))],
        "ce": [int(side.loc["SELL CE", "size"]) if "SELL CE" in side.index else 0, float(side["sum"].get("SELL CE", 0.0))],
        "costs": t.costs.sum(),
    }


def run_data(t: pd.DataFrame, capital: float) -> dict:
    t = t.sort_values(["date", "entry_time"]).reset_index(drop=True)
    daily = t.groupby("date").net_pnl.sum()
    months = [[m, round(g.net_pnl.sum()), round(g.net_pnl.sum() / capital * 100, 2), len(g), round((g.net_pnl > 0).mean() * 100, 1),
               round(_dd(g.net_pnl)), round(g.exit_reason.eq("SL").mean() * 100, 1)] for m, g in t.groupby(t.date.str[:7])]
    years = []
    for y, g in t.groupby(t.date.str[:4]):
        m = g.groupby(g.date.str[:7]).net_pnl.sum()
        years.append([y, round(g.net_pnl.sum()), round(g.net_pnl.sum() / capital * 100, 2), len(g), round((g.net_pnl > 0).mean() * 100, 1),
                      round(_dd(g.net_pnl)), f"{int((m > 0).sum())} / {len(m)}"])
    ex = t.groupby(["exit_reason", "reentry"]).net_pnl.agg(["size", "sum", "mean"]).sort_values("size", ascending=False)
    exits = [[f"{a}" + (" (re-entry)" if b == "haan" else ""), int(r["size"]), round(r["size"] / len(t) * 100, 1), round(r["sum"]), round(r["mean"])]
             for (a, b), r in ex.iterrows()]
    return {"totals": {k: _clean(v) if not isinstance(v, list) else v for k, v in totals(t, capital).items()},
            "months": months, "years": years, "exits": exits,
            "daily": [[d, round(float(v)), round(float(c))] for (d, v), c in zip(daily.items(), daily.cumsum())],
            "trades": t[TRADE_COLS].astype(object).where(t[TRADE_COLS].notna(), None).values.tolist(),
            "first": t.date.min(), "last": t.date.max()}


def write_html(path, runs: dict[tuple[str, str], pd.DataFrame], capital: float, rules: list[str], notes: list[str], checks: list[list],
               formulas: list[list], calib: list[list], periods: dict[str, str]):
    insts = list(dict.fromkeys(k[0] for k in runs))
    slips = list(dict.fromkeys(k[1] for k in runs))
    data = {"rules": rules, "notes": notes, "checks": checks, "formulas": formulas, "calib": calib, "capital": capital,
            "insts": insts, "slips": slips, "periods": periods,
            "runs": {f"{i}|{s}": run_data(t, capital) for (i, s), t in runs.items()},
            "generated": datetime.now().strftime("%d %b %Y %H:%M")}
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = (ASSETS / "stbt_template.html").read_text(encoding="utf-8").replace("__CSS__", css)
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else _clean(o)).replace("</", "<\\/")
    Path(path).write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")

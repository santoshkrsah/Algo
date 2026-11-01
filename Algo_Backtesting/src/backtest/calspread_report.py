"""Interactive HTML report (simple Hinglish) for the calendar spread backtest: switch by gap threshold and by slippage."""
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtest.report import ASSETS
from backtest.vmu_report import _clean, _dd, _runs

TRADE_COLS = ["symbol", "side", "entry_date", "pair", "lot", "entry_gap", "entry_gap_pct", "normal_pct", "entry_dist", "lots", "exit1",
              "exit_date", "exit_gap", "exit_reason", "rolled", "roll_date", "max_adverse_dist", "days", "gross_pnl", "costs", "net_pnl"]


def totals(t: pd.DataFrame, eq: pd.DataFrame, capital: float, margin_pair: float) -> dict:
    if t.empty:
        return {"trades": 0, "net": 0.0, "ret_pct": 0.0, "win_pct": None, "profit_factor": None, "max_dd": 0.0, "max_dd_pct": 0.0,
                "trade_days": 0, "plus_months": "0 / 0", "rolled": 0, "roll_failed": 0, "peak_lots": 0, "ret_margin": None}
    t = t.sort_values(["exit_date", "symbol"]).reset_index(drop=True)
    month = t.groupby(t.exit_date.str[:7]).net_pnl.sum()
    peak = eq.mtm.cummax().clip(lower=0)
    dd = float((eq.mtm - peak).min())
    w, l = t.loc[t.net_pnl > 0, "net_pnl"], t.loc[t.net_pnl <= 0, "net_pnl"]
    rolled = t[t.rolled == "haan"]
    peak_lots = int(eq.open_lots.max())
    return {
        "net": t.net_pnl.sum(), "ret_pct": t.net_pnl.sum() / capital * 100, "avg_month": month.mean(),
        "plus_months": f"{int((month > 0).sum())} / {len(month)}", "trades": len(t), "win_pct": (t.net_pnl > 0).mean() * 100,
        "avg_trade": t.net_pnl.mean(), "avg_win": w.mean() if len(w) else None, "avg_loss": l.mean() if len(l) else None,
        "win_loss_ratio": w.mean() / -l.mean() if len(w) and len(l) and l.mean() < 0 else None,
        "profit_factor": w.sum() / -l.sum() if l.sum() < 0 else None,
        "max_dd": dd, "max_dd_pct": float(((eq.mtm - peak) / (capital + peak)).min() * 100),
        "best_trade": t.net_pnl.max(), "worst_trade": t.net_pnl.min(),
        "win_streak": _runs(t.net_pnl > 0), "loss_streak": _runs(t.net_pnl <= 0),
        "rolled": len(rolled), "rolled_pct": len(rolled) / len(t) * 100,
        "roll_failed": int((rolled.net_pnl <= 0).sum()), "partial": int((t.exit1 != "-").sum()),
        "stop_pct": t.exit_reason.eq("risk stop").mean() * 100, "avg_days": t.days.mean(), "costs": t.costs.sum(),
        "peak_lots": peak_lots, "peak_margin": peak_lots * margin_pair,
        "ret_margin": t.net_pnl.sum() / (peak_lots * margin_pair) * 100 if peak_lots else None,
        "trade_days": int(t.entry_date.nunique()), "stocks": int(t.symbol.nunique()),
    }


def run_data(t: pd.DataFrame, eq: pd.DataFrame, capital: float, margin_pair: float) -> dict:
    tot = {k: _clean(v) for k, v in totals(t, eq, capital, margin_pair).items()}
    if t.empty:
        return {"totals": tot, "months": [], "years": [], "exits": [], "daily": [], "trades": [], "stocks": []}
    t = t.sort_values(["exit_date", "symbol"]).reset_index(drop=True)
    by = t.exit_date.str[:7]
    months = [[m, round(g.net_pnl.sum()), round(g.net_pnl.sum() / capital * 100, 2), len(g), round((g.net_pnl > 0).mean() * 100, 1),
               round(_dd(g.net_pnl)), int((g.rolled == "haan").sum())] for m, g in t.groupby(by)]
    years = []
    for y, g in t.groupby(t.exit_date.str[:4]):
        m = g.groupby(g.exit_date.str[:7]).net_pnl.sum()
        years.append([y, round(g.net_pnl.sum()), round(g.net_pnl.sum() / capital * 100, 2), len(g), round((g.net_pnl > 0).mean() * 100, 1),
                      round(_dd(g.net_pnl)), f"{int((m > 0).sum())} / {len(m)}"])
    ex = t.groupby("exit_reason").net_pnl.agg(["size", "sum", "mean"]).sort_values("size", ascending=False)
    exits = [[k, int(r["size"]), round(r["size"] / len(t) * 100, 1), round(r["sum"]), round(r["mean"])] for k, r in ex.iterrows()]
    st = t.groupby("symbol").agg(n=("net_pnl", "size"), w=("net_pnl", lambda x: (x > 0).mean() * 100), net=("net_pnl", "sum"),
                                 best=("net_pnl", "max"), worst=("net_pnl", "min"), dist=("entry_dist", "mean"), normal=("normal_pct", "mean"),
                                 lot=("lot", "last"), last=("exit_date", "max"))
    stocks = [[s, int(r.n), round(r.w, 1), round(r.net), round(r.best), round(r.worst), round(r.dist, 2), round(r.normal, 2), int(r.lot), r["last"]]
              for s, r in st.iterrows()]
    e = eq[eq.date >= t.entry_date.min()]
    daily_chg = e.mtm.diff().fillna(e.mtm)
    return {"totals": tot, "months": months, "years": years, "exits": exits,
            "daily": [[d, round(float(c)), round(float(m)), int(n)] for d, c, m, n in zip(e.date, daily_chg, e.mtm, e.open_lots)],
            "trades": t[TRADE_COLS].astype(object).where(t[TRADE_COLS].notna(), None).values.tolist(), "stocks": stocks}


def write_html(path, runs: dict[tuple[str, str], tuple[pd.DataFrame, pd.DataFrame]], capital: float, margin_pair: float, rules: list[str],
               notes: list[str], checks: list[list], formulas: list[list], freq: list[list], data_info: list[list], period: str, base: str):
    ths = list(dict.fromkeys(k[0] for k in runs))
    slips = list(dict.fromkeys(k[1] for k in runs))
    data = {"rules": rules, "notes": notes, "checks": checks, "formulas": formulas, "freq": freq, "data_info": data_info, "capital": capital,
            "margin_pair": margin_pair, "ths": ths, "slips": slips, "period": period, "base": base,
            "runs": {f"{a}|{b}": run_data(t, eq, capital, margin_pair) for (a, b), (t, eq) in runs.items()},
            "generated": datetime.now().strftime("%d %b %Y %H:%M")}
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = (ASSETS / "calspread_template.html").read_text(encoding="utf-8").replace("__CSS__", css)
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else _clean(o)).replace("</", "<\\/")
    Path(path).write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")

"""Interactive HTML report for the Vol 5L + 7% target backtest (VMU V2 look: one dashboard, switch between slippage runs)."""
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtest.report import ASSETS
from backtest.vmu_report import _clean, _dd, run_data
from backtest.vol5l import CAPITAL

TRADE_COLS = ["date", "symbol", "side", "qty", "entry", "exit", "exit_time", "exit_reason", "gross_pnl", "costs", "net_pnl",
              "max_gain_pct", "first_vol_lakh", "first_candle_pct"]


def run_data_v5(t: pd.DataFrame) -> dict:
    t = t.sort_values(["date", "symbol"]).reset_index(drop=True)
    d = run_data(t)
    d["totals"]["plus_months"] = d["totals"]["plus_months"].replace(" of ", " / ")
    d["totals"]["target_pct"] = float(t.exit_reason.str.startswith("target").mean() * 100)
    d["trades"] = t[TRADE_COLS].astype(object).values.tolist()
    ex = t.groupby("exit_reason").net_pnl.agg(["size", "sum", "mean"]).sort_values("size", ascending=False)
    d["exits"] = [[k, int(r["size"]), round(r["size"] / len(t) * 100, 1), round(r["sum"]), round(r["mean"])] for k, r in ex.iterrows()]
    d["years"] = []
    for y, g in t.groupby(t.date.str[:4]):
        m = g.groupby(g.date.str[:7]).net_pnl.sum()
        d["years"].append([y, round(g.net_pnl.sum()), round(g.net_pnl.sum() / CAPITAL * 100, 2), len(g),
                           round((g.net_pnl > 0).mean() * 100, 1), round(_dd(g.net_pnl)), f"{int((m > 0).sum())} / {len(m)}"])
    return d


def write_html(path, runs: dict[str, pd.DataFrame], rules: list[str], notes: list[str], checks: list[list], formulas: list[list],
               period: str):
    data = {"period": period, "rules": rules, "notes": notes, "checks": checks, "formulas": formulas, "capital": CAPITAL,
            "runs": {k: run_data_v5(t) for k, t in runs.items()}, "generated": datetime.now().strftime("%d %b %Y %H:%M")}
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = (ASSETS / "vol5l_template.html").read_text(encoding="utf-8").replace("__CSS__", css)
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"), default=_clean).replace("</", "<\\/")
    Path(path).write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")

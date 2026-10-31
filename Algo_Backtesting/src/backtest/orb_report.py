"""Interactive HTML report for VMU ORB V4: train and test periods, three slippage levels, the train parameter grid."""
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtest.orb import CAPITAL
from backtest.report import ASSETS
from backtest.vmu_report import _clean, run_data

TRADE_COLS = ["date", "symbol", "side", "qty", "entry_time", "entry", "exit_time", "exit", "exit_reason", "partial",
              "gross_pnl", "costs", "net_pnl", "hold_hours", "vol_ratio"]


def _run(t: pd.DataFrame) -> dict:
    d = run_data(t)
    d["totals"]["max_dd_cap_pct"] = _clean(d["totals"]["max_dd"] / CAPITAL * 100)
    d["trades"] = t.sort_values(["date", "entry_time"])[TRADE_COLS].astype(object).values.tolist()
    return d


def write_html(path, runs: dict[str, dict[str, pd.DataFrame]], grid: pd.DataFrame, best: dict, rules: list[str],
               notes: list[str], periods: dict[str, str]):
    """runs = {"TRAIN": {"0%": trades, ...}, "TEST": {...}}; grid = one row per train combination."""
    gcols = ["or_min", "vol_mult", "max_sl", "partial_r", "trades", "win_pct", "net", "profit_factor", "max_dd", "avg_trade", "chosen"]
    data = {"periods": periods, "rules": rules, "notes": notes, "capital": CAPITAL, "best": {k: _clean(v) for k, v in best.items()},
            "runs": {p: {k: _run(t) for k, t in r.items()} for p, r in runs.items()},
            "grid_cols": gcols, "grid": grid[gcols].astype(object).where(grid[gcols].notna(), None).values.tolist(),
            "generated": datetime.now().strftime("%d %b %Y %H:%M")}
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = (ASSETS / "orb_template.html").read_text(encoding="utf-8").replace("__CSS__", css)
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"), default=_clean).replace("</", "<\\/")
    Path(path).write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")

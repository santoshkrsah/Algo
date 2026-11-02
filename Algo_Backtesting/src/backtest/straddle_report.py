"""Interactive HTML report (simple Hinglish, new look with Trade viewer) for the layered straddle-selling backtest."""
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import viewer as viewer_assets
from backtest.report import ASSETS
from backtest.straddle import stats
from backtest.vmu_report import _clean

COLS = ["date", "index", "traded", "expiry", "dte", "strike", "lots", "lot", "entry_t", "ce0", "pe0", "prem", "vix", "gap", "pause", "adj", "sl", "tac", "hedges",
        "avg_done", "exit", "exit_t", "orders", "costs", "slip", "deployed", "max_target", "net_delta_max", "dd_stage", "carry", "pnl_core", "pnl_avg", "pnl_tac",
        "pnl_hedge", "cost_core", "cost_avg", "cost_tac", "cost_hedge", "c_brokerage", "c_stt", "c_exch", "c_stamp", "c_gst", "gross", "net", "equity", "dd",
        "adj_txt", "events"]


def rows_of(log: pd.DataFrame) -> list[list]:
    x = log.copy()
    x["events"] = x.events.map(lambda ev: [[int(t), w, l] for t, w, l in ev])
    x = x[COLS].astype(object)
    return x.where(x.notna(), None).values.tolist()


def index_series(bars: dict, days: list[str], step: int = 5) -> dict:
    """{date: [[minute, open, high, low, close], ...]} in <step>-minute candles of the 1-minute index."""
    out = {}
    for d in days:
        b = bars[d]
        out[d] = [[j, round(float(b[j, 0]), 2), round(float(b[j:j + step, 1].max()), 2), round(float(b[j:j + step, 2].min()), 2), round(float(b[min(j + step, 375) - 1, 3]), 2)]
                  for j in range(0, 375, step)]
    return out


def premium_series(day, expiry: str, strike: float, step: int = 5) -> list:
    """Model price of the core straddle (CE + PE at the entry strike) every <step> minutes, for the viewer."""
    ce, pe = day.contract(expiry, strike, "CE")[0], day.contract(expiry, strike, "PE")[0]
    return [[int(t), round(float(ce[t] + pe[t]), 2)] for t in range(0, 361, step)]


def write_html(path, runs: dict, labels: dict, chips: dict, subs: dict, invs: dict, base: str, capital: float, deploy: float, rules, notes, checks, formulas, data_info,
               series: dict, prem: dict, data_range: list[str], data_note: str, settings: list, split: str, slip_txt: str):
    """runs: {key: day log DataFrame}; labels / chips / subs: per variant key."""
    data = {"rules": rules, "notes": notes, "checks": checks, "formulas": formulas, "data_info": data_info, "capital": capital, "deploy": deploy, "series": series, "prem": prem,
            "data_range": data_range, "data_note": data_note, "settings": settings, "split": split, "slip_txt": slip_txt, "base": base, "cols": COLS,
            "variants": [[k, labels[k]] for k in runs], "runs": {k: {"rows": rows_of(v), "chips": chips[k], "sub": subs[k], "inv": invs[k]} for k, v in runs.items()},
            "generated": datetime.now().strftime("%d %b %Y %H:%M")}
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "straddle_template.html").read_text(encoding="utf-8")).replace("__CSS__", css)
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else _clean(o)).replace("</", "<\\/")
    Path(path).write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")


def check_row(label: str, log: pd.DataFrame, capital: float, split: str) -> list:
    """[label, days, win %, net, profit factor, max DD, worst day, avg per day, net fit, net verify]"""
    s = stats(log, capital)
    fit, ver = log[log.date < split], log[log.date >= split]
    return [label, s["days"], s["win_pct"], s["net"], s["profit_factor"], s["max_dd"], s["worst"], s["net"] / s["days"] if s["days"] else None,
            float(fit.net.sum()), float(ver.net.sum())]

"""Interactive HTML report (simple Hinglish, new look with Trade viewer) for the intraday call + future hedge backtest."""
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import viewer as viewer_assets
from backtest.callfut import EXITS, model_path, stats
from backtest.report import ASSETS
from backtest.vmu_report import _clean

BASE = ["date", "symbol", "strike", "expiry", "dte", "lot", "f_open", "f_close", "c_open", "c_close", "c_high", "c_low", "delta", "net_delta",
        "iv_prev", "iv_open", "iv_close", "k_prev_trades", "premium", "margin", "blocked", "model_ok"]
PER_EXIT = ["call", "fut", "why", "min", "gross", "costs", "net"]


def intraday_series(t: pd.DataFrame, minutes: dict, p, step: int = 5) -> dict:
    """{symbol|date: [[minute, fut open, high, low, close, model call], ...]} in <step>-minute bars, for the viewer."""
    out = {}
    for r in t.itertuples():
        g = minutes.get(r.symbol, {}).get(r.date)
        if g is None or not r.model_ok:
            continue
        mins, F, C = model_path(r, g, p)
        b = pd.DataFrame({"m": (mins // step * step).astype(int), "o": g.open.values, "f": F, "c": C})
        a = b.groupby("m").agg(o=("o", "first"), h=("f", "max"), l=("f", "min"), cl=("f", "last"), c=("c", "last")).reset_index()
        a["h"] = np.maximum(a.h, a.o)
        a["l"] = np.minimum(a.l, a.o)
        out[f"{r.symbol}|{r.date}"] = a.round(2).values.tolist()
    return out


def write_html(path, t: pd.DataFrame, grid: list, capital: float, rules, notes, checks, formulas, data_info, base: str, series: dict,
               data_range: list[str], data_note: str, p, settings: list, breakdown: dict):
    cols = BASE + [f"{e[0]}_{x}" for e in EXITS for x in PER_EXIT]
    x = t[cols].astype(object)
    data = {"rules": rules, "notes": notes, "checks": checks, "formulas": formulas, "data_info": data_info, "capital": capital, "series": series,
            "data_range": data_range, "data_note": data_note, "settings": settings, "exits": [[e[0], e[1]] for e in EXITS], "grid": grid,
            "rows": x.where(x.notna(), None).values.tolist(), "cols": BASE, "base": base, "breakdown": breakdown,
            "p": {"slip_opt": p.slip_opt, "slip_fut": p.slip_fut, "stt_opt": p.stt_opt, "stt_fut": p.stt_fut, "call_lots": p.call_lots, "fut_lots": p.fut_lots,
                  "top": p.top, "margin": p.margin},
            "generated": datetime.now().strftime("%d %b %Y %H:%M")}
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "callfut_template.html").read_text(encoding="utf-8")).replace("__CSS__", css)
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else _clean(o)).replace("</", "<\\/")
    Path(path).write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")


def grid_rows(name: str, t: pd.DataFrame, capital: float) -> list:
    out = []
    for key, label, *_ in EXITS:
        s = stats(t[key + "_net"], t.date, capital)
        out.append([name, key, label, s["trades"], s["win_pct"], s["net"], s["profit_factor"], s["max_dd"], s["avg"]])
    return out

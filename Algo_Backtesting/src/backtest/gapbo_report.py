"""Interactive HTML report (simple Hinglish, new look with Trade viewer on daily candles + 21 EMA) for the gap breakout backtest.

    write_html(path, data)      # data built by tools/run_gapbo.py (views with trades + mark-to-market equity, checks, claims, rules ...)
"""
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import viewer as viewer_assets
from backtest.breakout_report import _clean
from backtest.report import ASSETS


def write_html(path, data: dict) -> Path:
    data = {**data, "generated": datetime.now().strftime("%d %b %Y %H:%M")}
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "gapbo_template.html").read_text(encoding="utf-8")).replace("__CSS__", css).replace("__TITLE__", data["title"])
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(_clean(data), separators=(",", ":"), default=str).replace("</", "<\\/")
    path = Path(path)
    path.write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")
    return path


def series_for(D: dict, I: dict, trades: list[pd.DataFrame], before: int = 70, after: int = 10) -> dict:
    """Daily candles (adjusted) + 21 EMA of every traded stock around its trades: {symbol: [[date, o, h, l, c, ema], ...]}."""
    W, cal = D["W"], D["cal"]
    col = {s: j for j, s in enumerate(D["syms"])}
    spans: dict[str, list] = {}
    for t in trades:
        if t is None or t.empty:
            continue
        for sym, a, b in zip(t.symbol, t.i, t.x):
            spans.setdefault(sym, []).append((max(0, a - before), min(len(cal) - 1, b + after)))
    out = {}
    for sym, sp in spans.items():
        keep = np.zeros(len(cal), dtype=bool)
        for a, b in sp:
            keep[a:b + 1] = True
        rows = np.flatnonzero(keep)
        j = col[sym]
        o, h, l, c = (W[k][sym].to_numpy()[rows] for k in ("open", "high", "low", "close"))
        e = I["ema"][rows, j]
        out[sym] = [[cal[r], round(float(a), 2), round(float(b), 2), round(float(x), 2), round(float(y), 2), None if z != z else round(float(z), 2)]
                    for r, a, b, x, y, z in zip(rows, o, h, l, c, e) if y == y]
    return out

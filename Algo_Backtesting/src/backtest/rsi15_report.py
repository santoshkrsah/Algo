"""Interactive HTML report (simple Hinglish, new look with Trade viewer on 15-min candles + RSI) for the RSI 15m swing test.

    write_html(path, data)      # data built by tools/run_rsi15.py
    series_for(D, trades)       # 15m candles + RSI of every traded stock around its trades
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
    tpl = viewer_assets.apply((ASSETS / "rsi15_template.html").read_text(encoding="utf-8")).replace("__CSS__", css).replace("__TITLE__", data["title"])
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(_clean(data), separators=(",", ":"), default=str).replace("</", "<\\/")
    path = Path(path)
    path.write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")
    return path


def series_for(D: dict, t: pd.DataFrame, before: int = 45, after: int = 20) -> dict:
    """{symbol: {t: [unix sec, IST shown as UTC], o, h, l, c, r}} for the bars around every trade (union of windows)."""
    out = {}
    for sym, g in t.groupby("symbol"):
        b = D[sym]
        keep = np.zeros(len(b), dtype=bool)
        for fi, xi in zip(g.fi, g.xi):
            keep[max(0, fi - before): min(len(b), xi + after + 1)] = True
        x = b[keep]
        ts = ((x.datetime - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)).astype(np.int64)
        out[sym] = {"t": ts.tolist(), **{k[0]: x[k].round(2).tolist() for k in ("open", "high", "low", "close")},
                    "r": [None if v != v else round(float(v), 1) for v in x.rsi]}
    return out

"""Interactive HTML report (simple Hinglish, new look with Trade viewer) for the NIFTY PDH / PDL intraday backtest."""
import json
import math
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import viewer as viewer_assets
from backtest.pdh import hm
from backtest.report import ASSETS

DOW = ["Somvar", "Mangalvar", "Budhvar", "Guruvar", "Shukravar", "Shanivar", "Ravivar"]
# trade log columns (CSV and report); times are 3-min candle start times
COLS = ["date", "period", "dow", "side", "state", "level", "level_px", "signal_t", "entry_t", "entry_raw", "entry", "stop", "risk_pts", "target",
        "lots", "lot", "qty", "part_t", "part", "part_lots", "exit_t", "exit", "reason", "r", "r0", "scratch", "gross", "slip_rs",
        "c_brokerage", "c_stt", "c_exch", "c_stamp", "c_gst", "costs", "net", "mae_pts", "mae_r", "gap", "expiry", "orders", "intra_state"]


def trade_frame(trades: list, cut: str, scratch_r: float) -> pd.DataFrame:
    t = pd.DataFrame(trades)
    if t.empty:
        return pd.DataFrame(columns=COLS)
    t["period"] = np.where(t.date < cut, "IS", "OOS")
    t["dow"] = [DOW[d] for d in pd.to_datetime(t.date).dt.dayofweek]
    t["signal_t"] = t.signal_bar.map(hm)
    t["entry_t"] = t.entry_bar.map(hm)
    t["exit_t"] = t.exit_bar.map(hm)
    t["part_t"] = t.part_bar.map(lambda b: hm(int(b)) if b is not None and not (isinstance(b, float) and math.isnan(b)) else None)
    t["scratch"] = t.r.abs() <= scratch_r
    return t[COLS]


def _num(v):
    v = v.item() if hasattr(v, "item") else v
    if isinstance(v, float):
        return None if not math.isfinite(v) else round(v, 4)
    return v


def rows(t: pd.DataFrame) -> list:
    out = []
    for r in t.itertuples(index=False):
        out.append([_num(x) if not isinstance(x, (bool, np.bool_)) else int(x) for x in r])
    return out


def candles(m, days: list) -> dict:
    """1-min candles of the given days, small ints: per bar [open - previous close, high - open, open - low, close - open] x 20
    (NIFTY tick 0.05), first value = first open x 20."""
    di = {d: i for i, d in enumerate(m.days)}
    out = {}
    for d in days:
        a = np.round(m.m1[di[d]] * 20).astype(np.int64)
        prev = np.r_[a[0, 0], a[:-1, 3]]
        enc = np.c_[a[:, 0] - prev, a[:, 1] - a[:, 0], a[:, 0] - a[:, 2], a[:, 3] - a[:, 0]]
        out[d] = [int(a[0, 0])] + enc.ravel().tolist()
    return out


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return _num(o)


def write_html(path, data: dict):
    data = dict(_clean(data), generated=datetime.now().strftime("%d %b %Y %H:%M"))
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "pdh_template.html").read_text(encoding="utf-8")).replace("__CSS__", css)
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"), default=_num, allow_nan=False).replace("</", "<\\/")
    Path(path).write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")

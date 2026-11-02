"""Interactive HTML report (simple Hinglish, new look with Trade viewer on daily candles) for the breakout + put selling backtest.

    write_html(path, data)      # data built by tools/run_breakout.py (views with trades, checks, study, rules, formulas ...)
"""
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np

from backtest import viewer as viewer_assets
from backtest.report import ASSETS


def _clean(v):
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if hasattr(v, "item"):
        v = v.item()
    return None if isinstance(v, float) and not np.isfinite(v) else v


def write_html(path, data: dict, template: str = "breakout_template.html") -> Path:
    """template: breakout_template.html (report 18) or breakout2_template.html (full spec, tools/run_breakout2.py)."""
    data = {**data, "generated": datetime.now().strftime("%d %b %Y %H:%M")}
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / template).read_text(encoding="utf-8")).replace("__CSS__", css).replace("__TITLE__", data["title"])
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(_clean(data), separators=(",", ":"), default=str).replace("</", "<\\/")
    path = Path(path)
    path.write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")
    return path

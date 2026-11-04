"""Interactive HTML report (simple Hinglish, new look with Trade viewer on 2-hour candles + RSI) for the RSI swing backtest.

    write_html(path, data)      # data built by tools/run_rsi2h.py (views = instrument x version, instr = buy-hold + candles, rules ...)
"""
import json
import re
from datetime import datetime
from pathlib import Path

from backtest import viewer as viewer_assets
from backtest.breakout_report import _clean
from backtest.report import ASSETS


def write_html(path, data: dict) -> Path:
    data = {**data, "generated": datetime.now().strftime("%d %b %Y %H:%M")}
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "rsi2h_template.html").read_text(encoding="utf-8")).replace("__CSS__", css).replace("__TITLE__", data["title"])
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(_clean(data), separators=(",", ":"), default=str).replace("</", "<\\/")
    path = Path(path)
    path.write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")
    return path

"""Interactive HTML report (simple Hinglish, new look with Trade viewer) for the Gap + RSI + 21-EMA backtest."""
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtest import viewer as viewer_assets
from backtest.gaprsi import EXITS, GAPS, stats
from backtest.report import ASSETS
from backtest.vmu_report import _clean

COLS = ["entry_date", "symbol", "gap_pct", "rsi_s", "entry", "stop", "fill", "ema", "qty", "exit_date", "exit", "reason", "hold",
        "gross", "costs", "net", "R", "bull", "value", "target", "signal_date"]


def rows_of(t: pd.DataFrame) -> list[list]:
    x = t.copy()
    x["bull"] = x.bull.map({True: 1, False: 0}) if "bull" in x else None
    x = x[COLS].astype(object)
    return x.where(x.notna(), None).values.tolist()


def series_for(ind: pd.DataFrame, runs: dict, before_days: int = 75, after_days: int = 8) -> dict:
    """Daily candles + 21 EMA + smoothed RSI around every traded window (merged per stock)."""
    spans: dict[str, list] = {}
    for t in runs.values():
        for r in t[["symbol", "signal_date", "exit_date"]].itertuples():
            spans.setdefault(r.symbol, []).append((pd.Timestamp(r.signal_date) - pd.Timedelta(days=before_days), pd.Timestamp(r.exit_date) + pd.Timedelta(days=after_days)))
    out = {}
    by = {s: g for s, g in ind[ind.symbol.isin(spans)].groupby("symbol")}
    for sym, sp in spans.items():
        sp.sort()
        merged = [list(sp[0])]
        for a, b in sp[1:]:
            if a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        g = by[sym]
        d = pd.to_datetime(g.date)
        keep = pd.Series(False, index=g.index)
        for a, b in merged:
            keep |= (d >= a) & (d <= b)
        k = g[keep]
        out[sym] = k[["date", "open", "high", "low", "close", "ema", "rsi_s"]].round(2).astype(object).where(k[["date", "open", "high", "low", "close", "ema", "rsi_s"]].notna(), None).values.tolist()
    return out


def write_html(path, runs: dict, capital: float, rules, notes, checks, formulas, data_info, base: str, series: dict, data_range: list[str], data_note: str,
               meta: dict | None = None):
    """runs: {(side, gap, exit): sized trades}. base = 'side|gap|exit'."""
    grid, data_runs = [], {}
    for (side, gap, ex), t in runs.items():
        st = stats(t, capital)
        grid.append([side, gap, ex, dict((e[0], e[1]) for e in EXITS)[ex], st["trades"], st["win_pct"], st["net"], st["profit_factor"], st["max_dd"], st["avg_r"]])
        data_runs[f"{side}|{gap:g}|{ex}"] = rows_of(t)
    data = {"rules": rules, "notes": notes, "checks": checks, "formulas": formulas, "data_info": data_info, "capital": capital, "series": series,
            "data_range": data_range, "data_note": data_note, "exits": [[e[0], e[1]] for e in EXITS], "gaps": list(GAPS), "grid": grid, "runs": data_runs,
            "base": base, "meta": meta, "cols": COLS, "generated": datetime.now().strftime("%d %b %Y %H:%M")}
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "gaprsi_template.html").read_text(encoding="utf-8")).replace("__CSS__", css)
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else _clean(o)).replace("</", "<\\/")
    Path(path).write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")

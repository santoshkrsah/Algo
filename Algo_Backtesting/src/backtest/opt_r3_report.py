"""Interactive HTML report (simple Hinglish) for the option R3 momentum backtest: switch pivot x strike x exit."""
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtest import viewer as viewer_assets
from backtest.opt_r3 import EXITS, PER_TRADE
from backtest.report import ASSETS
from backtest.vmu_report import _clean

BASE = ["date", "symbol", "strike", "expiry", "dte", "p_high", "p_low", "p_close", "r3", "close", "above_pct",
        "n_open", "n_high", "n_low", "n_close", "trades", "lot"]


def stats(net: pd.Series, dates: pd.Series, capital: float) -> dict:
    if net.empty:
        return {"trades": 0, "net": 0.0, "win_pct": None, "profit_factor": None, "max_dd": 0.0, "avg": None}
    daily = net.groupby(dates.values).sum().sort_index()
    cum = daily.cumsum()
    dd = float((cum - cum.cummax().clip(lower=0)).min())
    w, l = net[net > 0], net[net <= 0]
    return {"trades": int(len(net)), "net": float(net.sum()), "ret_pct": float(net.sum() / capital * 100), "win_pct": float((net > 0).mean() * 100),
            "avg": float(net.mean()), "avg_win": float(w.mean()) if len(w) else None, "avg_loss": float(l.mean()) if len(l) else None,
            "profit_factor": float(w.sum() / -l.sum()) if l.sum() < 0 else None, "max_dd": dd}


def contract_series(d: pd.DataFrame, picked: dict, before_days: int = 25, after_days: int = 6) -> dict:
    """Daily candles + stock price of every contract that appears in a picked trade (key symbol|expiry|strike), around its signal days."""
    t = pd.concat(picked.values())[["symbol", "expiry", "strike", "date"]]
    t = t.assign(date=pd.to_datetime(t.date)).groupby(["symbol", "expiry", "strike"], observed=True).date.agg(["min", "max"]).reset_index()
    t["lo"] = (t["min"] - pd.Timedelta(days=before_days)).dt.strftime("%Y-%m-%d")
    t["hi"] = (t["max"] + pd.Timedelta(days=after_days)).dt.strftime("%Y-%m-%d")
    x = d[d.type == "CE"][["symbol", "expiry", "strike", "date", "open", "high", "low", "close", "und"]].merge(t[["symbol", "expiry", "strike", "lo", "hi"]], on=["symbol", "expiry", "strike"])
    x = x[(x.date >= x.lo) & (x.date <= x.hi)].sort_values(["symbol", "expiry", "strike", "date"])
    out = {}
    for (sym, exp, k), g in x.groupby(["symbol", "expiry", "strike"], observed=True):
        out[f"{sym}|{exp}|{int(k) if float(k).is_integer() else k}"] = g[["date", "open", "high", "low", "close", "und"]].round(2).values.tolist()
    return out


def write_html(path, picked: dict, signals: dict, capital: float, rules, notes, checks, formulas, part2: list[list], data_info, base: str,
               series: dict, data_range: list[str], data_note: str, slip: float):
    """picked / signals: {(pivot, k): DataFrame with exit columns}. base = 'pivot|k|exit'. series = contract_series()."""
    grid, runs = [], {}
    for (pv, k), t in picked.items():
        s_all = signals[(pv, k)]
        for key, label, *_ in EXITS:
            st = stats(t[key + "_net"], t.date, capital)
            sa = s_all[key + "_net"]
            grid.append([pv, k, key, label, st["trades"], st["win_pct"], st["net"], st["profit_factor"], st["max_dd"],
                         int(len(sa)), float(sa.mean() / 100) if len(sa) else None, float((sa > 0).mean() * 100) if len(sa) else None])
        cols = BASE + [f"{e[0]}_{x}" for e in EXITS for x in ("px", "why", "net")]
        runs[f"{pv}|{k}"] = t[cols].astype(object).where(t[cols].notna(), None).values.tolist()
    data = {"rules": rules, "notes": notes, "checks": checks, "formulas": formulas, "part2": part2, "data_info": data_info,
            "capital": capital, "series": series, "data_range": data_range, "data_note": data_note, "slip": slip, "per_trade": PER_TRADE, "exits": [[e[0], e[1]] for e in EXITS], "grid": grid, "runs": runs, "base": base,
            "cols": BASE, "generated": datetime.now().strftime("%d %b %Y %H:%M")}
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "opt_r3_template.html").read_text(encoding="utf-8")).replace("__CSS__", css)
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else _clean(o)).replace("</", "<\\/")
    Path(path).write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")

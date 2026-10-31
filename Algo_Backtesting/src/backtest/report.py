"""Statistics and the interactive HTML report (one self-contained file: charts, company table, every trade)."""
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ASSETS = Path(__file__).parent / "assets"


def stats(t: pd.DataFrame, capital: float) -> dict:
    """capital = money deployed at the busiest point (max trades x trade value); drawdown % is measured on it."""
    daily = t.groupby("date")["net_pnl"].sum()
    cum = daily.cumsum()
    dd = cum - cum.cummax().clip(lower=0)
    wins, losses = t.loc[t.net_pnl > 0, "net_pnl"], t.loc[t.net_pnl <= 0, "net_pnl"]
    sharpe = daily.mean() / daily.std() * np.sqrt(252) if len(daily) > 1 and daily.std() > 0 else float("nan")
    return {
        "Trading days with trades": len(daily), "Total trades": len(t),
        "Net profit (Rs)": t.net_pnl.sum(), "Gross profit (Rs)": t.gross_pnl.sum(), "Costs (Rs)": t.costs.sum(),
        "Return on capital": t.net_pnl.sum() / capital,
        "Win rate": len(wins) / len(t), "Profit factor": wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf"),
        "Average trade (Rs)": t.net_pnl.mean(), "Average win (Rs)": wins.mean() if len(wins) else 0.0,
        "Average loss (Rs)": losses.mean() if len(losses) else 0.0,
        "Best trade (Rs)": t.net_pnl.max(), "Worst trade (Rs)": t.net_pnl.min(),
        "Best day (Rs)": daily.max(), "Worst day (Rs)": daily.min(), "Winning days": float((daily > 0).mean()),
        "Max drawdown (Rs)": dd.min(), "Max drawdown %": dd.min() / capital, "Sharpe (daily, annualised)": sharpe,
        "Stop-loss exits": int((t.exit_reason == "stop-loss").sum()),
        "3pm square-off exits": int((t.exit_reason == "3pm square-off").sum()),
    }


def _group(t: pd.DataFrame, key) -> list[dict]:
    g = t.groupby(key)
    return [{"k": str(k), "trades": int(len(d)), "pnl": round(float(d.net_pnl.sum())),
             "win": round(float((d.net_pnl > 0).mean()), 4)} for k, d in g]


def payload(t: pd.DataFrame, s: dict, title: str, subtitle: str, rules: list[str], capital: float) -> dict:
    t = t.copy()
    t["ret_pct"] = (t.net_pnl / (t.qty * t.entry)).round(5) * 100
    dt = pd.to_datetime(t["date"])
    daily = t.groupby("date")["net_pnl"].sum()
    cum = daily.cumsum()
    dd = cum - cum.cummax().clip(lower=0)
    t["month"] = dt.dt.strftime("%Y-%m")
    t["year"] = dt.dt.year
    t["weekday"] = pd.Categorical(dt.dt.day_name(), ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
    losing_run = (daily <= 0).astype(int).groupby((daily > 0).cumsum()).cumsum().max()
    s = dict(s)
    s["Longest losing-day streak"] = int(losing_run)
    s["Expectancy per trade %"] = float(t.ret_pct.mean() / 100)

    comp = []
    for sym, d in t.groupby("symbol"):
        comp.append([sym, d.category.iloc[0], len(d), int((d.net_pnl > 0).sum()), round(float(d.net_pnl.sum())),
                     round(float(d.net_pnl.mean())), round(float(d.net_pnl.max())), round(float(d.net_pnl.min())),
                     round(float(d.ret_pct.mean()), 2)])
    cols = ["date", "symbol", "category", "qty", "r3", "entry", "exit", "exit_time", "exit_reason",
            "gross_pnl", "costs", "net_pnl", "ret_pct", "first_candle_volume"]
    hist_edges = [-10, -5, -3.5, -2.5, -1.5, -0.5, 0.5, 1.5, 2.5, 3.5, 5, 10]
    cnt, _ = np.histogram(t.ret_pct.clip(-9.99, 9.99), bins=hist_edges)
    labels = [f"{a:g} to {b:g}%" for a, b in zip(hist_edges[:-1], hist_edges[1:])]
    month_tbl = t.groupby(["year", t.month.str[5:].astype(int)])["net_pnl"].sum().round().to_dict()
    return {
        "title": title, "subtitle": subtitle, "rules": rules, "capital": capital,
        "generated": datetime.now().strftime("%d %b %Y %H:%M"),
        "kpi": {k: (None if (isinstance(v, float) and not np.isfinite(v)) else v) for k, v in s.items()},
        "daily": [[d, round(float(v)), round(float(cum[d])), round(float(dd[d]))] for d, v in daily.items()],
        "monthly": _group(t, "month"), "yearly": _group(t, "year"), "category": _group(t, "category"),
        "exit": _group(t, "exit_reason"), "weekday": _group(t, "weekday"),
        "hist": {"labels": labels, "counts": [int(c) for c in cnt]},
        "heat": [{"y": int(y), "m": int(m), "v": int(v)} for (y, m), v in month_tbl.items()],
        "companies": comp,
        "trades": t[cols].astype(object).where(t[cols].notna(), None).values.tolist(), "trade_cols": cols,
    }


def write_html(path, title, subtitle, s: dict, t: pd.DataFrame, rules: list[str], capital: float):
    data = payload(t, s, title, subtitle, rules, capital)
    tpl = (ASSETS / "report_template.html").read_text(encoding="utf-8")
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"), default=str).replace("</", "<\\/")
    html = tpl.replace("__TITLE__", title).replace("__CHARTJS__", js).replace("__DATA__", blob)
    Path(path).write_text(html, encoding="utf-8")

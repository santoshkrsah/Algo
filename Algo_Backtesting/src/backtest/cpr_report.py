"""Interactive HTML report for the CPR + VWAP + Open-Low backtest (vol5l look + settings grid, claim check, Nifty card)."""
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtest.calarb_report import _num, spec
from backtest.cpr import CAPITAL, ENTRIES, EXITS, SPLIT_DAY, split_stats
from backtest.report import ASSETS
from backtest.vmu_report import _clean, _dd, run_data

STAT = ["trades", "win_pct", "net", "pf", "exp", "max_dd"]
STAT_H = ["Trade", "Win %", "Net", "Profit factor", "Expectancy / trade", "Max DD"]
STAT_F = ["int", "pct", "rsc", "n2", "rsc", "rs"]


def run_data_cpr(t: pd.DataFrame) -> dict:
    t = t.assign(cpr_step_pct=(t.pv / t.y_pv - 1) * 100, open_vs_tc_pct=(t.o / t.top - 1) * 100,
                 entry_type=t.entry_type.map(ENTRIES)).sort_values(["date", "entry_time", "symbol"]).reset_index(drop=True)
    d = run_data(t)
    d["totals"]["plus_months"] = d["totals"]["plus_months"].replace(" of ", " / ")
    cols = ["date", "symbol", "side", "entry_type", "o", "l", "body_pct", "tail_body", "cpr_step_pct", "open_vs_tc_pct", "entry",
            "entry_time", "sl", "exit", "exit_time", "exit_reason", "qty", "gross_pnl", "costs", "net_pnl", "mae_pct", "mfe_pct"]
    x = t[cols].copy()
    for k in ["o", "l", "entry", "sl", "exit"]:
        x[k] = x[k].round(2)
    for k in ["body_pct", "tail_body", "cpr_step_pct", "open_vs_tc_pct", "mae_pct", "mfe_pct", "gross_pnl", "costs", "net_pnl"]:
        x[k] = x[k].round(2)
    d["trades"] = spec(["Date", "Stock", "Side", "Entry type", "Open", "Low", "Body %", "Tail / body", "CPR kitna upar %",
                        "Open vs TC %", "Entry", "Entry samay", "SL", "Exit", "Exit samay", "Exit ka karan", "Qty", "Gross P&L",
                        "Kharcha", "Net P&L", "Max ulta move %", "Max seedha move %"], cols,
                       ["", "", "", "", "n2", "n2", "n2", "n2", "spct", "spct", "n2", "", "n2", "n2", "", "", "int", "rsc", "rs", "rsc",
                        "spct", "spct"], _num(x), (0, 1), 1, 15)
    d["stocks"] = spec(["Stock", "Trade", "Win %", "Net", "Sabse accha trade", "Sabse bura trade", "Aakhri trade"],
                       ["stock", "trades", "win_pct", "net", "best", "worst", "last_trade"], ["", "int", "pct", "rsc", "rsc", "rsc", ""],
                       d["stocks"], (3, -1), 0)
    ex = t.groupby("exit_reason").net_pnl.agg(["size", "sum", "mean"]).sort_values("size", ascending=False)
    d["exits"] = [[k, int(r["size"]), round(r["size"] / len(t) * 100, 1), round(r["sum"]), round(r["mean"])] for k, r in ex.iterrows()]
    d["years"] = []
    for y, g in t.groupby(t.date.str[:4]):
        m = g.groupby(g.date.str[:7]).net_pnl.sum()
        d["years"].append([y, round(g.net_pnl.sum()), round(g.net_pnl.sum() / CAPITAL * 100, 2), len(g), round((g.net_pnl > 0).mean() * 100, 1),
                           round(_dd(g.net_pnl)), f"{int((m > 0).sum())} / {len(m)}"])
    d["split"] = {k: _clean(v) for k, v in split_stats(t).items()}
    d["totals"]["median_risk"] = float(t.risk_pct.median()) if len(t) else None
    return d


def label(b: dict) -> str:
    """One grid setting in words."""
    body = "limit nahi" if b["body"] is None or pd.isna(b["body"]) else f"{b['body'] * 100:g}%"
    cpr = {"pivot": "Pivot upar", "gap": "CPR gap (no overlap)"}.get(b["cpr"], b["cpr"])
    near = {"tc": "open TC se 0.5%", "inside": "open CPR ke andar / 0.25% upar"}.get(b["near"], b["near"])
    return (f"{b['side']} | {ENTRIES[b['entry']]} | {EXITS[b['exit']]} | {cpr} | {near} | "
            f"Open=Low {b['tol'] * 100:.2f}% | body {body} | {int(b['max_trades'])} trade/din")


def _stat_cells(r: dict, prefix: str) -> list:
    return [r[f"{prefix}_{k}"] for k in STAT]


def write_html(path, runs: dict, rules, notes, formulas, checks_def, checks, grid: pd.DataFrame, best, claim, funnel, nifty: pd.DataFrame,
               period: str):
    keys = list(STAT)
    chk_rows = []
    for name, row in zip([c[0] for c in checks_def], checks):
        r = dict(zip([f"{p}_{k}" for p in ("is", "oos", "all") for k in keys], row[1:]))
        chk_rows.append([name] + _stat_cells(r, "all") + [r["is_net"], r["is_pf"], r["oos_trades"], r["oos_net"], r["oos_pf"]])
    chk = spec(["Kya badla (baaki main setting jaisa)"] + STAT_H + ["2021-24 Net", "2021-24 PF", "2025-26 Trade", "2025-26 Net", "2025-26 PF"],
               ["change"] + keys + ["is_net", "is_pf", "oos_trades", "oos_net", "oos_pf"], [""] + STAT_F + ["rsc", "n2", "int", "rsc", "n2"],
               [[_clean(v) for v in r] for r in chk_rows], (0, 1))
    g = grid.copy()
    g["entry"], g["exit"] = g.entry.map(ENTRIES), g.exit.map(EXITS)
    g["tol"] = (g.tol * 100).round(2).astype(str) + "%"
    g["body"] = g.body.map(lambda b: "limit nahi" if b is None or pd.isna(b) else f"{b * 100:g}%")
    g["cpr"] = g.cpr.map({"pivot": "Pivot upar", "gap": "gap (no overlap)"})
    g["near"] = g.near.map({"tc": "TC se 0.5%", "inside": "CPR ke andar / 0.25% upar"})
    gcols = ["side", "entry", "exit", "cpr", "near", "tol", "body", "max_trades", "is_trades", "is_net", "is_pf", "is_win_pct",
             "oos_trades", "oos_net", "oos_pf", "oos_win_pct", "all_trades", "all_net", "all_pf", "all_exp", "all_max_dd"]
    grid_spec = spec(["Side", "Entry", "Exit", "CPR shart", "Open kahan", "Open=Low chhoot", "Body limit", "Trade / din",
                      "2021-24 Trade", "2021-24 Net", "2021-24 PF", "2021-24 Win %", "2025-26 Trade", "2025-26 Net", "2025-26 PF",
                      "2025-26 Win %", "Kul Trade", "Kul Net", "Kul PF", "Expectancy", "Max DD"], gcols,
                     ["", "", "", "", "", "", "", "int", "int", "rsc", "n2", "pct", "int", "rsc", "n2", "pct", "int", "rsc", "n2", "rsc", "rs"],
                     _num(g[gcols].round(2)), (9, -1), None, [0, 1, 2, 3, 4, 5, 6, 7])
    nf = None
    if len(nifty):
        n = nifty.round(1)
        sm = n.groupby("side").agg(days=("pts", "size"), avg=("pts", "mean"), tot=("pts", "sum"), sl=("sl_hit", "mean"),
                                   best=("best_pts", "median"), win=("pts", lambda x: (x > 0).mean() * 100)).round(1).reset_index()
        nf = {"summary": _num(sm), "rows": spec(["Date", "Side", "Open", "Entry (9:25)", "SL", "Din ka best (points)", "15:15 tak (points)",
                                                "SL laga?", "Result (points)"],
                                               ["date", "side", "open", "entry", "sl", "best_pts", "close_pts", "sl_hit", "pts"],
                                               ["", "", "n2", "n2", "n2", "n2", "sgn", "", "sgn"],
                                               _num(n.assign(sl_hit=n.sl_hit.map({True: "haan", False: "nahi"}))), (0, -1), None, 1)}
    data = {"period": period, "rules": rules, "notes": notes, "formulas": formulas, "capital": CAPITAL, "split_day": SPLIT_DAY,
            "runs": {k: run_data_cpr(t) for k, t in runs.items()}, "checks": chk, "grid": grid_spec,
            "best": {k: _clean(v) for k, v in best.items()} if best else None,
            "best_label": label(best) if best else "",
            "claim": [[_clean(v) for v in r] for r in claim], "funnel": funnel, "nifty": nf,
            "generated": datetime.now().strftime("%d %b %Y %H:%M")}
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = (ASSETS / "cpr_template.html").read_text(encoding="utf-8").replace("__CSS__", css)
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"), default=_clean).replace("</", "<\\/")
    Path(path).write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")

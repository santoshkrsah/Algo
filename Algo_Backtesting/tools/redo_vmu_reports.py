"""Rebuild reports 05_vmu_v3 and 06_vmu_v2 in the new look (same trades / numbers, no new backtest).

Reads the saved trade CSVs + the data embedded in the old report (expected numbers, best combination, 243-combination
grid, checks), adds 1-min candles from the store (adjust=False = same prices as the trades) and writes report.html with
assets/vmu_new_template.html. The old report is kept as report_old.html.

    python tools/redo_vmu_reports.py
"""
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backtest import fast, r11  # noqa: E402
from backtest import viewer as viewer_assets  # noqa: E402
from backtest.report import ASSETS  # noqa: E402

RES = Path(__file__).resolve().parents[1] / "results"
CAP = 70_000
EXPO = 175_000
TRADE_COLS = ["date", "sym", "side", "t_sig", "t_in", "entry", "t_out", "exit", "sl", "tgt", "reason", "qty", "value", "gross0", "slip", "charges", "net", "R", "info",
              "entry_f", "exit_f", "legs", "sl_final"]
WHY = {"initial SL": "Shuru SL", "breakeven SL": "Breakeven SL", "trailing SL": "Trailing SL", "15:15 square-off": "15:15 square-off"}

RULES_COMMON = [
    ("Stock kaise chune", [
        "Har din NSE ke wo stocks jo us din F&O me NAHI the.",
        "Market filter: Nifty ka gap (aaj open / kal close) -1.5% se +1.5% ke beech ho tabhi us din trade.",
        "Signal candle = 9:15 se 9:25 (10 minute). Volume >= N x pichle 3 din ka 9:15-9:25 average volume, aur turnover >= Rs 5 crore.",
        "Green candle (close > open) = BUY, red = SELL (short), barabar = skip.",
        "Range % = (high - low) / open; sabse bada range wale top 2 stock.",
    ]),
    ("Time", ["1-min candles. Entry 9:25 pe. 15:15 pe sab band."]),
]
MONEY = ("Paisa / risk", [
    "Capital Rs 70,000, compounding NAHI. Din me max 2 trade.",
    "Har trade Rs 35,000 margin, MIS 5x = Rs 1,75,000 exposure; qty = floor(1,75,000 / entry).",
])


def costs_card(slip_txt):
    return ("Kharcha", [f"Kharcha 0.10% (entry value pe) har trade. Slippage {slip_txt} (entry aur exit dono pe)."])


V2_RULES = RULES_COMMON[:1] + [("Time", ["1-min candles. Entry 9:25 pe (9:25 candle ka open). 15:15 pe sab band."]),
                               ("Volume / SL (V2)", ["Volume filter 7x.", "Stop loss fixed 3%: BUY entry x 0.97, SELL entry x 1.03. Bhaav SL ke paar khula to us candle ke open pe exit.",
                                                     "Target nahi, trailing nahi, breakeven nahi. Exit = SL ya 15:15."]), MONEY]
V3_RULES = RULES_COMMON[:1] + [("Time", ["1-min candles. Entry 9:25 pe (9:25 candle ka open). 15:15 pe sab band."]),
                               ("Best match setting (243 me se)", ["Volume filter 5x.", "Stop loss fixed 4% (entry se).",
                                                                  "Breakeven: 1.5% profit pe SL entry pe.", "Trailing: 2% profit ke baad SL best bhaav se 2% peeche chalta hai.",
                                                                  "SL sirf profit ki taraf hilta hai. Exit = koi bhi SL ya 15:15."]),
                               ("243 combinations kaise bane", ["Volume 5x / 7x / 10x, SL candle / 3% / 4%, breakeven 3 type, trailing 3 type, slippage 0 / 0.3 / 0.5% = 3 x 3 x 3 x 3 x 3 = 243.",
                                                                "Best match = jiska result video ke daave (200 trade, 67.5% win, Rs 5.7 lakh ...) ke sabse paas (score sabse kam)."]), MONEY]

FORMULAS = [
    ("Nifty gap %", "(aaj Nifty open / kal Nifty close - 1) x 100;  -1.5% <= gap <= +1.5%"),
    ("Volume ratio", "9:15-9:25 volume / pichle 3 din ka 9:15-9:25 average volume"),
    ("Turnover", "sum((high + low + close) / 3 x volume) 9:15-9:25 ke 10 candles pe;  >= Rs 5 crore"),
    ("Range %", "(10-min high - low) / 9:15 open x 100;  top 2"),
    ("Entry", "9:25 candle ka open"),
    ("Qty", "floor(1,75,000 / entry)"),
    ("Fill (slippage)", "buy fill = bhaav x (1 + slip);  sell fill = bhaav x (1 - slip)"),
    ("Gross", "qty x (sell fill - buy fill)"),
    ("Kharcha", "0.10% x qty x entry"),
    ("Net P&L", "gross - kharcha"),
    ("R multiple", "net / (qty x |entry - shuru SL|)"),
    ("Win rate", "net > 0 wale trade / kul trade x 100"),
    ("Profit factor", "jeete trades ka kul net / hare trades ka kul net"),
    ("Max drawdown", "trade ke kram me kul net ka peak se sabse bada gir"),
    ("Return %", "net / Rs 70,000 x 100"),
]
V2_SL = ("Stop loss", "BUY: entry x 0.97;  SELL: entry x 1.03;  bhaav SL ke paar khula to us candle ka open")
V3_SL = [("Stop loss", "entry x (1 -/+ 4%)"), ("Breakeven", "best bhaav >= entry x 1.015 (buy) -> SL = entry"),
         ("Trailing SL", "best bhaav >= entry x 1.02 (buy) -> SL = best x (1 - 2%);  sell me ulta"), ("Score (best match)", "average(|result - daava| / |daava|) 7 numbers pe; sabse kam = best")]


def old_data(folder):
    s = (RES / folder / "report.html").read_text(encoding="utf-8")
    if "vmu_new" in s[:3000] or "condbar" in s:
        s = (RES / folder / "report_old.html").read_text(encoding="utf-8")
    i = s.index("const D = ") + 10
    return json.loads(s[i:s.index("\n", i)].rstrip().rstrip(";"))


def pack(o, h, l, c, end):
    a = np.round(np.c_[o, h, l, c][:end + 1] * 20).astype(np.int64)
    base = int(a[0, 0])
    return [0, base, (a - base).ravel().tolist()]


def candles_for(t: pd.DataFrame) -> dict:
    G = {}
    for d, g in t.groupby("date"):
        m1 = fast.day_1m(d, symbols=g["symbol"].tolist(), adjust=False)
        if m1.empty:
            continue
        S, o, h, l, c = r11.grid(m1, g["symbol"].tolist())
        for s in g["symbol"]:
            if s in S:
                i = S.index(s)
                G[(d, s)] = (o[i], h[i], l[i], c[i])
    return G


def mins(hhmm):
    a, b = hhmm.split(":")
    return int(a) * 60 + int(b) - 555


def variant(t: pd.DataFrame, slip: float, G: dict, label: str, rules, formulas, settings, extra, wtab, notes, title, sub) -> dict:
    t = t.sort_values(["date", "symbol"]).reset_index(drop=True)
    rows, cand = [], []
    for r in t.itertuples(index=False):
        d = 1 if r.side == "BUY" else -1
        gross0 = d * r.qty * (r.exit - r.entry)
        ef, xf = (r.entry * (1 + slip), r.exit * (1 - slip)) if d == 1 else (r.entry * (1 - slip), r.exit * (1 + slip))
        why = WHY.get(r.exit_reason, r.exit_reason)
        risk = r.qty * abs(r.entry - r.initial_sl)
        slf = r.initial_sl if r.exit_reason == "initial SL" else (r.exit if "SL" in r.exit_reason else None)
        legs = [[r.exit_time, int(r.qty), round(r.exit, 2), why, 1 if d * (r.exit - r.entry) >= 0 else -1]]
        rows.append([r.date, r.symbol, r.side, "09:25", "09:25", r.entry, r.exit_time, r.exit, r.initial_sl, None, why, int(r.qty), round(r.qty * r.entry),
                     round(gross0), round(gross0 - r.gross_pnl), round(r.costs), round(r.net_pnl), round(r.net_pnl / risk, 2) if risk else None,
                     f"range {r.range_pct:.2f}%, volume {r.vol_ratio:.1f}x", round(ef, 2), round(xf, 2), legs, slf])
        g = G.get((r.date, r.symbol))
        cand.append(pack(*g, min(374, mins(r.exit_time) + 15)) if g else None)
    val = t.qty * t.entry
    peak = max((val[t.date == d].sum() for d in t.date.unique()), default=0)
    inv = [["Capital", "Rs 70,000, compounding nahi"], ["Margin har trade", "Rs 35,000 (MIS 5x = Rs 1,75,000 exposure)"], ["Qty rule", "floor(1,75,000 / entry)"],
           ["Max ek stock me", "1 trade / din, ~Rs 1,75,000"], ["Max trade / din", "2 (max ~Rs 3,50,000 exposure, margin Rs 70,000)"],
           ["Average position", f"Rs {val.mean():,.0f}"], ["Sabse badi position", f"Rs {val.max():,.0f}"], ["Ek din me sabse zyada laga (peak)", f"Rs {peak:,.0f}"]]
    return {"title": title, "cond_label": label, "subtitle": sub, "header_sub": "NSE intraday · Volume Move Up (VMU) · backtest & trade audit",
            "cap_text": "Rs 70,000", "generated": datetime.now().strftime("%d %b %Y %H:%M"), "data_range": [t.date.min(), t.date.max()], "capital": CAP,
            "universe_note": "Universe: har din ke non-F&O NSE stocks (data store, AMFI Jun-2026 list se bane: survivorship bias). F&O list futures data ki shuruaati date se andaaza. "
                             "Prices = asli (adjust=False, split adjust nahi). Ye report purane run (31 Oct 2026) ke trades se naye design me dobara bana; numbers wahi hain.",
            "verdict_note": "", "settings": settings, "rules": rules, "assumptions": notes, "formulas": formulas, "invest": inv, "trade_cols": TRADE_COLS,
            "trades": rows, "candles": cand, "days": [], "extra": extra, "wtab": wtab,
            "kpi5": ["Kharcha + slippage", f"Rs {int(t.costs.sum() + sum(r[14] for r in rows)):,}", "0.10% kharcha + slippage"]}


def stat(t):
    w, l = t.net_pnl[t.net_pnl > 0], t.net_pnl[t.net_pnl <= 0]
    return [len(t), round((t.net_pnl > 0).mean() * 100, 1), round(t.net_pnl.sum()), round(w.sum() / -l.sum(), 2) if l.sum() < 0 else None, round(t.net_pnl.min())]


def write(folder, data, title):
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "vmu_new_template.html").read_text(encoding="utf-8")).replace("__CSS__", css).replace("__TITLE__", title)
    blob = json.dumps(data, separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else str(o)).replace("</", "<\\/")
    out = RES / folder
    if not (out / "report_old.html").exists():
        shutil.copy(out / "report.html", out / "report_old.html")
    p = out / "report.html"
    p.write_text(tpl.replace("__CHARTJS__", (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")).replace("__DATA__", blob), encoding="utf-8")
    print(folder, round(p.stat().st_size / 1e6, 2), "MB")


NOTES_V2 = [
    "Slippage dono taraf (entry aur exit) lagti hai. 0.10% kharcha entry value pe.",
    "Turnover = 10 one-minute candles ka sum((high + low + close) / 3 x volume).",
    "F&O list: har stock ke futures data ki shuruaati date se andaaza (sirf abhi ke contracts store me hain). Jo stocks Nov 2026 se pehle F&O se nikal gaye, wo poore period non-F&O gine gaye.",
    "SL entry candle (9:25) pe bhi lag sakta hai. 15:15 candle exit candle hai, isliye SL 15:14 tak check hota hai.",
    "Ek stock ko 9:25 ki candle aur pichle 3 din ka 9:15-9:25 volume chahiye, tabhi wo gina gaya.",
]
NOTES_V3 = NOTES_V2[:1] + [
    "Ek candle me SL aur naya high/low dono chhuye to pehle SL maana. SL ke paar gap khula to us candle ke open pe fill.",
    "Stop candle band hone ke baad hilta hai aur agli candle se lagta hai.",
] + NOTES_V2[2:4]


def v2():
    od = old_data("06_vmu_v2")
    E = od["expected"]
    slips = {"s0": ("0%", 0.0, "trades_slip_0pct.csv"), "s03": ("0.3%", 0.003, "trades_slip_0.3pct.csv"), "s05": ("0.5%", 0.005, "trades_slip_0.5pct.csv")}
    T = {k: pd.read_csv(RES / "06_vmu_v2" / f) for k, (_, _, f) in slips.items()}
    G = candles_for(T["s0"])
    exp_rows = [["Video ka daava", E["trades"], f"{E['win_pct']}%", f"Rs {E['net']:,}", "", f"Rs {E['worst_trade']:,}"]]
    for k, (lab, _, _) in slips.items():
        s = stat(T[k])
        exp_rows.append([f"Backtest, slippage {lab}", s[0], f"{s[1]}%", f"Rs {s[2]:,}", s[3], f"Rs {s[4]:,}"])
    checks = [[c[0], c[1], f"{c[2]}%", f"Rs {c[3]:,}"] for c in od["checks"]]
    data = {}
    for k, (lab, sp, _) in slips.items():
        settings = [["Universe", "non-F&O NSE stocks (har din)", True], ["Nifty gap", "-1.5% se +1.5%"], ["Signal", "9:15-9:25 candle"], ["Volume", "7x (3 din avg)", True],
                    ["Turnover", ">= Rs 5 crore"], ["Pick", "top 2 by range %"], ["Entry", "9:25 open, green = buy, red = sell", True], ["SL", "fixed 3%", True],
                    ["Target / trail", "nahi"], ["Exit", "SL ya 15:15"], ["Capital", "Rs 70,000, compounding nahi", True], ["Position", "Rs 1.75 lakh (5x), max 2 / din"],
                    ["Kharcha", "0.10%"], ["Slippage", lab, True]]
        extra = [{"title": "Video ka daava vs backtest", "hint": "Saare 3 slippage run ek saath.", "head": ["", "Trade", "Win %", "Net", "PF", "Sabse bura trade"], "rows": exp_rows},
                 {"title": "Farq dhoondhne ke checks (0% slippage, ek baar me ek badlav)", "hint": "", "head": ["Badlav", "Trade", "Win %", "Net"], "rows": checks}]
        data[k] = variant(T[k], sp, G, f"Slippage {lab}", V2_RULES + [costs_card(lab)], FORMULAS[:6] + [V2_SL] + FORMULAS[6:], settings, extra, None, NOTES_V2,
                          f"VMU Equity V2 - slippage {lab} (2026-01-01 se 2026-10-23)",
                          f"NSE intraday, 1-min, non-F&O stocks | volume 7x, SL 3% | capital Rs 70,000, COMPOUNDING NAHI | slippage {lab} + 0.10% kharcha | report naye design me: {datetime.now():%d %b %Y}")
    write("06_vmu_v2", data, "VMU Equity V2")


def v3():
    od = old_data("05_vmu_v3")
    E, B = od["expected"], od["best"]
    t = pd.read_csv(RES / "05_vmu_v3" / "trades_best_match.csv")
    G = candles_for(t)
    s = stat(t)
    exp_rows = [["Video ka daava", E["trades"], f"{E['win_pct']}%", f"Rs {E['net']:,}", f"Rs {E['avg_loss']:,}", f"Rs {E['worst_trade']:,}", E["median_hold_h"], f"{E['sl_pct']}%"],
                ["Best match", B["trades"], f"{B['win_pct']}%", f"Rs {B['net']:,}", f"Rs {B['avg_loss']:,}", f"Rs {B['worst_trade']:,}", B["median_hold_h"], f"{B['sl_pct']}%"]]
    gc = od["grid_cols"]
    ix = {c: i for i, c in enumerate(gc)}
    grid = [[r[ix["vol_mult"]], r[ix["sl_mode"]], r[ix["be"]], r[ix["trail"]], r[ix["slip"]], r[ix["trades"]], r[ix["win_pct"]], r[ix["net"]], r[ix["profit_factor"]],
             r[ix["max_dd"]], r[ix["median_hold_h"]], r[ix["sl_pct"]], r[ix["score"]], r[ix["best"]]] for r in od["grid"]]
    pc = lambda v: None if v is None else f"{v * 100:g}%"
    grid = [[f"{g[0]}x", g[1], pc(g[2]) or "nahi", pc(g[3]) or "nahi", pc(g[4])] + g[5:] for g in grid]
    wtab = {"label": "Saare combinations", "title": "243 combinations", "hint": "Har setting ka result. Haan = best match (video ke daave ke sabse paas). Score kam = daave ke zyada paas.",
            "filter_col": 4, "filter_label": "Saari slippage", "sort": [12, 1], "file": "combinations.csv", "hl_col": 13,
            "heads": ["volume", "sl_mode", "breakeven", "trail", "slippage", "trades", "win_pct", "net", "pf", "max_dd", "median_hold_h", "initial_sl_pct", "score", "best"],
            "cols": [["Volume"], ["SL"], ["Breakeven"], ["Trail"], ["Slippage"], ["Trade", "f0"], ["Win %", "f2"], ["Net", "rs", 1], ["PF", "f2"], ["Max DD", "rs", 1],
                     ["Median hold (ghante)", "f2"], ["Shuru SL %", "f2"], ["Score", "f2"], ["Best"]],
            "rows": grid}
    settings = [["Universe", "non-F&O NSE stocks (har din)", True], ["Nifty gap", "-1.5% se +1.5%"], ["Signal", "9:15-9:25 candle"], ["Volume", "5x (3 din avg)", True],
                ["Turnover", ">= Rs 5 crore"], ["Pick", "top 2 by range %"], ["Entry", "9:25 open, green = buy, red = sell", True], ["SL", "fixed 4%", True],
                ["Breakeven", "1.5% pe"], ["Trailing", "2% ke baad, 2% gap"], ["Exit", "SL ya 15:15"], ["Capital", "Rs 70,000, compounding nahi", True],
                ["Position", "Rs 1.75 lakh (5x), max 2 / din"], ["Kharcha", "0.10%"], ["Slippage", "0%", True], ["Combinations", "243 test kiye"]]
    extra = [{"title": "Video ka daava vs best match", "hint": "243 me se jo daave ke sabse paas tha.", "head": ["", "Trade", "Win %", "Net", "Avg loss", "Sabse bura", "Median hold (ghante)", "Shuru SL %"], "rows": exp_rows}]
    data = {"best": variant(t, 0.0, G, "Best match (243 me se)", V3_RULES + [costs_card("0%")], FORMULAS[:6] + V3_SL + FORMULAS[6:], settings, extra, wtab, NOTES_V3,
                            "VMU Equity V3 - best match (2026-01-01 se 2026-10-23)",
                            f"NSE intraday, 1-min, non-F&O stocks | 243 combinations, best match: volume 5x, SL 4%, breakeven 1.5%, trail 2% | capital Rs 70,000, COMPOUNDING NAHI | slippage 0% + 0.10% kharcha | report naye design me: {datetime.now():%d %b %Y}")}
    write("05_vmu_v3", data, "VMU Equity V3")
    return s


if __name__ == "__main__":
    v2()
    v3()

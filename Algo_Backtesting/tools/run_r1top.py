"""Rule No 1 backtest: top gainer + Standard Pivot R3 breakout (5-min, long, MIS). See src/backtest/r1top.py.

    python tools/run_r1top.py                              # today (latest day in the store)
    python tools/run_r1top.py --start 2026-10-01 --end 2026-11-19 --name r1top_oct_nov
    python tools/run_r1top.py --no-telegram                 # quick local check
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backtest import fast, r1top  # noqa: E402
from backtest import viewer as viewer_assets  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402
from backtest.r1top import CFG, COST, hhmm  # noqa: E402
from backtest.report import ASSETS  # noqa: E402

SITE = "https://santoshkumar.infinityfree.io/"
TRADE_COLS = ["date", "sym", "t_sig", "t_in", "entry", "t_out", "exit", "sl", "tgt", "reason", "qty", "value", "gross0", "slip", "charges", "net", "R", "info",
              "c_brokerage", "c_stt", "c_exchange", "c_stamp", "c_gst", "entry_f", "exit_f", "r3", "part", "legs", "sl_final"]

RULES = [
    ("Stock kaise chune (watchlist)", [
        "Top gainer: 9:20, 9:25 aur 9:30 pe har stock ka pichle din ke close se % gain dekho; filters pass karne wale top 20 = watchlist.",
        "Price kam se kam Rs 30.",
        "Pichle 20 din ka average volume kam se kam 5 lakh shares.",
        "ATR (14 din) kam se kam 2% aur aaj ka gap-up kam se kam 2%.",
    ]),
    ("Time", [
        "5-min candle. Market 9:15 pe khulta hai.",
        "Sirf teen candle dekhte hain: jo 9:20, 9:25 aur 9:30 pe close hoti hain. 9:30 ke baad naya trade nahi.",
        "15:00 pe jo bhi khula hai sab band.",
    ]),
    ("Entry (sirf long)", [
        "R3 = Standard Pivot ka R3, kal ke High / Low / Close se.",
        "Watchlist ka stock jiski 5-min candle pehli baar R3 ke upar close ho -> agle minute ke open pe market buy.",
        "Ek stock me din me sirf 1 trade.",
    ]),
    ("Exit", [
        "Stop loss: entry se 2% neeche (SL-M order).",
        "Target: 4% upar (risk:reward 1:2) - baaki qty yahan bechi.",
        "Stock 2% profit pe pahunche to 50% qty bech do.",
        "Trailing SL: 1% profit pe SL 1% upar (-2% se -1%), phir har 1% aur profit pe SL 1% aur upar (2% pe entry, 3% pe +1% ...).",
        "15:00 pe sab band.",
    ]),
    ("Paisa / risk", [
        "Capital Rs 1 crore, MIS 5x leverage. Profit dobara nahi lagaya (compounding nahi).",
        "Risk har trade capital ka 2% = Rs 2 lakh. Qty = 2,00,000 / (entry - SL).",
        "Ek saath max 3 position. Ek hi candle pe zyada signal ho to sabse bada gainer pehle.",
        "Din ka loss (band + khule trade) 2% = Rs 2 lakh pahunche to sab band, us din naya trade nahi.",
    ]),
    ("Kharcha", [
        "Slippage 0.10% har fill pe (buy aur sell dono).",
        "Angel One intraday charges: brokerage Rs 20 ya 0.03% (jo kam ho) har order, STT 0.025% sell pe, NSE 0.00297%, SEBI, stamp 0.003% buy pe, GST 18%.",
    ]),
]

ASSUMPTIONS = [
    "Firstock ki purani 'top gainer' list nahi milti, isliye data se banayi: filters pass karne wale stocks me pichle close se % gain ke hisaab se top 20 (har check time pe nayi list).",
    "'R3 ke upar close' = 5-min candle ka close R3 se bada. Agar candle pehle se R3 ke upar khuli (bada gap), tab bhi signal maana.",
    "Entry signal candle ke turant baad wale minute ke open pe (market order + 0.10% slippage). SL-M sirf stop loss ke liye.",
    "Ek minute ke andar SL aur target dono chhuye to pehle SL maana (safe side). Bhaav SL ke neeche khula to open pe exit.",
    "Trailing SL minute ke high se update hota hai aur agle minute se lagta hai.",
    "Daily loss limit me band trade ka net + khule trade ka current loss (slippage ke saath) gina; hit hone pe agle minute ke open pe sab band.",
    "Ek SL hit = Rs 2 lakh risk + slippage + charges, isliye ek hi poora SL lagte hi daily loss limit bhi hit ho jaati hai.",
    "Avg volume / ATR / gap sirf pichle dino ke data se (aaj ka future data use nahi kiya).",
]

FORMULAS = [
    ("% gain (top gainer)", "(candle close / kal ka close - 1) x 100"),
    ("Gap %", "(aaj ka open / kal ka close - 1) x 100"),
    ("ATR %", "average(True Range, pichle 14 din) / kal ka close x 100;  True Range = max(H-L, |H-kal C|, |L-kal C|)"),
    ("Avg volume", "pichle 20 din ke volume ka average (aaj shaamil nahi)"),
    ("Pivot (Standard)", "P = (kal H + kal L + kal C) / 3"),
    ("R3 (Standard)", "R3 = kal H + 2 x (P - kal L)"),
    ("Signal", "5-min candle close (9:20 / 9:25 / 9:30) > R3, pehli baar"),
    ("Entry", "agle minute ka open;  entry fill = entry x (1 + 0.10%)"),
    ("Stop loss", "SL = entry x (1 - 2%)"),
    ("Target", "Target = entry x (1 + 2 x 2%) = entry x 1.04"),
    ("50% exit", "high >= entry x 1.02 -> qty ka 50% entry x 1.02 pe becho"),
    ("Trailing SL", "n = floor((max high / entry - 1) x 100 - 1) + 1  (jab profit >= 1%);  SL = entry x (1 + (-2 + n x 1)%)"),
    ("Exit fill", "exit x (1 - 0.10%)  (SL / target / time / loss limit sab)"),
    ("Qty", "floor(1,00,00,000 x 2% / (entry x 2%))  ->  position ~ Rs 1 crore;  margin = value / 5 (MIS), kul margin <= capital"),
    ("Gross", "sum(qty_leg x (exit_leg - entry))"),
    ("Slippage Rs", "gross - sum(qty_leg x (exit fill - entry fill))"),
    ("Charges", "brokerage min(20, 0.03% x order value) har order + STT 0.025% x sell + NSE 0.00297% x turnover + SEBI 0.0001% x turnover + stamp 0.003% x buy + GST 18% x (brokerage + NSE + SEBI)"),
    ("Net P&L", "sum(qty_leg x (exit fill - entry fill)) - charges"),
    ("R multiple", "net / (capital x 2%)"),
    ("Daily loss limit", "band trades ka net + khule trades ka (close x (1 - 0.10%) - entry fill) x qty <= -2% x capital"),
    ("Win rate", "net > 0 wale trade / kul trade x 100"),
    ("Profit factor", "jeete trades ka kul net / hare trades ka kul net (positive)"),
    ("Max drawdown", "trade band hone ke kram me kul net ka peak se sabse bada gir"),
    ("Return %", "net / 1,00,00,000 x 100"),
]


def _clean(v):
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, np.bool_):
        return bool(v)
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float):
        return round(v, 4) if np.isfinite(v) else None
    return v


def pack(o, h, l, c):
    """Whole-day 1-min candles (9:15 .. 15:05) -> [first minute, base, flat (o, h, l, c) - base] in Rs 0.05 ticks."""
    a = np.round(np.c_[o, h, l, c][:351] * 20).astype(np.int64)
    base = int(a[0, 0])
    return [0, base, (a - base).ravel().tolist()]


def trade_rows(T: pd.DataFrame) -> list:
    rows = []
    for t in T.itertuples(index=False):
        ch = t.charges
        legs = [[hhmm(m), q, round(p, 2), why, 1 if p >= t.entry else -1] for m, q, p, f, why in t.legs]
        info = f"gain {t.chg:+.2f}% (rank {t.rank}), gap {t.gap:+.2f}%, ATR {t.atr:.2f}%, candle close {t.sig_close:.2f}"
        rows.append([t.date, t.sym, t.t_sig, hhmm(t.e), round(t.entry, 2), hhmm(t.x), round(t.exit, 2), round(t.sl0, 2), round(t.tgt, 2), t.reason, int(t.qty),
                     round(t.qty * t.entry), round(t.gross0), round(t.slip_rs), round(ch["total"]), round(t.net), round(t.net / (CFG["capital"] * CFG["risk_pct"] / 100), 2), info,
                     round(ch["brokerage"], 2), round(ch["stt"], 2), round(ch["exchange"], 2), round(ch["stamp"], 2), round(ch["gst"], 2),
                     round(t.entry_f, 2), round(t.exit_f, 2), round(t.r3, 2), round(t.part, 2), legs, round(t.sl, 2)])
    return rows


def build(days, T, W, info, G, n_universe) -> dict:
    T = T.sort_values(["date", "e", "rank"]).reset_index(drop=True) if len(T) else T
    rows = trade_rows(T) if len(T) else []
    candles = [pack(*G[(t.date, t.sym)]) for t in T.itertuples(index=False)] if len(T) else []
    watch = []
    if len(W):
        for r in W.sort_values(["date", "k", "rank"]).itertuples(index=False):
            watch.append([r.date, r.t, int(r.rank), r.symbol, round(r.chg, 2), round(r.gap_pct, 2), round(r.atr_pct, 2), round(r.avg_vol / 1e5, 2), round(r.c5, 2),
                          round(r.r3, 2), round((r.c5 / r.r3 - 1) * 100, 2), "haan" if r.signal else ("R3 ke upar (signal pehle aa chuka)" if r.above_r3 else "nahi")])
    dd = []
    for d in info:
        td = T[T["date"] == d["date"]] if len(T) else T
        dd.append({"date": d["date"], "watch": d["passed_filters"], "signals": d["signals"], "trades": len(td), "wins": int((td["net"] > 0).sum()) if len(td) else 0,
                   "net": round(float(d["net"])), "killed": d["killed"]})
    skipped = [[d["date"], s, t, why] for d in info for s, t, why in d["skipped"]]
    cap = CFG["capital"]
    if len(T):
        val = T["qty"] * T["entry"]
        T2 = T.assign(val=val)
        peak = 0.0
        for d, g in T2.groupby("date"):
            for m in range(375):
                peak = max(peak, g[(g["e"] <= m) & (g["x"] > m)]["val"].sum())
        inv = [["Capital", "Rs 1,00,00,000 (1 crore), compounding nahi"], ["Risk har trade", "2% = Rs 2,00,000"], ["Qty rule", "2,00,000 / (entry x 2%) -> position ~ Rs 1 crore"],
               ["Leverage", "MIS 5x -> Rs 1 crore position pe ~Rs 20 lakh margin"], ["Max ek stock me", "1 trade / din, ~Rs 1 crore"], ["Max khuli position", "3 (max ~Rs 3 crore, margin ~Rs 60 lakh)"],
               ["Average position", f"Rs {val.mean():,.0f}"], ["Sabse badi position", f"Rs {val.max():,.0f}"],
               ["Ek saath sabse zyada laga (peak)", f"Rs {peak:,.0f} (margin Rs {peak / CFG['leverage']:,.0f})"]]
    else:
        inv = [["Capital", "Rs 1,00,00,000 (1 crore), compounding nahi"], ["Risk har trade", "2% = Rs 2,00,000"], ["Qty rule", "2,00,000 / (entry x 2%)"],
               ["Leverage", "MIS 5x"], ["Max khuli position", "3"], ["Peak paisa laga", "Rs 0 (koi trade nahi)"]]
    settings = [["Watchlist", f"top {CFG['top_n']} gainer (data se)", True], ["Universe", f"{n_universe} NSE stocks (store)"], ["Price", ">= Rs 30"],
                ["Avg volume", ">= 5 lakh shares (20 din)"], ["ATR", ">= 2%", True], ["Gap-up", ">= 2%", True], ["Timeframe", "5-min"],
                ["Entry", "R3 (Standard) ke upar pehli close, 9:20 / 9:25 / 9:30", True], ["Side", "sirf long"], ["SL / Target", "2% / 4% (1:2)", True],
                ["50% exit", "+2% pe"], ["Trailing", "1% trigger, 1% step"], ["Time exit", "15:00"], ["Capital", "Rs 1 crore, compounding nahi", True],
                ["Risk / trade", "2% (Rs 2 lakh)"], ["Leverage", "MIS 5x"], ["Max open", "3"], ["Daily loss limit", "2% (Rs 2 lakh)"],
                ["Slippage", "0.10% har fill", True], ["Charges", "Angel One intraday"], ["Order", "Market entry, SL-M stop"]]
    one = len(days) == 1
    note = ""
    if any(d["killed"] for d in info):
        note = "Daily loss limit " + ", ".join(f"{d['date']} {d['killed']}" for d in info if d["killed"]) + " pe hit hui."
    return _clean({
        "title": f"Rule No 1 - Top gainer R3 breakout ({days[0] if one else days[0] + ' se ' + days[-1]})",
        "subtitle": f"NSE intraday, 5-min, sirf long, MIS 5x | {'sirf ' + days[0] + ' ka din' if one else str(len(days)) + ' trading din'} | capital Rs 1 crore, COMPOUNDING NAHI | slippage 0.10% + Angel One charges on | report bana: {datetime.now():%d %b %Y %H:%M}",
        "generated": datetime.now().strftime("%d %b %Y %H:%M"), "data_range": [days[0], days[-1]], "capital": cap, "n_universe": n_universe,
        "verdict_note": note + (" Sirf ek din ka test hai - isse strategy ke baare me pakka faisla nahi kar sakte." if one else ""),
        "settings": settings, "rules": RULES, "assumptions": ASSUMPTIONS, "formulas": FORMULAS, "invest": inv, "trade_cols": TRADE_COLS,
        "trades": rows, "candles": candles, "watch": watch, "days": dd, "skipped": skipped,
    })


def write_html(out: Path, data: dict) -> Path:
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "r1top_template.html").read_text(encoding="utf-8")).replace("__CSS__", css).replace("__TITLE__", "Rule No 1 R3 Breakout")
    blob = json.dumps(data, separators=(",", ":"), default=str).replace("</", "<\\/")
    p = out / "report.html"
    p.write_text(tpl.replace("__CHARTJS__", (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")).replace("__DATA__", blob), encoding="utf-8")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start"), ap.add_argument("--end"), ap.add_argument("--name")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    last = fast.days()[-1]
    d0, d1 = a.start or a.end or last, a.end or a.start or last
    days = fast.days(d0, d1)
    if not days:
        print(f"no store days between {d0} and {d1}")
        return 1
    name = a.name or (f"r1top_{days[0].replace('-', '')}" if len(days) == 1 else f"r1top_{days[0].replace('-', '')}_{days[-1].replace('-', '')}")
    out = result_dir(name)
    tg = (lambda s: None) if a.no_telegram else send
    t0 = time.time()
    tg(f"Rule No 1 backtest run shuru: {days[0]} -> {days[-1]} ({len(days)} din), folder {out.name}")
    T, W, info, G = r1top.run(days)
    n_universe = int(fast.panel(days[-1], days[-1], columns=["close"])["symbol"].nunique())
    data = build(days, T, W, info, G, n_universe)
    rep = write_html(out, data)
    if len(T):
        T.drop(columns=["legs", "charges", "i"]).to_csv(out / "trades.csv", index=False)
    if len(W):
        W.to_csv(out / "watchlist.csv", index=False)
    net = sum(r[TRADE_COLS.index("net")] for r in data["trades"])
    nt = len(data["trades"])
    wins = sum(1 for r in data["trades"] if r[TRADE_COLS.index("net")] > 0)
    summary = {"folder": out.name, "days": days, "trades": nt, "wins": wins, "net": round(net), "skipped": data["skipped"], "day_rows": data["days"],
               "report_mb": round(rep.stat().st_size / 1e6, 2), "secs": round(time.time() - t0)}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

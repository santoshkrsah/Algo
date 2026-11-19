"""Rule No 1.1 backtest: Pivot R3 breakout (buy) + R3 reversal (sell), 5-min, MIS. See src/backtest/r11.py.

    python tools/run_r11.py --start 2021-10-01 --end 2026-11-18 --name r11_r3_2021_2026
    python tools/run_r11.py --start 2026-10-01 --end 2026-10-31 --name r11_test --no-telegram
"""
import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backtest import fast, r11  # noqa: E402
from backtest import viewer as viewer_assets  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.notify import send  # noqa: E402
from backtest.r11 import CFG, hhmm  # noqa: E402
from backtest.report import ASSETS  # noqa: E402

TRADE_COLS = ["date", "sym", "side", "t_sig", "t_in", "entry", "t_out", "exit", "sl", "tgt", "reason", "qty", "value", "gross0", "slip", "charges", "net", "R", "info",
              "c_brokerage", "c_stt", "c_exchange", "c_stamp", "c_gst", "entry_f", "exit_f", "r3", "part", "legs", "sl_final"]

RULES = [
    ("Stock kaise chune", [
        "Company ka market cap Rs 5000 crore se upar (us samay ki AMFI list se).",
        "Price kam se kam Rs 30.",
        "Pichle 20 din ka average volume kam se kam 5 lakh shares.",
        "ATR (14 din) kam se kam 2% aur aaj ka gap-up kam se kam 2% (buy aur sell dono ke liye gap-up).",
    ]),
    ("Time", [
        "5-min candle. Market 9:15 pe khulta hai.",
        "Signal sirf un candles se: jo 9:20, 9:25 aur 9:30 pe close hoti hain. 9:30 ke baad naya signal nahi.",
        "Order (trigger) 15:00 tak khula rehta hai. 15:00 pe jo bhi khula hai sab band.",
    ]),
    ("Entry - Buy", [
        "R3 = Standard Pivot ka R3 (kal ke High / Low / Close se).",
        "Pehli GREEN 5-min candle jo R3 ke upar close ho -> uske HIGH ke upar buy (SL-M stop order).",
        "Din me max 3 buy trade: jo pehle trigger ho wahi pehle.",
    ]),
    ("Entry - Sell (short)", [
        "Pehli RED 5-min candle jo R3 ke upar close ho -> uske LOW ke neeche sell (SL-M stop order).",
        "Din me max 3 sell trade: 3 se zyada signal ho to sabse bade gap wale 3.",
    ]),
    ("Exit", [
        "Buy ka SL: entry se 2% neeche. Sell ka SL: din ki pehli 5-min candle (9:15-9:20) ka high.",
        "Target: risk ka 2 guna (1:2).",
        "2% profit pe 50% qty band.",
        "Trailing SL: 1% profit pe SL 1% (entry ka) aage, phir har 1% aur profit pe SL 1% aur aage.",
        "15:00 pe sab band.",
    ]),
    ("Paisa / risk", [
        "Capital Rs 1 crore har din (compounding NAHI). MIS 5x.",
        "Risk har trade 2% = Rs 2 lakh. Qty = 2,00,000 / (entry - SL), lekin ek position max Rs 1 crore x 5 / 6 = Rs 83.3 lakh.",
        "Ek saath max 6 position (3 buy + 3 sell). Ek stock me din me 1 hi trade.",
        "Din ka kul loss (band + khule trade) 2% = Rs 2 lakh pahunche to sab band, us din naya trade nahi.",
    ]),
    ("Kharcha", [
        "Slippage 0.10% har fill pe.",
        "Angel One intraday charges: brokerage Rs 20 ya 0.03% (jo kam ho) har order, STT 0.025% sell pe, NSE 0.00297%, SEBI, stamp 0.003% buy pe, GST 18%.",
    ]),
]

ASSUMPTIONS = [
    "Market cap: AMFI ki 6-mahine wali list (30 Jun / 31 Dec) agle mahine ki 10 taarikh se use ki (jab wo public ho chuki hoti hai). Aage ki list kabhi use nahi ki.",
    "Price >= 30 aaj ke open pe check kiya. Avg volume / ATR sirf pichle dino se.",
    "Sell ke liye bhi gap-up >= 2% (aapke jawab ke hisaab se).",
    "Ek stock pe buy aur sell dono signal aa sakte hain; jo pehle trigger ho wahi trade, doosra cancel (1 trade / stock / din).",
    "Sell orders 9:20 / 9:25 / 9:30 pe gap ke hisaab se dobara rank hote hain: jo top 3 me nahi, uska order us waqt live nahi.",
    "Agar sell entry pehli candle ke high se upar ho (SL galat side) to trade nahi liya.",
    "Ek minute me SL aur target dono chhuye to pehle SL maana (safe side). Bhaav trigger / SL ke paar khula to open pe fill.",
    "Qty cap: har position max Rs 1.67 crore (5x / 3 position).",
    "Trailing SL minute ke high (buy) / low (sell) se update hota hai, agle minute se lagta hai.",
]

FORMULAS = [
    ("Gap %", "(aaj ka open / kal ka close - 1) x 100"),
    ("ATR %", "average(True Range, pichle 14 din) / kal ka close x 100;  True Range = max(H-L, |H-kal C|, |L-kal C|)"),
    ("Avg volume", "pichle 20 din ke volume ka average"),
    ("Pivot / R3", "P = (kal H + kal L + kal C) / 3;  R3 = kal H + 2 x (P - kal L)"),
    ("Buy signal", "5-min candle (9:20 / 9:25 / 9:30) close > open AND close > R3, pehli baar"),
    ("Sell signal", "5-min candle close < open AND close > R3, pehli baar"),
    ("Entry", "buy = max(minute open, candle high) jab high toote;  sell = min(minute open, candle low) jab low toote"),
    ("Stop loss", "buy: entry x 0.98;  sell: 9:15-9:20 candle ka high"),
    ("Risk / share", "|entry - SL|"),
    ("Target", "buy: entry + 2 x risk;  sell: entry - 2 x risk"),
    ("50% exit", "buy: entry x 1.02;  sell: entry x 0.98 -> qty ka 50%"),
    ("Trailing SL", "n = floor(max profit % - 1) + 1 (jab profit >= 1%);  buy SL = shuru SL + n x 1% x entry;  sell SL = shuru SL - n x 1% x entry"),
    ("Qty", "min(2,00,000 / risk per share, 1,66,66,666 / entry)  (1.67 crore = 1 crore x 5 / 3)"),
    ("Fill (slippage)", "buy fill = bhaav x 1.001;  sell fill = bhaav x 0.999"),
    ("Gross", "buy: sum(qty x (exit - entry));  sell: sum(qty x (entry - exit))"),
    ("Charges", "brokerage min(20, 0.03% x order) har order + STT 0.025% x sell value + NSE 0.00297% x turnover + SEBI 0.0001% x turnover + stamp 0.003% x buy value + GST 18% x (brokerage + NSE + SEBI)"),
    ("Net P&L", "fills se P&L - charges"),
    ("R multiple", "net / Rs 2,00,000"),
    ("Daily loss limit", "band trades ka net + khule trades ka MTM (minute close pe, slippage ke saath) <= -Rs 2,00,000"),
    ("Win rate", "net > 0 wale trade / kul trade x 100"),
    ("Profit factor", "jeete trades ka kul net / hare trades ka kul net"),
    ("Max drawdown", "trade band hone ke kram me kul net ka peak se sabse bada gir"),
    ("Return %", "net / 1,00,00,000 x 100"),
]


COND = {
    "c1": {"label": "Condition 1 - Buy", "side": "BUY", "cfg": {"sides": ("BUY",), "max_open": 3},
           "what": "R3 ke upar pehli GREEN candle ka high toote to buy"},
    "c2": {"label": "Condition 2 - Sell", "side": "SELL", "cfg": {"sides": ("SELL",), "max_open": 3},
           "what": "R3 ke upar pehli RED candle ka low toote to sell (short)"},
}


def rules_for(side: str) -> list:
    """Only the rules of one condition (Buy or Sell)."""
    out = []
    for head, items in RULES:
        if head == ("Entry - Sell (short)" if side == "BUY" else "Entry - Buy"):
            continue
        if head == "Entry - Sell (short)":
            items = ["R3 = Standard Pivot ka R3 (kal ke High / Low / Close se)."] + items
        if head == "Exit":
            items = [("Buy ka SL: entry se 2% neeche." if side == "BUY" else "Sell ka SL: din ki pehli 5-min candle (9:15-9:20) ka high.")] + items[1:]
        if head == "Paisa / risk":
            items = ["Capital Rs 1 crore har din (compounding NAHI). MIS 5x. Ye condition akele chali (apna capital, apna loss limit).",
                     "Risk har trade 2% = Rs 2 lakh. Qty = 2,00,000 / (entry - SL), lekin ek position max Rs 1 crore x 5 / 3 = Rs 1.67 crore.",
                     "Ek saath max 3 position. Ek stock me din me 1 hi trade.",
                     "Din ka kul loss (band + khule trade) 2% = Rs 2 lakh pahunche to sab band, us din naya trade nahi."]
        out.append((head, items))
    return out


def assumptions_for(side: str) -> list:
    out = []
    for a in ASSUMPTIONS:
        if "buy aur sell dono signal" in a:
            continue
        if side == "BUY" and ("Sell orders" in a or "sell entry" in a or "Sell ke liye" in a):
            continue
        out.append(a)
    return out


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


def pack(o, h, l, c, end):
    a = np.round(np.c_[o, h, l, c][:end + 1] * 20).astype(np.int64)
    base = int(a[0, 0])
    return [0, base, (a - base).ravel().tolist()]


def trade_rows(T: pd.DataFrame) -> list:
    rows = []
    for t in T.itertuples(index=False):
        ch = t.charges
        d = 1 if t.side == "BUY" else -1
        legs = [[hhmm(m), q, round(p, 2), why, 1 if d * (p - t.entry) >= 0 else -1] for m, q, p, f, why in t.legs]
        info = f"gap {t.gap:+.2f}%, ATR {t.atr:.2f}%, mcap {t.mcap:,.0f} cr, candle O/C {t.o5:.2f}/{t.c5:.2f}, trigger {t.trig:.2f}"
        rows.append([t.date, t.sym, t.side, t.t_sig, hhmm(t.e), round(t.entry, 2), hhmm(t.x), round(t.exit, 2), round(t.sl0, 2), round(t.tgt, 2), t.reason, int(t.qty),
                     round(t.qty * t.entry), round(t.gross0), round(t.slip_rs), round(ch["total"]), round(t.net), round(t.net / (CFG["capital"] * CFG["risk_pct"] / 100), 2), info,
                     round(ch["brokerage"], 2), round(ch["stt"], 2), round(ch["exchange"], 2), round(ch["stamp"], 2), round(ch["gst"], 2),
                     round(t.entry_f, 2), round(t.exit_f, 2), round(t.r3, 2), round(t.part, 2), legs, round(t.sl, 2)])
    return rows


def build(days, T, SG, info, G, ck: str) -> dict:
    cd = COND[ck]
    side_hi = cd["side"]
    T = T.sort_values(["date", "e", "sym"]).reset_index(drop=True) if len(T) else T
    rows = trade_rows(T) if len(T) else []
    candles = [pack(*G[(t.date, t.sym)], min(374, int(t.x) + 15)) for t in T.itertuples(index=False)] if len(T) else []
    skip_map = {(d["date"], s, sd): why for d in info for s, sd, _, why in d["skipped"]}
    tr_set = {(t.date, t.sym, t.side) for t in T.itertuples(index=False)} if len(T) else set()
    watch = []
    if len(SG):
        for r in SG.sort_values(["date", "k", "side", "symbol"]).itertuples(index=False):
            key = (r.date, r.symbol, r.side)
            res = "trade hua" if key in tr_set else skip_map.get(key, "loss limit ke baad aaya (din band)")
            watch.append([r.date, r.t, r.side, r.symbol, round(r.gap, 2), round(r.atr, 2), round(r.avg_vol / 1e5, 2), round(r.mcap), round(r.o5, 2), round(r.c5, 2),
                          round(r.r3, 2), round(r.trig, 2), res])
    cnt = Counter((w[12], w[2]) for w in watch if w[12] != "trade hua")
    skip_counts = sorted([[k, cnt.get((k, "BUY"), 0), cnt.get((k, "SELL"), 0)] for k in {k for k, _ in cnt}], key=lambda x: -(x[1] + x[2]))
    dd = []
    for d in info:
        td = T[T["date"] == d["date"]] if len(T) else T
        dd.append({"date": d["date"], "watch": d["passed"], "signals": d["signals"], "buy_sig": d["buy_sig"], "sell_sig": d["sell_sig"], "trades": len(td),
                   "wins": int((td["net"] > 0).sum()) if len(td) else 0, "net": round(float(d["net"])), "killed": d["killed"]})
    cap = CFG["capital"]
    if len(T):
        val = T["qty"] * T["entry"]
        peak = 0.0
        for d, g in T.assign(val=val).groupby("date"):
            for m in range(375):
                peak = max(peak, g[(g["e"] <= m) & (g["x"] > m)]["val"].sum())
        inv = [["Capital", "Rs 1,00,00,000 (1 crore) har din, compounding nahi"], ["Risk har trade", "2% = Rs 2,00,000"],
               ["Qty rule", "min(2,00,000 / (entry - SL), Rs 1.67 crore / entry)"], ["Leverage", "MIS 5x -> Rs 5 crore buying power"],
               ["Max ek stock me", "1 trade / din, max Rs 1.67 crore"], ["Max khuli position", "3, max Rs 5 crore, margin Rs 1 crore"],
               ["Average position", f"Rs {val.mean():,.0f}"], ["Sabse badi position", f"Rs {val.max():,.0f}"],
               ["Ek saath sabse zyada laga (peak)", f"Rs {peak:,.0f} (margin Rs {peak / CFG['leverage']:,.0f})"]]
    else:
        inv = [["Capital", "Rs 1 crore, compounding nahi"], ["Peak paisa laga", "Rs 0 (koi trade nahi)"]]
    settings = [["Universe", "market cap >= Rs 5000 cr (AMFI, us samay ki list)", True], ["Price", ">= Rs 30"], ["Avg volume", ">= 5 lakh shares (20 din)"],
                ["ATR", ">= 2%", True], ["Gap-up", ">= 2%", True], ["Timeframe", "5-min"],
                ["Condition", cd["label"], True], ["Entry", cd["what"], True],
                ["Signal candles", "9:20 / 9:25 / 9:30"], ["Trigger valid", "15:00 tak"],
                ["Max trade", "3 / din"], ["Pick", "jo pehle trigger ho" if side_hi == "BUY" else "sabse bada gap"],
                ["SL", "entry se 2%" if side_hi == "BUY" else "pehli 5-min candle ka high", True], ["Target", "1:2"], ["50% exit", "2% profit pe"], ["Trailing", "1% trigger, 1% step"],
                ["Time exit", "15:00"], ["Capital", "Rs 1 crore, compounding nahi", True], ["Risk / trade", "2% (Rs 2 lakh)"], ["Leverage", "MIS 5x, max Rs 1.67 cr / position"],
                ["Max open", "3"], ["Daily loss limit", "2% (Rs 2 lakh), band + khule"], ["Slippage", "0.10% har fill", True], ["Charges", "Angel One intraday"], ["Order", "SL-M (stop market)"]]
    note = f"Daily loss limit {sum(1 for d in info if d['killed'])} din hit hui."
    return _clean({
        "title": f"Rule No 1.1 - {cd['label']} ({days[0]} se {days[-1]})", "cond": ck, "cond_label": cd["label"],
        "subtitle": f"NSE intraday, 5-min, {cd['label']} (akele chali), MIS 5x | {len(days)} trading din | capital Rs 1 crore har din, COMPOUNDING NAHI | slippage 0.10% + Angel One charges on | report bana: {datetime.now():%d %b %Y %H:%M}",
        "generated": datetime.now().strftime("%d %b %Y %H:%M"), "data_range": [days[0], days[-1]], "capital": cap,
        "universe_note": "Stock list: data store ke NSE stocks (AMFI Jun-2026 list se bane, survivorship bias) jinka market cap us samay ki AMFI list me >= 5000 cr tha. "
                         "Har 6-mahine ke 5000cr+ stocks me se ~3-4% (jaise HDFC, MINDTREE, ISEC jo merge / delist ho gaye) ka price data nahi hai. Prices split/bonus adjusted.",
        "verdict_note": note, "settings": settings, "rules": rules_for(side_hi), "assumptions": assumptions_for(side_hi), "formulas": FORMULAS, "invest": inv, "trade_cols": TRADE_COLS,
        "trades": rows, "candles": candles, "watch": watch, "days": dd, "skip_counts": skip_counts, "skipped": [],
    })


def write_html(out: Path, data: dict) -> Path:
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "r11_template.html").read_text(encoding="utf-8")).replace("__CSS__", css).replace("__TITLE__", "Rule No 1.1 R3 Buy Sell")
    blob = json.dumps(data, separators=(",", ":"), default=str).replace("</", "<\\/")
    p = out / "report.html"
    p.write_text(tpl.replace("__CHARTJS__", (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")).replace("__DATA__", blob), encoding="utf-8")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True), ap.add_argument("--end", required=True), ap.add_argument("--name", required=True)
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--log")
    a = ap.parse_args()
    days = fast.days(a.start, a.end)
    out = result_dir(a.name)
    tg = (lambda s: None) if a.no_telegram else send
    logf = open(a.log, "a", encoding="utf-8") if a.log else None

    def log(s):
        print(s, flush=True)
        if logf:
            logf.write(s + "\n")
            logf.flush()

    t0 = time.time()
    tg(f"Rule No 1.1 run shuru: {days[0]} -> {days[-1]} ({len(days)} din), Condition 1 + Condition 2 alag, folder {out.name}")
    data, summary = {}, {"folder": out.name, "start": days[0], "end": days[-1], "days": len(days)}
    for ck, cd in COND.items():
        log(f"--- {cd['label']} ---")
        T, SG, info, G = r11.run(days, cfg={**CFG, **cd["cfg"]}, log=log)
        data[ck] = build(days, T, SG, info, G, ck)
        if len(T):
            T.drop(columns=["legs", "charges", "i"]).to_csv(out / f"trades_{ck}.csv", index=False)
        if len(SG):
            SG.to_csv(out / f"signals_{ck}.csv", index=False)
        summary[ck] = stats(data[ck], info)
        tg(f"{cd['label']} khatam: {summary[ck]['trades']} trade, net Rs {summary[ck]['net']:,}, PF {summary[ck]['pf']}")
    rep = write_html(out, data)
    summary.update(report_mb=round(rep.stat().st_size / 1e6, 2), secs=round(time.time() - t0))
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    log(json.dumps(summary, indent=1, default=str))
    return 0


def stats(data: dict, info: list) -> dict:
    rows = data["trades"]
    ni = TRADE_COLS.index("net")
    net = sum(r[ni] for r in rows)
    wins = [r[ni] for r in rows if r[ni] > 0]
    loss = [r[ni] for r in rows if r[ni] <= 0]
    eq, pk, dd = 0.0, 0.0, 0.0
    for r in sorted(rows, key=lambda r: (r[0], r[6])):
        eq += r[ni]; pk = max(pk, eq); dd = min(dd, eq - pk)
    side = {s: {"n": sum(1 for r in rows if r[2] == s), "net": round(sum(r[ni] for r in rows if r[2] == s))} for s in ("BUY", "SELL")}
    years = {}
    for r in rows:
        y = years.setdefault(r[0][:4], {"n": 0, "net": 0})
        y["n"] += 1; y["net"] += r[ni]
    return {"trades": len(rows), "wins": len(wins), "net": round(net),
               "win_pct": round(len(wins) / max(1, len(rows)) * 100, 1), "pf": round(sum(wins) / -sum(loss), 2) if loss and sum(loss) < 0 else None,
               "max_dd": round(-dd), "side": side, "years": {k: {"n": v["n"], "net": round(v["net"])} for k, v in sorted(years.items())},
               "killed_days": sum(1 for d in info if d["killed"]),
               "gross": round(sum(r[TRADE_COLS.index("gross0")] for r in rows)), "slip": round(sum(r[TRADE_COLS.index("slip")] for r in rows)),
               "charges": round(sum(r[TRADE_COLS.index("charges")] for r in rows))}


if __name__ == "__main__":
    sys.exit(main())

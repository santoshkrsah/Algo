"""IPO base / VCP swing + ROC market cycle (YouTube: Financially Free podcast with Harsh Singh, pcq6tj7FZv8).
Rules: strategies/IPO_VCP_SWING_RULES.md. Writes results/NN_ipo_vcp_swing/: report.html, trades_ipo.csv, trades_ath.csv,
equity_*.csv, checks.csv, alloc.csv, summary.json.

    python tools/run_ipovcp.py [--status-file s.txt] [--no-telegram]
"""
import argparse
import json
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest import ipovcp as R  # noqa: E402
from backtest import viewer as viewer_assets  # noqa: E402
from backtest.breakout_report import _clean  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.notify import send  # noqa: E402
from backtest.report import ASSETS  # noqa: E402

TAG_TECH = "@albert_max"
NAME = "ipo_vcp_swing"
TITLE = "IPO Base / VCP Swing + ROC Cycle"
LINK = "https://www.youtube.com/watch?v=pcq6tj7FZv8"

P_IPO = R.Params(strat="ipo", start="2016-01-01")
P_ATH = R.Params(strat="ath", start="2011-01-01", entry_units=2, min_turnover=5e7)

COMMON_SET = [["Stop loss", "average price - 10%", 1], ["Exit", "lagatar 2 red close 21 EMA ke neeche -> agle din open", 1],
              ["Re-entry", "2 green close 21 EMA ke upar (60 din me, max 3)"], ["Market filter", "Smallcap 100 ROC 20 (monthly): >= 100 OFF, <= 0 ON", 1],
              ["Losing streak", "5 stop loss lagatar -> naya unit 35% size"], ["Max stock", "5 ek saath"],
              ["Capital", "Rs 10 lakh, compounding nahi, margin nahi", 1], ["Kharcha", "Angel One delivery (STT 0.1% buy + sell, brokerage min(Rs 20, 0.1%), DP Rs 23.60 ...)", 1],
              ["Slippage", "0.10% har fill (SME 0.50%)", 1], ["Fill", "signal din ke close ke baad agle din ka open"], ["Side", "sirf long, delivery"]]
SET_IPO = [["Universe", "NSE mainboard IPO, listing 2016 se (NSE list), listing ke 500 din (~2 saal) tak", 1], ["Liquidity", "20 din avg turnover >= Rs 1 crore"],
           ["Entry 1", "VCP breakout (60 din, 3 hisse, depth ghat-ti, aakhri <= 10%)", 1], ["Top-up", "close > listing day high (pichhle 20 close neeche the)", 1],
           ["Size", "1 unit = 10% = Rs 1 lakh, ek stock max 2 unit", 1]] + COMMON_SET
SET_ATH = [["Universe", "saare mainboard stocks (AMFI Jun-2026 list + abhi listed IPO), 2011 se", 1], ["Liquidity", "20 din avg turnover >= Rs 5 crore"],
           ["Leader", "close >= all-time high x 0.95 + 63 din return > Smallcap 100", 1], ["Entry", "VCP breakout (60 din, 3 hisse, depth ghat-ti, aakhri <= 10%)", 1],
           ["Size", "2 unit = 20% = Rs 2 lakh har stock, top-up nahi", 1]] + COMMON_SET

RULES = [
    "Source: YouTube podcast 'My 2 Swing Trading Strategies ...' (Financially Free, guest Harsh Singh, 30 Oct 2025). Rules transcript se nikaale; jo video me saaf nahi tha wo neeche 'Jo baat clear nahi thi' me.",
    "Candle: daily (din ka). NSE cash, delivery, sirf long (kharido phir becho). Har signal din ke close pe dekha, order agle trading din ke open pe.",
    "Strategy 1 - IPO base: sirf NSE mainboard IPO (listing 1 Jan 2016 se), listing ke 500 trading din (~2 saal) tak naya setup. Pichhle 20 din ka average turnover kam se kam Rs 1 crore.",
    "Listing level = listing wale din ka high. Entry 1 = VCP breakout (neeche wala rule), listing ke kam se kam 60 din baad. Entry 2 = close listing high ke upar aur pichhle 20 close uske neeche the: pehle se stock hai to top-up, nahi to naya stock.",
    "VCP rule: pichhle 60 din ko 3 hisson me baanto (30, 20, 10 din). Har hisse ki girawat (max high se min low) pichhle hisse se kam ho, aakhri hissa 10% ya kam, pehla 10% ya zyada, har hisse ka low pichhle se upar (higher low), aakhri hisse ka high 60 din ke high ke 90% ke upar. Aaj ka close aakhri 10 din ke high (pivot) ke upar aur 21 EMA ke upar = buy signal.",
    "Strategy 2 - ATH VCP: saare mainboard stocks, turnover Rs 5 crore+, close all-time high (2010 se data) ke 5% ke andar, 63 din ka return Smallcap 100 index se zyada, aur VCP breakout = buy. Ek stock = 20% (Rs 2 lakh).",
    "Paisa (strategy 1): 1 unit = Rs 1 lakh (10% capital), ek stock me max 2 unit (Rs 2 lakh). Ek saath max 5 stock. Compounding nahi: har unit shuruaati Rs 10 lakh ka 10% hi. Cash kam ho to trade skip (margin nahi).",
    "Stop loss: average buy price se 10% neeche. Din ka low stop ko chhuye to stop pe becho (gap neeche khula to open pe).",
    "Trailing exit: lagatar do din red candle (close < open) jinka close 21 EMA ke neeche ho -> agle din open pe poora becho. Ek hi red candle ke baad agle din wapas upar aaye to exit nahi.",
    "Re-entry: 21 EMA exit ke baad 60 trading din ke andar lagatar do green candle 21 EMA ke upar close ho -> agle din open pe utne hi unit wapas. Max 3 re-entry. Stop loss wale exit ke baad re-entry nahi.",
    "Market cycle filter: Smallcap 100 index ka ROC (20 mahine, monthly close). ROC 100 ya upar -> OFF (koi naya trade / top-up / re-entry nahi, khule trade apne rule se). OFF ke baad ROC 0 ya neeche -> ON. Mahine ke close ka faisla agle mahine ke pehle din se.",
    "Losing streak: lagatar 5 trade stop loss pe band hue to agla har unit 35% size ka, jab tak koi trade profit me band na ho.",
    "Ek din me jagah se zyada signal: pehle top-up, phir re-entry, phir naye stock jinka 63 din ka return sabse zyada.",
    "Kharcha Angel One delivery ke hisaab se + slippage 0.10% har fill (SME 0.50%). Test ke aakhri din jo khula tha wo us din ke close pe band maana.",
    "Allocation test (alag): ON me poora paisa index me (NIFTY 50 ROC 18 / Smallcap 100 ROC 20 ka apna index), OFF me GOLDBEES. NIFTY: 45 pe OFF, 0 pe ON. Smallcap: 100 pe OFF, 0 pe ON. Switch agle mahine ke pehle din ke open pe, 0.2% kharcha.",
]
NOTES = [
    "Video ek podcast hai, exact rulebook nahi. VCP, base, 'listing price' jaise shabd chart dekh ke bole gaye; hamne unke formula banaye (Rules me). Harsh ki aankh jaisa pattern formula se pakka nahi milega.",
    "Fundamental screening (niche product, PE vs competitor, ROCE / ROE > 20%, concall, ESM-2 se nikalna) test nahi ho sakta (data nahi). Isliye saare IPO / saare leaders liye, sirf liquidity filter.",
    "'Listing day ka price' = listing din ka high maana (Fyers adjusted). Issue price split / bonus adjusted nahi hota, isliye use nahi kiya.",
    "'Thodi quantity pehle, breakout pe aur' = 1 unit pehli entry + 1 unit top-up. '5 stock ka portfolio' = max 5 stock. Capital Rs 10 lakh video me nahi tha (default).",
    "'Do red candle 21 EMA ke neeche' = dono din close < open aur close < 21 EMA; exit agle din ke open pe (signal close ke baad hi pata chalta hai).",
    "'Wapas 21 EMA reclaim / do green candle' = do din lagatar close > open aur close > 21 EMA; sirf 60 din tak aur max 3 baar (video me limit nahi thi).",
    "ROC: '0 ke paas' = 0 ya neeche, '45 / 100 ke paas' = 45 / 100 ya upar. Ye numbers Harsh ne purane chart dekh ke bataye ('backtested number'), isliye purana result un numbers pe fit hai (in-sample). 19 saal me sirf ~9 switch: thode faisle, luck ka bada role.",
    "'45 pe equity kam karo' ko stock strategy me 'naya trade nahi' maana (khule trade chalte rahe). Allocation test me poora paisa gold me.",
    "IPO list NSE 'past issues' se (2016 se). 2016 se pehle ke IPO nahi. Delist / merge hue 47 IPO (9 mainboard) ka data Fyers pe nahi (survivorship bias, chhota). ATH VCP ka stock list AMFI Jun-2026 + abhi listed IPO (survivorship bias: jo stock beech me gayab ho gaye wo test me nahi).",
    "All-time high = 2010 se data ka sabse uncha high (2010 se pehle listed stocks ka asli ATH isse upar ho sakta hai).",
    "SME IPO: Harsh SME bhi karta hai, par SME lot me trade hote hain (min ~Rs 1-2 lakh) aur bahut illiquid; alag check me 0.50% slippage ke saath, lot size nahi lagaya.",
    "Dividend nahi joda (na strategy me, na index buy-hold me). Tax (STCG / LTCG) nahi joda.",
    "Sirf 5 slot hain, isliye chhota sa farak (slippage, ek trade skip) aage ke saare trade badal deta hai. Isi wajah se kabhi 2x slippage wala run base se thoda upar aa jaata hai (ATH VCP me hua) - ye luck / path ka asar hai, strategy ka edge nahi.",
    "IPO base me average sirf ~30% paisa laga (1 unit = 10%, top-up kam hote hain, 5 stock limit) - isliye CAGR kam. Max 10 stock wala check dekho.",
]
FORMULAS = [
    ["21 EMA", "EMA(close, 21): alpha = 2 / 22, EMA[t] = alpha x close[t] + (1 - alpha) x EMA[t-1]"],
    ["VCP hisse", "A = din t-60..t-31, B = t-30..t-11, C = t-10..t-1; depth = (max high - min low) / max high (har hisse ka)"],
    ["VCP rule", "depth A > depth B > depth C, depth C <= 10%, depth A >= 10%, low C >= low B >= low A, high C >= 0.90 x 60-din high"],
    ["VCP signal", "VCP rule AUR close[t] > high C (pivot) AUR close[t] > 21 EMA[t]"],
    ["Listing breakout", "close[t] > listing high AUR max(close[t-20..t-1]) < listing high; listing high = listing din ka high"],
    ["ATH filter", "close[t] >= 0.95 x max(high[2010..t-1]); 63-din return = close[t] / close[t-63] - 1 > Smallcap 100 ka 63-din return"],
    ["Turnover", "average(close x volume) pichhle 20 din (aaj samet)"],
    ["Entry fill", "agle din ka open x (1 + slippage); slippage 0.10% (SME 0.50%)"],
    ["Qty", "floor(unit value x unit / entry fill); unit value = Rs 10 lakh x 10% (losing streak me x 0.35)"],
    ["Average price", "(saari buy fills x qty ka jod) / kul qty"],
    ["Stop loss", "stop = average price x 0.90; din ka low <= stop -> exit = min(open, stop) x (1 - slippage)"],
    ["21 EMA exit", "red[t] = close < open AUR close < 21 EMA; red[t-1] AUR red[t] -> exit = agle din open x (1 - slippage)"],
    ["Re-entry", "green[t] = close > open AUR close > 21 EMA; green[t-1] AUR green[t] (EMA exit ke 60 din me) -> agle din open pe wapas"],
    ["ROC (monthly)", "ROC[m] = (close[m] / close[m - N] - 1) x 100; Smallcap 100: N = 20, NIFTY 50: N = 18"],
    ["Market state", "ON -> OFF jab ROC >= 100 (Nifty 45); OFF -> ON jab ROC <= 0; mahine m ka state mahine m+1 me lagu"],
    ["Slippage", "qty x (entry open + exit price) x slippage"],
    ["Gross P&L", "qty x exit fill - saari buy fills ka jod"],
    ["Brokerage", "har order min(Rs 20, 0.1% x order value)"],
    ["STT", "0.1% x (buy value + sell value)"],
    ["Exchange + SEBI", "0.00297% + 0.0001% x (buy value + sell value)"],
    ["Stamp duty", "0.015% x buy value"],
    ["GST", "18% x (brokerage + exchange + SEBI)"],
    ["DP charge", "Rs 20 + 18% GST = Rs 23.60 har sell"],
    ["Net P&L", "gross P&L - (brokerage + STT + exchange + SEBI + stamp + GST + DP)"],
    ["Return % (trade)", "net / buy value x 100"],
    ["Equity (roz)", "cash + khule stocks ki qty x aaj ka close"],
    ["Max drawdown", "min(equity / ab tak ki sabse oonchi equity - 1)"],
    ["CAGR", "(aakhri equity / shuru ki equity) ^ (1 / saal) - 1"],
    ["Win rate", "net > 0 wale trade / kul trade"],
    ["Profit factor", "jeete trades ka jod / haare trades ka jod (bina minus)"],
    ["Buy-hold", "Rs 10 lakh x index close / pehle din ka index open (dividend nahi)"],
    ["Allocation", "switch: value x (1 - 0.2%) -> naye asset ke open pe units; roz equity = units x close; cash = 6% saal (daily)"],
]


def trade_dicts(t: pd.DataFrame) -> list[dict]:
    keep = ["id", "symbol", "kind", "base", "sme", "sig_date", "entry_date", "entry_raw", "entry", "topup_date", "topup_raw", "avg", "exit_date", "exit_raw", "exit",
            "reason", "units", "qty", "buy_val", "gross", "slip", "c_brok", "c_stt", "c_exch", "c_sebi", "c_stamp", "c_gst", "c_dp", "costs", "net", "ret_pct",
            "days", "stop", "mae_pct", "mfe_pct", "lh", "pivot", "listing"]
    x = t[keep].rename(columns={"base": "base_kind"}).copy()
    num = x.select_dtypes("number").columns.difference(["id", "qty", "days", "units"])
    x[num] = x[num].round(2)
    return [{k: (None if (isinstance(v, float) and not np.isfinite(v)) else v) for k, v in r.items()} for r in x.astype(object).where(x.notna(), None).to_dict("records")]


def series_for(runs: list[dict], before: int = 60, after: int = 25) -> dict:
    keep = {}
    for r in runs:
        for s, g in r["trades"].groupby("symbol"):
            x = r["V"][s]
            m = keep.setdefault(s, (x, np.zeros(len(x["dates"]), dtype=bool)))[1]
            for fi, xi in zip(g.fi, g.xi):
                m[max(0, fi - before): min(len(m), xi + after + 1)] = True
    out = {}
    for s, (x, m) in keep.items():
        b, f = x["b"], x["f"]
        out[s] = {"d": b.date[m].tolist(), **{k[0]: np.round(b[k].to_numpy()[m], 2).tolist() for k in ("open", "high", "low", "close")},
                  "e": np.round(f["ema"][m], 2).tolist()}
    return out


def bh_period(U, name, lo, hi):
    ix = U["idx"][name]
    g = ix[(ix.date >= lo) & (ix.date <= hi)]
    if g.empty:
        return None
    prev = ix[ix.date < lo]
    start = float(prev.close.iloc[-1]) if len(prev) else float(g.open.iloc[0])
    return (float(g.close.iloc[-1]) / start - 1) * 100


def bh_years(U, name, dates):
    out = {}
    for y in sorted({d[:4] for d in dates}):
        lo, hi = max(dates[0], f"{y}-01-01"), min(dates[-1], f"{y}-12-31")
        out[y] = bh_period(U, name, lo, hi)
    return out


def bh_stats(U, name, dates, capital):
    b = R.index_bh(U, name, dates, capital)
    v = b.equity.to_numpy()
    yrs = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days / 365.25
    return b, {"ret_pct": (v[-1] / capital - 1) * 100, "cagr": ((v[-1] / capital) ** (1 / yrs) - 1) * 100, "max_dd_pct": float((v / np.maximum.accumulate(v) - 1).min() * 100)}


def strategy_blob(U, key, p, variants, status) -> tuple[dict, dict, list]:
    status(f"{key}: universe + base run")
    V = R.universe(U, p)
    base = R.run(U, p, V)
    S = R.stats(base, p)
    print(f"[{key}] universe {len(V)}, signals {base['n_signals']}, trades {S['trades']}, net {S['net']:,.0f}, cagr {S['cagr']:.1f}, pf {S['pf']}", flush=True)
    checks, runs = [{"label": "Base (video rules + hamare assumptions)", "s": S}], {}
    for label, q in variants:
        status(f"{key}: extra check - {label}")
        need_v = (q.ema, q.sme, q.min_turnover, q.topup) != (p.ema, p.sme, p.min_turnover, p.topup)
        r = R.run(U, q, R.universe(U, q) if need_v else V)
        s = R.stats(r, q)
        checks.append({"label": label, "s": s})
        runs[label] = r
        print(f"  {label}: {s['trades']} trades net {s['net']:,.0f} cagr {s['cagr'] or 0:.1f} pf {s['pf'] or 0:.2f} dd {s['max_dd_pct']:.1f}%", flush=True)
    stress = next(r for lab, r in runs.items() if lab.startswith("Slippage 2x"))
    t, e = base["trades"], base["equity"]
    dates = e.date.tolist()
    v = e.equity.to_numpy()
    r6 = v[126:] / v[:-126] - 1 if len(v) > 126 else np.array([0.0])          # 126 trading days ~ 6 months
    i6 = int(np.argmax(r6))
    S = {**S, "best6": float(r6.max() * 100), "best6_from": dates[i6], "best6_to": dates[min(i6 + 126, len(dates) - 1)], "worst6": float(r6.min() * 100)}
    bh, bhs = bh_stats(U, "SMALLCAP100", dates, p.capital)
    bh2, bhs2 = bh_stats(U, "NIFTY50", dates, p.capital)
    periods = []
    for label, lo, hi in R.PERIODS[key]:
        hi2 = min(hi, dates[-1])
        s = R.stats(base, p, lo, hi2)
        st = R.stats(stress, p, lo, hi2)
        periods.append({"label": label, "lo": lo, "hi": hi2, "s": s, "bh": bh_period(U, "SMALLCAP100", lo, hi2), "stress_net": st["net"]})
    stress_net = R.stats(stress, p)["net"]
    ok = all(x["s"]["net"] > 0 for x in periods) and stress_net > 0
    pass_note = ("har period me profit aur 2x slippage pe bhi profit." if ok else
                 "loss wala period: " + ", ".join(x["label"] for x in periods if x["s"]["net"] <= 0) + ("; 2x slippage pe loss" if stress_net <= 0 else ""))
    cost_sum = {k: float(t["c_" + k].sum()) for k in ("brok", "stt", "exch", "sebi", "stamp", "gst", "dp")}
    sk = base["skipped"].why.value_counts()
    skipped = [[f"Chhoota: {k}", int(v)] for k, v in sk.items()]
    unit = p.capital * p.unit_pct
    inv = [["Shuruaati capital", "Rs 10,00,000 (fixed, compounding nahi, margin nahi)"],
           ["Risk har trade", f"1 unit Rs {unit:,.0f} x 10% stop = ~Rs {unit * 0.1:,.0f} (1% capital); {p.entry_units} unit entry = {p.entry_units}%"],
           ["Qty ka rule", "floor(unit value x unit / entry fill)"], ["Max ek stock me", f"{p.max_units} unit = Rs {unit * p.max_units:,.0f} (20% capital)"],
           ["Max khuli position", f"{p.max_stocks} stock (sabse zyada ek saath: {S['max_positions']}, average {S['avg_positions']:.1f})"],
           ["Average / sabse badi position", f"Rs {t.buy_val.mean():,.0f} / Rs {t.buy_val.max():,.0f}"],
           ["Sabse zyada paisa ek din me laga", f"Rs {S['peak_deployed']:,.0f} ({S['peak_deployed'] / p.capital * 100:.0f}% capital)"],
           ["Average paisa laga", f"Rs {S['avg_deployed']:,.0f} ({S['avg_deployed'] / p.capital * 100:.0f}%); market me time {S['in_mkt']:.0f}% din"]]
    beat = S["cagr"] is not None and S["cagr"] > bhs["cagr"]
    vnote = ("Kharche ke baad bhi profit" if S["net"] > 0 else "Kharche ke baad loss") + ("; Smallcap 100 buy-hold se zyada CAGR." if beat else
             f"; par Smallcap 100 buy-hold (CAGR {bhs['cagr']:.1f}%) se kam, drawdown {S['max_dd_pct']:.0f}% vs index {bhs['max_dd_pct']:.0f}%.")
    name = "1) IPO base (VCP + listing breakout)" if key == "ipo" else "2) ATH leaders VCP"
    sub = ("NSE mainboard IPO (2016 se), VCP breakout + listing-high top-up, 21 EMA trail" if key == "ipo"
           else "All-time high ke paas wale leaders, VCP breakout, 21 EMA trail")
    blob = {"name": name, "sub": sub, "settings": (SET_IPO if key == "ipo" else SET_ATH) + [["Period", f"{dates[0]} -> {dates[-1]}"]],
            "stats": S, "bh_stats": {"small": bhs, "nifty": bhs2}, "bh_year": bh_years(U, "SMALLCAP100", dates), "bh2_year": bh_years(U, "NIFTY50", dates),
            "periods": periods, "stress_net": stress_net, "pass": ok, "pass_note": pass_note, "checks": checks, "cost_sum": cost_sum, "skipped": skipped,
            "n_signals": base["n_signals"], "universe": base["universe"], "inv": inv, "verdict_note": vnote,
            "dates": dates, "eq": e.equity.round(0).tolist(), "bh": bh.equity.round(0).tolist(), "bh2": bh2.equity.round(0).tolist(), "trades": trade_dicts(t)}
    return blob, base, checks


def alloc_blob(U, status) -> dict:
    status("ROC allocation test")
    runs = [("NIFTY 50 ROC 18: 45 pe gold, 0 pe wapas (video)", R.Alloc(), True),
            ("NIFTY 50 ROC 18: zero-cross pe wapas", R.Alloc(mode="cross"), False),
            ("Smallcap 100 ROC 20: 100 pe gold, 0 pe wapas (video, Harsh ka)", R.Alloc(index="SMALLCAP100", n=20, sell=100.0), True),
            ("Smallcap 100 ROC 20: zero-cross pe wapas", R.Alloc(index="SMALLCAP100", n=20, sell=100.0, mode="cross"), False),
            ("NIFTY 50 ROC 18, OFF me cash 6%", R.Alloc(off_asset="CASH"), False),
            ("Smallcap 100 ROC 20, OFF me cash 6%", R.Alloc(index="SMALLCAP100", n=20, sell=100.0, off_asset="CASH"), False)]
    per = [("2007-04-01", "2021-12-31"), ("2022-01-01", "2023-12-31"), ("2024-01-01", "2099-12-31")]

    def per_cagr(e):
        out = []
        for lo, hi in per:
            g = e[(e.date >= lo) & (e.date <= hi)]
            prev = e[e.date < lo]
            s0 = float(prev.equity.iloc[-1]) if len(prev) else float(g.equity.iloc[0])
            yrs = max((pd.Timestamp(g.date.iloc[-1]) - pd.Timestamp(prev.date.iloc[-1] if len(prev) else g.date.iloc[0])).days / 365.25, 1e-9)
            out.append(((float(g.equity.iloc[-1]) / s0) ** (1 / yrs) - 1) * 100)
        return out

    rows, curves, switches, first = [], [], [], None
    for label, q, video in runs:
        a = R.alloc(U, q)
        first = first or a
        rows.append({"label": label, "s": a["stats"], "per": per_cagr(a["equity"]), "video": video})
        curves.append({"label": label.split(":")[0] + (" (zero-cross)" if q.mode == "cross" else "") + (" cash" if q.off_asset == "CASH" else ""), "eq": a["equity"].equity.round(0).tolist(), "bh": False, "show": video})
        if video:
            for r in a["switches"].to_dict("records"):
                switches.append([label.split(":")[0], r["date"], r["from"], r["to"], r["roc"]])
        print(f"  alloc {label}: {a['stats']['cagr']:.1f}% cagr, dd {a['stats']['max_dd_pct']:.1f}%, {a['stats']['switches']} switches", flush=True)
    days = first["equity"].date.tolist()
    for name, label in (("NIFTY50", "NIFTY 50 buy-hold"), ("SMALLCAP100", "Smallcap 100 buy-hold"), ("GOLDBEES", "GOLDBEES buy-hold")):
        b, s = bh_stats(U, name, days, 1_000_000.0)
        rows.append({"label": label, "s": {**s, "end": float(b.equity.iloc[-1]), "switches": None, "on_pct": None}, "per": per_cagr(b), "video": False})
        curves.append({"label": label, "eq": b.equity.round(0).tolist(), "bh": True, "show": True})
    keep = [c for c in curves if c["show"]]
    step = 5                                                    # weekly points keep the page small
    idx = list(range(0, len(days), step)) + ([len(days) - 1] if (len(days) - 1) % step else [])
    sm = R.roc_monthly(U["idx"]["SMALLCAP100"], 20)
    st = R.roc_state(sm, 100.0, "level")
    nm = R.roc_monthly(U["idx"]["NIFTY50"], 18)
    months = [m for m in sm.index if m >= "2006-01"]
    v = rows[0]["s"], rows[2]["s"]
    warn = (f"Dhyan do: video wale ROC rule ka purana result bahut achha dikhta hai (NIFTY rule CAGR {v[0]['cagr']:.1f}% vs NIFTY buy-hold {rows[6]['s']['cagr']:.1f}%, "
            f"Smallcap rule {v[1]['cagr']:.1f}% vs Smallcap buy-hold {rows[7]['s']['cagr']:.1f}%). Par ye levels (18 / 45, 20 / 100) Harsh ne isi purane chart ko dekh ke chune hain "
            f"(in-sample), aur 2007 se sirf {v[0]['switches']} / {v[1]['switches']} switch hue - bahut kam faisle, isliye luck ka bada hissa. Gold ka 2007-12 aur 2024-26 ka bada rally bhi isme madad karta hai. Ise aage ki guarantee mat samjho.")
    return {"warn": warn, "rows": rows, "dates": [days[i] for i in idx], "curves": [{**c, "eq": [c["eq"][i] for i in idx]} for c in keep],
            "switches": switches, "roc": {"months": months, "small": [None if x != x else round(float(x), 1) for x in sm.roc.reindex(months)],
                                          "nifty": [None if x != x else round(float(x), 1) for x in nm.roc.reindex(months)],
                                          "small_on": [bool(x) for x in st.reindex(months)], "shade": float(np.nanmax(sm.roc.reindex(months)) + 10)}}


def write_html(path: Path, data: dict) -> Path:
    data = {**data, "generated": datetime.now().strftime("%d %b %Y %H:%M")}
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "ipovcp_template.html").read_text(encoding="utf-8")).replace("__CSS__", css).replace("__TITLE__", TITLE)
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(_clean(data), separators=(",", ":"), default=str).replace("</", "<\\/")
    path.write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")
    return path


def run(args, status) -> int:
    t0 = time.time()
    out = result_dir(NAME)
    status("IPO/VCP backtest: data load (daily stocks + IPO list + index)")
    U = R.load()
    print(f"loaded {len(U['stocks'])} stocks, {len(U['ipo'])} IPO rows in {time.time() - t0:.0f}s", flush=True)
    v_ipo = [("ROC filter OFF (har signal liya)", R.with_params(P_IPO, regime="off")),
             ("Filter: NIFTY 50 ROC 18 (0 / 45)", R.with_params(P_IPO, regime="nifty")),
             ("Filter: zero-cross pe wapas ON", R.with_params(P_IPO, regime_mode="cross")),
             ("63 EMA trailing (21 ki jagah)", R.with_params(P_IPO, ema=63)),
             ("Re-entry nahi", R.with_params(P_IPO, reentry=False)),
             ("Top-up retest pe (breakout ki jagah)", R.with_params(P_IPO, topup="retest")),
             ("SME IPO bhi (0.50% slippage)", R.with_params(P_IPO, sme=True)),
             ("Slippage 2x (stress)", R.with_params(P_IPO, slip=0.002, slip_sme=0.01)),
             ("Compounding (unit = aaj ki equity ka 10%)", R.with_params(P_IPO, compound=True)),
             ("Losing-streak rule nahi", R.with_params(P_IPO, streak_size=1.0)),
             ("Max 10 stock (5 ki jagah)", R.with_params(P_IPO, max_stocks=10)),
             ("Bina kharcha, bina slippage (sirf signal ki taakat)", R.with_params(P_IPO, costs=False, slip=0.0, slip_sme=0.0))]
    v_ath = [("ROC filter OFF (har signal liya)", R.with_params(P_ATH, regime="off")),
             ("Filter: NIFTY 50 ROC 18 (0 / 45)", R.with_params(P_ATH, regime="nifty")),
             ("Filter: zero-cross pe wapas ON", R.with_params(P_ATH, regime_mode="cross")),
             ("63 EMA trailing (21 ki jagah)", R.with_params(P_ATH, ema=63)),
             ("Re-entry nahi", R.with_params(P_ATH, reentry=False)),
             ("Relative strength filter nahi", R.with_params(P_ATH, rs=False)),
             ("Slippage 2x (stress)", R.with_params(P_ATH, slip=0.002)),
             ("Compounding (unit = aaj ki equity ka 10%)", R.with_params(P_ATH, compound=True)),
             ("Max 10 stock x 10% (5 x 20% ki jagah)", R.with_params(P_ATH, max_stocks=10, entry_units=1)),
             ("Bina kharcha, bina slippage (sirf signal ki taakat)", R.with_params(P_ATH, costs=False, slip=0.0))]
    b_ipo, r_ipo, c_ipo = strategy_blob(U, "ipo", P_IPO, v_ipo, status)
    b_ath, r_ath, c_ath = strategy_blob(U, "ath", P_ATH, v_ath, status)
    A = alloc_blob(U, status)
    status("IPO/VCP backtest: HTML report likh raha hoon")
    first_day = min(U["stocks"][s].date.iloc[0] for s in U["stocks"])
    last_day = max(b_ipo["dates"][-1], b_ath["dates"][-1])
    lst = U["ipo"]
    n_main = int((lst.type == "Equity").sum())
    n_sme = int((lst.type == "SME").sum())
    data_info = [["Stocks", f"Fyers daily candles (split / bonus adjusted): {len(U['stocks'])} stocks (AMFI Jun-2026 list 1,374 + abhi listed IPO stocks), 2010 se {last_day}"],
                 ["IPO list", f"NSE 'past issues' (2016 se): {n_main} mainboard + {n_sme} SME IPO; aaj ka ticker symbol-change list se. Strategy 1 me {b_ipo['universe']} mainboard IPO (pehli Fyers candle listing ke 7 din ke andar)."],
                 ["Missing", "47 IPO (9 mainboard) delist / merge ho gaye - Fyers pe data nahi (survivorship bias)."],
                 ["Index", "NIFTY 50 (2005 se), Nifty Smallcap 100 (2005 se) daily, Fyers. GOLDBEES ETF daily (Mar 2007 se), Fyers adjusted."],
                 ["Signal", f"IPO: {b_ipo['n_signals']:,} signal -> {b_ipo['stats']['trades']} trade; ATH VCP: {b_ath['n_signals']:,} signal -> {b_ath['stats']['trades']} trade"],
                 ["Adjust", "Fyers daily prices split / bonus adjusted (listing high bhi adjusted). Demerger adjust nahi kiya."],
                 ["Survivorship", "ATH VCP: stock list = AMFI Jun-2026 + abhi listed IPO (jo stock beech me delist / gayab ho gaye wo nahi). IPO: NSE ki poori IPO list, sirf delist wale missing."],
                 ["Dividend / tax", "dividend nahi joda, tax nahi joda"]]
    blob = {"title": TITLE, "capital": P_IPO.capital, "data_range": [first_day, last_day], "data_note": "Fyers daily candles (adjusted) + NSE IPO list + index / GOLDBEES daily",
            "order": ["ipo", "ath"], "S": {"ipo": b_ipo, "ath": b_ath}, "alloc": A, "rules": RULES, "notes": NOTES, "formulas": FORMULAS, "data_info": data_info,
            "ema_label": "21 EMA", "series": series_for([r_ipo, r_ath]), "link": LINK}
    path = write_html(out / "report.html", blob)
    for key, r in (("ipo", r_ipo), ("ath", r_ath)):
        r["trades"].drop(columns=["fi", "xi"]).to_csv(out / f"trades_{key}.csv", index=False)
        r["equity"].to_csv(out / f"equity_{key}.csv", index=False)
        r["skipped"].to_csv(out / f"skipped_{key}.csv", index=False)
    pd.DataFrame([{"strategy": k, "check": c["label"], **c["s"]} for k, cs in (("ipo", c_ipo), ("ath", c_ath)) for c in cs]).to_csv(out / "checks.csv", index=False)
    pd.DataFrame([{"rule": r["label"], **r["s"], "dev_cagr": r["per"][0], "val_cagr": r["per"][1], "latest_cagr": r["per"][2]} for r in A["rows"]]).to_csv(out / "alloc.csv", index=False)
    summary = {"title": TITLE, "folder": out.name, "link": LINK,
               "ipo": {"stats": b_ipo["stats"], "periods": {p["label"]: p["s"] for p in b_ipo["periods"]}, "pass": b_ipo["pass"], "bh": b_ipo["bh_stats"]},
               "ath": {"stats": b_ath["stats"], "periods": {p["label"]: p["s"] for p in b_ath["periods"]}, "pass": b_ath["pass"], "bh": b_ath["bh_stats"]},
               "alloc": {r["label"]: r["s"] for r in A["rows"]}, "report_mb": round(path.stat().st_size / 1e6, 1)}
    (out / "summary.json").write_text(json.dumps(_clean(summary), indent=1, default=str), encoding="utf-8")
    print(f"done {time.time() - t0:.0f}s -> {path} ({path.stat().st_size / 1e6:.1f} MB)", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--status-file")
    args = ap.parse_args()
    status = (lambda s: Path(args.status_file).write_text(s + "\n", encoding="utf-8")) if args.status_file else (lambda s: None)
    try:
        return run(args, status)
    except Exception:
        err = traceback.format_exc()
        print(err, flush=True)
        if not args.no_telegram:
            send(f"{TITLE} run me error {TAG_TECH}:\n{err[-1500:]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

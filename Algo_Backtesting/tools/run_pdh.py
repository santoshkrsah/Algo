"""NIFTY intraday PDH / PDL trend backtest on 3-min bars: 3,360 settings, pick on the in-sample part (first ~60% of days),
check on the out-of-sample part, the four claims (win rate, PDH / PDL vs open / previous close / random levels, 9:30 vs 11:00,
partial + breakeven vs a fixed exit), splits, trade logs, HTML report + Telegram.

    python tools/run_pdh.py
    python tools/run_pdh.py --no-telegram
"""
import argparse
import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest import pdh as P  # noqa: E402
from backtest import pdh_report as rep  # noqa: E402
from backtest.config import ROOT, result_dir  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
DIMS = list(P.GRID)
LBL = {"entry": {"A": "A breakout", "B": "B rejection"}, "window": {"W1": "W1 9:30 se", "W2": "W2 11:00 se"},
       "cutoff": {"14:30": "entry 14:30 tak", "15:00": "entry 15:00 tak"}, "chase": {0.0015: "chase 0.15%", 0.0025: "chase 0.25%", 0.004: "chase 0.40%"},
       "stop": {"S1": "S1 signal candle", "S2-0.1": "S2 level 0.1%", "S2-0.2": "S2 level 0.2%", "S3-0.1": "S3 tight (0.1%)", "S3-0.2": "S3 tight (0.2%)"},
       "exit": P.EXITS, "max_trades": {2: "din me 2 trade", 1: "din me 1 trade"}, "trend": {"daily": "trend daily close", "intraday": "trend 3-min close"}}
DIM_NAME = {"entry": "Entry", "window": "Time window", "cutoff": "Aakhri entry", "chase": "No-chase", "stop": "SL", "exit": "Exit",
            "max_trades": "Trade / din", "trend": "Trend"}
PERIODS = ("is", "oos", "all")


def short(v: dict) -> str:
    return " | ".join([v["entry"], v["window"], v["cutoff"], f"{v['chase'] * 100:.2f}%", v["stop"], v["exit"], f"{v['max_trades']}/din", v["trend"]])


def long_label(v: dict) -> str:
    return ", ".join(LBL[k][v[k]] for k in DIMS)


def rs(v) -> str:
    return ("-" if v < 0 else "+") + f"Rs {abs(v):,.0f}"


def pick(row) -> dict:
    return P.variant(**{k: (row[k].item() if hasattr(row[k], "item") else row[k]) for k in DIMS})


def compact(s: dict) -> dict:
    """Split stats -> {period: {n, net, win, loss, scr, pf, dd, exp_r, exp_r0, aw_r, al_r, ret, per_day, costs}}."""
    return {k: {x: s[k][x] for x in ("n", "net", "win", "loss", "scr", "pf", "dd", "exp_r", "exp_r0", "aw_r", "al_r", "ret", "per_day", "costs", "avg")}
            for k in PERIODS}


def srow(label: str, s: dict) -> list:
    i, o, a = s["is"], s["oos"], s["all"]
    return [label, i["n"], i["net"], i["pf"], i["exp_r"], o["n"], o["net"], o["pf"], o["exp_r"], a["win"], a["exp_r0"]]


def sensitivity(m, v: dict, gi: dict) -> list:
    """One change at a time around setting v: other grid values (from the grid) + risk 0.5%, 2x slippage, max SL."""
    out = []
    for d in DIMS:
        for val in P.GRID[d]:
            if val == v[d]:
                continue
            r = gi.get(tuple((val if k == d else v[k]) for k in DIMS))
            if r is not None:
                out.append(srow(f"{DIM_NAME[d]}: {LBL[d][val]}", {p: {x: r[f"{p}_{x}"] for x in ("n", "net", "pf", "exp_r", "win", "exp_r0")} for p in PERIODS}))
    for lab, kw in [("Risk 0.5% (Rs 2,500) / trade", {"risk_pct": 0.005}), ("Slippage 2 point (dugna, stress)", {"slip": 2.0}),
                    ("Max SL 0.35% of price", {"max_stop_pct": 0.0035}), ("Max SL 0.75% of price", {"max_stop_pct": 0.0075})]:
        tr, _ = P.run(m, dict(v, **kw))
        out.append(srow(lab, P.split_stats(m, P.as_rows(tr))))
    return out


def level_check(m, v: dict, seeds: int) -> dict:
    """Same rules on other levels: day open, previous close, random levels at the PDH / PDL distance (several seeds)."""
    res = {}
    for lv in ("pdh", "open", "pclose"):
        tr, _ = P.run(m, dict(v, level=lv))
        res[lv] = compact(P.split_stats(m, P.as_rows(tr)))
    rnd = []
    for s in range(seeds):
        tr, _ = P.run(m, dict(v, level="random", seed=s))
        rnd.append(compact(P.split_stats(m, P.as_rows(tr))))
    summ = {}
    for p in PERIODS:
        nets = [r[p]["net"] for r in rnd]
        e = [r[p]["exp_r"] for r in rnd if r[p]["exp_r"] is not None]
        e0 = [r[p]["exp_r0"] for r in rnd if r[p]["exp_r0"] is not None]
        summ[p] = {"n": float(np.mean([r[p]["n"] for r in rnd])), "net": float(np.mean(nets)), "net_min": min(nets), "net_max": max(nets),
                   "exp_r": float(np.mean(e)), "exp_r0": float(np.mean(e0)), "win": float(np.mean([r[p]["win"] for r in rnd if r[p]["win"] is not None])),
                   "pf": float(np.mean([r[p]["pf"] for r in rnd if r[p]["pf"] is not None])),
                   "beat_pct": float(np.mean([x >= res["pdh"][p]["net"] for x in nets]) * 100),
                   "beat0_pct": float(np.mean([x >= (res["pdh"][p]["exp_r0"] or 0) for x in e0]) * 100)}
    res["random"] = summ
    return res


def paired(g: pd.DataFrame, dim: str, a, b, metric: str) -> dict:
    """Settings that differ only in <dim>: share where b beats a (IS / OOS) and the average difference."""
    others = [k for k in DIMS if k != dim]
    x = g[g[dim] == a].set_index(others)
    y = g[g[dim] == b].set_index(others)
    j = x.merge(y, left_index=True, right_index=True, suffixes=("_a", "_b"))
    out = {"pairs": len(j)}
    for p in ("is", "oos"):
        da, db = j[f"{p}_{metric}_a"], j[f"{p}_{metric}_b"]
        out[f"{p}_better"] = float((db > da).mean() * 100)
        out[f"{p}_diff"] = float((db - da).mean())
    return out


def groups(g: pd.DataFrame) -> list:
    out = []
    for d in DIMS:
        for val in P.GRID[d]:
            x = g[g[d] == val]
            out.append([DIM_NAME[d], LBL[d][val], len(x), x.all_n.mean(), x.is_exp_r0.mean(), x.oos_exp_r0.mean(), x.is_exp_r.mean(), x.oos_exp_r.mean(),
                        (x.is_net > 0).mean() * 100, (x.oos_net > 0).mean() * 100, x.all_win.mean()])
    return out


def faisla(g, best, base_s, best_s, lev_best, lev_base, cw, cd, rob, sens_best, base_t) -> list:
    tight = g[g.stop.isin(["S1", "S3-0.1", "S3-0.2"])]
    wide = g[g.stop.isin(["S2-0.1", "S2-0.2"])]
    bs, ba = best_s, base_s
    top_win = g[g.all_n >= 100].sort_values("all_win", ascending=False).iloc[0]
    stress = next(r for r in sens_best if r[0].startswith("Slippage 2"))
    lb, lr = lev_best["pdh"]["all"], lev_best["random"]["all"]
    out = [
        f"<b>{len(g):,} setting</b> try ki (2 entry x 2 time window x 2 aakhri entry time x 3 no-chase x 5 SL x 7 exit x 2 trade/din x 2 trend). "
        f"Slippage + kharche ke baad in-sample (pehle 60%) me <b>{(g.is_net > 0).sum()}</b> setting profit me, out-of-sample (baaki 40%) me "
        f"<b>{(g.oos_net > 0).sum()}</b>, dono me <b>{((g.is_net > 0) & (g.oos_net > 0)).sum()}</b>. Yaani strategy jaisi likhi hai waisi <b>kaam nahi karti</b>.",
        f"Baseline (A breakout, 11:00 se, S3, E1 trailing): {ba['all']['n']} trade, win {ba['all']['win']:.0f}%, net <b>{rs(ba['all']['net'])}</b> "
        f"(in-sample {rs(ba['is']['net'])}, out-of-sample {rs(ba['oos']['net'])}), har trade average {ba['all']['exp_r']:+.2f}R.",
        f"In-sample ki sabse achhi setting (<b>{long_label(best)}</b>) bhi in-sample me {rs(bs['is']['net'])} (PF {bs['is']['pf']:.2f}) aur out-of-sample me "
        f"<b>{rs(bs['oos']['net'])}</b> (PF {bs['oos']['pf']:.2f}, {bs['oos']['n']} trade). Slippage 2 point pe out-of-sample {rs(stress[6])}.",
        f"Kyun: price ka apna edge bahut chhota hai. Bina slippage aur kharche ke tight SL (S1 / S3) wali settings average {tight.is_exp_r0.mean():+.2f}R (in-sample) / "
        f"{tight.oos_exp_r0.mean():+.2f}R (out-of-sample) kamati, par slippage + kharche ke baad {tight.is_exp_r.mean():+.2f}R / {tight.oos_exp_r.mean():+.2f}R. "
        f"Baseline me SL median {base_t.risk_pts.median():.0f} point ka hota hai, aur 2 point slippage (aana + jaana) + average Rs {base_t.costs.mean():,.0f} kharcha "
        f"har trade pe lagbhag {(tight.is_exp_r0 - tight.is_exp_r).mean():.1f}R kha jaata hai. "
        f"Chauda SL (S2) me kharcha kam chubhta hai ({wide.is_exp_r.mean():+.2f}R / {wide.oos_exp_r.mean():+.2f}R) par price ka edge wahan bhi lagbhag zero "
        f"({wide.is_exp_r0.mean():+.2f}R / {wide.oos_exp_r0.mean():+.2f}R).",
        f"Daava (a) '60-65% win rate': <b>galat</b>. {len(g):,} me se sabse zyada win rate {top_win.all_win:.1f}% ({short(pick(top_win))}, average {top_win.all_exp_r:+.2f}R). "
        f"60% ke upar ek bhi setting nahi. Baseline: win {ba['all']['win']:.0f}%, loss {ba['all']['loss']:.0f}%, scratch {ba['all']['scr']:.0f}%.",
        f"Daava (b) 'PDH / PDL baaki levels se behtar': <b>sabit nahi</b>. In-sample best ke rules pe PDH / PDL {rs(lb['net'])}, din ka open "
        f"{rs(lev_best['open']['all']['net'])}, kal ka close {rs(lev_best['pclose']['all']['net'])}, random levels (same doori, {P.CFG['random_seeds']} baar) average "
        f"{rs(lr['net'])} - {lr['beat_pct']:.0f}% random runs PDH / PDL ke barabar ya behtar. Baseline ke rules pe ulta: {lev_base['random']['all']['beat_pct']:.0f}% random "
        f"runs barabar ya behtar (PDH / PDL {rs(lev_base['pdh']['all']['net'])}, random average {rs(lev_base['random']['all']['net'])}). Kabhi thoda aage, kabhi peeche - "
        f"pakka fark nahi, aur har level pe loss.",
        f"Daava (c) '11:00 ke baad trade 9:30 se behtar': <b>galat</b>. Jo settings sirf time window me alag hain, unme 11:00 wali sirf {cw['is_better']:.0f}% (in-sample) / "
        f"{cw['oos_better']:.0f}% (out-of-sample) baar behtar thi; average fark {cw['is_diff']:+.2f}R / {cw['oos_diff']:+.2f}R per trade.",
        f"Daava (d) '1R pe aadha book + breakeven SL behtar': <b>sabit nahi</b>. Same 2R target (E2 vs F2): partial wala {cd['is_better']:.0f}% (in-sample) / "
        f"{cd['oos_better']:.0f}% (out-of-sample) baar behtar, average fark {cd['is_diff']:+.2f}R / {cd['oos_diff']:+.2f}R. Partial se win rate badhta hai "
        f"par average R nahi.",
        f"Overfitting: in-sample aur out-of-sample ki ranking ka correlation {rob['rank_corr']:.2f} - yaani jo setting pehle kharab thi wo baad me bhi kharab rahi "
        f"(result kismat nahi, structure hai: tight SL hamesha sabse bura). Best setting ke {rob['nb']} padosi (ek cheez badli) me "
        f"{rob['nb_is_pos']:.0f}% in-sample aur {rob['nb_oos_pos']:.0f}% out-of-sample profit me. Har setting me in-sample {int(g.is_n.min())}+ trade the (200 se kam ka "
        f"sample nahi), par out-of-sample kuch settings me {int(g.oos_n.min())} tak hi trade.",
        "Faisla: is strategy ko NIFTY futures pe paper / live chalane ki salah nahi. Agar aage dekhna hai to 1-min futures data (asli basis + spread) aur kam friction "
        "wala instrument chahiye - par yahan bina kharche ke bhi edge bahut chhota hai.",
    ]
    return out


RULES = [
    "Data: NIFTY 50 index (spot) ke 1-minute candle, 1 Oct 2021 se 30 Oct 2026. Inko 3-minute candle me badla (9:15 se 3:30). "
    "Futures ka purana 1-minute data nahi hai, isliye spot ka price hi futures ka price maana; kharcha NIFTY futures ka lagaya.",
    "Level: PDH = kal ka High, PDL = kal ka Low (sirf normal session 9:15 - 3:30). Faisla sirf in do level pe.",
    "Trend (din ke close se): close kal ke High se upar -> UP. Close kal ke Low se neeche -> DOWN. Dono nahi -> pichhla trend hi chalta rahe. "
    "UP me sirf BUY, DOWN me sirf SELL. Doosra version: yahi rule din ke andar har 3-min close pe.",
    "Time: W1 = entry 9:30 se, W2 = entry 11:00 se. Nayi entry 14:30 ke baad nahi (doosra version 15:00). Har trade 3:15 pe band.",
    "Entry A (breakout): UP me 3-min candle PDH ke upar band -> agli candle ke open pe BUY. DOWN me candle PDL ke neeche band -> agli candle ke open pe SELL.",
    "Entry B (rejection): DOWN me price PDH ya PDL tak upar aaya (candle ne level chhua) par candle level ke neeche hi band -> SELL. "
    "UP me price PDL ya PDH tak neeche aaya, candle level ke upar band -> BUY.",
    "Gap: din level ke paar khula to jab tak price wapas level ko chhu na le, entry nahi.",
    "No chase: entry ke time price level se 0.15% / 0.25% / 0.40% (teeno test) se zyada aage nikal gaya ho to trade chhodo.",
    "SL: S1 = signal candle ka doosra sira (BUY me low, SELL me high). S2 = level ke andar 0.1% ya 0.2%. S3 = S1 aur S2 me jo entry ke zyada paas ho. "
    "SL entry se 0.5% se zyada door ho to trade nahi.",
    "Qty: risk = capital ka 1% (Rs 5,000). Lots = (5,000 / (SL point x lot size)) neeche round. Max 10 lot, aur margin (12% x lot x price) "
    "capital se zyada nahi. 1 lot bhi na bane to trade nahi. Lot size us din ka (50 -> 25 -> 75 -> 65).",
    "Exit: +1R pe aadhe lots becho, baaki ka SL entry pe (breakeven). Baaki ke liye 5 tarike: E1 swing trailing, E2 target 2R, E3 target 3R, "
    "E4 target doosra level, E5 3:15 tak. Mukable ke liye bina partial: F1 = pura 1R pe, F2 = pura 2R pe.",
    "Din me max 2 trade (doosra version 1). Ek time pe ek hi trade. Kisi level pe loss hua to us din us level pe dobara entry nahi.",
    "Fill: signal 3-min candle band hone pe pata chalta hai, order agli candle ke open pe. Ek candle me SL aur target dono aaye to SL pehle maana. "
    "Price SL ke paar khula to open ke price pe exit (bura price).",
    "Kharcha: Rs 20 har order, STT (sell pe), exchange, SEBI, stamp (buy pe), GST. Slippage 1 point har buy aur har sell pe.",
    "Check: pehle 60% din (1 Oct 2021 - 30 Sep 2024) pe best setting chuni, baaki 40% (1 Oct 2024 - 30 Oct 2026) pe bina badle chalayi. "
    "Paisa: capital Rs 5,00,000 fixed - profit dobara nahi lagaya (compounding nahi).",
]
NOTES = [
    "Spot vs futures: futures spot se thoda upar-neeche (basis) chalta hai. Din ke andar dono saath chalte hain, par asli futures me spread / basis ka thoda fark aayega.",
    "Entry A ka signal: candle level ke paar band ho, aur pichhli candle level ke andar band hui ho YA isi candle ne level chhua ho. Isliye breakout ke baad level ka "
    "retest (chhu ke wapas paar band) bhi A ka signal hai. Din ki pehli candle ke liye 'pichhla close' = din ka open (gap rule isi se lagta hai).",
    "Entry B: candle ne level chhua (BUY me low <= level, SELL me high >= level), level ke sahi taraf band hui, aur pichhli candle bhi usi taraf band thi.",
    "E4 'doosra level': brief me 'PDL for longs' likha tha, par BUY ke liye PDL entry ke neeche hota hai. Isliye target = trade ki disha me agla level "
    "(PDL pe BUY -> PDH, PDH pe SELL -> PDL). Agar aage koi level nahi (jaise PDH breakout BUY) to 3:15 tak hold (E5 jaisa).",
    "1 lot ka trade aadha nahi ho sakta: 1R pe kuch nahi becha, bas SL breakeven pe gaya aur baaki rule chala. 3 lot -> 1 becha, 2 baaki.",
    "Breakeven / trailing SL candle band hone pe badla (agli candle se laagu). Swing low / high = 2 candle dono taraf se neeche / upar; 2 candle baad pakka hota hai. "
    "Trailing sirf 1R ke baad.",
    "Margin cap brief me nahi tha: Rs 5 lakh me NIFTY futures ke 2-4 lot hi aate hain (margin ~12%). Isliye kai trade me risk 1% se kam laga; R har trade ke asli risk pe hai.",
    "R = net P&L / (SL point x qty), entry fill price se. Win = net R > +0.1, loss = net R < -0.1, scratch = beech me. Same-level rule me 'loss' = net P&L < 0.",
    "Trend ke liye Jan 2021 se roz ke official close liye (warm-up), PDH / PDL 1-min data se (normal session).",
    "Selection: in-sample me sabse zyada net Rs wali setting (kam se kam 100 in-sample trade). Sab settings loss me thi, isliye ye 'sabse kam loss' wali hai.",
    "Random level: har din, kisi random doosre din ka PDH / PDL open se jitna door tha utna hi aaj ke open se door level. 20 alag random run.",
    "Entry A me E4 aur E5 lagbhag ek jaise hain: breakout ke baad trade ki disha me koi level bacha nahi hota (PDH ke upar BUY, PDL ke neeche SELL), isliye E4 bhi 3:15 tak hold. Sirf jab agli candle level ke andar khule tab wahi level target ban jaata hai.",
]
NOT_TESTED = [
    "Volume filter (signal candle volume > 1.5 x pichhle 20 ka average): NIFTY index ka volume data mid-2025 se pehle nahi hai, isliye test nahi kiya.",
    "Asli futures 1-min data (basis, spread): purane contracts ka minute data nahi hai.",
]
FORMULAS = [
    ["3-min candle", "Open = pehle minute ka open; High = 3 minute ka max; Low = 3 minute ka min; Close = teesre minute ka close (9:15, 9:18 ... 3:27)"],
    ["PDH / PDL", "PDH = kal ka High;  PDL = kal ka Low (9:15 - 3:30)"],
    ["Trend", "Close > kal ka High -> UP;  Close < kal ka Low -> DOWN;  warna pichhla trend"],
    ["Signal A", "BUY: Close > PDH  AUR  (pichhla close <= PDH  YA  Low <= PDH);   SELL: Close < PDL  AUR  (pichhla close >= PDL  YA  High >= PDL)"],
    ["Signal B", "BUY: Low <= level  AUR  Close > level  AUR  pichhla close > level;   SELL: High >= level  AUR  Close < level  AUR  pichhla close < level"],
    ["No chase", "BUY: (agli candle ka open - level) / level > X -> chhodo;  SELL: (level - open) / level > X -> chhodo"],
    ["Entry", "BUY = agli candle ka open + slippage;  SELL = open - slippage   (slippage 1 point)"],
    ["SL", "S1 = signal candle Low (BUY) / High (SELL);  S2 = level x (1 - b) BUY / level x (1 + b) SELL, b = 0.1% ya 0.2%;  S3 = BUY me max(S1, S2), SELL me min(S1, S2)"],
    ["Risk (R)", "R = |Entry - SL| (point);  R > 0.5% x price -> trade nahi"],
    ["Lots", "Lots = neeche round( min( 5,000 / (R x lot size),  10,  5,00,000 / (12% x lot size x price) ) );  Lots < 1 -> trade nahi;  Qty = Lots x lot size"],
    ["1R / target", "1R = Entry + R (BUY) / Entry - R (SELL);  2R, 3R isi tarah;  E4 = trade ki disha me agla level"],
    ["Partial", "1R chhua -> neeche round(Lots / 2) lots 1R pe;  baaki ka SL = Entry (agli candle se)"],
    ["Trailing (E1)", "Swing low = Low jo 2 candle pehle aur 2 baad se kam ho;  naya SL = max(SL, swing low) BUY;  SELL me swing high, min"],
    ["Candle ke andar", "Open SL ke paar -> exit Open pe;  Low <= SL -> SL;  phir 1R;  phir target (SL hamesha pehle)"],
    ["Exit fill", "BUY trade: exit price - slippage;  SELL trade: exit price + slippage;  3:15 = 3:15 candle ka open"],
    ["Gross P&L", "BUY: Qty x (exit fill - entry fill);  SELL: Qty x (entry fill - exit fill)   (partial aur baaki alag-alag)"],
    ["Kharcha", "Har order: Rs 20 + exchange x value + 0.0001% SEBI + STT (sirf sell) + 0.002% stamp (sirf buy) + 18% GST x (20 + exchange + SEBI)"],
    ["Net P&L", "Gross P&L - kharcha"],
    ["R multiple", "Net R = Net P&L / (R x Qty);  Raw R = bina slippage aur kharche ka P&L / (R x Qty)"],
    ["Win / loss / scratch", "Win = Net R > 0.1;  Loss = Net R < -0.1;  Scratch = beech me;  Win rate = Win / kul trade x 100"],
    ["Average jeet / haar (R)", "Jeete trades ka average Net R;  hare trades ka average Net R"],
    ["Expectancy", "Kul Net R / kul trade (aur Rs me: kul Net / kul trade)"],
    ["Profit factor", "Profit wale trades ka jod / loss wale trades ke loss ka jod (bina minus)"],
    ["Max drawdown", "Kul = trade ke hisaab se Net ka jod;  Peak = max(0, ab tak ka sabse uncha Kul);  Max DD = (Kul - Peak) ka sabse bada minus"],
    ["Return %", "Net / 5,00,000 x 100 (compounding nahi)"],
    ["Trade / din", "Kul trade / us hisse ke trading din"],
    ["MAE", "Trade ke dauraan entry se sabse bura price (point aur R me)"],
    ["In / out-of-sample", "1 Oct 2021 - 30 Sep 2024 = in-sample (setting chuni);  1 Oct 2024 - 30 Oct 2026 = out-of-sample (check)"],
    ["Paired check", "Do settings jo sirf ek cheez me alag hain:  'behtar %' = jitne jodon me nayi wali ka average Net R zyada / kul jode x 100"],
]
COST_RATES = [
    ["Brokerage", "Rs 20 har order (entry, partial, exit alag order)", "poora samay"],
    ["STT (futures, sell pe)", "0.01%", "31 Mar 2023 tak"],
    ["STT", "0.0125%", "1 Apr 2023 - 30 Sep 2024"],
    ["STT", "0.02%", "1 Oct 2024 - 31 Mar 2026"],
    ["STT", "0.05%", "1 Apr 2026 se (Budget 2026, team ke purane backtest jaisa)"],
    ["Exchange (NSE futures)", "0.002% value pe, buy + sell", "30 Sep 2024 tak"],
    ["Exchange", "0.00173%", "1 Oct 2024 se"],
    ["SEBI fee", "Rs 10 / crore = 0.0001%", "poora samay"],
    ["Stamp duty", "0.002% buy pe", "poora samay"],
    ["GST", "18% x (brokerage + exchange + SEBI)", "poora samay"],
    ["Slippage", "1 point har fill (2 point stress test)", "poora samay"],
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="pdh_pdl_nifty_2021_2026")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--workers", type=int, default=7)
    ap.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "pdh_market.pkl", help="parsed market cache (rebuilt if missing)")
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    tell = (lambda msg: None) if a.no_telegram else send
    t0 = time.time()
    log = lambda msg: print(f"[{(time.time() - t0) / 60:5.1f} min] {msg}", flush=True)  # noqa: E731
    out = result_dir(a.name)
    tell("PDH / PDL NIFTY BACKTEST STARTED: 3-min candle, 3,360 setting, in-sample Oct 2021 - Sep 2024, out-of-sample Oct 2024 - Oct 2026, "
         "futures kharcha + 1 point slippage.")
    try:
        if a.rebuild and a.cache.exists():
            a.cache.unlink()
        m = P.load_market(cache=a.cache)
        log(f"market {len(m.days)} days {m.days[0]} -> {m.days[-1]}, cut {m.cut}, dropped {m.dropped}")
        last = [time.time()]

        def prog(done, total):
            log(f"grid {min(done, total):,} / {total:,}")
            if time.time() - last[0] > 600:
                last[0] = time.time()
                tell(f"PDH / PDL: grid {min(done, total):,} / {total:,} setting ho gayi")
        res = P.run_many(P.grid_variants(), a.cache, workers=a.workers, progress=prog)
        g = pd.DataFrame([P.flat(*r) for r in res])
        g.to_csv(out / "grid.csv", index=False)
        gi = {tuple(r[k] for k in DIMS): r for r in g.to_dict("records")}
        ok = g[g.is_n >= P.CFG["min_is_trades"]]
        best = pick(ok.loc[ok.is_net.idxmax()])
        other = pick(ok[ok.entry != best["entry"]].loc[lambda x: x.is_net.idxmax()])
        log(f"grid done: IS>0 {(g.is_net > 0).sum()}, OOS>0 {(g.oos_net > 0).sum()}; best {short(best)}")
        tell(f"PDH / PDL: grid poora ({len(g):,} setting). Kharche ke baad in-sample me {(g.is_net > 0).sum()} profit me, out-of-sample me "
             f"{(g.oos_net > 0).sum()}. Ab levels / splits / report.")

        keys = [("base", "Baseline: A, W2 (11:00), S3, E1", P.variant()), ("best", "In-sample best", best),
                ("other", f"In-sample best (Entry {other['entry']})", other)]
        variants, trade_days, frames = [], set(), {}
        for key, lab, v in keys:
            tr, cnt = P.run(m, v)
            t = rep.trade_frame(tr, m.cut, P.CFG["scratch_r"])
            t.assign(setting=short(v)).to_csv(out / f"trade_log_{key}.csv", index=False, float_format="%.4f")
            trade_days |= set(t.date)
            frames[key] = t
            s = compact(P.split_stats(m, P.as_rows(tr)))
            variants.append({"key": key, "label": lab, "short": short(v), "long": long_label(v), "v": v, "stats": s, "counts": dict(cnt),
                             "trades": rep.rows(t), "sens": sensitivity(m, v, gi), "levels": level_check(m, v, P.CFG["random_seeds"])})
            log(f"{key}: {len(t)} trades, all net {s['all']['net']:,.0f}")
        vb, vs = variants[0], variants[1]

        # claims (c) and (d) on all settings, robustness
        cw = paired(g, "window", "W1", "W2", "exp_r")
        cd = paired(g, "exit", "F2", "E2", "exp_r")
        exits = []
        for e in P.GRID["exit"]:
            x = g[g.exit == e]
            row = [P.EXITS[e], len(x), x.is_win.mean(), x.oos_win.mean(), x.is_exp_r.mean(), x.oos_exp_r.mean(), x.is_exp_r0.mean(), x.oos_exp_r0.mean()]
            for f in ("F1", "F2"):
                pr = paired(g, "exit", f, e, "exp_r") if e != f else None
                row += [pr["is_better"], pr["oos_better"]] if pr else [None, None]
            exits.append(row)
        wins = [[LBL["window"][w], len(g[g.window == w]), g[g.window == w].all_n.mean(), g[g.window == w].is_win.mean(), g[g.window == w].oos_win.mean(),
                 g[g.window == w].is_exp_r.mean(), g[g.window == w].oos_exp_r.mean(), g[g.window == w].is_exp_r0.mean(), g[g.window == w].oos_exp_r0.mean()]
                for w in P.GRID["window"]]
        nb = [gi.get(tuple((val if k == d else best[k]) for k in DIMS)) for d in DIMS for val in P.GRID[d] if val != best[d]]
        rob = {"combos": len(g), "rank_corr": float(g.is_exp_r.rank().corr(g.oos_exp_r.rank())), "nb": len(nb),
               "nb_is_pos": float(np.mean([r["is_net"] > 0 for r in nb]) * 100), "nb_oos_pos": float(np.mean([r["oos_net"] > 0 for r in nb]) * 100)}
        hi_win = g[g.all_n >= 100].sort_values("all_win", ascending=False).head(10)
        claims = {
            "a": [[short(pick(r)), r.all_n, r.all_win, r.all_loss, r.all_scr, r.all_exp_r, r.all_net] for _, r in hi_win.iterrows()],
            "a_n60": int((g.all_win >= 60).sum()), "a_max": float(g[g.all_n >= 100].all_win.max()),
            "c": cw, "c_rows": wins, "d": cd, "d_rows": exits,
        }
        top = [[short(pick(r)), r.is_n, r.is_net, r.is_pf, r.is_exp_r, r.oos_n, r.oos_net, r.oos_pf, r.oos_exp_r, r.all_win]
               for _, r in ok.sort_values("is_net", ascending=False).head(15).iterrows()]
        gcols = DIMS + ["is_n", "is_win", "is_net", "is_pf", "is_exp_r", "is_exp_r0", "oos_n", "oos_win", "oos_net", "oos_pf", "oos_exp_r", "oos_exp_r0", "all_n", "all_scr", "all_dd"]
        grid_rows = [[(x.item() if hasattr(x, "item") else x) for x in r] for r in g[gcols].itertuples(index=False)]
        days = m.days
        cut_prev = days[days.index(m.cut) - 1]
        data = {
            "title": "NIFTY PDH / PDL trend (3-min)", "capital": P.CFG["capital"], "cut": m.cut, "cut_prev": cut_prev, "data_range": [days[0], days[-1]],
            "data_note": "NIFTY 50 spot 1-min candles (Fyers), 3-min me badle; futures ka kharcha", "scratch_r": P.CFG["scratch_r"],
            "settings": [["Data", "NIFTY 50 spot 1-min -> 3-min candle"], ["Capital", "Rs 5,00,000 fixed, compounding nahi", 1], ["Risk / trade", "1% = Rs 5,000", 1],
                         ["Qty", "lots = risk / (SL x lot), max 10, margin 12% capital ke andar"], ["Slippage", "1 point har fill", 1],
                         ["Kharcha", "Rs 20/order, STT, exchange, SEBI, stamp, GST (futures)", 1], ["Level", "PDH / PDL (kal ka High / Low)"],
                         ["Max SL", "0.5% of price"], ["Ek time pe", "1 trade"], ["Check", f"in-sample {days[0]} - {cut_prev} / out-of-sample {m.cut} - {days[-1]}"]],
            "variants": variants, "cols": rep.COLS, "dims": DIMS, "dim_name": DIM_NAME, "lbl": {k: {str(x): y for x, y in d.items()} for k, d in LBL.items()},
            "grid_cols": gcols, "grid": grid_rows, "groups": groups(g), "top": top, "claims": claims, "rob": rob,
            "faisla": faisla(g, best, vb["stats"], vs["stats"], vs["levels"], vb["levels"], cw, cd, rob, vs["sens"], frames["base"]),
            "rules": RULES, "notes": NOTES, "not_tested": NOT_TESTED, "formulas": FORMULAS, "cost_rates": COST_RATES,
            "data_info": [
                ["Price data", f"NIFTY 50 spot 1-min candles, {len(days):,} trading din ({days[0]} se {days[-1]}). 3-min candle 9:15, 9:18 ... 3:27 (125 roz)."],
                ["Chhode gaye din", ", ".join(f"{d} ({w})" for d, w in sorted(m.dropped.items())) + ". Inka High / Low agle din ka PDH / PDL nahi bana."],
                ["Trend warm-up", "Jan - Sep 2021 ke official daily candle (trend state shuru karne ke liye). Har din ka close = NSE ka official close."],
                ["Lot size", "NIFTY near-month futures: 50 (25 Apr 2024 tak), 25 (30 Jan 2025 tak), 75 (30 Dec 2025 tak), 65 (uske baad). Nov 2024 se NSE bhavcopy se mila."],
                ["Expiry din", f"NIFTY weekly expiry: Nov 2024 se NSE bhavcopy se, pehle Guruvar (chhutti ho to pichhla din). {int(m.expiry.sum())} expiry din."],
                ["Gap din", f"{int((m.gap != 0).sum())} din open PDH ke upar ya PDL ke neeche khula."],
                ["Trend din", f"UP {int((m.state == 1).sum())} din, DOWN {int((m.state == -1).sum())} din (daily close wala trend)."],
                ["In / out-of-sample", f"{days[0]} se {cut_prev} ({sum(d < m.cut for d in days)} din, ~60%) / {m.cut} se {days[-1]} ({sum(d >= m.cut for d in days)} din)"],
                ["Kitni setting", f"{len(g):,} grid setting + har dikhayi setting ke liye 4 extra (risk 0.5%, slippage 2, max SL 0.35% / 0.75%) + levels (open, kal ka close, 20 random)."],
            ],
            "candles": rep.candles(m, sorted(trade_days)),
        }
        rep.write_html(out / "report.html", data)
        mb = (out / "report.html").stat().st_size / 1e6
        log(f"report {mb:.1f} MB")
        (out / "summary.txt").write_text("\n".join(x.replace("<b>", "").replace("</b>", "") for x in data["faisla"]), encoding="utf-8")
    except Exception:
        tell(f"{TAG_TECH} PDH / PDL backtest FAILED:\n{traceback.format_exc()[-1500:]}")
        raise
    site = json.loads((ROOT / "site" / "site.json").read_text(encoding="utf-8"))["url"]
    num = out.name.split("_", 1)[0]
    bs, ba = vs["stats"], vb["stats"]
    msg = (f"{TAG_TRADING} PDH / PDL NIFTY BACKTEST DONE ({(time.time() - t0) / 60:.1f} min). 3-min candle, capital Rs 5 L fixed, risk 1%, slippage 1 point + futures kharcha.\n"
           f"{len(g):,} setting: kharche ke baad in-sample me {(g.is_net > 0).sum()} profit me, out-of-sample me {(g.oos_net > 0).sum()}.\n"
           f"Baseline (A, 11:00, S3, E1): {ba['all']['n']} trade, win {ba['all']['win']:.0f}%, net {rs(ba['all']['net'])}.\n"
           f"In-sample best ({short(best)}): in-sample {rs(bs['is']['net'])}, OUT-OF-SAMPLE {rs(bs['oos']['net'])} (PF {bs['oos']['pf']:.2f}).\n"
           f"60-65% win rate ka daava galat (max {claims['a_max']:.0f}%). PDH/PDL random levels se behtar nahi. 11:00 9:30 se behtar nahi. Partial + breakeven se fayda nahi.\n"
           f"Faisla: kaam nahi karti.\nWebsite: {site}\nReport: {site}#report-{num}")
    tell(msg)
    print(msg)
    if not a.no_telegram:
        send_document(out / "report.html", "PDH / PDL NIFTY report (browser me kholo). Rules tab me rules, neeche Formulas.")
    print("done ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

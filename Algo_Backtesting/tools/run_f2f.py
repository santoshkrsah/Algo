"""Abhishek Kadam F2F (video Ib9u18tXTJc): S1 Sensex 9:50 strangle + S2 STBT range breakout (Nifty, Sensex), each with and without hedge.

    python tools/run_f2f.py              # full run + report + Telegram
    python tools/run_f2f.py --no-telegram
"""
import argparse
import json
import re
import sys
import time
import traceback
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backtest import f2f, stbt, viewer as viewer_assets  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.notify import send  # noqa: E402
from backtest.report import ASSETS  # noqa: E402
from backtest.stbt_report import totals  # noqa: E402
from backtest.vmu_report import _clean  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
NAME = "kadam_f2f_The_Intraday_Options_Strategies_Pro_Traders_Actually_Use_Face2Face_Abhishek_Kadam"
CAPITAL = 400_000
SLIPS = {"0.5%": 0.005, "1%": 0.01, "0%": 0.0}
HEDGES = [["nohedge", "Hedge nahi"], ["hedge", "Hedge ke saath"]]
STRATS = [["S1", "S1 Sensex 9:50 strangle"], ["S2N", "S2 Nifty STBT"], ["S2S", "S2 Sensex STBT"]]
COLS = ["date", "symbol", "side", "strike", "hedge", "expiry", "dte", "entry_time", "und_entry", "und_sl", "exit_date", "exit_time", "und_exit", "iv_pct",
        "opt_entry", "opt_exit", "hedge_entry", "hedge_exit", "qty", "exit_reason", "reentry", "gross_pnl", "costs", "net_pnl"]

RULES = [
    ["Common (dono strategy)", [
        "Video: Abhishek Kadam (Capita Engineers) Face2Face, Elearnmarkets / Vivek Bajaj. Speaker khud bolta hai: strategy mat chalao, backtest khud karo.",
        "Har strategy me <b>1 lot</b> (Sensex lot 10, Jan 2025 se 20; Nifty lot 50 / 25 / 75 / 65 us waqt ke hisaab se), capital <b>Rs 4,00,000</b> har strategy (video: Rs 3 lakh margin + Rs 1 lakh buffer). Profit dobara invest nahi hota.",
        "Do tarah ke test: <b>bina hedge</b> (jaisa video me bola) aur <b>hedge ke saath</b> (Ayush ne dono maange). Slippage 0.5% (main), 1% aur 0% (sirf dekhne ke liye).",
        "Option price asli nahi, <b>model</b> se (Black-76), jo asli BSE / NSE ke roz ke close prices se check kiya hai. Rules tab me model check dekho.",
    ]],
    ["S1: Sensex 9:50 strangle (intraday)", [
        "Instrument: <b>Sensex weekly options</b>. Sensex me weekly options 15 May 2023 se hain, isliye test 15 May 2023 se.",
        "Din: <b>Monday se Thursday</b>. Sirf wo din jab nearest weekly expiry <b>0 se 3 din</b> door ho (DTE 0-3). Isse door ho to us din trade nahi.",
        "Entry: <b>9:50</b> baje CALL aur PUT dono <b>sell</b>. Har side me wo strike jiska premium <b>Rs 60</b> ke sabse paas ho (CALL index ke upar, PUT neeche).",
        "Stop loss: har leg alag, premium <b>50% badh jaye</b> (Rs 60 pe bechi to Rs 90 pe SL).",
        "Re-entry: SL lagne ke baad, <b>14:30 se pehle</b>, agar usi option ka price wapas apni <b>entry price tak gir jaye</b> to <b>ek baar</b> dobara sell (naya SL = naye entry price ka +50%). Sirf ek re-entry.",
        "Exit: SL na lage to <b>15:15</b> baje dono leg band.",
        "Hedge wala version: har sold leg ke saath ek door ka option <b>buy</b> (premium lagbhag Rs 10), same qty; leg band hone pe hedge bhi band; re-entry pe hedge dobara.",
    ]],
    ["S2: STBT (positional) range breakout", [
        "Index: <b>Nifty</b> (2021-10 se) aur <b>Sensex</b> (2023-05 se), dono alag. Video me index nahi bataya tha; example 22,000 Nifty jaisa tha.",
        "Range: <b>9:15 se 11:15</b> ka High aur Low. 11:15 ke baad 15:15 tak koi 1 min candle High ke upar <b>close</b> ho to <b>ITM PUT sell</b>, Low ke neeche close ho to <b>ITM CALL sell</b>. Din me sirf pehla breakout.",
        "Strike: <b>1 strike ITM</b> (PUT ke liye index ke upar wala pehla strike, CALL ke liye neeche wala).",
        "Stop loss: index entry se <b>0.5%</b> ulta chala jaye (PUT sell pe 0.5% neeche, CALL sell pe 0.5% upar).",
        "Re-entry: SL ke baad usi din 15:15 se pehle index wapas entry price pe aaye to <b>ek baar</b> dobara.",
        "Exit: SL na lage to <b>agle trading din 9:35</b> (STBT: aaj sell, kal buy).",
        "Hedge wala version: sold option ke saath same type ka <b>2% OTM</b> option buy, same qty.",
    ]],
]
NOTES = [
    "Ayush ke jawab (24 Nov 2026): DTE 0-3 + Mon-Thu = nearest weekly expiry, 0-3 DTE wale din; re-entry = jaisa best lage; S2 index aur ITM = jaisa best lage; hedge dono tarah (ke saath aur bina).",
    "Sensex ka weekly expiry din BSE bhavcopy se nikala: Friday (May 2023 - Dec 2024), Tuesday (Jan 2025 - Aug 2025), Thursday (Sep 2025 se). Isliye 0-3 DTE rule ka matlab alag saalon me alag din hai: Thursday expiry me Mon-Thu = 3,2,1,0 DTE. Tuesday era me sirf Mon (1 DTE) aur Tue (0 DTE) trade hue; Friday era me Tue-Thu (3,2,1 DTE).",
    "'Re-entry' video me saaf nahi tha. Hamne: SL ke baad (14:30 se pehle) option price wapas pehli entry price tak aaye to ek baar dobara sell.",
    "S1 me '9:15 ya 9:50' ka confusion tha; 9:50 liya. S1 hedge video me nahi tha; Ayush ne hedge ke saath bhi maanga, to Rs 10 premium wala door ka option liya.",
    "S1 ka Rs 60 premium wala option asli me 1 min ke data se milna chahiye, par expired option ka 1 min data nahi milta (sirf 15 Sep - 20 Nov 2026 ke kuch strike). Isliye price model se: Black-76, IV = pichhle din ke close pe isi expiry ke OTM options (Rs 20-200) ka asli implied vol x scale (Rules tab me).",
    "Model me ek din me IV sthir hai (asli me din me badalta-ghatta hai) aur skew nahi hai. Door ka hedge option asli me model se mehenga hoga (skew), yaani asli hedge ka kharcha zyada hoga.",
    "S1 SL check har 1 min candle ke worst price (CALL pe High, PUT pe Low) pe model price se hota hai; gap ho to candle ke open price pe fill.",
    "S2 me IV = k x pichhle 20 din ki realised vol (k asli close prices se). ITM option ka price asli ke paas hai (Rules tab ka model check dekho).",
    "S2 ka result accha dikh sakta hai kyunki sold ITM option ka delta ~0.6-0.7 hai (index ki direction pakadta hai). Model asli bid-ask ko nahi pakadta; isliye 1% slippage wala result bhi dekho.",
    "Kharcha: Rs 20 har order, STT 0.1% (sell premium pe), exchange 0.0325% (Sensex) / 0.035% (Nifty), SEBI, stamp 0.003% (buy), GST 18%. 2026 ke rate poore samay ke liye (Oct 2024 se pehle STT kam tha).",
    "Video ka 79% intraday / 20% positional / 1% buying portfolio idea aur Value-at-Risk test nahi kiya (rules nahi the). Dono strategy alag-alag test hui; combined nahi.",
    "Video ke claim: S1 max drawdown ~1-1.3%, S2 saal-wise 46% / 26% / 44%. Speaker khud kehta hai backtest return 50% kam aur drawdown double maano.",
    "Pinned comment / YouTube comments nahi padhe (bina login nahi milte).",
]
FORMULAS = [
    ["Expiry / DTE", "Expiry = nearest weekly expiry (BSE bhavcopy);  DTE = (expiry date - aaj ki date) calendar din"],
    ["Time (T)", "T = expiry din 15:30 tak bacha samay / 365 din"],
    ["Forward (F)", "F = Spot x e^((r - q) x T),  r = 6.5%, q = 1.2%"],
    ["d1, d2", "d1 = [ln(F / K) + IV^2 x T / 2] / (IV x root(T));   d2 = d1 - IV x root(T)"],
    ["CALL premium", "e^(-r x T) x [F x N(d1) - K x N(d2)]   (N = normal distribution)"],
    ["PUT premium", "e^(-r x T) x [K x N(-d2) - F x N(-d1)]"],
    ["S1: IV", "IV = m x (pichhle trading din ke close pe isi expiry aur isi side ke OTM options (Rs 20-200) ke implied vol ka median);  m asli close se (bias 0)"],
    ["S1: strike", "Entry pe, CALL ke liye index ke upar aur PUT ke liye neeche, 100 ke step me, wo strike jiska |model premium - 60| sabse kam"],
    ["S1: SL level", "SL = entry premium x 1.5;  SL laga jab 1 min candle ke worst index pe (CALL: High, PUT: Low) model premium >= SL"],
    ["S1: SL fill", "Fill = SL;  agar candle ke open pe hi premium >= SL (gap) to open wala premium"],
    ["S1: re-entry", "SL ke baad 14:30 se pehle, candle close pe premium <= pehli entry price  ->  dobara sell, naya SL = naye entry price x 1.5"],
    ["S1: hedge", "Hedge strike = sold strike se door wala jiska |model premium - 10| sabse kam; leg ke saath hi band"],
    ["S2: range", "H = 9:15-11:14 ka sabse bada High;  L = sabse chhota Low"],
    ["S2: breakout", "11:15-15:14 candle: Close > H -> SELL PE;  Close < L -> SELL CE   (pehla breakout)"],
    ["S2: strike", "PUT: K = (floor(E / step) + 1) x step;  CALL: K = (ceil(E / step) - 1) x step   (Nifty step 50, Sensex step 100)"],
    ["S2: SL", "PUT sell: SL = E x 0.995;  CALL sell: SL = E x 1.005  (candle Low / High is level ko chhue)"],
    ["S2: IV", "IV = k x RV, RV = pichhle 20 din ke daily close ke log return ka std x root(252);  IV 8% se 80% ke beech"],
    ["Slippage (s)", "Sold option: sell = P x (1 - s), buy back = P x (1 + s);  Hedge: buy = P x (1 + s), sell = P x (1 - s)"],
    ["Gross P&L", "Gross = lot x [(Sold sell - Sold buy back) + (Hedge sell - Hedge buy)]"],
    ["Kharcha", "Brokerage = Rs 20 x orders;  STT = 0.1% x sell value;  Exchange = 0.0325% (Sensex) / 0.03503% (Nifty) x (buy + sell);  SEBI = 0.0001% x (buy + sell);  Stamp = 0.003% x buy;  GST = 18% x (Brokerage + Exchange + SEBI)"],
    ["Net P&L", "Net = Gross - Kharcha"],
    ["Win rate", "Win % = (Net > 0 wale trade / kul trade) x 100"],
    ["Profit factor", "PF = jeete trades ka Net jod / hare trades ka loss jod (bina minus)"],
    ["Return %", "Return = Net / 4,00,000 x 100 (compounding nahi)"],
    ["Max drawdown", "Kul = ab tak ke Net ka jod;  Peak = max(0, ab tak ka sabse uncha Kul);  Max DD = (Kul - Peak) ka sabse bada minus"],
]


def stat_row(label, t):
    s = totals(t, CAPITAL)
    return [label, s["trades"], round(s["win_pct"], 1), round(s["net"]), s["profit_factor"], round(s["max_dd"])]


def candles(under: pd.DataFrame, days: set) -> dict:
    u = under[under.day.isin(days)].copy()
    u["b"] = u.m // 5 * 5
    g = u.groupby(["day", "b"]).agg(o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last")).reset_index()
    out = {}
    for day, x in g.groupby("day"):
        out[day] = [[int(r.b), round(r.o, 1), round(r.h, 1), round(r.l, 1), round(r.c, 1)] for r in x.itertuples()]
    return out


def model_calib(unders) -> tuple[list, dict]:
    rows, ks = [], {}
    for name in ("SENSEX", "NIFTY"):
        b = f2f.calibration_rows(name, unders[name])
        near = b[b.mny.abs() < 0.015]
        k, sc, me = f2f.fit_k(near)
        ks[name] = k
        itm = b[(b.mny < 0) & (b.mny > -0.012)]
        if len(itm):
            iv = np.clip(itm.rv.values * k, 0.08, 0.8)
            F = f2f.fwd(itm.und.values, itm.Ty.values)
            pm = np.where(itm.cp.values == "CE", f2f.price_v(F, itm.K.values, "CE", itm.Ty.values, iv), f2f.price_v(F, itm.K.values, "PE", itm.Ty.values, iv))
            itm_e = float(np.median(np.abs(pm - itm.close.values) / itm.close.values)) * 100
        else:
            itm_e = float("nan")
        rows.append([f"{name}: ATM / ITM (IV = k x 20-din RV)", len(near), round(sc * 100, 1), round(me * 100, 1),
                     f"k = {k:.2f}. Sirf 1 ITM strike ke paas (S2 wala option): median galti {itm_e:.1f}%. {b.day.min()} se {b.day.max()} ke roz ke close."])
    w = f2f.wing_iv("SENSEX", unders["SENSEX"])
    m, info = f2f.fit_wing_mult("SENSEX", unders["SENSEX"], w)
    rows.append(["SENSEX S1: OTM Rs 25-150, DTE 0-3, pichhle din ka wing IV x m", info["n"], info["med_abs_err"], info["med_err"],
                 f"m = {m:.2f}. Galti badi hai (OTM option sasta hota hai, IV roz badalta hai); bias ~0 hai. Isliye S1 ka result model ke bharose hai, dhyan se padho."])
    return rows, {"k": ks, "m": m, "wing": w}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default=NAME)
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    tell = (lambda msg: None) if a.no_telegram else send
    out = result_dir(a.name)
    t0 = time.time()
    try:
        unders = {"SENSEX": f2f.load_under("SENSEX"), "NIFTY": f2f.load_under("NIFTY")}
        calib, cal = model_calib(unders)
        tell(f"Kadam F2F: model calibration done (S2 k: Nifty {cal['k']['NIFTY']:.2f}, Sensex {cal['k']['SENSEX']:.2f}; S1 m {cal['m']:.2f}). Ab 18 run (S1, S2 Nifty, S2 Sensex x hedge x slippage).")
        lk, exps = f2f.wing_lookup(cal["wing"]), f2f.sensex_expiries()
        runs = {}

        def s2(name, hedge, slip, **kw):
            p = stbt.Params(iv_mult=cal["k"][name], slip=slip, capital=CAPITAL, month_stop=None, hedge_pct=0.02 if hedge else None, lot_fn=f2f.lot_on)
            t = stbt.simulate(name, unders[name], stbt.with_params(p, **kw), start=(f2f.SENSEX_FROM if name == "SENSEX" else None))
            t["dte"] = (pd.to_datetime(t.expiry) - pd.to_datetime(t.date)).dt.days
            return t

        def s1(hedge, slip, **kw):
            p = f2f.S1(hedge=hedge, slip=slip, iv_mult=cal["m"], capital=CAPITAL)
            return f2f.simulate_s1(unders["SENSEX"], lk, exps, replace(p, **kw))

        for hk, hname in HEDGES:
            for lbl, sv in SLIPS.items():
                runs[f"S1|{hk}|{lbl}"] = s1(hk == "hedge", sv)
                runs[f"S2N|{hk}|{lbl}"] = s2("NIFTY", hk == "hedge", sv)
                runs[f"S2S|{hk}|{lbl}"] = s2("SENSEX", hk == "hedge", sv)
                tell(f"Kadam F2F: {hname}, slippage {lbl} done ({(time.time() - t0) / 60:.1f} min).")
        for k, t in runs.items():
            t.to_csv(out / ("trades_" + k.replace("|", "_").replace("%", "pct") + ".csv"), index=False)
        checks = [["S1 Sensex strangle (bina hedge)"], stat_row("Jaisa rules me hai", runs["S1|nohedge|0.5%"])]
        for lab, kw in [("Re-entry nahi", {"reentry": False}), ("SL 30% (50% ki jagah)", {"sl": 0.3}), ("SL 80%", {"sl": 0.8}), ("Premium Rs 40 (60 ki jagah)", {"prem": 40.0}),
                        ("Premium Rs 100", {"prem": 100.0}), ("Mon-Fri, har DTE (<= 5 din)", {"weekdays": (0, 1, 2, 3, 4), "max_dte": 5}), ("Entry 9:20 (9:50 ki jagah)", {"entry_m": 560}),
                        ("Exit 14:30 (15:15 ki jagah)", {"exit_m": 870}), ("IV 20% kam", {"iv_mult": cal["m"] * 0.8}), ("IV 20% zyada", {"iv_mult": cal["m"] * 1.2})]:
            checks.append(stat_row(lab, s1(False, 0.005, **kw)))
        tell(f"Kadam F2F: S1 checks done ({(time.time() - t0) / 60:.1f} min). S2 checks chal rahe hain.")
        for name, key in (("NIFTY", "S2N"), ("SENSEX", "S2S")):
            checks += [[f"S2 {name} STBT (bina hedge)"], stat_row("Jaisa rules me hai", runs[f"{key}|nohedge|0.5%"])]
            for lab, kw in [("Re-entry nahi", {"reentry": False}), ("SL = premium 50% (index 0.5% ki jagah)", {"sl_prem": 0.5}), ("Range 9:15-9:50, breakout 9:50 ke baad", {"range_end": 590}),
                            ("2 strike ITM", {"itm_steps": 2}), ("Overnight nahi: usi din 15:15 exit", {"exit_next": False}),
                            ("IV 20% kam", {"iv_mult": cal["k"][name] * 0.8}), ("IV 20% zyada", {"iv_mult": cal["k"][name] * 1.2})]:
                checks.append(stat_row(lab, s2(name, False, 0.005, **kw)))
        series = {}
        for sym in ("SENSEX", "NIFTY"):
            days = set()
            for k, t in runs.items():
                if len(t) and t.symbol.iloc[0] == sym:
                    days |= set(t.date) | set(t.exit_date)
            series[sym] = candles(unders[sym], days)
        info = {}
        base_inv = [["Capital", f"Rs {CAPITAL:,} (har strategy, fixed, compounding nahi)"], ["Qty", "1 lot har trade (Sensex 10 / 20, Nifty 25-75 us waqt ke hisaab se)"]]
        specs = [
            ("S1", [["Index", "Sensex"], ["Entry", "9:50 CALL + PUT sell", 1], ["Premium", "Rs 60", 1], ["SL", "50% premium", 1], ["Re-entry", "1"], ["Din / DTE", "Mon-Thu, DTE 0-3", 1], ["Exit", "15:15"],
                    ["Hedge version", "Rs 10 premium wala option"]], [["Ek din me max positions", "2 leg (hedge ke saath 4 option)"]]),
            ("S2N", [["Index", "Nifty"], ["Range", "9:15-11:15", 1], ["Strike", "1 ITM", 1], ["SL", "0.5% index", 1], ["Re-entry", "1"], ["Exit", "agle din 9:35", 1], ["Hedge version", "2% OTM option"]],
             [["Ek time me max positions", "1 (hedge ke saath 2 option)"]]),
            ("S2S", [["Index", "Sensex"], ["Range", "9:15-11:15", 1], ["Strike", "1 ITM", 1], ["SL", "0.5% index", 1], ["Re-entry", "1"], ["Exit", "agle din 9:35", 1], ["Hedge version", "2% OTM option"]],
             [["Ek time me max positions", "1 (hedge ke saath 2 option)"]]),
        ]
        for key, set_, inv in specs:
            t = runs[f"{key}|nohedge|0.5%"]
            info[key] = {"first": str(t.date.min()), "last": str(t.exit_date.max()), "settings": set_, "invest": base_inv + inv}
        settings = [["Universe", "Index options (stock filter nahi)"], ["Option price", "Model (Black-76), asli close se calibrated", 1], ["Slippage", "0.5% premium (main), 1%, 0%", 1],
                    ["Kharcha", "Rs 20/order, STT 0.1%, charges", 1], ["Capital", "Rs 4,00,000 har strategy, compounding nahi", 1], ["Qty", "1 lot", 1], ["Position limit", "S1: 2 leg, S2: 1"], ["Hedge", "Dono: ke saath + bina"]]
        data = {"rules": RULES, "notes": NOTES, "formulas": FORMULAS, "checks": checks, "calib": calib, "capital": CAPITAL, "strats": STRATS, "hedges": HEDGES,
                "slips": list(SLIPS), "slipv": SLIPS, "base": ["S1", "nohedge", "0.5%"], "cols": COLS, "info": info, "settings": settings,
                "data_range": ["2021-10-01", "2026-11-20"], "data_note": "S1 + S2 Sensex: 15 May 2023 se. S2 Nifty: Oct 2021 se.", "series": series,
                "generated": datetime.now().strftime("%d %b %Y %H:%M"),
                "runs": {k: t[COLS].astype(object).where(t[COLS].notna(), None).values.tolist() for k, t in runs.items()}}
        css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
        tpl = viewer_assets.apply((ASSETS / "f2f_template.html").read_text(encoding="utf-8")).replace("__CSS__", css)
        js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
        blob = json.dumps(data, separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else _clean(o)).replace("</", "<\\/")
        (out / "report.html").write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")
    except Exception:
        tell(f"{TAG_TECH} Kadam F2F backtest FAILED:\n{traceback.format_exc()[-1500:]}")
        raise
    lines = []
    for key, lab in STRATS:
        for hk, hn in HEDGES:
            s = totals(runs[f"{key}|{hk}|0.5%"], CAPITAL)
            lines.append(f"{lab} | {hn}: {s['trades']} trade, win {s['win_pct']:.0f}%, net Rs {s['net']:,.0f} ({s['ret_pct']:.0f}% on 4L), PF {s['profit_factor'] or 0:.2f}, max DD Rs {s['max_dd']:,.0f}")
    msg = (f"{TAG_TRADING} KADAM F2F BACKTEST DONE ({(time.time() - t0) / 60:.1f} min), slippage 0.5%, option price MODEL se (S1 pe zyada bharosa mat karo):\n" + "\n".join(lines))
    tell(msg)
    print(msg)
    print("done ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

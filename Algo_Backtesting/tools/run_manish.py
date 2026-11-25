"""Manish Chandra wait & trade strangle (video YvmsB1KIcyg): backtest, checks and HTML report.

    python tools/run_manish.py               # full run + report + Telegram progress
    python tools/run_manish.py --no-telegram
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backtest import f2f, manish, viewer as viewer_assets  # noqa: E402
from backtest.config import DATA, result_dir  # noqa: E402
from backtest.notify import send  # noqa: E402
from backtest.report import ASSETS  # noqa: E402
from backtest.stbt_report import totals  # noqa: E402
from backtest.vmu_report import _clean  # noqa: E402
from run_f2f import PERIODS, candles  # noqa: E402

TAG_TECH = "@albert_max"
NAME = "manish_strangle_After_10_Years_I_finally_found_this_strategy"
CAPITAL = 1_000_000
VIDEO_ID = "YvmsB1KIcyg"
VIDEO_URL = "https://www.youtube.com/watch?v=" + VIDEO_ID
VIDEO_TITLE = "After 10 Years I finally found this strategy"
TITLE = "Manish Chandra Wait & Trade Strangle - " + VIDEO_TITLE
SLIPS = {"0.5%": 0.005, "0.3%": 0.003, "1%": 0.01}
HEDGES = [["hedge", "Hedge (video: lot 2 aate hi)"], ["nohedge", "Hedge nahi"]]
STRATS = [["X1515", "Exit agle din 15:15"], ["X920", "Exit agle din 9:20"]]
EXIT_M = {"X1515": 915, "X920": 560}
COLS = ["date", "symbol", "side", "leg", "strike", "hedge", "expiry", "dte", "ref_prem", "entry_time", "und_entry", "und_sl", "exit_date", "exit_time",
        "und_exit", "iv_pct", "opt_entry", "opt_exit", "hedge_entry", "hedge_exit", "hedge_time", "qty", "exit_reason", "vix", "gross_pnl", "costs", "net_pnl"]

RULES = [
    ["Kya trade hota hai", [
        "Video: <b>Manish Chandra</b>, channel SAHI. Speaker khud kehta hai: strategy copy mat karo, apna variation banao.",
        "<b>NIFTY</b> ke options <b>SELL</b> (option writing). <b>Agle hafte (next week)</b> ki expiry, taaki gamma risk kam rahe.",
        "Har trading din naya setup. Aaj sell, <b>agle trading din band</b> (STBT). Pichhle din ki position agle din tak saath chalti hai.",
        "Capital <b>Rs 10,00,000</b>, har lot <b>1 NIFTY lot</b> (75 / 50 / 25 / 75 / 65 us waqt ke hisaab se). Profit dobara invest nahi hota (compounding nahi).",
    ]],
    ["Entry (wait & trade)", [
        "<b>9:20</b> baje CALL aur PUT me wo strike chuno jiska premium <b>Rs 100</b> ke sabse paas ho. Is premium ko P0 kehte hain.",
        "<b>Lot 1:</b> premium 5% gir ke <b>0.95 x P0</b> (lagbhag 95) pe aaye tab sell.",
        "<b>Lot 2:</b> wahi strike, premium 15% gir ke <b>0.85 x P0</b> (lagbhag 85) pe aaye tab ek aur lot sell.",
        "CALL aur PUT dono side alag-alag. Naya lot sirf usi din 15:15 se pehle. Din me max 2 CALL lot + 2 PUT lot.",
    ]],
    ["Stop loss, trailing aur exit", [
        "Har lot pe stop loss: premium entry se <b>30% upar</b>.",
        "<b>Trailing:</b> premium entry ke har <b>1%</b> favour me (neeche) jaye to stop bhi <b>1%</b> neeche: 30% &rarr; 29% &rarr; 28% ... Stop kabhi upar nahi jata.",
        "Stop na lage to <b>agle din 15:15</b> (main test) ya <b>agle din 9:20</b> (dusra test) pe band. Video me exit ka time nahi bataya tha.",
        "Target nahi, re-entry nahi, koi indicator / VIX filter nahi, koi din chhodne ka rule nahi.",
    ]],
    ["Hedge", [
        "<b>Lot 2 aate hi</b> us side ke har khule lot ke liye ek door ka option <b>buy</b> (premium lagbhag <b>Rs 30</b>), same qty. Hedge apne lot ke saath hi band.",
        "Lot 2 na aaye to hedge nahi: aisa lot raat bhar bina hedge rehta hai (video: risk position size se sambhalo).",
        "Comparison ke liye <b>bina hedge</b> wala result bhi diya hai.",
    ]],
    ["Risk aur size (video)", [
        "Pehle drawdown dekho, phir lot tay karo. Speaker ka apna limit: capital ka <b>5%</b> drawdown. Strategies ke drawdown <b>jod</b> ke size karo.",
        "Chhote size se shuru, equity badhe to size badhao. Report me 'size card' sirf andaza hai; test 1 lot pe hi hai.",
    ]],
]
NOTES = [
    "SABSE ZARURI: option price asli nahi, <b>model</b> se hai (Black-76). Next week ke Rs 100 wale strike ka asli 1 min data ek bhi din ka nahi hai. Entry, stop aur trailing sab premium pe hain, isliye result sirf andaza hai.",
    "Model me din ke andar IV sthir hai: har din pichhle close ka IV, agle din entry wale din ke close ka IV. Asli bazaar me IV din bhar badalta hai, bid-ask aur skew bhi alag hota hai. Trailing stop asli me zyada ya kam baar lag sakta hai.",
    "Model me time decay raat aur weekend me bhi hota hai (calendar time). Extra checks me 'time decay sirf market hours me' aur 'IV 20% kam / zyada' wali lines zaroor dekho: model ki assumption badalne se profit / loss ka sign tak badal sakta hai, isliye is test se pakka faisla mat lo.",
    "IV ka multiplier m poore period ke asli close se fit kiya (thoda aage ka data use hua). Strategy ke rules pe koi tuning nahi ki.",
    "Assumptions: index NIFTY; next week = doosri sabse paas weekly expiry; har din naya trade; wait & trade usi din 15:15 tak; 5% / 15% dono 9:20 premium se; hedge Rs 30; exit agle din 15:15.",
    "Ek hi 1 min candle me stop pehle ke candles ke low se banta hai; stop laga to stop level pe fill (gap ho to candle ke open pe). Entry level pe, gap ho to open pe.",
    "Kharcha: Rs 20 har order, STT 0.1% sell premium pe, exchange 0.03503%, SEBI, stamp 0.003% buy, GST 18% (2026 ke rate poore samay ke liye). Slippage 0.5% (main), 0.3% (video wala) aur 1%. Margin alag se check nahi kiya.",
    "Video ka result (~60 mahine me ~9 loss mahine, return / drawdown ~4) StockMock screen ka hai; screen ki saari settings aur lot size bole nahi gaye. Speaker ki strategy basket, health card, rotation aur AI wali do strategy test nahi hui.",
]
FORMULAS = [
    ["Expiry / DTE", "Expiry = aaj se doosri sabse paas weekly expiry (next week);  DTE = expiry date - aaj (calendar din)"],
    ["Time (T)", "T = expiry din 15:30 tak bache minute / (365 x 1440)"],
    ["Forward (F)", "F = Spot x e^((r - q) x T),  r = 6.5%, q = 1.2%"],
    ["d1, d2", "d1 = [ln(F / K) + IV^2 x T / 2] / (IV x root(T));   d2 = d1 - IV x root(T)"],
    ["CALL / PUT premium", "CALL = e^(-rT) x [F x N(d1) - K x N(d2)];   PUT = e^(-rT) x [K x N(-d2) - F x N(-d1)]"],
    ["IV", "IV = m x (pichhle close pe isi expiry aur isi side ke OTM options (Rs 50-180) ke implied vol ka median);  agle din ke liye entry din ke close wala"],
    ["Strike", "9:20 open pe, CALL index ke upar / PUT neeche (50 ke step), jiska |premium - 100| sabse kam; P0 75-130 ke bahar ho to skip"],
    ["Entry", "Lot 1: premium <= P0 x 0.95;  Lot 2: premium <= P0 x 0.85 (CALL ka sasta bhav candle Low pe, PUT ka candle High pe)"],
    ["Stop / trailing", "n = floor((Entry - sabse kam premium) / (1% x Entry));   SL = Entry x 1.30 - n x 1% x Entry;   laga jab premium >= SL"],
    ["Hedge", "Lot 2 ke time sold strike se door wala strike jiska |premium - 30| sabse kam"],
    ["Qty", "1 lot (lot size tareekh ke hisaab se)"],
    ["Slippage (s)", "Sold: sell = P x (1 - s), buy back = P x (1 + s);  Hedge: buy = P x (1 + s), sell = P x (1 - s)"],
    ["Gross P&L", "Gross = qty x [(Sold sell - Sold buy back) + (Hedge sell - Hedge buy)]"],
    ["Kharcha", "Rs 20 x orders + STT 0.1% x sell + exchange 0.03503% x (buy + sell) + SEBI 0.0001% x (buy + sell) + stamp 0.003% x buy + GST 18% x (brokerage + exchange + SEBI)"],
    ["Net P&L", "Net = Gross - Kharcha"],
    ["Win rate", "Win % = (Net > 0 wale lot / kul lot) x 100"],
    ["Profit factor", "PF = jeete trades ka Net jod / hare trades ka loss jod (bina minus)"],
    ["Return %", "Return = Net / 10,00,000 x 100 (compounding nahi)"],
    ["Max drawdown", "Kul = ab tak ke Net ka jod;  Peak = max(0, ab tak ka sabse uncha Kul);  Max DD = (Kul - Peak) ka sabse bada minus"],
    ["Size card", "Lot gunaa = floor((5% x capital) / |1 lot wale test ka Max DD|)"],
]


def row(label, t):
    s = totals(t.assign(reentry="nahi"), CAPITAL)
    return [label, s["trades"], round(s["win_pct"], 1), round(s["net"]), s["profit_factor"], round(s["max_dd"])]


def concurrency(t: pd.DataFrame) -> tuple[int, float]:
    """(most sold lots open at once, most premium open at once in rupees)."""
    ev = []
    for r in t.itertuples():
        v = r.qty * r.opt_entry
        ev.append((f"{r.date} {r.entry_time}", 1, v))
        ev.append((f"{r.exit_date} {r.exit_time}", -1, -v))
    n = val = top_n = 0
    top_v = 0.0
    for _, dn, dv in sorted(ev, key=lambda e: (e[0], e[1])):
        n, val = n + dn, val + dv
        top_n, top_v = max(top_n, n), max(top_v, val)
    return top_n, top_v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default=NAME)
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    tell = (lambda msg: None) if a.no_telegram else send
    out = result_dir(a.name)
    t0 = time.time()

    def say(msg):
        print(msg, flush=True)
        tell("Manish strangle: " + msg)

    try:
        under = f2f.load_under("NIFTY")
        v = pd.read_csv(DATA / "index" / "INDIAVIX_FROM2021_1m.csv", usecols=["datetime", "open"])
        v = v[v.datetime.str[11:16] == "09:20"]
        vix = dict(zip(v.datetime.str[:10], v.open))
        lk, m_fit, info = manish.iv_table(under)
        A, exps = manish.arrays(under), manish.expiries(under)
        say(f"model calibration done (m {info['m']}, median galti {info['med_abs_err']}%). Ab main run.")

        def sim(**kw):
            return manish.simulate(A, lk, m_fit, exps, replace(manish.P(), **kw), f2f.START, vix)

        raw = {k: sim(exit_m=EXIT_M[k]) for k, _ in STRATS}
        runs = {f"{k}|{hk}|{lbl}": manish.apply(raw[k], sv, hk == "hedge").assign(reentry="nahi")
                for k, _ in STRATS for hk, _ in HEDGES for lbl, sv in SLIPS.items()}
        for k, t in runs.items():
            t[COLS].to_csv(out / ("trades_" + k.replace("|", "_").replace("%", "pct") + ".csv"), index=False)
        base = runs["X1515|hedge|0.5%"]
        say(f"main run done ({(time.time() - t0) / 60:.1f} min). Ab extra checks (entry time 9:20-9:30 aur baaki).")
        checks = [["Entry time stability (video ka test: har minute)"]]
        for mm in range(560, 571):
            lab = f"Entry 9:{mm - 540:02d}" + (" (rules wala)" if mm == 560 else "")
            checks.append(row(lab, base if mm == 560 else manish.apply(sim(entry_m=mm), 0.005, True)))
        checks.append(["Ek-ek rule badal ke"])
        checks.append(row("Jaisa rules me hai", base))
        for lab, kw in [("Trailing nahi (sirf 30% SL)", {"trail": False}), ("Lot 2 nahi (isliye hedge bhi nahi)", {"wait2": None}),
                        ("Wait & trade nahi: 9:20 pe seedha 1 lot", {"wait1": 0.0, "wait2": None}), ("SL 20%", {"sl": 0.20}), ("SL 40%", {"sl": 0.40}),
                        ("Hedge premium Rs 20", {"hedge_prem": 20.0}), ("Hedge premium Rs 50", {"hedge_prem": 50.0}),
                        ("Exit agle din 12:00", {"exit_m": 720}), ("Overnight nahi: usi din 15:15 exit", {"exit_next": False}),
                        ("Isi hafte ki expiry (next week nahi)", {"expiry_n": 0}), ("Model: time decay sirf market hours me", {"trading_theta": True}),
                        ("IV 20% kam", {"iv_mult": 0.8}), ("IV 20% zyada", {"iv_mult": 1.2})]:
            checks.append(row(lab, manish.apply(sim(**kw), 0.005, True)))
        checks.append(["BINA HEDGE wale result pe model ke checks"])
        checks.append(row("Bina hedge, jaisa rules me hai", runs["X1515|nohedge|0.5%"]))
        for lab, kw in [("Bina hedge: time decay sirf market hours me", {"trading_theta": True}), ("Bina hedge: IV 20% kam", {"iv_mult": 0.8}),
                        ("Bina hedge: IV 20% zyada", {"iv_mult": 1.2}), ("Bina hedge: usi din 15:15 exit", {"exit_next": False})]:
            checks.append(row(lab, manish.apply(sim(**kw), 0.005, False)))
        say(f"checks done ({(time.time() - t0) / 60:.1f} min). Ab report.")
        days = set()
        for t in runs.values():
            days |= set(t.date) | set(t.exit_date)
        series = {"NIFTY": candles(under, days)}
        settings = [["Universe", "NIFTY index options (stock filter nahi)"], ["Expiry", "Next week", 1], ["Option price", "Model (Black-76), asli close se calibrated", 1],
                    ["Strike", "9:20 pe premium ~Rs 100", 1], ["Entry", "Wait & trade 5% aur 15%", 1], ["SL", "30% premium + trailing 1% : 1%", 1],
                    ["Hedge premium", "~Rs 30, lot 2 pe"], ["Target / re-entry", "Nahi"], ["Din / filter", "Har trading din, koi filter nahi"],
                    ["Capital", "Rs 10,00,000, compounding nahi", 1], ["Qty", "1 lot har leg", 1], ["Risk per trade", "Fixed rupaye nahi: SL 30% premium (gap me zyada)"],
                    ["Max per stock", "Lagu nahi (index)"], ["Slippage", "0.5% premium (main), 0.3%, 1%", 1], ["Kharcha", "Rs 20/order, STT 0.1%, charges", 1],
                    ["Period", "1 Jan 2021 - 23 Nov 2026"]]
        infos = {}
        for k, lab in STRATS:
            t = runs[f"{k}|hedge|0.5%"]
            top_n, top_v = concurrency(t)
            infos[k] = {"first": str(t.date.min()), "last": str(t.exit_date.max()), "settings": [["Exit", lab, 1]],
                        "invest": [["Capital", "Rs 10,00,000 (fixed, compounding nahi)"], ["Qty", "1 lot har leg (25 - 75 us waqt ke hisaab se)"],
                                   ["Risk per trade", "SL 30% premium: Rs 100 x 30% x 65 qty = lagbhag Rs 2,000 har lot (gap me zyada)"],
                                   ["Max per stock", "Lagu nahi (index)"], ["Ek time me max khule sold lot", f"{top_n}"],
                                   ["Peak money deployed (khula sold premium)", f"Rs {top_v:,.0f}"]]}
        calib = [["NIFTY next week: OTM Rs 60-150, DTE 6-15, pichhle close ka IV x m", info["n"], info["med_abs_err"], info["med_err"],
                  f"m = {info['m']}. Aadhe options me galti {info['med_abs_err']}% se kam, 80% me {info['p80']}% se kam. Ye sirf close ka check hai, din ke andar ka nahi."]]
        data = {"title": TITLE, "source": {"title": VIDEO_TITLE, "url": VIDEO_URL, "id": VIDEO_ID}, "periods": PERIODS, "rules": RULES,
                "notes": [re.sub(r"<[^>]+>", "", n) for n in NOTES], "formulas": FORMULAS, "checks": checks, "calib": calib, "capital": CAPITAL,
                "strats": STRATS, "hedges": HEDGES, "slips": list(SLIPS), "slipv": SLIPS, "base": ["X1515", "hedge", "0.5%"], "cols": COLS, "info": infos,
                "settings": settings, "data_range": [f2f.START, f2f.END], "data_note": "NIFTY 1 min + roz ke option close", "series": series,
                "generated": datetime.now().strftime("%d %b %Y %H:%M"),
                "runs": {k: t[COLS].astype(object).where(t[COLS].notna(), None).values.tolist() for k, t in runs.items()}}
        css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
        tpl = viewer_assets.apply((ASSETS / "manish_template.html").read_text(encoding="utf-8")).replace("__CSS__", css)
        js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
        blob = json.dumps(data, separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else _clean(o)).replace("</", "<\\/")
        (out / "report.html").write_text(tpl.replace("__TITLE__", TITLE.replace("&", "&amp;")).replace("__VURL__", VIDEO_URL).replace("__VID__", VIDEO_ID)
                                         .replace("__VTITLE__", VIDEO_TITLE).replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")
        summ = {}
        for key, t in runs.items():
            s = totals(t, CAPITAL)
            summ[key] = {"lots": s["trades"], "win": round(s["win_pct"], 1), "net": round(s["net"]), "pf": s["profit_factor"], "dd": round(s["max_dd"]),
                         "gross": round(float(t.gross_pnl.sum())), "costs": round(float(t.costs.sum())),
                         "periods": {n: round(float(t.net_pnl[(t.date >= a_) & (t.date <= b_)].sum())) for n, a_, b_ in PERIODS},
                         "years": {y: round(float(g.net_pnl.sum())) for y, g in t.groupby(t.date.str[:4])},
                         "months": f"{int((t.groupby(t.date.str[:7]).net_pnl.sum() > 0).sum())} / {t.date.str[:7].nunique()}"}
        (out / "summary.json").write_text(json.dumps({"runs": summ, "calib": calib, "checks": checks, "skipped": raw["X1515"].attrs.get("skipped")},
                                                     indent=1, default=float), encoding="utf-8")
    except Exception:
        tell(f"{TAG_TECH} Manish strangle backtest FAILED:\n{traceback.format_exc()[-1500:]}")
        raise
    for key, s in summ.items():
        print(key, s["net"], s["pf"] and round(s["pf"], 2), s["dd"])
    say(f"backtest + report ban gaya ({(time.time() - t0) / 60:.1f} min). Ab check karke website upload.")
    print("done ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

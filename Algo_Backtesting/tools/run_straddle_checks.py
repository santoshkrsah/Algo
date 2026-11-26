"""Report 10's "Extra checks" table: every row of the "Kya badla" column as its own full backtest and its own report.

Rows 1-24: one report each with a NIFTY and a SENSEX button (only the chosen index's rules, formulas, days and trade viewer
are shown). Rows 25-26: one BANKNIFTY report each. Same engine, costs and slippage as report 10; period to 23 Nov 2026
(NIFTY / BANKNIFTY from 1 Nov 2021, SENSEX from 15 May 2023). Strike adjustment on SENSEX: 200 points scaled by
SENSEX / NIFTY at 9:20 of that day.

    python tools/run_straddle_checks.py sim        # simulate everything -> .cache/straddle_checks/runs.pkl (+ check vs report 10)
    python tools/run_straddle_checks.py build      # write the 26 reports from that file
"""
import json
import pickle
import re
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import run_straddle as R  # noqa: E402
from backtest import straddle as S, viewer as viewer_assets  # noqa: E402
from backtest.config import ROOT, result_dir  # noqa: E402
from backtest.notify import send  # noqa: E402
from backtest.report import ASSETS  # noqa: E402
from backtest.straddle_report import COLS, premium_series, rows_of  # noqa: E402
from backtest.vmu_report import _clean  # noqa: E402

END, SPLIT = "2026-11-23", "2024-11-01"
FROM = {"NIFTY": ("2021-10-25", "2021-11-01"), "SENSEX": ("2023-05-08", "2023-05-15"), "BANKNIFTY": ("2021-10-25", "2021-11-01")}
CACHE = ROOT / ".cache" / "straddle_checks"
HEAD = "Straddle + Risk Layer (2021 - 2026 retest)"
P0, CORE, BASE = R.P0, R.CORE, R.RISK_ONLY


def W(**kw) -> S.Params:
    return S.with_params(BASE, **kw)


# (slug, "Kya badla" name exactly as in report 10, Params, what is different from the main test)
TESTS = [
    ("main", "Main test: straddle + risk layer", BASE, "Kuch nahi badla: yahi main setup hai (9:20 ATM straddle SELL + leg SL, din ka loss limit, trailing)."),
    ("no_leg_sl", "Leg SL band", W(leg_sl=None), "Main test se: leg SL hata diya. Din ka loss limit aur trailing waise hi."),
    ("no_day_sl", "Din ka loss limit band", W(day_sl=None), "Main test se: din ka loss limit (kill switch) hata diya. Leg SL aur trailing waise hi."),
    ("no_trail", "Trailing band", W(trail=None), "Main test se: trailing profit lock hata diya. Leg SL aur din ka loss limit waise hi."),
    ("no_risk", "Poori risk layer band (sirf straddle bechna)", CORE, "Main test se: poori risk layer hata di. Sirf straddle bechna aur 3:15 pe band."),
    ("leg_sl_50", "Leg SL 50%", W(leg_sl=0.5), "Main test se: leg SL 75% ki jagah 50%."),
    ("leg_sl_100", "Leg SL 100%", W(leg_sl=1.0), "Main test se: leg SL 75% ki jagah 100%."),
    ("day_sl_1", "Din ka loss limit 1%", W(day_sl=0.01), "Main test se: din ka loss limit 1.5% ki jagah 1%."),
    ("day_sl_2", "Din ka loss limit 2%", W(day_sl=0.02), "Main test se: din ka loss limit 1.5% ki jagah 2%."),
    ("trail_30", "Trailing 30% pe chalu", W(trail=0.3), "Main test se: trailing 50% ki jagah 30% profit pe chalu."),
    ("trail_70", "Trailing 70% pe chalu", W(trail=0.7), "Main test se: trailing 50% ki jagah 70% profit pe chalu."),
    ("stop_touch", "Stop ka confirm nahi (1 minute me touch pe)", W(confirm=1), "Main test se: stop ke liye 2 lagatar close nahi chahiye, 1 minute me level touch hote hi stop."),
    ("adj", "Strike adjustment chalu (ek leg 200 pt)", W(adj_pts=P0.adj_pts), "Main test me strike adjustment layer chalu ki."),
    ("tactical", "Tactical buy chalu", W(tactical=True), "Main test me tactical buy layer chalu ki."),
    ("scalp", "Delta scalping chalu", W(scalp=True), "Main test me delta scalping (futures hedge) layer chalu ki."),
    ("dd_scale", "Drawdown scaling chalu", W(dd_steps=P0.dd_steps), "Main test me drawdown scaling layer chalu ki."),
    ("pause", "Pause chalu (gap / VIX / Budget)", W(pause=True), "Main test me pause layer chalu ki: kuch din trade nahi."),
    ("avg", "Averaging chalu", W(avg=True), "Main test me averaging layer chalu ki."),
    ("all_layers", "Report 16 ka main test (saare layer chalu)", P0, "Report 16 wala setup: risk layer ke saath adjustment, tactical buy, delta scalping, drawdown scaling aur pause sab chalu (averaging band)."),
    ("entry_915", "Entry 9:15 pe", W(entry_t=0), "Main test se: entry 9:20 ki jagah 9:15 (din ke open pe)."),
    ("next_week", "Agli weekly expiry", W(expiry="next"), "Main test se: sabse paas wali ki jagah uske baad wali expiry."),
    ("hold_expiry", "Expiry tak hold (har din 3:15 pe square-off nahi)", W(hold="expiry"), "Main test se: roz 3:15 pe band nahi, expiry din tak hold."),
    ("slip_0", "Slippage 0", W(slip_opt=0.0, slip_fut=0.0), "Main test se: slippage 0 (sirf dekhne ke liye; asli me slippage hota hi hai)."),
    ("slip_high", "Slippage zyada: option 1%, futures 0.05%", W(slip_opt=0.01, slip_fut=0.0005), "Main test se: slippage option 0.5% ki jagah 1%, futures 0.02% ki jagah 0.05%."),
    ("bn_risk", "BANKNIFTY: straddle + risk layer", S.with_params(BASE, index="BANKNIFTY"), "Main test wala setup BANKNIFTY pe."),
    ("bn_core", "BANKNIFTY: sirf straddle", S.with_params(CORE, index="BANKNIFTY"), "BANKNIFTY pe sirf straddle bechna, koi risk layer nahi."),
]


def indices_of(p: S.Params) -> list[str]:
    return ["BANKNIFTY"] if p.index == "BANKNIFTY" else ["NIFTY", "SENSEX"]


# ---------------------------------------------------------------- simulation
def one_minute(bars: np.ndarray) -> list[int]:
    """Flat ints per 1-minute candle: open - last close, high - open, open - low, close - open (price x 20)."""
    o, h, l, c = (np.rint(bars[:, i] * 20).astype(np.int64) for i in range(4))
    prev = np.concatenate([[0], c[:-1]])
    return np.column_stack([o - prev, h - o, o - l, c - o]).ravel().tolist()


def sim_index(index: str, say) -> dict:
    t0 = time.time()
    load_from, start = FROM[index]
    mk = S.Market(index, load_from, END)
    nifty = S._minutes(S.DATA / "index" / S.SPOT_FILE["NIFTY"], load_from, END) if index == "SENSEX" else None
    todo = {slug: replace(p, index=index) for slug, _, p, _ in TESTS if index in indices_of(p)}
    sims = {k: S.Sim(p) for k, p in todo.items()}
    prem, traded, ratios, ratio = {}, set(), {}, None
    days = mk.days(start, END)
    for i, day in enumerate(days):
        d = mk.day(day)
        if nifty is not None:                                     # 200 NIFTY points -> the same move in SENSEX points
            if day in nifty:
                ratio = float(d.S[5] / nifty[day][4, 3])
            ratios[day] = ratio
        for k, s in sims.items():
            if nifty is not None and todo[k].adj_pts is not None:
                s.p = replace(todo[k], adj_pts=todo[k].adj_pts * ratio)
            s.step(d)
            r = s.log[-1]
            if r["traded"]:
                traded.add(day)
                key = f"{index}|{day}|{r['expiry']}|{r['strike']:g}"
                if key not in prem and r["expiry"] in d.smiles:
                    prem[key] = premium_series(d, r["expiry"], float(r["strike"]))
        if i % 100 == 0:
            say(f"{index}: {i}/{len(days)} din ({(time.time() - t0) / 60:.0f} min)")
    logs = {k: S.frame(s) for k, s in sims.items()}
    for k, f in logs.items():
        gap = float((f[["pnl_core", "pnl_avg", "pnl_tac", "pnl_hedge"]].sum(axis=1) - f.gross).abs().max())
        assert gap < 1.0, (index, k, gap)
    out = {"logs": logs, "prem": prem, "series": {f"{index}|{d}": one_minute(mk.bars[d]) for d in sorted(traded)},
           "first": days[0], "last": days[-1], "ndays": len(days), "ratios": ratios}
    if index == "NIFTY":
        out["validate"] = R.validate(mk)
    say(f"{index}: {len(sims)} setup poore ({(time.time() - t0) / 60:.1f} min)")
    return out


def report10_checks() -> dict:
    h = (next((ROOT / "results").glob("10_*")) / "report.html").read_text(encoding="utf-8")
    i = h.find("const D = ")
    d, _ = json.JSONDecoder().raw_decode(h, h.index("{", i))
    return {c[0]: c for c in d["checks"] if len(c) > 1}


def verify(data: dict) -> list[str]:
    """NIFTY / BANKNIFTY cut at report 10's last day (3 Nov 2026) must give report 10's numbers."""
    old, lines = report10_checks(), []
    for slug, label, p, _ in TESTS:
        index = "BANKNIFTY" if p.index == "BANKNIFTY" else "NIFTY"
        f = data[index]["logs"][slug]
        s = S.stats(f[f.date <= "2026-11-03"], BASE.capital)
        c = old[label]
        ok = s["days"] == c[1] and abs(s["net"] - c[3]) < 1
        lines.append(f"{'OK  ' if ok else 'DIFF'} {label}: ab {s['days']} din net {s['net']:,.0f} | report 10 {c[1]} din net {c[3]:,.0f}")
    return lines


# ---------------------------------------------------------------- text per report
def hm(t: int) -> str:
    return R._hm(t)


def rules_of(label: str, change: str, p: S.Params, index: str, first: str, last: str) -> list[str]:
    touch = p.confirm <= 1
    conf = "1 minute ke andar level touch hote hi" if touch else f"{p.confirm} lagatar 1-min close level ke paar rahe tab"
    exp = {"NIFTY": "NIFTY (NSE weekly expiry)", "SENSEX": "SENSEX (BSE, sabse paas wali weekly expiry)",
           "BANKNIFTY": "BANKNIFTY (NSE; Nov 2024 tak weekly expiry, uske baad sirf monthly)"}[index]
    r = [f"Is report me test: {label}. {change}",
         f"Index: {exp}. Period {first} se {last}. Fit = {SPLIT} se pehle, verify = {SPLIT} se.",
         "Paisa: capital Rs 30 lakh, usme se 1/3 (Rs 10 lakh) margin me, baaki 2/3 buffer. Lots = Rs 10 lakh / (10% x lot size x index), neeche round (kam se kam 1). Profit dobara invest nahi hota (compounding nahi).",
         f"Entry: har trading din {hm(p.entry_t)} pe ATM straddle SELL (CE aur PE, same strike, {'sabse paas wali' if p.expiry == 'cur' else 'uske baad wali (agli)'} expiry)."]
    if p.hold == "day":
        r.append("Exit: 3:15 pe jo bhi khula hai sab band (intraday).")
    else:
        r.append("Exit: roz band nahi karte; position expiry din 3:15 tak rakhi jaati hai (raat ko bhi). Expiry ke baad agle din nayi straddle.")
    if p.leg_sl is not None:
        r.append(f"Leg SL: kisi leg ka premium entry se {p.leg_sl * 100:g}% upar jaye ({conf}) to sirf wo leg band. Doosra leg chalta rehta hai. Band leg dobara nahi bechte.")
    else:
        r.append("Leg SL nahi hai.")
    if p.day_sl is not None:
        r.append(f"Din ka loss limit (kill switch): din ka P&L capital ke {p.day_sl * 100:g}% (Rs {p.day_sl * p.capital:,.0f}) loss tak pahunche ({conf}) to sab band.")
    else:
        r.append("Din ka loss limit nahi hai.")
    if p.trail is not None:
        r.append(f"Trailing: profit becha hua premium (Rs) ka {p.trail * 100:g}% ho jaye to trailing chalu. Uske baad profit apne sabse upar wale level ka aadha reh jaye ({conf}) to sab band.")
    else:
        r.append("Trailing profit lock nahi hai.")
    if p.adj_pts is not None:
        pts = "200 x (us din 9:20 ka SENSEX / NIFTY) point, yaani lagbhag 600 - 700 point (NIFTY ke 200 point ke barabar move)" if index == "SENSEX" else f"{p.adj_pts:g} point"
        r.append(f"Strike adjustment: ek bechi hui leg ka premium entry se {pts} upar jaye ({conf}) to wo leg buy back karo aur usi type ka naya strike becho jiska premium doosre leg ke barabar ho. Din me max {p.adj_max}.")
    if p.avg:
        r.append(f"Averaging: poori straddle ka premium entry se {p.avg_trigger * 100:g}% upar ho to {p.avg_frac * 100:g}% lots aur becho (din me ek baar), jab tak din ka loss limit ke aadhe se kam ho.")
    if p.tactical:
        r.append(f"Tactical buy: leg SL lagne ke baad index opening range (9:15 - 9:30) ko usi taraf tode (CE leg SL = upar, PE leg SL = neeche) to ATM option BUY. Core lots ka {p.tac_frac * 100:g}%, SL premium ka {p.tac_sl * 100:g}%, 2:30 ke baad nahi.")
    if p.scalp:
        r.append(f"Delta scalping: saare option ka net delta (har core lot) +-{p.band:g} se bahar jaye aur {p.macd_tf}-minute MACD (12, 26, 9) histogram hedge ki taraf palte to futures se hedge. Delta wapas band me aaye to hedge band.")
    if p.dd_steps:
        r.append("Drawdown scaling: equity peak se " + " / ".join(f"{x * 100:g}%" for x in p.dd_steps) + " neeche jaye to har baar size aadha; wapas us limit ke aadhe tak aaye to size badhta hai.")
    if p.pause:
        r.append(f"Pause (us din trade nahi): gap {p.gap_max * 100:g}% se zyada, ya India VIX entry tak pichhle close se {p.vix_jump * 100:g}% upar, ya Budget din (1 Feb 2025, 1 Feb 2026).")
    exch = "BSE 0.0325%" if index == "SENSEX" else "NSE 0.03503%"
    r.append(f"Kharcha: Rs 20 har order, STT (tareekh ke hisaab se 4 rate), exchange {exch} premium pe, SEBI, stamp, GST 18%. Slippage option {p.slip_opt * 100:g}%" + (f", futures {p.slip_fut * 100:g}%" if p.scalp else "") + " har fill pe.")
    return r


def formulas_of(p: S.Params, index: str) -> list:
    keep = {"Straddle premium", "Option price (Black-76)", "Forward F", "IV per strike", "Fill price", "Gross P&L", "Net P&L", "Return %", "Win rate", "Profit factor", "Max drawdown"}
    f = [x for x in R.FORMULAS if x[0] in keep]
    f.insert(1, ["Lots", "floor(capital x 1/3 / (10% x lot size x index)); kam se kam 1" + ("  x 0.5^(drawdown stage)" if p.dd_steps else "")])
    conf = "1 minute ke andar touch" if p.confirm <= 1 else f"{p.confirm} lagatar 1-min close"
    if p.leg_sl is not None:
        f.append(["Leg SL", f"leg ka price >= entry price x {1 + p.leg_sl:g} ({conf})"])
    if p.day_sl is not None:
        f.append(["Kill switch", f"din ka P&L <= -{p.day_sl * 100:g}% x capital ({conf})"])
    if p.trail is not None:
        f.append(["Trailing", f"cycle profit >= {p.trail * 100:g}% x becha hua premium (Rs) ke baad: profit < 50% x peak profit ({conf})"])
    if p.adj_pts is not None:
        pts = "200 x SENSEX(9:20) / NIFTY(9:20)" if index == "SENSEX" else f"{p.adj_pts:g}"
        f.append(["Strike adjustment", f"leg price >= entry + {pts} pt: leg buy back; naya strike = jiska price doosre leg ke price ke sabse paas (+-40 strike me)"])
    if p.avg:
        f.append(["Averaging", f"CE + PE >= (1 + {p.avg_trigger:g}) x entry premium: lots x {p.avg_frac:g} aur sell"])
    if p.tactical:
        f.append(["Tactical buy", f"leg SL ke baad index > opening range high (CE SL) / < low (PE SL): ATM option buy, lots = max(1, round({p.tac_frac * 100:g}% x core lots)), SL = {p.tac_sl * 100:g}% premium"])
    if p.scalp:
        f += [["Delta", "CALL: e^(-rT) x N(d1); PUT: -e^(-rT) x N(-d1);  net delta / lot = sum(side x delta x qty) / lot size / core lots"],
              ["Hedge", f"|net delta / lot| > {p.band:g} aur {p.macd_tf}-min MACD histogram palta: futures lots = round(|net delta|); band me aate hi band"],
              ["MACD histogram", "EMA(12) - EMA(26) ka signal EMA(9) se farq"]]
    if p.pause:
        f.append(["Pause", f"|open / pichhla close - 1| > {p.gap_max:g}  ya  VIX / pichhla VIX - 1 > {p.vix_jump:g}  ya Budget din"])
    f += [["Kharcha", "Rs 20 x orders + STT (sell side) + exchange (" + ("BSE option 0.0325%" if index == "SENSEX" else "NSE option 0.03503%") + (", futures 0.00173%" if p.scalp else "")
           + ") + SEBI 0.0001% + stamp (buy side) + GST 18% x (brokerage + exchange + SEBI)"],
          ["STT (option sell" + (" / futures sell" if p.scalp else "") + ")", "1 Apr 2023 se pehle 0.05%, phir 0.0625% (1 Apr 2023), 0.1% (1 Oct 2024), 0.15% (1 Apr 2026)" + ("; futures 0.01% / 0.0125% / 0.02% / 0.05%" if p.scalp else "")],
          ["Average / din", "net / trade wale din"]]
    if index != "SENSEX":
        f.append(["Purana lot size (7 Jul 2024 tak)", "median(turnover / (contracts x (strike + (open+close)/2))), NSE ke round lot pe snap"])
    return f


def notes_of(p: S.Params, index: str) -> list[str]:
    n = ["Option price model se hai: expired index options ka 1-minute data nahi milta. Asli 1-min index + Black-76; har strike ka IV us din ke asli bhavcopy ke open aur close price se, beech me seedhi line. Entry / exit ke aas-paas price asli ke paas, par din ke andar ke stop ki timing andaza hai.",
         "Ye setup pehle ke report ke result dekh ke chuna gaya tha (Dec 2024 - Oct 2026 ka data), isliye us daur ka result 'dekha hua' hai. Asli me result kam aa sakta hai.",
         "Short straddle ka tail risk bada hai: ek gap ya news din mahino ka premium kha sakta hai. 'Sabse bura din' dekho.",
         "Margin 10% x contract value maana (asli SPAN alag hota hai). Lots poore number me.",
         "Fills 1-min price pe + slippage. Bahut tez move me asli fill isse door ho sakta hai."]
    if p.hold == "expiry":
        n.append("Raat ko position rakhi jaati hai; raat ka move agle din ke P&L me jodte hain. Din ka loss limit har din alag gina jaata hai.")
    if index == "SENSEX":
        n += ["SENSEX option data BSE bhavcopy se, 15 May 2023 se (usse pehle Sensex weekly option nahi the). Purani BSE file (4 Jan 2024 tak) me expiry din ka option close nahi hota, wo din trade nahi hue; baaki bhi kuch expiry din chhoote. Isliye NIFTY se seedha compare mat karo.",
              "BSE exchange charge 0.0325% poore period ke liye flat maana. Sensex ke option NIFTY se kam liquid hain; model ki jaanch Sensex pe asli 1-min price se nahi ho sakti."]
        if p.adj_pts is not None:
            n.append("Strike adjustment ka 200 point NIFTY ke liye tha; SENSEX pe ise us din ke SENSEX / NIFTY bhaav se bada kiya (jaisa kaha gaya). Purani report me SENSEX pe 200 hi tha, isliye numbers alag honge.")
    else:
        n.append("Nov 2021 se 7 Jul 2024 tak NSE ki purani bhavcopy me lot size aur trade count nahi hote: lot size turnover se nikala, liquidity filter volume se.")
    if index == "BANKNIFTY":
        n.append("BANKNIFTY me Nov 2024 se pehle weekly expiry thi, uske baad sirf monthly: dono daur ka behaviour alag hai.")
    return n


def info_of(p: S.Params, index: str, f: pd.DataFrame, d: dict, validate: list) -> list:
    tr = f[f.traded == 1]
    skipped = f[f.traded == 0]
    out = [["Index 1-minute", f"{index} (Fyers 1-min) + India VIX (1-min)"],
           ["Option data", ("BSE" if index == "SENSEX" else "NSE") + " index option bhavcopy (daily, expired contracts bhi): har strike ka Open / Close / trades"],
           ["Din", f"{len(tr)} trade din, {len(skipped)} din trade nahi, kul {len(f)} trading din ({f.date.min()} se {f.date.max()})"],
           ["Lot size (data se)", ", ".join(str(x) for x in sorted(set(tr.lot.dropna().astype(int))))]]
    if len(skipped):
        why = skipped.apply(lambda r: "Pause" if r.pause else r.exit, axis=1).value_counts()
        out.append(["Trade kyun nahi", ", ".join(f"{k}: {v} din" for k, v in why.items())])
    if index == "SENSEX" and p.adj_pts is not None:
        x = np.array([v for v in d["ratios"].values() if v]) * p.adj_pts
        out.append(["Adjustment point (SENSEX)", f"average {x.mean():.0f} point (kam se kam {x.min():.0f}, zyada se zyada {x.max():.0f})"])
    if index == "NIFTY":
        out += validate
    return out


def chips_of(p: S.Params, index: str) -> list:
    c = R.chips_of(replace(p, index=index))
    c[0] = ["Index", {"NIFTY": "NIFTY (weekly)", "SENSEX": "SENSEX (BSE weekly)", "BANKNIFTY": "BANKNIFTY (weekly Nov 2024 tak, phir monthly)"}[index], 1]
    if index == "SENSEX" and p.adj_pts is not None:
        c = [["Strike adjust", f"200 pt x SENSEX/NIFTY (ek leg), din me max {p.adj_max}", 1] if x[0] == "Strike adjust" else x for x in c]
    return c


# ---------------------------------------------------------------- build
def title_of(label: str) -> str:
    return f"{HEAD} - {label}"


def build(data: dict) -> list:
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "straddle_check_template.html").read_text(encoding="utf-8")).replace("__CSS__", css)
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    made = []
    for n, (slug, label, p, change) in enumerate(TESTS, 1):
        out = result_dir(f"straddle_check_{n:02d}_{slug}")
        runs, series, prem, stats = {}, {}, {}, {}
        for index in indices_of(p):
            d = data[index]
            f = d["logs"][slug]
            q = replace(p, index=index)
            used = {f"{index}|{r.date}|{r.expiry}|{r.strike:g}" for r in f[f.traded == 1].itertuples()}
            prem.update({k: v for k, v in d["prem"].items() if k in used})
            days = {f"{index}|{x}" for x in f[f.traded == 1].date}
            series.update({k: v for k, v in d["series"].items() if k in days})
            s = S.stats(f, p.capital)
            stats[index] = {**s, "fit": float(f[f.date < SPLIT].net.sum()), "verify": float(f[f.date >= SPLIT].net.sum()), "first": f.date.min(), "last": f.date.max()}
            runs[index] = {"rows": rows_of(f), "chips": chips_of(p, index), "sub": R.sub_of(label, q), "inv": R.inv_of(q), "or": bool(p.tactical),
                           "doc": {"rules": rules_of(label, change, q, index, f.date.min(), f.date.max()), "notes": notes_of(q, index), "formulas": formulas_of(q, index),
                                   "data_info": info_of(q, index, f, d, data["NIFTY"]["validate"]), "data_range": [f.date.min(), f.date.max()], "split": SPLIT,
                                   "settings": [["Test (Kya badla)", label, 1], ["Capital", "Rs 30,00,000 (fixed, compounding nahi)", 1], ["Margin", "1/3 capital, 10% x lot x index har straddle"],
                                                ["Period", f"{f.date.min()} se {f.date.max()}", 1], ["Option price", "model (asli 1-min index + Black-76)"], ["Kharcha", "Rs 20/order, STT, exchange, stamp, GST"]],
                                   "data_note": ("BSE" if index == "SENSEX" else "NSE") + " option bhavcopy + 1-min index"}}
        title = title_of(label)
        first = indices_of(p)[0]
        payload = {"title": title, "capital": p.capital, "deploy": p.deploy, "cols": COLS, "base": first, "variants": [[k, k] for k in runs], "runs": runs,
                   "series": series, "prem": prem, "generated": datetime.now().strftime("%d %b %Y %H:%M")}
        blob = json.dumps(payload, separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else _clean(o)).replace("</", "<\\/")
        (out / "report.html").write_text(tpl.replace("__TITLE__", title).replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")
        for index in runs:
            data[index]["logs"][slug].drop(columns=["events"]).to_csv(out / f"days_{index}.csv", index=False)
        (out / "summary.json").write_text(json.dumps({"label": label, "title": title, "stats": stats}, indent=1, default=float), encoding="utf-8")
        made.append((out.name, label, stats))
        print(f"{out.name}: " + " | ".join(f"{k} net {v['net']:,.0f}" for k, v in stats.items()), flush=True)
    return made


def main() -> int:
    what = sys.argv[1]
    status_file = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    CACHE.mkdir(parents=True, exist_ok=True)

    def say(msg: str):
        print(msg, flush=True)
        if status_file:
            status_file.write_text(f"Straddle 26 report: {msg}\n", encoding="utf-8")

    try:
        if what == "sim":
            data = {}
            for index in ("NIFTY", "SENSEX", "BANKNIFTY"):
                data[index] = sim_index(index, say)
                send(f"Straddle 26 report: {index} ke saare setup ki simulation poori.")
            pickle.dump(data, open(CACHE / "runs.pkl", "wb"))
            lines = verify(data)
            print("\n".join(lines))
            bad = [x for x in lines if x.startswith("DIFF")]
            send(f"Straddle 26 report: jaanch (3 Nov 2026 tak) report 10 se: {len(lines) - len(bad)} / {len(lines)} test bilkul match.")
        else:
            data = pickle.load(open(CACHE / "runs.pkl", "rb"))
            made = build(data)
            print(len(made), "reports")
    except Exception:
        import traceback
        err = traceback.format_exc()
        print(err, flush=True)
        send(f"@albert_max Straddle 26 report run me error:\n{err[-1500:]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

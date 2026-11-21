"""Report 10 ('Straddle + Risk Layer (2021 - 2026 retest)') ke rules MCX Crude oil, Gold, Silver, Natural gas pe, wahi period (1 Nov 2021 - 3 Nov 2026).

MCX ke options ka koi data nahi hai, isliye option ka price model: asli 1-min MCX futures + Black-76, IV = pichhle 20 session ki realized vol x 1.10.
Engine = backtest.straddle.Sim (same as report 10); MCX time / kharcha / expiry: backtest/straddle_mcx.py.

    python tools/run_mcx_straddle.py
    python tools/run_mcx_straddle.py --no-telegram
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest import straddle as S  # noqa: E402
from backtest import straddle_mcx as M  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402
from backtest.straddle_report import check_row, write_html  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
NAME = "mcx_straddle_risk_2021_2026"
TITLE = "MCX Straddle + Risk Layer (2021 - 2026): Crude oil, Gold, Silver, Natural gas"
START, END, SPLIT = "2021-11-01", "2026-11-03", "2024-11-01"
ROOTS = ["CRUDEOIL", "GOLD", "SILVER", "NATURALGAS"]
IV_MULT = 1.10

P0 = S.Params()
CORE = S.with_params(P0, adj_pts=None, tactical=False, scalp=False, leg_sl=None, day_sl=None, trail=None, dd_steps=(), pause=False)
BASE = S.with_params(CORE, leg_sl=P0.leg_sl, day_sl=P0.day_sl, trail=P0.trail, entry_t=M.ENTRY_T)      # report 10 main test, MCX entry 9:20


def W(**kw) -> S.Params:
    return S.with_params(BASE, **kw)


# (key, label, params, market variant)  market variant: None = base market, ("iv", x) = other IV factor, ("mini",) = mini lot
CHECKS = [("Risk layer ka ek-ek hissa band", None, None),
          ("core", "Sirf straddle (risk layer poori band)", W(leg_sl=None, day_sl=None, trail=None)),
          ("nosl", "Leg SL band", W(leg_sl=None)),
          ("noday", "Din ka loss limit band", W(day_sl=None)),
          ("notrail", "Trailing band", W(trail=None)),
          ("Risk layer ke numbers", None, None),
          ("sl50", "Leg SL 50%", W(leg_sl=0.5)),
          ("sl100", "Leg SL 100%", W(leg_sl=1.0)),
          ("d1", "Din ka loss limit 1%", W(day_sl=0.01)),
          ("d2", "Din ka loss limit 2%", W(day_sl=0.02)),
          ("tr30", "Trailing 30% pe", W(trail=0.3)),
          ("tr70", "Trailing 70% pe", W(trail=0.7)),
          ("c1", "Stop ka confirm nahi (1 minute me touch pe)", W(confirm=1)),
          ("Entry aur slippage", None, None),
          ("e0", "Entry 9:00 pe (open)", W(entry_t=0)),
          ("s0", "Slippage 0", W(slip_opt=0.0, slip_fut=0.0)),
          ("s2", "Slippage zyada: option 1%", W(slip_opt=0.01, slip_fut=0.0005)),
          ("Option ka price (model) ka asar: IV = realized vol x ...", None, None),
          ("iv09", "IV = realized vol x 0.90", ("iv", 0.9)),
          ("iv10", "IV = realized vol x 1.00 (fair price)", ("iv", 1.0)),
          ("iv12", "IV = realized vol x 1.20", ("iv", 1.2)),
          ("Mini lot (ek lot chhota)", None, None),
          ("mini", "Mini contract ka lot (Gold 100 g, Silver 5 kg, Crude 10 bbl, NatGas 250 mmBtu)", ("mini",))]


def _hm(t: int) -> str:
    m = 540 + int(t)
    return f"{m // 60}:{m % 60:02d}"


def candles(bars: np.ndarray, step: int = 15) -> list:
    n = len(bars)
    return [[j, round(float(bars[j, 0]), 2), round(float(bars[j:j + step, 1].max()), 2), round(float(bars[j:j + step, 2].min()), 2), round(float(bars[min(j + step, n) - 1, 3]), 2)]
            for j in range(0, n, step)]


def prem_series(d: M.MDay, expiry: str, strike: float, step: int = 15) -> list:
    ce, pe = d.contract(expiry, strike, "CE")[0], d.contract(expiry, strike, "PE")[0]
    return [[int(t), round(float(ce[t] + pe[t]), 2)] for t in range(0, M.EXIT_T + 1, step)]


def fix_text(f: pd.DataFrame) -> pd.DataFrame:
    """Engine strings are NSE times (3:15); this report squares off at 23:15."""
    f = f.copy()
    f["exit"] = f.exit.str.replace("Expiry din 3:15", "Expiry din 11:15 pm").str.replace("3:15 square off", "11:15 pm square off")
    f["events"] = f.events.map(lambda ev: [[t, w.replace("3:15 square off", "11:15 pm square off"), lay] for t, w, lay in ev])
    return f


def run_root(root: str, say, status) -> dict:
    """Every variant of one commodity through the same days. Returns {key: log} + chart series of the main run."""
    t0 = time.time()
    mk = M.Market(root, START, END, iv_mult=IV_MULT)
    mk_iv = {x: M.Market(root, START, END, iv_mult=x, base=mk) for x in (0.9, 1.0, 1.2)}
    mk_mini = M.Market(root, START, END, iv_mult=IV_MULT, lot=M.MINI[root][0], base=mk)
    groups = [(mk, {"main": BASE, **{k: p for k, lb, p in CHECKS if lb and isinstance(p, S.Params)}}),
              *[(m, {f"iv{int(round(x * 10)):02d}": BASE}) for x, m in mk_iv.items()], (mk_mini, {"mini": BASE})]
    M.patch_engine()
    logs, series, prem = {}, {}, {}
    for gi, (m, variants) in enumerate(groups):
        sims = {k: S.Sim(p) for k, p in variants.items()}
        days = m.days(START, END)
        for i, dstr in enumerate(days):
            d = m.day(dstr)
            if d is None:
                continue
            for k, s in sims.items():
                s.step(d)
                if k == "main" and gi == 0 and s.log[-1]["traded"]:
                    r = s.log[-1]
                    series[f"{root}|{dstr}"] = candles(d.bars)
                    if r["expiry"] in d.smiles:
                        prem[f"{root}|{dstr}|{r['expiry']}|{r['strike']:g}"] = prem_series(d, r["expiry"], float(r["strike"]))
            if i % 250 == 0:
                status(f"{root}: group {gi + 1}/{len(groups)}, din {i}/{len(days)}, {len(sims)} setup")
                print(f"{root} g{gi} {i}/{len(days)} {time.time() - t0:.0f}s", flush=True)
        for k, s in sims.items():
            logs[k] = fix_text(S.frame(s))
    for k, f in logs.items():
        gap = float((f[["pnl_core", "pnl_avg", "pnl_tac", "pnl_hedge"]].sum(axis=1) - f.gross).abs().max())
        assert gap < 1.0, (root, k, gap)
    return {"logs": logs, "series": series, "prem": prem, "mk": mk}


def combined(logs_by_root: dict, key: str) -> pd.DataFrame:
    parts = [logs_by_root[r][key][["date", "net", "traded"]] for r in ROOTS]
    x = pd.concat(parts).groupby("date", as_index=False).agg(net=("net", "sum"), traded=("traded", "max"))
    return x


# ------------------------------------------------------------------------------------------------ texts
def chips_of(root: str, p: S.Params) -> list:
    sp = M.SPEC[root]
    return [["Commodity", f"{sp['name']} (MCX, monthly option, nearest expiry)", 1], ["Entry", f"{_hm(p.entry_t)} pe ATM straddle SELL", 1], ["Exit", "11:15 pm square-off (intraday)"],
            ["Lot", f"{sp['lot_txt']} (qty {sp['qty']})"], ["Strike adjust / Averaging / Tactical / Delta", "sab band"],
            ["Leg SL", "band" if p.leg_sl is None else f"{p.leg_sl * 100:g}%"], ["Din ka loss limit", "band" if p.day_sl is None else f"{p.day_sl * 100:g}% capital"],
            ["Trailing", "band" if p.trail is None else f"{p.trail * 100:g}% ke baad"], ["Stop confirm", f"{p.confirm} lagatar 1-min close"],
            ["Slippage", f"option {p.slip_opt * 100:g}%", 1], ["Option price", f"model: Black-76, IV = realized vol (20 din) x {IV_MULT:g}", 1]]


def inv_of(root: str, p: S.Params) -> list:
    return [["Shuruaati capital", f"Rs {p.capital:,.0f} (fixed, compounding nahi)"], ["Margin me lagaya", f"{p.deploy * 100:.0f}% = Rs {p.capital * p.deploy:,.0f} (baaki buffer)"],
            ["Ek straddle ka margin", f"{p.margin_pct * 100:g}% x lot x futures price (SPAN ka andaza)"], ["Lots", "margin budget / ek straddle ka margin, neeche round, kam se kam 1 lot (Gold / Silver me 1 lot budget se bada ho sakta hai)"],
            ["Din ka loss limit", "band" if p.day_sl is None else f"{p.day_sl * 100:g}% = Rs {p.day_sl * p.capital:,.0f} (kill switch)"],
            ["Leg SL", "band" if p.leg_sl is None else f"premium {p.leg_sl * 100:g}% upar"], ["Slippage", f"option {p.slip_opt * 100:g}% har fill"]]


SETTINGS = [["Report", "report 10 (Straddle + Risk Layer 2021 - 2026 retest) ke rules MCX pe", 1], ["Commodity", "Crude oil, Gold, Silver, Natural gas (MCX)", 1],
            ["Period", "1 Nov 2021 se 3 Nov 2026 (report 10 wala, fit = Nov 2024 se pehle, verify = Nov 2024 se)", 1],
            ["Capital", "Rs 30,00,000 har commodity ke liye alag (fixed, compounding nahi)", 1], ["Margin", "1/3 capital (Rs 10 lakh), ek straddle ka margin = 10% x lot x futures price"],
            ["Option price", f"MODEL (MCX option data nahi): Black-76 + IV = pichhle 20 session ki realized vol x {IV_MULT:g}, smile / skew nahi", 1],
            ["Kharcha", "Rs 20/order, CTT 0.05% (sell), MCX exchange 0.0418% premium pe, stamp, GST"], ["Slippage", "option 0.5% har fill", 1]]

RULES = [
    "Ye report 10 ('Straddle + Risk Layer 2021 - 2026 retest') ke rules MCX ke 4 commodity pe chalane ka test hai: Crude oil, Gold, Silver, Natural gas. Har commodity alag test, "
    "alag Rs 30 lakh capital. Period wahi: 1 Nov 2021 se 3 Nov 2026 (fit = Nov 2024 se pehle, verify = Nov 2024 se). Rules ke numbers report 10 ke hi hain, kuch badla nahi (sirf MCX ke time aur kharche).",
    "Option ka data: MCX ke expired options ka koi data nahi hai. Isliye har option ka price MODEL se: asli 1-min MCX futures price + Black-76. IV (volatility) har din ek hi number: "
    f"pichhle 20 session ki realized vol x {IV_MULT:g} (smile / skew nahi). Matlab ye sirf 'rules ka test' hai, asli option prices pe nahi. IV ka factor 0.9 / 1.0 / 1.2 pe bhi 'Extra checks' me hai.",
    "Option monthly hai, har din sabse paas wali expiry (jis din expiry hai us din bhi). Underlying wo futures jo us option ke baad expire hota hai. Expiry ki dates approximate rule se (asli calendar available nahi).",
    "Paisa: capital Rs 30 lakh, usme se 1/3 (Rs 10 lakh) margin me. Lots = Rs 10 lakh / (10% x lot x futures price), neeche round, kam se kam 1 lot. Lot: Crude 100 bbl, Gold 1 kg, Silver 30 kg, Natural gas 1250 mmBtu. "
    "Gold aur Silver me 1 lot ka margin Rs 10 lakh se bada hota hai, phir bhi 1 lot lete hain. Profit dobara invest nahi hota (compounding nahi).",
    "Entry: har trading din 9:20 pe (MCX 9:00 pe khulta hai) ATM straddle SELL (CE aur PE, same strike). Koi din chhoda nahi jaata (pause band).",
    "Exit: 11:15 pm pe jo bhi khula hai sab band (intraday, raat ko kuch nahi). MCX 11:30 / 11:55 pm tak chalta hai, square-off 15 min pehle (report 10 jaisa).",
    "Leg SL: kisi leg ka premium entry se 75% upar ho jaye (2 lagatar 1-min close) to sirf wo leg band. Doosra leg 11:15 pm tak ya agle stop tak chalta hai.",
    "Din ka loss limit (kill switch): din ka P&L capital ke 1.5% (Rs 45,000) ke loss tak ya usse neeche 2 lagatar 1-min close rahe to sab band.",
    "Trailing: profit becha hua premium (Rs) ka 50% ho jaye to trailing chalu; uske baad profit apne sabse upar wale level ka aadha reh jaye (2 lagatar 1-min close) to sab band.",
    "Band layers (report 10 jaisa): strike adjustment, averaging, tactical buy, delta scalping, drawdown scaling, pause (gap / VIX / Budget).",
    "Kharcha: Rs 20 har order, CTT 0.05% (sirf bechne pe, premium pe), MCX exchange charge 0.0418% premium pe, SEBI, stamp 0.003% (kharidne pe), GST 18%. Slippage option 0.5% har fill pe.",
]
NOTES = [
    "SABSE ZARURI: result option ke asli prices pe nahi, model pe hai. Straddle becha hua premium (entry price) aur uska din ka chaal dono model se aate hain, aur seller ka edge seedha IV ke factor (1.10) pe tika hai. "
    "Extra checks me IV x 0.9 pe Crude me loss, x 1.0 pe bhi kam / ya thoda profit dikhta hai - yaani 'profit' ka bada hissa maani hui 'option premium > realized vol' wali baat se hai. "
    "Asli MCX option me spread, skew, IV ka din me badalna aur kam liquidity (khaas kar Gold / Silver ke door ke strike) alag asar daalenge.",
    "Option price me time decay trading-session ke hisaab se (252 session / saal) liya hai, calendar din se nahi, taaki ek session ki decay us session ke realized variance se match kare. IV isi basis pe hai.",
    "Expiry calendar approximate hai: Crude (futures 19 tarikh, option usse 2 working din pehle), Natural gas (futures ~25 tarikh, option 2 din pehle), Gold (option mahine ka aakhri working din), Silver (aakhri working din se 2 din pehle). "
    "Fyers ke abhi ke symbol master se milaya, par purane saalon ke asli dates nahi mile. Holiday ka dhyaan nahi (sirf Sat / Sun).",
    "Underlying futures: Fyers ka near / next month continuous series. Nearest option jis futures pe hai (Gold / Silver me aksar agla bi-monthly futures) wahi use hua. Roll ke aas-paas ek-do din ka futures contract galat ho sakta hai (calendar approximate).",
    "Gold aur Silver ka 1 lot bada hai (Gold 1 kg ~ Rs 1.3 - 1.7 crore, Silver 30 kg ~ Rs 50 lakh - 85 lakh), isliye margin budget (Rs 10 lakh) se zyada lagta hai aur 1 lot ka loss bhi bada. Mini lot ka alag check 'Extra checks' me hai.",
    "Natural gas me kai baar tez gap aur bada roll spread hota hai, isliye 'sabse bura din' din ke loss limit (Rs 45,000) se kaafi bada ho sakta hai - stop 1-min close pe check hota hai, gap me fill door ho sakta hai.",
    "Kharche me CTT 0.05% (options sell) poore period ke liye flat maana; sahi rate saal ke hisaab se badla ho sakta hai (Budget). Trading expert se confirm karna baaki hai.",
    "MCX ka session 9:00 - 23:30 (garmi me US daylight saving ke saath 23:55). Hum 23:15 pe square-off karte hain, isliye dono me same. Aadhe din ke / Muhurat session wale din (bars kam) chhode gaye hain.",
    "Ye rules report 10 (NIFTY) ke result dekh ke chune gaye the; MCX pe naya (unseen) data hai. Par yahan bhi har commodity me alag se kuch tune nahi kiya (koi number badla nahi).",
    "Pause / drawdown scaling band hai (report 10 ke main test jaisa), isliye koi din skip nahi hota (sirf data / contract na mile to).",
]
FORMULAS = [
    ["Straddle premium", "CE price + PE price (same strike); ATM strike = round(futures / step) x step  (step ~ price ka 0.3% - 1.3%, nice number)"],
    ["Lots", "max(1, floor(capital x 1/3 / (10% x lot x futures price)))"],
    ["Option price (Black-76)", "CALL = e^(-rT) x [F x N(d1) - K x N(d2)], PUT = e^(-rT) x [K x N(-d2) - F x N(-d1)], d1 = (ln(F/K) + IV^2 x T / 2) / (IV x sqrt(T)), d2 = d1 - IV x sqrt(T), r = 6%, F = us option ka underlying futures (1-min)"],
    ["T (time to expiry)", "(bache hue poore session + aaj ke session ka bacha hissa) / 252;  aaj ka bacha hissa = (870 - t) / 870, t = 9:00 se minute"],
    ["IV", "sqrt(average(pichhle 20 session ka variance) x 252) x 1.10;  session variance = (5-min log return)^2 ka sum + raat ka gap^2 (roll din pe gap nahi)"],
    ["Leg SL", "leg ka price >= entry price x 1.75 (2 lagatar 1-min close)"],
    ["Kill switch", "din ka P&L <= -1.5% x capital (2 lagatar 1-min close)"],
    ["Trailing", "cycle profit >= 50% x becha hua premium (Rs) ke baad: profit <= 50% x peak profit"],
    ["Fill price", "buy = price x (1 + slippage), sell = price x (1 - slippage)"],
    ["Gross P&L", "sum(side x (exit fill - entry fill) x lots x lot) dono leg ka   (side: bechne -1)"],
    ["Kharcha", "Rs 20 x orders + CTT (sell) 0.05% + exchange 0.0418% + SEBI 0.0001% + stamp 0.003% (buy) + GST 18% x (brokerage + exchange + SEBI)"],
    ["Net P&L", "Gross P&L - kharcha"],
    ["Return %", "net / Rs 30,00,000 x 100 (compounding nahi)"],
    ["Win rate", "net > 0 wale trade din / kul trade din x 100"],
    ["Profit factor", "jeete dino ka kul profit / hare dino ka kul loss"],
    ["Max drawdown", "roz ke kul P&L ki curve ka sabse bada gir (peak se neeche)"],
]


def main_run(args, status) -> int:
    t0 = time.time()
    out = result_dir(NAME)
    say = (lambda m: None) if args.no_telegram else send
    say(f"Run shuru: {TITLE}. 1 Nov 2021 - 3 Nov 2026, har commodity alag, {len(CHECKS) + 3} setup x 4. Folder {out.name}.")
    res = {}
    for r in ROOTS:
        status(f"{r} simulation (futures data + model option)")
        res[r] = run_root(r, say, status)
        s = S.stats(res[r]["logs"]["main"], BASE.capital)
        say(f"{M.SPEC[r]['name']} base done: {s['days']} din, net Rs {s['net']:,.0f}, PF {s['profit_factor'] or 0:.2f}, max DD Rs {s['max_dd']:,.0f} ({time.time() - t0:.0f}s)")
    logs = {r: res[r]["logs"] for r in ROOTS}
    status("Report ban raha hai")

    view = {r: (f"{M.SPEC[r]['name']}: straddle + risk layer", logs[r]["main"]) for r in ROOTS}
    runs = {k: v[1] for k, v in view.items()}
    labels = {k: v[0] for k, v in view.items()}
    series, prem = {}, {}
    for r in ROOTS:
        series.update(res[r]["series"])
        prem.update(res[r]["prem"])
    cap = BASE.capital
    checks = [["Sab 4 commodity milake (alag-alag Rs 30 lakh, P&L jod ke)"], check_row("Base: straddle + risk layer", combined(logs, "main"), cap, SPLIT)]
    for key, lb, p in CHECKS:
        if lb is not None:
            checks.append(check_row(lb, combined(logs, key if not key.startswith("iv") else key), cap, SPLIT))
    for r in ROOTS:
        checks.append([f"{M.SPEC[r]['name']} (lot {M.SPEC[r]['lot_txt']}): base aur extra checks"])
        checks.append(check_row("Base: straddle + risk layer", logs[r]["main"], cap, SPLIT))
        for key, lb, p in CHECKS:
            if lb is not None:
                lbl = f"{lb} ({M.MINI[r][1]})" if key == "mini" else lb
                checks.append(check_row(lbl, logs[r][key], cap, SPLIT))
    chips = {r: chips_of(r, BASE) for r in ROOTS}
    subs = {r: f"{labels[r]} | MCX {M.SPEC[r]['name']} monthly ATM straddle SELL 9:20 pe, 11:15 pm pe square-off | option price model (futures + Black-76)" for r in ROOTS}
    invs = {r: inv_of(r, BASE) for r in ROOTS}
    lots = {r: sorted(set(logs[r]["main"][logs[r]["main"].traded == 1].lots.astype(int))) for r in ROOTS}
    data_info = [["Futures 1-minute", "MCX CRUDEOIL / GOLD / SILVER / NATURALGAS near + next month continuous (Fyers), Sep 2021 se Nov 2026 (Algo_Historical_Data/data/bars)"],
                 ["Option data", "NAHI hai (MCX ke expired option ka koi data nahi) - saare option price model se"],
                 *[[f"{M.SPEC[r]['name']} din", f"{int(logs[r]['main'].traded.sum())} trade din ({logs[r]['main'].date.min()} se {logs[r]['main'].date.max()}); lots {min(lots[r])} - {max(lots[r])}"] for r in ROOTS]]
    write_html(out / "report.html", runs, labels, chips, subs, invs, "CRUDEOIL", cap, BASE.deploy, RULES, NOTES, checks, FORMULAS, data_info, series, prem, [START, END],
               "MCX futures 1-min (Fyers); option price = model (Black-76, IV = realized vol x 1.10), asli option data nahi", SETTINGS, SPLIT, "option 0.5% har fill", title=TITLE,
               check_hint="Base (straddle + risk layer) se ek baar me sirf ek cheez badli. Pehle 4 commodity ka jod, phir har commodity alag. Net = poora period; Fit / Verify = do hisse. "
                          "IV wale rows dikhate hain ki result model ke maane hue premium pe kitna tika hai.",
               sub="MCX Crude oil / Gold / Silver / Natural gas &middot; report 10 ke rules")
    # patch the page for MCX (times, note, footer)
    h = (out / "report.html").read_text(encoding="utf-8")
    h = h.replace('"data_range"', '"t0":540,"note":"Chart asli 1-min MCX futures ka hai. Option ka price model (Black-76, IV = realized vol x 1.10) hai - asli MCX option data nahi hai, isliye entry / exit ke prices bhi model ke hain.",'
                  '"foot":"Purane result se aage ki guarantee nahi hoti. MCX ke option ka asli data nahi hai: option price model hai (futures + Black-76), isliye result IV ke maane hue factor pe tika hai (Rules / Notes dekho).","data_range"', 1)
    (out / "report.html").write_text(h, encoding="utf-8")
    for r in ROOTS:
        for k, f in logs[r].items():
            f.drop(columns=["events"]).to_csv(out / f"days_{r}_{k}.csv", index=False)

    lines = []
    for r in ROOTS:
        for k in ["main"] + [c[0] for c in CHECKS if c[1]]:
            s = S.stats(logs[r][k], cap)
            lines.append(f"{r} {k}: {s['days']} din, win {s['win_pct']:.0f}%, net Rs {s['net']:,.0f}, PF {s['profit_factor'] or 0:.2f}, max DD Rs {s['max_dd']:,.0f}, worst Rs {s['worst']:,.0f}")
    (out / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    if not args.no_telegram:
        msg = [f"{TITLE} DONE ({out.name}) {TAG_TRADING}", "Report 10 ke rules, MCX futures + model option (IV = realized vol x 1.10), Rs 30L har commodity, 1 Nov 2021 - 3 Nov 2026:"]
        for r in ROOTS:
            s = S.stats(logs[r]["main"], cap)
            m = logs[r]["main"]
            msg.append(f"{M.SPEC[r]['name']}: {s['days']} din, win {s['win_pct']:.0f}%, net Rs {s['net']:,.0f} ({s['ret_pct']:.0f}%), PF {s['profit_factor'] or 0:.2f}, max DD Rs {s['max_dd']:,.0f}, worst day Rs {s['worst']:,.0f}, "
                       f"fit Rs {m[m.date < SPLIT].net.sum():,.0f} / verify Rs {m[m.date >= SPLIT].net.sum():,.0f}")
        tot = S.stats(combined(logs, "main"), cap)
        msg.append(f"4 milake: net Rs {tot['net']:,.0f}, PF {tot['profit_factor'] or 0:.2f}, max DD Rs {tot['max_dd']:,.0f}")
        iv = {r: S.stats(logs[r]["iv10"], cap)["net"] for r in ROOTS}
        msg.append("IV x1.0 (fair price) pe net: " + ", ".join(f"{M.SPEC[r]['name']} Rs {v:,.0f}" for r, v in iv.items()))
        msg.append("Dhyan: option price model hai (asli MCX option data nahi), result IV ke factor pe tika hai. Details report ke Rules / Notes me.")
        send("\n".join(msg))
        send_document(out / "report.html", f"{TITLE} ({out.name})")
    print(f"done {time.time() - t0:.0f}s -> {out}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--status-file")
    args = ap.parse_args()
    status = (lambda s: Path(args.status_file).write_text(s + "\n", encoding="utf-8")) if args.status_file else (lambda s: None)
    try:
        return main_run(args, status)
    except Exception:
        err = traceback.format_exc()
        print(err, flush=True)
        if not args.no_telegram:
            send(f"{TITLE} run me error {TAG_TECH}:\n{err[-1500:]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

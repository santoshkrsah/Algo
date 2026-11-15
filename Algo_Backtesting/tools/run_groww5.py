"""5 intraday strategies from the Groww video (Aseem Singhal) - backtest + report.

    python tools/run_groww5.py              # candidates (cached) -> trades -> numbers -> results/NN_groww5_intraday/report.html
    python tools/run_groww5.py --fresh      # recompute the candidates (after a data update / rule change)
    python tools/run_groww5.py --notify     # after the website upload: finish message + HTML report to Telegram
Rules: strategies/GROWW5_INTRADAY_RULES.md. Data: fast store (stocks 1m), data/bars (NIFTY futures 1m), data/daily (zones).
"""
import argparse
import json
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest import fast  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.groww5 import data, evaluate, report, run  # noqa: E402
from backtest.groww5.config import ASSUMPTIONS, CONFIG, NAME, STRATS, TITLE_SUFFIX, VIDEO  # noqa: E402

SITE = json.loads((Path(__file__).resolve().parents[1] / "site" / "site.json").read_text(encoding="utf-8"))["url"]
M = CONFIG["money"]

RULES = {
    "common": [
        "Market: NSE intraday (MIS). Stocks = AMFI Jun-2026 ke 100 large cap (video ke example Tata Motors, Infosys, ICICI Bank sab large cap). NIFTY wali strategy NIFTY near-month futures me.",
        "Har strategy ka alag Rs 10 lakh. Compounding NAHI (profit wapas nahi lagaya). Risk = 1% = Rs 10,000 har trade.",
        "Stock qty = 10,000 / (entry - SL), lekin position Rs 5 lakh se zyada nahi. NIFTY lots = 10,000 / (SL points x lot size), round, kam se kam 1, zyada se zyada 6.",
        "Ek strategy me ek saath max 5 trade, ek stock me ek hi. 15:15 pe jo khula hai wo band.",
        "Entry: signal wali candle band hone ke baad agle 1-min candle ke open pe. SL / target 1-min candle se check: chhua to usi bhaav pe, gap se paar gaya to open pe. Ek hi minute me dono -> SL maana.",
        "Kharche: brokerage, STT, NSE, SEBI, stamp, GST + slippage (stocks 0.03% har side, NIFTY 2 point har side). Stress test me slippage 2x.",
        "Video me SL, target, qty, kitne trade - kuch nahi bataya. Jo number video me nahi the wo hamare assumption hain (neeche alag likhe).",
    ],
    "scalp": [
        "1-min chart. Video: 3 red candle lagatar (strong move) + aaj ka low toote -> SELL. Confirmation: Parabolic SAR price ke upar.",
        "BUY ulta (hamara assumption): 3 green candle lagatar + candle aaj ke ab tak ke high ke upar band + PSAR price ke neeche.",
        "'Low toota' = candle ka close aaj ke pichhle low ke neeche (09:15 se pichhli candle tak). PSAR = 0.02 / 0.02 / 0.2 (TradingView default).",
        "Entry agle 1-min open. SL = un 3 candles ka high (short) / low (long). Target nahi. 5 candle (5 min) baad exit - video: max 5 candle hold.",
        "Signal sirf 09:30 - 15:00. Ek stock pe din me max 2 trade, strategy me din me max 10.",
    ],
    "momo": [
        "NIFTY near-month futures, 5-min candle (video: NIFTY 50, 5-min, staircase / Dow theory).",
        "Swing high / low = 5 candle fractal (beech wali candle ka high/low dono taraf 2-2 candles se upar/neeche), 2 candle baad pakka hota hai.",
        "Staircase upar = aakhri 2 swing high badhte (HH) + aakhri 2 swing low badhte (HL). Pullback ke baad naya higher low pakka hote hi BUY.",
        "SL = wo swing low. Har naya higher low SL ko upar le jaata hai (trailing). Target nahi. 15:15 pe band.",
        "SHORT ulta (lower high + lower low, naya lower high pe SELL) - video me sirf buy tha, isliye long aur short alag dikhaye. Signal 09:30 - 14:30, din me max 3 entry.",
    ],
    "brk": [
        "Zone daily chart se (video: bada timeframe). Daily swing high / low (5 din left + 5 din right), pichhle 250 din, 1.5% ke andar wale ek zone. Touch = zone me kitne swing.",
        "Video: zone 5-6 baar touch ho to breakout ke chance. Isliye sirf 5 ya zyada touch wale zone.",
        "Aaj ke liye: kal ke close ke upar sabse paas wala zone = resistance, neeche wala = support.",
        "15-min candle resistance zone ke UPAR band -> BUY; support zone ke NEECHE band -> SELL (breakdown). Entry agle candle ke open pe.",
        "SL = zone ka doosra kinara. Target = 2 x risk (2R). Warna 15:15 pe band. Signal 09:30 - 14:45, ek stock-din me ek trade.",
        "Video ka doosra tarika (toot ke wapas level pe aaye tab entry = pullback) Variants tab me alag test kiya.",
    ],
    "rev": [
        "Wahi daily zone. Video: zone 2-3 baar touch ho to reversal ke chance. Isliye sirf 2-3 touch wale zone (4 touch skip).",
        "Resistance: 15-min candle zone ko chhuye (high zone ke andar/upar) aur zone ke neeche RED band ho -> SELL. Support: zone chhuye aur upar GREEN band -> BUY.",
        "Zone aaj pehle hi toot gaya (candle zone ke paar band) to us din reversal nahi.",
        "SL = zone ka doosra kinara ya aaj ka high/low (jo door ho). Target = 2R. Warna 15:15 pe band. Signal 09:30 - 14:45.",
    ],
    "news": [
        "News ka data nahi -> 'news stock' = gap 2% ya zyada (open vs kal ka close). Video ka example: ICICI Bank news pe bada gap up, pehli candle me selling, poore din gira.",
        "Gap UP + pehli 5-min candle (09:15-09:20) RED -> pehli candle ke low ke neeche jaate hi SELL (1-min, 11:00 tak).",
        "Gap DOWN + pehli candle GREEN -> high ke upar BUY (ulta, hamara assumption - video me nahi tha).",
        "SL = pehli candle ka doosra kinara (short me high). Target nahi, 15:15 pe band (video: poore din girta raha). Din me max 5 trade, sabse bada gap pehle.",
    ],
}

FORMULAS = [
    ["Gap %", "(aaj ka open / kal ka close - 1) x 100"],
    ["Aaj ka low / high ab tak", "09:15 se pichhli 1-min candle tak ka sabse kam low / sabse zyada high"],
    ["Parabolic SAR", "SAR = SAR_pichhla + AF x (EP - SAR_pichhla); AF 0.02 se shuru, har naye EP pe +0.02, max 0.2; price SAR ko kaate to palat (SAR = EP)"],
    ["Swing high (daily, k=5 / 5-min, k=2)", "candle ka high = uske k left + k right candles ka sabse bada high (low ke liye ulta); k candle baad pakka"],
    ["Zone", "swing prices sort karke: zone = ek ke baad ek price jab tak <= pehla x 1.015; zone low/high = min/max (kam se kam center +-0.25%); touch = count"],
    ["Entry fill", "agle 1-min candle ka open; stop-entry (news) = trigger, gap ho to us minute ka open"],
    ["SL / target fill", "1-min low <= SL (long) -> exit = min(open, SL); high >= target -> exit = max(open, target); short ulta; dono ek minute me -> SL"],
    ["Target (zones)", "entry + side x 2 x |entry - SL|"],
    ["Stock qty", "floor( min( 10,000 / |entry - SL| , 5,00,000 / entry ) )"],
    ["NIFTY lots", "min( 6, max(1, round( 10,000 / (|entry - SL| x lot size) )) ); lot size 75 / 50 / 25 / 75 / 65 date se"],
    ["Slippage", "stocks: entry x (1 + side x 0.03%), exit x (1 - side x 0.03%); NIFTY: +-2 point har fill; stress = 2x"],
    ["Gross P&L", "side x (exit fill - entry fill) x qty"],
    ["Charges (stocks)", "brokerage min(20, 0.03%) har order + STT 0.025% sell + NSE 0.00297% + SEBI 0.0001% (buy+sell) + stamp 0.003% buy + GST 18% x (brokerage + NSE + SEBI)"],
    ["Charges (NIFTY fut)", "Rs 20 har order + STT sell (0.01% / 0.0125% Apr-23 / 0.02% Oct-24 / 0.05% Apr-26) + NSE 0.00173% + SEBI + stamp 0.002% buy + GST 18%"],
    ["Net P&L", "gross - charges"],
    ["R multiple", "net / (|entry - SL| x qty)"],
    ["Win rate", "net > 0 wale trade / kul trade x 100"],
    ["Profit factor", "sab profit ka jod / sab loss ka jod"],
    ["Return %", "net / Rs 10 lakh x 100 (har strategy; 'Sab ek saath' me Rs 50 lakh)"],
    ["Max drawdown", "roz ke kul net ke peak se sabse bada gir (Rs)"],
    ["Sharpe", "roz ke net ka average / std x sqrt(248)"],
    ["Peak money deployed", "ek waqt pe khuli saari positions ki value ka sabse bada jod"],
]


def settings():
    common = [["Universe", "AMFI Jun-2026 large cap 100 (+ NIFTY fut)", 1], ["Capital", "Rs 10 lakh har strategy, compounding nahi", 1],
              ["Risk / trade", "1% = Rs 10,000", 1], ["Max / position", "Rs 5 lakh (stock), 6 lot (NIFTY)", 0], ["Max open", "5 (NIFTY: 1)", 0],
              ["Slippage", "0.03% / 2 pt har side (stress 2x)", 1], ["Charges", "brokerage + STT + NSE + SEBI + stamp + GST", 1],
              ["Entry", "agle 1-min open", 0], ["Square-off", "15:15", 0], ["Bias", "survivorship (aaj ki list)", 0]]
    per = {
        "scalp": [["Strategy", "1) Scalping 1-min", 1], ["Setup", "3 red/green + day low/high break", 1], ["Filter", "PSAR 0.02/0.2", 0],
                  ["SL", "3 candle ka high/low", 0], ["Exit", "5 min baad", 1], ["Side", "long + short", 0], ["Limit", "2/stock, 10/din", 0]],
        "momo": [["Strategy", "2) Momentum NIFTY 5-min", 1], ["Instrument", "NIFTY near-month fut", 1], ["Setup", "HH+HL, naya HL pe buy", 1],
                 ["SL", "swing low, trailing", 0], ["Side", "long + short", 0], ["Limit", "3/din", 0], ["Period", "Feb 2018 se", 0]],
        "brk": [["Strategy", "3) Breakout 15-min", 1], ["Zone", "daily swing 5+5, 250 din, 1.5%", 0], ["Touch", ">= 5", 1],
                ["Entry", "15-min close zone ke paar (direct)", 1], ["SL / target", "zone ka doosra kinara / 2R", 0]],
        "rev": [["Strategy", "4) Reversal 15-min", 1], ["Zone", "daily swing 5+5, 250 din, 1.5%", 0], ["Touch", "2-3", 1],
                ["Entry", "zone chhu ke ulti rang ki 15-min candle", 1], ["SL / target", "zone kinara ya din high/low / 2R", 0]],
        "news": [["Strategy", "5) News / gap 5-min", 1], ["News proxy", "gap >= 2%", 1], ["Setup", "gap up + red 1st candle -> short", 1],
                 ["Trigger", "1st candle low/high toote (11:00 tak)", 0], ["SL / exit", "1st candle high/low / 15:15", 0], ["Limit", "5/din", 0]],
    }
    return {"common": common, **per}


def peak_deployed(t: pd.DataFrame) -> float:
    best = 0.0
    for _, g in t.groupby("date"):
        ev = sorted([(e, v) for e, v in zip(g["e"], g["value"])] + [(x, -v) for x, v in zip(g["x"], g["value"])], key=lambda z: (z[0], z[1]))
        cur = 0.0
        for _, v in ev:
            cur += v
            best = max(best, cur)
    return best


def invest_card(k, t):
    if k == "momo":
        qty = f"lots = round(10,000 / (SL points x lot size)), 1 - 6 lot; average {t['lots'].mean():.1f} lot"
        mx = "6 lot (margin ~Rs 1.5 lakh / lot)"
        opn = "1 (ek hi NIFTY position)"
    else:
        qty = "floor(min(10,000 / SL door, 5 lakh / entry))"
        mx = "Rs 5 lakh value (MIS ~Rs 1 lakh margin)"
        opn = "5"
    return [["Capital", "Rs 10 lakh (fixed, compounding nahi)"], ["Risk per trade", "1% = Rs 10,000 (SL tak)"], ["Qty rule", qty], ["Max per position", mx],
            ["Max open positions", opn], ["Average position", f"Rs {t['value'].mean():,.0f}" if len(t) else "n/a"],
            ["Sabse badi position", f"Rs {t['value'].max():,.0f}" if len(t) else "n/a"], ["Peak money deployed (ek saath)", f"Rs {peak_deployed(t):,.0f}"],
            ["Average risk (SL tak)", f"Rs {t['risk_rs'].mean():,.0f}" if len(t) else "n/a"]]


def verdict_text(k, E, B):
    p = E[k]["periods"][E[k]["full_key"]]
    g, c = p["gross_per_trade"] or 0, p["cost_per_trade"] or 0
    txt = f"Ek trade ka average gross (kharche se pehle) Rs {g:,.0f}, kharcha (slippage + charges) Rs {c:,.0f}."
    if k == "news":
        t = B[("news", "base", 1.0)]
        s = t[t["side"] < 0]
        parts = []
        for name, a, b in evaluate.periods("news")[:3]:
            x = s[(s["date"] >= a) & (s["date"] <= b)]
            parts.append(f"{name.split(' ')[0]} Rs {x['net'].sum():,.0f}")
        txt += f" Sirf video wala hissa (gap UP + red candle -> SHORT): {len(s)} trade, net Rs {s['net'].sum():,.0f} ({', '.join(parts)}). Ye data dekh ke nikala hai, isliye sirf observation - pass nahi."
    return txt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args()
    out = result_dir(NAME)
    if a.notify:
        from backtest.notify import send, send_document
        s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        num = int(out.name.split("_", 1)[0])
        lines = [f"{v['num']}) {v['short']}: {v['trades']} trade, net Rs {v['net']:,.0f} ({v['ret_pct']:.1f}%), PF {v['pf']:.2f}, latest 2024-26 Rs {v['latest']:,.0f}, "
                 f"stress Rs {v['stress']:,.0f} -> {'PASS' if v['pass'] else 'FAIL'}" for v in s["strats"]]
        send(f"Backtest khatam: 5 Intraday Strategies (Groww video) - {TITLE_SUFFIX} (@ayushanand440)\n"
             f"Stocks Oct 2021 - 13 Nov 2026 (100 large cap, 1-min), NIFTY fut Feb 2018 se. Rs 10 lakh har strategy, 1% risk, compounding nahi, costs + 0.03% slippage.\n"
             + "\n".join(lines) + f"\n{s['n_pass']} / 5 pass. {s['n_variants']} variants bhi test kiye (sirf jaanch).\n{s['headline']}\n"
             f"Reports website: {SITE}\nYe report: {SITE}#report-{num}")
        send_document(out / "report.html", caption=f"5 Intraday Strategies (Groww) - {TITLE_SUFFIX} (report {num:02d})")
        return 0
    if a.fresh:
        (out / "candidates.pkl").unlink(missing_ok=True)
    C = run.collect(out)
    B = run.books(C)
    with open(out / "books.pkl", "wb") as f:
        pickle.dump(B, f)
    stock_days = fast.days(CONFIG["data"]["start"], CONFIG["data"]["end"])
    F = data.nifty()
    fut_days = [str(d) for d in F.days]
    E = evaluate.evaluate(B, stock_days, fut_days)
    for k in STRATS:
        E[k]["verdict_text"] = verdict_text(k, E, B)
        t = B[(k, "base", 1.0)]
        t.drop(columns=["info"]).assign(info=t["info"].map(json.dumps)).to_csv(out / f"trades_{k}.csv", index=False)
    syms = data.universe()
    cov = data.coverage(syms, F)
    cov.to_csv(out / "coverage.csv", index=False)
    n_var = sum(len(v) - 1 for v in run.VARIANTS.values())
    summ = []
    for k, (num, short, name) in STRATS.items():
        p, l = E[k]["periods"][E[k]["full_key"]], E[k]["periods"]["Latest / FINAL 2024 - 2026"]
        summ.append({"key": k, "num": num, "short": short, "trades": p["trades"], "net": p["net"], "ret_pct": p["ret_pct"], "pf": p["pf"] or 0, "win": p["win"],
                     "latest": l["net"], "latest_pf": l["pf"] or 0, "stress": E[k]["stress"][E[k]["full_key"]]["net"], "pass": E[k]["verdict"]["pass"]})
    best = max(summ, key=lambda x: x["net"])
    n_pass = sum(x["pass"] for x in summ)
    headline = (f"Sabse accha: {best['short']} (net Rs {best['net']:,.0f}), lekin 2023 validation me loss, isliye FAIL. Baaki 4 me kharche (slippage + charges) gross se zyada."
                if n_pass == 0 else f"{n_pass} strategy pass.")
    json.dump({"strats": summ, "n_pass": n_pass, "n_variants": n_var, "headline": headline, "details": {k: {kk: E[k][kk] for kk in ("periods", "stress", "verdict", "variants")} for k in E}},
              open(out / "summary.json", "w", encoding="utf-8"), indent=1, default=str)
    rules = {k: v for k, v in RULES.items()}
    invest = {k: invest_card(k, B[(k, "base", 1.0)]) for k in STRATS}
    p = report.write_html(out, E=E, B=B, F=F, stock_days=stock_days, fut_days=fut_days, settings=settings(), rules=rules, formulas=FORMULAS,
                          assumptions=ASSUMPTIONS, data_rows=cov.astype(str).values.tolist(), invest=invest, extra={"n_variants": n_var, "headline": headline})
    print(p, round(p.stat().st_size / 1e6, 1), "MB")
    for x in summ:
        print(x)
    print(headline)
    return 0


if __name__ == "__main__":
    sys.exit(main())

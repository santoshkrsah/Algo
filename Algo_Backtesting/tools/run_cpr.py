"""CPR + VWAP + Open-Low backtest: every setting of the rulebook, write results/NN_<name>/report.html and send it.

    python tools/run_cpr.py                                   # 2021-10-01 .. 2026-10-28, reads every day file once
    python tools/run_cpr.py --cached --no-telegram            # reuse prepared.pkl (rebuild the report only)
"""
import argparse
import itertools
import pickle
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backtest import cpr  # noqa: E402
from backtest.config import DATA, result_dir  # noqa: E402
from backtest.cpr_report import write_html  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
SLIPS = {"0%": 0.0, "0.1%": 0.001, "0.2%": 0.002}
GRID_SLIP = 0.001
MAIN = dict(side="BUY", tol=0.0005, cpr="pivot", near="tc", body=0.01, entry="A", exit="hold", max_trades=2)
GRID = dict(side=["BUY", "SELL"], tol=[0.0, 0.0005, 0.001], cpr=["pivot", "gap"], near=["tc", "inside"], body=[0.01, 0.015, None],
            entry=list(cpr.ENTRIES), exit=list(cpr.EXITS), max_trades=[1, 2])
MIN_IS_TRADES = 100

RULES = [
    "Samay: 1 Oct 2021 se 28 Oct 2026. Sirf F&O stocks (us din ki NSE list), sirf intraday. 1 minute data se 10 minute candle banayi (9:15 se shuru).",
    "Signal candle: din ki pehli 10 minute candle (9:15 se 9:25).",
    "Shart 1 - Open = Low: candle ka open aur low barabar (0.05% tak ka farak chalega). Candle green ho (close > open).",
    "Shart 2 - CPR upar chadh raha: aaj ka Pivot > kal ka Pivot. (CPR = kal ke High, Low, Close se.)",
    "Shart 3 - Open CPR ke paas: open, aaj ke TC (CPR ki upar wali line) se 0.5% ke andar. Bahut upar gap wala stock nahi.",
    "Shart 4 - Tail (neeche ki wick) body se chhoti.",
    "Shart 5 - Body bahut lambi nahi: body < 1% (price ka).",
    "Entry A: 9:25 wali candle ke open pe BUY (signal candle band hote hi). SL = signal candle ka low.",
    "Exit: 3:15 baje (15:15 candle ke open pe) square-off, ya SL lage to pehle. SL 1 minute candle pe check hota hai.",
    "Din me max 2 trade. Zyada stock mile to jiski tail sabse chhoti, wo pehle.",
    "Paisa: capital Rs 70,000. Har trade Rs 1,75,000 ka maal (35,000 margin x 5). Qty = 1,75,000 / buy price (point hata ke). Profit dobara nahi lagaya.",
    "Kharcha: har trade pe trade value ka 0.10%. Slippage 0%, 0.1% aur 0.2% (buy aur sell dono pe) - teeno alag chalaye.",
    "Upar wali 'main setting' hai. Video ki baaki baatein (VWAP pe entry B, dusri CPR shart, target, trailing, short side) "
    "'Ek cheez badli' table aur 'Saari settings' tab me alag-alag chala ke di hain.",
    "Rules chunne ke liye period: Oct 2021 - Dec 2024 (in-sample). Test ke liye alag period: Jan 2025 - Oct 2026 (out-of-sample).",
]
NOTES = [
    "Video me sirf 2-3 jeete hue chart the. Yahan har signal gina hai, haare hue bhi.",
    "Open = Low ki chhoot (0 / 0.05% / 0.1%), 'CPR ke paas' (TC se 0.5% / CPR ke andar ya 0.25% upar), body ki limit (1% / 1.5% / koi nahi) - "
    "ye numbers video me nahi the, hamne maane. Sab try kiye.",
    "CPR ki dusri shart (gap / no overlap) = aaj ka CPR ka neeche wala hissa, kal ke CPR ke upar wale hisse se upar. TC aur BC me jo bada wo upar wala.",
    "Entry B: 9:25 ke baad pehli 10 minute candle jiska low VWAP ko chhua aur close VWAP ke upar hua, aur jo 12:30 tak band ho gayi. "
    "Entry agli minute ke open pe. SL = us candle ka low, ya VWAP se 0.1% neeche (VWAP us candle ke close ke waqt ka).",
    "VWAP = (High+Low+Close)/3 x volume ka jod / volume ka jod, 9:15 se roz naya.",
    "Trailing: har 10 minute candle band hone pe SL upar khiskta hai (pichhli candle ka low, ya VWAP), agli minute se lagu. SL kabhi neeche nahi aata.",
    "Agar ek hi minute me SL aur target dono touch ho jaye to maana SL pehle laga (bura case). Gap se SL ke paar khule to us minute ke open pe exit.",
    "Agar entry ka price SL pe ya usse neeche ho (9:25 pe hi gir gaya), to trade nahi liya.",
    "Short side (Open = High, CPR neeche, VWAP ke neeche) video me is part me nahi samjhaya - alag variant ki tarah test kiya.",
    "Kal se 20% se zyada gap wale din chhod diye (data / split ka jump ho sakta hai).",
    "F&O list = NSE price band list me 'No Band' wale stocks, jo hamare data me hain. Hamari stock list aaj ke market cap se bani hai, "
    "isliye jo stock F&O se bahar ho ke chhote ho gaye wo purane saalon me kam hain (survivorship bias).",
    "Nifty / Bank Nifty weekly option ka purana minute data hamare paas nahi hai (expired contracts). Isliye index version me sirf Nifty 50 "
    "index ke points dikhaye hain, option premium nahi. Bank Nifty / Fin Nifty ka index data bhi nahi hai. Nifty me SL ka order nahi dekha: "
    "15:15 se pehle kabhi bhi SL chhua to SL maana.",
]
FORMULAS = [
    ["Pivot (P)", "P = (kal ka High + kal ka Low + kal ka Close) / 3"],
    ["BC", "BC = (kal ka High + kal ka Low) / 2"],
    ["TC", "TC = 2 x P - BC"],
    ["CPR upar / neeche", "Upar = max(TC, BC), Neeche = min(TC, BC)"],
    ["CPR chadh raha (main)", "Aaj ka P > kal ka P"],
    ["CPR chadh raha (gap)", "Aaj ka CPR Neeche > kal ka CPR Upar"],
    ["Signal candle", "9:15 se 9:24 ki 1 min candles: O = pehla open, H = sabse bada high, L = sabse chhota low, C = aakhri close"],
    ["Open = Low", "(O - L) / O <= 0.05%  aur  C > O"],
    ["Tail aur body", "Tail = O - L,  Body = C - O,  shart: Tail < Body"],
    ["Body limit", "Body / O < 1%"],
    ["Open CPR ke paas", "|O / TC - 1| <= 0.5%"],
    ["VWAP", "VWAP = jod((H+L+C)/3 x Volume) / jod(Volume), 9:15 se us minute tak"],
    ["Entry A", "E = 9:25 wali 1 min candle ka Open"],
    ["Entry B", "Pehli 10 min candle (12:30 tak band): Low <= VWAP aur Close > VWAP  ->  E = agli minute ka Open"],
    ["SL", "A: SL = L (signal candle low).  B: SL = us candle ka Low, ya VWAP x 0.999"],
    ["Risk (R)", "R = E - SL"],
    ["Target 1:2 / 1:3", "Target = E + 2 x R  /  E + 3 x R"],
    ["Trailing (10 min low)", "SL = max(SL, pichhli band 10 min candle ka Low)"],
    ["Trailing (VWAP)", "SL = max(SL, pichhli band 10 min candle ke close pe VWAP)"],
    ["VWAP close exit", "10 min candle ka Close < VWAP  ->  agli minute ke Open pe bech do"],
    ["SL laga?", "1 min candle ka Low <= SL  ->  Exit = min(us candle ka Open, SL)"],
    ["Target laga?", "1 min candle ka High >= Target  ->  Exit = max(Open, Target)"],
    ["3:15 square-off", "Exit = 15:15 wali 1 min candle ka Open"],
    ["Short side", "Sab ulta: Open = High, CPR neeche, SL = signal candle ka High, Close < VWAP"],
    ["Quantity", "Qty = 1,75,000 / E (neeche round)"],
    ["Slippage ke baad", "BUY: kharid = E x (1 + s), bech = Exit x (1 - s).  SELL: ulta.  s = 0, 0.001, 0.002"],
    ["Gross P&L", "BUY: Qty x (bech - kharid).  SELL: Qty x (pehle bech - baad me kharid)"],
    ["Kharcha", "Kharcha = 0.10% x Qty x E"],
    ["Net P&L", "Net = Gross - Kharcha"],
    ["Win rate", "Win % = (Net > 0 wale trade / kul trade) x 100"],
    ["Expectancy", "Expectancy = kul Net / kul trade (ek trade ka average)"],
    ["Average profit / loss", "Avg profit = jeete trades ka Net / jeete trade;  Avg loss = hare trades ka Net / hare trade"],
    ["Profit factor", "PF = jeete trades ka Net jod / hare trades ka loss jod (bina minus). 1 se upar = profit"],
    ["Max drawdown", "Peak = max(0, ab tak ka sabse uncha kul Net);  DD = kul Net - Peak;  Max DD = sabse bada minus DD"],
    ["Max drawdown %", "DD % = (kul Net - Peak) / (70,000 + Peak) x 100"],
    ["Return %", "Return = kul Net / 70,000 x 100"],
    ["Max adverse / favourable move", "MAE = entry ke baad exit tak sabse kharab price vs E (%), MFE = sabse accha price vs E (%)"],
    ["5% wala daawa", "Din ka best = (9:25 ke baad 15:15 se pehle ka sabse uncha High / E - 1) x 100.  % stocks jinka din ka best >= 5%"],
    ["In-sample / out-of-sample", "In-sample = 2025 se pehle ke trade, out-of-sample = 1 Jan 2025 se"],
]


def grid(cands, trades) -> pd.DataFrame:
    """Every combination of GRID at GRID_SLIP. Each filter is applied once, each trade subset is cut once."""
    parts = {k: g for k, g in trades.groupby(["side", "entry_type", "exit_type"], observed=True)}
    rows = []
    for side, tol, cp, near, body in itertools.product(GRID["side"], GRID["tol"], GRID["cpr"], GRID["near"], GRID["body"]):
        c = cands[cands.side.eq(side)]
        c = c[cpr.passes(c, tol, cp, near, body)][cpr.CAND_COLS]
        for entry, ex, mt in itertools.product(GRID["entry"], GRID["exit"], GRID["max_trades"]):
            t = parts.get((side, entry, ex), trades.iloc[0:0])
            p = dict(side=side, tol=tol, cpr=cp, near=near, body=body, entry=entry, exit=ex, max_trades=mt)
            rows.append({**p, **cpr.split_stats(cpr.pick(c, t, GRID_SLIP, mt))})
    return pd.DataFrame(rows)


CHECKS = [
    ("Main setting (jaisa Rules me)", {}),
    ("Entry B: VWAP pullback, SL = candle low", {"entry": "B_low"}),
    ("Entry B: VWAP pullback, SL = VWAP se 0.1% neeche", {"entry": "B_vwap"}),
    ("CPR shart: gap (aaj CPR neeche > kal CPR upar)", {"cpr": "gap"}),
    ("Open CPR ke andar ya 0.25% upar tak", {"near": "inside"}),
    ("Open = Low bilkul (0% chhoot)", {"tol": 0.0}),
    ("Open = Low 0.1% tak chhoot", {"tol": 0.001}),
    ("Body limit 1.5%", {"body": 0.015}),
    ("Body ki koi limit nahi", {"body": None}),
    ("Exit: target 1:2", {"exit": "rr2"}),
    ("Exit: target 1:3", {"exit": "rr3"}),
    ("Exit: trailing SL pichhli 10 min candle ka low", {"exit": "trail_candle"}),
    ("Exit: trailing SL VWAP", {"exit": "trail_vwap"}),
    ("Exit: 10 min candle VWAP ke neeche band = exit", {"exit": "vwap_close"}),
    ("Din me sirf 1 trade", {"max_trades": 1}),
    ("Short side (Open = High, CPR neeche)", {"side": "SELL"}),
]


def claim(cands, daily) -> dict:
    """His claim: the stocks that pass rarely move less than 5-6%. All passing stocks, not just the 2 traded."""
    out = []
    for side, label in (("BUY", "Main filter pass (BUY)"), ("SELL", "Short filter pass (SELL)")):
        c = cands[cands.side.eq(side)]
        c = c[cpr.passes(c, **{k: MAIN[k] for k in ("tol", "cpr", "near", "body")}) & c.day_best_pct.notna()]
        out.append([label, len(c), (c.day_best_pct >= 5).mean() * 100, (c.close_pct >= 5).mean() * 100, c.day_best_pct.median(),
                    c.close_pct.median()])
    f = daily[daily.fno & daily.up_pct.notna()]
    out.append(["Koi bhi F&O stock (bina filter), BUY ki taraf", len(f), (f.up_pct >= 5).mean() * 100, None, f.up_pct.median(), None])
    out.append(["Koi bhi F&O stock (bina filter), SELL ki taraf", len(f), (f.down_pct >= 5).mean() * 100, None, f.down_pct.median(), None])
    return out


def funnel(cands, daily) -> list:
    c = cands[cands.side.eq("BUY")]
    m = MAIN
    steps = [("F&O stock-din (9:15 candle ke saath)", int(daily.fno.sum()))]
    ok = c.tol_pct <= m["tol"] * 100 + 1e-9
    steps.append(("+ Open = Low (0.05%), green, tail < body", int(ok.sum())))
    ok &= c.pv.notna() & c.y_pv.notna() & ((c.o / c.prev_close - 1).abs() < 0.2) & (c.pv > c.y_pv)
    steps.append(("+ aaj ka Pivot > kal ka Pivot", int(ok.sum())))
    ok &= (c.o / c.top - 1).abs() <= 0.005
    steps.append(("+ open TC ke 0.5% ke andar", int(ok.sum())))
    ok &= c.body_pct < m["body"] * 100
    steps.append(("+ body < 1% (sab shartein)", int(ok.sum())))
    days = c[ok].date.nunique()
    steps.append((f"Kitne din kam se kam 1 stock mila (kul {daily.date.nunique()} din me)", int(days)))
    return [[a, b] for a, b in steps]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-10-01")
    ap.add_argument("--end", default="2026-10-28")
    ap.add_argument("--name", default="cpr_vwap_openlow")
    ap.add_argument("--cached", action="store_true")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    tell = (lambda m: None) if a.no_telegram else send
    out = result_dir(a.name)
    t0 = last = time.time()
    tell(f"CPR + VWAP + OPEN-LOW BACKTEST STARTED ({a.start} to {a.end}), F&O stocks. Main: 10-min candle Open=Low, Pivot upar, open TC ke paas, "
         "body < 1% -> 9:25 BUY, SL candle low, 3:15 exit. Saath me saare variants. Progress har 10 min.")

    def on_day(done, total, n):
        nonlocal last
        if time.time() - last >= 600:
            last = time.time()
            tell(f"CPR backtest progress: din {done}/{total} padhe, {n:,} candidates, {(time.time() - t0) / 60:.0f} min")

    try:
        pkl = out / "prepared.pkl"
        if a.cached and pkl.exists():
            daily, cands, trades = pickle.load(open(pkl, "rb"))
        else:
            daily, cands, trades = cpr.prepare(a.start, a.end, on_day)
            pickle.dump((daily, cands, trades), open(pkl, "wb"))
        tell(f"CPR backtest: data padh liya ({(time.time() - t0) / 60:.1f} min). {len(cands):,} candidates, {len(trades):,} trade combinations. "
             f"Ab {int(np.prod([len(v) for v in GRID.values()])):,} settings chala rahe hain.")
        runs = {k: cpr.select(cands, trades, s, **MAIN) for k, s in SLIPS.items()}
        for k, t in runs.items():
            t.to_csv(out / f"trades_main_slip_{k.rstrip('%')}pct.csv", index=False)
        g = grid(cands, trades)
        g.to_csv(out / "all_settings.csv", index=False)
        chk = [[label, *cpr.split_stats(cpr.select(cands, trades, GRID_SLIP, **{**MAIN, **o})).values()] for label, o in CHECKS]
        elig = g[g.is_trades >= MIN_IS_TRADES]
        best = elig.loc[elig.is_net.idxmax()].to_dict() if len(elig) else None
        nf = cpr.nifty(DATA / "index" / "NIFTY50_1m.csv")
        write_html(out / "report.html", runs, RULES, NOTES, FORMULAS, CHECKS, chk, g, best, claim(cands, daily), funnel(cands, daily), nf,
                   f"{a.start} se {a.end}")
    except Exception:
        tell(f"{TAG_TECH} CPR backtest FAILED:\n{traceback.format_exc()[-1500:]}")
        raise
    lines = []
    for k, t in runs.items():
        s = cpr.split_stats(t)
        lines.append(f"Slippage {k}: {s['all_trades']} trades | win {s['all_win_pct'] or 0:.1f}% | net Rs {s['all_net']:,.0f} | "
                     f"PF {s['all_pf'] or 0:.2f} | max DD Rs {s['all_max_dd']:,.0f} | in-sample Rs {s['is_net']:,.0f} / out-of-sample Rs {s['oos_net']:,.0f}")
    cl = claim(cands, daily)
    msg = (f"{TAG_TRADING} CPR + VWAP + OPEN-LOW BACKTEST DONE ({(time.time() - t0) / 60:.1f} min)\n"
           f"MAIN setting (BUY, entry 9:25, SL candle low, 3:15 exit, 2 trade/din):\n" + "\n".join(lines) +
           f"\n\n5% wala daawa: filter pass karne wale {cl[0][1]:,} stock-din me sirf {cl[0][2]:.1f}% din 9:25 ke baad 5%+ upar gaye "
           f"(bina filter koi bhi F&O stock: {cl[2][2]:.1f}%). 3:15 pe 5%+ upar: {cl[0][3]:.1f}%.")
    if best:
        msg += (f"\n\nSabse accha setting 2021-2024 pe ({best['side']}, entry {best['entry']}, exit {best['exit']}, CPR {best['cpr']}, "
                f"near {best['near']}, tol {best['tol'] * 100:.2f}%, body {best['body']}, {int(best['max_trades'])}/din): in-sample Rs {best['is_net']:,.0f} -> "
                f"2025-26 me Rs {best['oos_net']:,.0f} ({int(best['oos_trades'])} trade). Slippage 0.1%.")
    tell(msg)
    print(msg)
    if not a.no_telegram:
        send_document(out / "report.html", "CPR + VWAP + Open-Low report (browser me kholo). Rules tab me saare rules, neeche Formulas.")
    print("done ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

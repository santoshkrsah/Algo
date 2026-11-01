"""Option buyer "R3 momentum" (stock CALL premium closes above its own R3, BTST): Part 1 on 2 years of NSE option bhavcopy
(daily, expired contracts too), Part 2 on the ~2 weeks of real 1-minute option data; report + Telegram.

    python tools/run_opt_r3.py
    python tools/run_opt_r3.py --no-telegram

Needs:  (Algo_Historical_Data) python -m fyers_data.download_calendar bhavopt --start 2024-11-01 --end 2026-10-29
"""
import argparse
import json
import re
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backtest import opt_r3 as o  # noqa: E402
from backtest import opt_r3_1m as m1  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402
import pandas as pd  # noqa: E402
from backtest.opt_r3_report import contract_series, stats, write_html  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
START, END = "2024-11-01", "2026-10-29"
CAPITAL, SLIP = 200_000.0, 0.01
PIVOTS, STRIKES = ["classic", "camarilla"], [0, 1, 2]
SETTINGS = [["Universe", "NSE F&O stocks ke CALL option (sabse paas wali expiry)", 1], ["Slippage", "1% har fill (buy aur sell dono)", 1], ["Kharcha", "Rs 20 per order, STT 0.1% sell, exchange, stamp, GST"],
            ["Capital", "Rs 2,00,000 (fixed, compounding nahi)"], ["Har trade", "Rs 10,000 ka premium, din me max 2"], ["Liquidity", "signal din 200+ trade, kal 50+ trade, premium Rs 1+"], ["Entry", "signal din ka option close (BTST)"]]
DATA_NOTE = "Part 1: NSE option bhavcopy (daily), 2024-11-01 se 2026-10-29. Part 2: Fyers 1-min option data, lagbhag 15-28 Oct 2026."
RULES = [
    "Samay: NSE stock option bhavcopy (har contract ka din ka Open/High/Low/Close) 1 Nov 2024 se 29 Oct 2026. Expired contracts bhi shamil, "
    "isliye har signal gina gaya (sirf achhe wale nahi).",
    "Stocks: saare NSE F&O stocks ke CALL option, sabse paas wali expiry (signal ke din ke baad wali).",
    "Strike: signal ke din stock ke price ke sabse paas wala strike = ATM; uske upar wala = 1 OTM; do upar = 2 OTM. Teeno alag test kiye.",
    "R3 option ke apne premium pe, pichhle trading din ke High (H), Low (L), Close (C) se. Classic: P = (H+L+C)/3, R3 = H + 2 x (P - L). "
    "Camarilla: R3 = C + 1.1 x (H - L) / 4. Dono alag test kiye.",
    "Signal (Setup A, din ka aakhri candle): option ka us din ka NSE closing price R3 ke upar. NSE ka option close = aakhri 30 minute ka "
    "average price, yaani lagbhag aakhri 30-min candle ka close.",
    "Liquidity: signal ke din us option me kam se kam 200 trade aur pichhle din 50 trade; premium Rs 1 se upar.",
    "Entry: signal wale din ke closing price pe BUY (BTST - raat bhar hold).",
    "Exit (agle trading din) - 8 tarike alag test kiye: (1) agle din open, (2) agle din close (~3:15), (3-8) SL 20% ya 30% premium ka, "
    "target 1:1 / 1:2 / 1:3, dono na lage to agle din close.",
    "Paisa: capital Rs 2,00,000. Har trade me Rs 10,000 ka premium (20% SL pe Rs 2,000 = capital ka 1% risk). Din me max 2 trade "
    "(jinka us din Rs turnover sabse zyada), yaani max 2 khule trade.",
    "Kharcha: Rs 20 har order (buy + sell), STT 0.1% sell premium pe, exchange 0.03503%, SEBI, stamp 0.003% buy pe, GST 18%. "
    "Slippage 1% premium ka, buy aur sell dono pe.",
    "Part 2 (alag tab): Oct 2026 ka asli 1-minute option data - Setup A (30-min / 1-hour candle R3 ke upar close, BTST) aur "
    "Setup B (pehli 15-min candle R3 ke upar close, intraday), rulebook ke hisaab se.",
]
NOTES = [
    "Video me exit, SL, target kuch nahi bataya tha, isliye rulebook ka grid test kiya. Saare combination 'Saransh' ki table me hain.",
    "Daily data me din ke andar kya pehle hua pata nahi. Agar agle din SL aur target dono ki range aayi to SL pehle maana (sabse bura case). "
    "Agle din ka open hi SL ke neeche (ya target ke upar) khule to exit open pe.",
    "Setup A ke 'koi bhi 30-min / 1-hour candle' aur Setup B (pehli 15-min candle) ke liye intraday option candles chahiye. Fyers expired "
    "contracts ka data nahi deta, isliye ye sirf Oct 2026 ke ~2 hafte (Part 2) pe test hue.",
    "Agle din 9:30 / 10:30 wale exit daily data me nahi ho sakte (sirf open aur close hai); Part 2 me 10:30 exit test kiya.",
    "Qty fractional hai (har trade exactly Rs 10,000). Asli me lot size se kharidna padta hai, jaise TECHM 600 shares ka lot.",
    "Contract ka pichhle din aur agle din trade hona zaroori (warna R3 ya exit price nahi). Agle din trade na hua to wo signal chhoda.",
    "Strike ATM / OTM us din traded strikes me se, stock ke closing price ke hisaab se.",
    "PUT wala version: rulebook me 'S3 ke neeche' likha tha; option kharidne ke liye maine PUT ke premium ka R3 ke upar close liya (extra check).",
    "Stock ke R3 (option ki jagah) wala version yahan nahi kiya; stock ke R3 pe pehle wala equity backtest (03_full_2021_2026) hai.",
]
FORMULAS = [
    ["Kal ka H, L, C", "Option contract ke pichhle trading din ka High, Low, Close (NSE bhavcopy)"],
    ["Classic pivot", "P = (H + L + C) / 3;   R3 = H + 2 x (P - L)"],
    ["Camarilla", "R3 = C + 1.1 x (H - L) / 4"],
    ["Signal", "Aaj ka option Close > R3  AUR  aaj ke trades >= 200  AUR  kal ke trades >= 50  AUR  Close >= Rs 1"],
    ["ATM strike", "Aaj traded strikes me |Strike - stock price| sabse kam;  1 OTM = agla upar wala, 2 OTM = do upar"],
    ["Din me 2 trade", "Turnover = contracts x lot size x Close;  sabse zyada turnover wale 2 signal"],
    ["Buy price", "Buy = Close x (1 + 0.01)"],
    ["Qty", "Qty = 10,000 / Buy"],
    ["SL / Target", "SL = Entry x (1 - s);  Target = Entry x (1 + s x R)   (s = 0.20 ya 0.30,  R = 1, 2, 3)"],
    ["Exit (SL wale)", "Agle din Open <= SL -> Open;  Open >= Target -> Open;  Low <= SL -> SL;  High >= Target -> Target;  warna agle din Close"],
    ["Sell price", "Sell = Exit x (1 - 0.01)"],
    ["Gross P&L", "Qty x (Sell - Buy)"],
    ["Kharcha", "Rs 40 brokerage + 0.1% STT (sell value) + 0.03503% exchange (buy + sell) + 0.0001% SEBI + 0.003% stamp (buy) + 18% GST (brokerage + exchange + SEBI)"],
    ["Net P&L", "Gross - Kharcha"],
    ["Avg return (saare signal)", "= saare signals ka Net / 10,000 x 100 ka average"],
    ["Win rate", "Net > 0 wale trade / kul trade x 100"],
    ["Profit factor", "Jeete trades ka jod / hare trades ka loss jod (bina minus)"],
    ["Expectancy", "Kul Net / kul trade"],
    ["Max drawdown", "Roz ke Net ka jod = Kul;  Peak = max(0, ab tak ka sabse uncha Kul);  Max DD = (Kul - Peak) ka sabse bada minus"],
    ["Part 2 candles", "1-min candles ko 9:15 se 15/30/60 min me joda;  din ka aakhri 30-min candle = 15:15-15:30"],
    ["Part 2 SL/Target", "SL 30%, target 1:2 (Entry x 1.6), har 1-min candle pe check (dono ek candle me to SL pehle)"],
]


def combo(d, pivot, typ, k, ex, slip=SLIP, per_day=o.MAX_PER_DAY, filt=None):
    s = o.with_exits(o.signals(d, pivot, typ), slip)
    if filt is not None:
        s = filt(s)
    t = o.pick(s, k, per_day)
    st = stats(t[ex + "_net"], t.date, CAPITAL)
    return [st["trades"], st["win_pct"], round(st["net"]), st["profit_factor"], round(st["max_dd"])]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="opt_r3_2024_2026")
    ap.add_argument("--base", default="classic|0|open")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="redo only the report (new design): reuse checks / Part 2 / data info from the existing report.html")
    a = ap.parse_args()
    tell = (lambda msg: None) if a.no_telegram or a.rebuild else send
    out = result_dir(a.name)
    t0 = time.time()
    tell("OPTION R3 MOMENTUM BACKTEST STARTED: stock CALL option ka premium apne R3 ke upar close -> BTST. Part 1: NSE option bhavcopy "
         "Nov 2024 - Oct 2026 (Classic + Camarilla, ATM / 1 OTM / 2 OTM, 8 exit). Part 2: Oct 2026 ka 1-min data (15m/30m/1h setups).")
    try:
        d = o.load(START, END)
        picked, sigs = {}, {}
        for pv in PIVOTS:
            s = o.with_exits(o.signals(d, pv, "CE"), SLIP)
            for k in STRIKES:
                sigs[(pv, k)] = s[s.k == k]
                picked[(pv, k)] = o.pick(s, k)
                picked[(pv, k)].to_csv(out / f"trades_{pv}_k{k}.csv", index=False)
            tell(f"Option R3: {pv} pivot done - {len(s):,} signal (ATM/1 OTM/2 OTM mila ke).")
        bp, bk, be = a.base.split("|")
        bk = int(bk)
        if a.rebuild:
            old = json.loads(re.search(r"const D = (\{.*?\});\nconst \$", (out / "report.html").read_text(encoding="utf-8"), re.S).group(1))
            checks, p2, data_info = old["checks"], old["part2"], old["data_info"]
            days = sorted(d.date.unique())
            write_html(out / "report.html", picked, sigs, CAPITAL, RULES, NOTES, checks, FORMULAS, p2, data_info, a.base, contract_series(d, picked),
                       [days[0], days[-1]], DATA_NOTE, SLIP, SETTINGS)
            print("rebuilt ->", out)
            return 0
        checks = [
            ["Jaisa rules me hai"] + combo(d, bp, "CE", bk, be),
            ["PUT option (PE ka premium apne R3 ke upar)"] + combo(d, bp, "PE", bk, be),
            ["Slippage 0%"] + combo(d, bp, "CE", bk, be, slip=0.0),
            ["Slippage 2%"] + combo(d, bp, "CE", bk, be, slip=0.02),
            ["Din me sirf 1 trade"] + combo(d, bp, "CE", bk, be, per_day=1),
            ["Din me 5 trade"] + combo(d, bp, "CE", bk, be, per_day=5),
            ["Expiry ke 7 din andar wale signal chhodo"] + combo(d, bp, "CE", bk, be, filt=lambda s: s[s.dte > 7]),
            ["Sirf expiry ke 7 din andar wale signal"] + combo(d, bp, "CE", bk, be, filt=lambda s: s[s.dte <= 7]),
            ["Close R3 se kam se kam 5% upar ho"] + combo(d, bp, "CE", bk, be, filt=lambda s: s[s.above_pct >= 5]),
        ]
        tell("Option R3: Part 1 poora. Ab Part 2 (1-min data) chal raha hai.")
        cons = m1.load_contracts()
        p2 = []
        for pv in PIVOTS:
            t = m1.run(cons, pv)
            t.to_csv(out / f"part2_1min_{pv}.csv", index=False)
            for (setup, ex, k), g in t.groupby(["setup", "exit_rule", "k"]):
                p2.append([pv, setup, ex, ["ATM", "1 OTM", "2 OTM"][int(k)], len(g), (g.net_pnl > 0).mean() * 100, g.ret_pct.mean(), g.net_pnl.sum()])
        days = sorted(d.date.unique())
        data_info = [
            ["Data (Part 1)", f"NSE stock option bhavcopy, {len(days)} trading din ({days[0]} se {days[-1]}), {d.symbol.nunique()} stocks, "
                              f"{len(d):,} contract-din (sirf jin din trade hua, 2 sabse paas wali expiry)"],
            ["Data (Part 2)", f"Fyers 1-min, {len(cons)} stocks ke Nov 2026 CALL (har stock ~11 strike), lagbhag 15-28 Oct 2026"],
            ["Signal (Part 1)", ", ".join(f"{pv}: {sum(len(sigs[(pv, k)]) for k in STRIKES):,}" for pv in PIVOTS) + " (ATM + 1 OTM + 2 OTM, bina limit)"],
        ]
        write_html(out / "report.html", picked, sigs, CAPITAL, RULES, NOTES, checks, FORMULAS, p2, data_info, a.base, contract_series(d, picked),
                   [days[0], days[-1]], DATA_NOTE, SLIP, SETTINGS)
    except Exception:
        tell(f"{TAG_TECH} Option R3 backtest FAILED:\n{traceback.format_exc()[-1500:]}")
        raise
    lines = []
    for (pv, k), t in picked.items():
        for ex in ("open", "close", "sl30_t2"):
            st = stats(t[ex + "_net"], t.date, CAPITAL)
            sa = sigs[(pv, k)][ex + "_net"]
            lines.append(f"{pv} {['ATM', '1OTM', '2OTM'][k]} {ex}: {st['trades']} tr | win {st['win_pct'] or 0:.0f}% | net Rs {st['net']:,.0f} | "
                         f"PF {st['profit_factor'] or 0:.2f} | saare {len(sa)} signal avg {sa.mean() / 100 if len(sa) else 0:+.2f}%")
    msg = f"{TAG_TRADING} OPTION R3 MOMENTUM BACKTEST DONE ({(time.time() - t0) / 60:.1f} min). Capital Rs 2 L, Rs 10k har trade, din me max 2, slippage 1%.\n" + "\n".join(lines)
    tell(msg)
    print(msg)
    if not a.no_telegram:
        send_document(out / "report.html", "Option R3 momentum report (browser me kholo). Rules tab me rules, neeche Formulas.")
    print("done ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

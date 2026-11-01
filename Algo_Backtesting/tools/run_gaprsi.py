"""Gap + RSI(10) + 21-EMA backtest (long gap-up breakout, short gap-down reversal) on daily candles; report + Telegram.

    python tools/run_gaprsi.py                 # needs results/13_gap_rsi_ema/daily.pkl (built from the 15m equity candles)
    python tools/run_gaprsi.py --no-telegram
"""
import argparse
import pickle
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from backtest import gaprsi as G  # noqa: E402
from backtest import gaprsi_data as D  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.gaprsi_report import series_for, write_html  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
NAME = "gap_rsi_ema"
BASE = "long|2|ema"
RULES = [
    "Samay: daily candle, 1 Oct 2021 se 28 Oct 2026 (hamare equity data ki poori range). Daily candle 15-min candles se banayi: open = 9:15 ka open, "
    "high / low = din ka sabse bada / chhota, close = aakhri 15-min candle ka close. Split / bonus ke liye price adjust kiye (Assumptions dekho).",
    "Stocks: wahi stocks jinke F&O futures hain (lagbhag 270, 'liquid F&O' wali list). Cash market me kharidna-bechna.",
    "Indicators: 21-din EMA (close pe). RSI 10-din ka, phir uska 3-din simple average (smooth RSI). Monthly chart: month-end close ka 10-mahine EMA "
    "(pichhle poore mahine tak ka).",
    "Gap-up = aaj ka open pichhle din ke High se upar, aur gap % (open vs pichhla close) kam se kam T. Gap-down = open pichhle din ke Low se neeche, "
    "gap % kam se kam T. T = 1%, 2%, 3% - teeno alag chala ke dekhe.",
    "LONG entry (bullish gap breakout): gap-up ho, open pichhle 20 din ke sabse uncha High (resistance) se upar ho, din ka close 21 EMA se upar ho, "
    "smooth RSI 60 se upar aur 90 se neeche ho, aur din ka close monthly 10-EMA se upar ho (long-term uptrend). Din ka close gap-fill level se upar ho.",
    "SHORT entry (bearish gap reversal): gap-down ho aur pichhle din ka smooth RSI 70 se upar tha (overbought). Din ka close gap-fill level se neeche ho.",
    "Entry price: gap wale din ka close.",
    "Gap-fill level: pichhle din ka close.",
    "Stop-loss (close ke hisaab se): LONG - din ka close 21 EMA se neeche, ya gap fill (close pichhle din ke close se neeche) - jo pehle ho. "
    "SHORT - close gap-fill level se upar (close pichhle din ke close se upar); close 21 EMA se upar bhi stop hai, par sirf tab jab entry ke din price EMA ke neeche tha.",
    "Paisa: capital Rs 10,00,000. Har trade me capital ka 1% (Rs 10,000) risk: qty = 10,000 / (entry - stop level). Ek stock me max 20% capital. "
    "Ek saath max 10 stock. Leverage nahi (cash khatam to trade nahi). Profit dobara nahi lagaya.",
    "Exit (8 tarike alag test kiye): (1) 21-EMA trail - stop hi exit hai, koi target nahi (max 250 din), (2-4) target 1:2 / 1:3 / 1:5 (target = entry +/- R x risk, "
    "max 60 din), (5) monthly - sirf gap-fill stop + close monthly 10-EMA se neeche aaye to exit, (6-8) 20 / 40 / 60 din ka time stop (stop bhi lagu).",
    "Kharcha: LONG delivery - brokerage 0, STT 0.1% (buy + sell), exchange, SEBI, stamp 0.015% (buy), GST, DP charge Rs 15.34 + GST. "
    "SHORT stock futures se maana - Rs 20 per order, STT 0.02% (sell), exchange, SEBI, stamp, GST. Slippage 0.1% har fill pe.",
]
NOTES = [
    "Summary me exact numbers nahi the, isliye rulebook ke [A] parameter liye. Upar wale rules me jo bhi number hai wo 'maana hua' hai; "
    "Extra checks me ek-ek karke badal ke dikhaya hai.",
    "Monthly confirm me 20-mahine ki jagah 10-mahine EMA liya, kyunki 20-mahine ke liye 20 mahine ka data chahiye aur hamare paas 5 saal hi hain "
    "(trade Jun 2023 se hi shuru hote). 20-mahine wala extra check me hai.",
    "'10-din RSI, 90 overbought / 10 oversold, moving average pe' ko 'RSI ka 3-din average' maana. Long me 60 se 90 ke beech, short me 70 se upar.",
    "Stop 'close' pe lagta hai (din ke andar high / low se nahi), isliye exit price us din ka close hota hai - gap ke baad asli loss stop level se zyada ho sakta hai. "
    "Target din ke andar touch hone pe maana (agar open hi target ke paar khule to open pe).",
    "Stop level (jisse qty nikalta hai) = entry ke sabse paas wala level: gap-fill ya 21 EMA, jo entry ke neeche (long) / upar (short) sabse paas ho.",
    "Fyers ka data zyada tar pehle se split / bonus adjusted hai (282 events me), 13 events me nahi tha - wo maine NSE corporate actions se adjust kiye. "
    "12 galat price print (jaise 21 Sep 2023 ko kai stocks ka ek din ka galat close) hata diye. 19 bade jump (demerger jaise ABFRL, ALLCARGO) data me bache; "
    "inke baad 60 din tak signal nahi, aur trade ke beech aisa jump aaye to pichhle din ke close pe exit.",
    "Gap 25% se bada ho to trade nahi (wo corporate action hota hai).",
    "Universe aaj ki F&O list se bani hai (2024-26 ke bhavcopy me jo stocks the). Purane saal me jo stock F&O me nahi the ya baad me nikal gaye, unka haal alag ho sakta hai "
    "(survivorship / look-ahead). Extra check me 'saare liquid stocks' wala bhi hai.",
    "SHORT cash market me raat bhar nahi ho sakta, isliye stock futures se maana. Futures ka basis, roll aur margin nahi joda; position value hi cash se kaati.",
    "Short side me 'close 21 EMA ke upar' wala stop tab hi jab entry pe price EMA ke neeche ho, warna mirror karne pe trade usi din stop ho jata.",
    "Ek stock me ek samay me ek hi position. Same din kai signal aaye to bade gap wale pehle. Cash / 10 stock ki limit se jo trade nahi hua, wo gina nahi gaya.",
    "Pura size agar cash ya limit ki wajah se aadhe se kam mil raha ho to trade chhoda (chhote 'junk' trade se bachne ke liye).",
    "Bajaj Finance ka 'triangle breakdown, put kharido' example me pattern ke rules nahi the, isliye test nahi kiya. Promoter buying jaise fundamental inputs bhi nahi.",
    "Summary me 1:15 se 1:100 reward ka claim tha. Is test me 'Top 5% trade ka hissa' (Saransh -> Saare numbers) dikhata hai ki profit kitne bade winners pe tika hai.",
]
FORMULAS = [
    ["EMA 21", "EMA[t] = close[t] x k + EMA[t-1] x (1 - k),   k = 2 / (21 + 1)"],
    ["RSI 10", "up = max(close[t] - close[t-1], 0);  down = max(close[t-1] - close[t], 0);  RS = (up ka Wilder average, 10) / (down ka Wilder average, 10);  RSI = 100 - 100 / (1 + RS)"],
    ["Smooth RSI", "RSI_s = pichhle 3 din ke RSI ka simple average"],
    ["Monthly EMA 10", "Month-end close ki series pe EMA(10);  mahine ke andar pichhle poore mahine ki value use hoti hai (aage ka data nahi dekhte)"],
    ["Gap %", "Gap % = (aaj ka open / pichhla close - 1) x 100"],
    ["Gap-up / gap-down", "Gap-up: open > pichhla High;   Gap-down: open < pichhla Low"],
    ["Resistance", "Resistance = pichhle 20 din ke High ka maximum;  long ke liye open > Resistance"],
    ["LONG signal", "Gap-up  AND  gap % >= T  AND  open > Resistance  AND  close > EMA21  AND  60 < RSI_s < 90  AND  close > Monthly EMA10  AND  close > gap-fill"],
    ["SHORT signal", "Gap-down  AND  gap % <= -T  AND  pichhle din ka RSI_s > 70  AND  close < gap-fill"],
    ["Gap-fill level (F)", "F = pichhle din ka close"],
    ["Entry (E)", "E = gap wale din ka close;   fill: Buy = E x (1 + s),  Short = E x (1 - s),   s = 0.001"],
    ["Stop level (S)", "LONG: S = max(F, EMA21 entry pe);   SHORT: S = min(F, EMA21 entry pe, sirf agar entry pe close < EMA21)"],
    ["Risk per share", "Risk = |E - S|   (slippage ke saath)"],
    ["Qty", "Qty = floor( min( 10,000 / Risk,  2,00,000 / E,  bacha hua cash / E ) );  agar ye 10,000 / Risk ke aadhe se kam ho to trade nahi"],
    ["LONG stop hit", "Close < F   YA   Close < EMA21 (us din ka EMA)   ->  exit = us din ka Close, slippage ke saath"],
    ["SHORT stop hit", "Close > F   YA   (agar EMA stop chalu) Close > EMA21   ->  exit = us din ka Close"],
    ["Target (R)", "LONG: Target = E + R x Risk, High >= Target to exit = max(Open, Target) agar open paar na ho to Target;   SHORT: mirror"],
    ["Monthly exit", "LONG: Close < Monthly EMA10 (pichhle poore mahine tak ka)  ->  exit"],
    ["Time stop", "N-wa trading din (entry ke baad) ka Close"],
    ["Corporate action jump", "Din ka |close / pichhla close - 1| > 35%  ->  pichhle din ke close pe exit"],
    ["Gross P&L", "LONG: Qty x (Exit - Entry);   SHORT: Qty x (Entry - Exit)"],
    ["Kharcha (LONG)", "STT 0.1% x (buy + sell value) + exchange 0.00307% x (buy + sell) + SEBI 0.0001% x (buy + sell) + stamp 0.015% x buy + GST 18% x (exchange + SEBI) + DP Rs 15.34 x 1.18"],
    ["Kharcha (SHORT, futures)", "Rs 40 + STT 0.02% x sell + exchange 0.00173% x (buy + sell) + SEBI 0.0001% x (buy + sell) + stamp 0.002% x buy + GST 18% x (brokerage + exchange + SEBI)"],
    ["Net P&L", "Net = Gross - Kharcha"],
    ["R multiple", "R = Net / (Qty x Risk per share)"],
    ["Expectancy", "Expectancy (R) = saare trades ke R ka average"],
    ["Win rate", "Win % = (Net > 0 wale trade / kul trade) x 100"],
    ["Profit factor", "PF = jeete trades ka Net jod / hare trades ka loss jod (bina minus). 1 se upar = profit"],
    ["Top 5% ka hissa", "= sabse achhe 5% trades ka Net jod / kul Net x 100"],
    ["Max drawdown", "Roz (exit din) ka Net jod = Kul;  Peak = max(0, ab tak ka sabse uncha Kul);  Max DD = (Kul - Peak) ka sabse bada minus"],
    ["Nifty bull / bear", "Entry ke din Nifty close > Nifty ke pichhle 200 din ke close ka average  ->  bull"],
]


def run_combo(ind, nifty, p, side, ex, sig=None, arrs=None):
    t = G.trades(ind, p, side, ex, arrs=arrs, sig=sig)
    return G.size(t, p, nifty)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    tell = (lambda m: None) if a.no_telegram else send
    out = result_dir(NAME)
    t0 = time.time()
    tell("GAP + RSI + 21-EMA BACKTEST STARTED: daily candle, F&O stocks, Oct 2021 - Oct 2026. Long gap-up breakout + short gap-down reversal, "
         "gap 1 / 2 / 3%, 8 exit tarike, 1% risk per trade, capital Rs 10 L. Progress har 10 min.")
    try:
        _, adj = pickle.load(open(out / "daily.pkl", "rb"))
        ind = D.indicators(adj)
        fno = frozenset(D.fno_symbols())
        nifty = D.nifty_daily()
        arrs = G._arrays(ind, 10)
        base = G.Params(universe=fno)
        runs = {}
        for side in ("long", "short"):
            for gap in G.GAPS:
                p = G.with_params(base, gap=gap)
                sg = G.signals(ind, p, side)
                for ex in G.EXIT:
                    runs[(side, gap, ex)] = run_combo(ind, nifty, p, side, ex, sg, arrs)
                tell(f"Gap RSI EMA: {side} gap {gap:g}% done - {len(sg)} signal, {len(runs[(side, gap, 'ema')])} trade (21-EMA exit). {(time.time() - t0) / 60:.1f} min")
        for (side, gap, ex), t in runs.items():
            t.to_csv(out / f"trades_{side}_{gap:g}_{ex}.csv", index=False)

        bside, bgap, bex = BASE.split("|")

        def chk(name, side=bside, ex=bex, **kw):
            kw = dict(kw)
            ar = G._arrays(ind, kw["monthly_n"]) if kw.get("monthly_n") not in (None, 0, 10) else arrs
            p = G.with_params(base, gap=float(bgap), **kw)
            t = run_combo(ind, nifty, p, side, ex, arrs=ar)
            s = G.stats(t, p.capital)
            return [name, s["trades"], s["win_pct"], round(s["net"]), s["profit_factor"], s["avg_r"], round(s["max_dd"])]

        checks = [
            chk("Jaisa rules me hai (long, gap 2%, 21-EMA exit)"),
            chk("Resistance (20-din high) wali shart nahi", resist=False),
            chk("Monthly confirm 20-mahine EMA se", monthly_n=20),
            chk("Monthly confirm nahi", monthly_n=0),
            chk("RSI 70 se upar (60 ki jagah)", rsi_lo=70),
            chk("RSI 50 se upar (60 ki jagah)", rsi_lo=50),
            chk("RSI ki koi shart nahi", rsi_lo=0, rsi_hi=101),
            chk("Gap-fill = pichhle din ka High (close ki jagah)", fill="hl"),
            chk("Entry agle din ke open pe", entry="next_open"),
            chk("Slippage 0%", slip=0.0),
            chk("Slippage 0.3%", slip=0.003),
            chk("Universe: saare liquid stocks (20-din turnover Rs 10 Cr+), sirf F&O nahi", universe=None, min_turn=1e8),
            chk("Ek saath max 5 stock", max_pos=5),
            chk("Risk 0.5% per trade", risk=0.005),
            chk("Risk 2% per trade", risk=0.02),
            chk("SHORT: RSI 80 se upar", side="short", rsi_short=80.0),
            chk("SHORT: RSI 90 se upar", side="short", rsi_short=90.0),
            chk("SHORT: gap-fill = pichhle din ka Low", side="short", fill="hl"),
        ]
        tell(f"Gap RSI EMA: {len(runs)} combination aur {len(checks)} extra check ho gaye. Report ban rahi hai. {(time.time() - t0) / 60:.1f} min")
        sig_long = G.signals(ind, G.with_params(base, gap=2.0), "long")
        first = min(t.entry_date.min() for k, t in runs.items() if len(t) and k[0] == "long")
        first_s = min(t.entry_date.min() for k, t in runs.items() if len(t) and k[0] == "short")
        info = [
            ["Data", f"Daily candle (15-min candles se bana), {ind.symbol.nunique():,} stocks, {ind.date.min()} se {ind.date.max()}; F&O universe {len(fno & set(ind.symbol))} stocks"],
            ["Pehla trade", f"Long: {first} (monthly 10-EMA ke liye 10 mahine ka data chahiye), Short: {first_s}"],
            ["Signal (long, gap 2%)", f"{len(sig_long)} gap-up din (saare filter ke baad), {sig_long.symbol.nunique()} stocks"],
            ["Saaf kiya", "12 galat price print hataye, 13 split/bonus adjust kiye, 19 bade jump (demerger) pe signal band / forced exit (results/13_gap_rsi_ema/ me CSV)"],
            ["Nifty", "Nifty 50 index 1-min data se daily close; 200-din average ke upar / neeche = bull / bear"],
        ]
        series = series_for(ind, runs)
        write_html(out / "report.html", runs, base.capital, RULES, NOTES, checks, FORMULAS, info, BASE, series, [ind.date.min(), ind.date.max()],
                   "Daily data: equity 15-min candles se bana, 1 Oct 2021 se 28 Oct 2026. Pehla long trade " + first + ".")
    except Exception:
        tell(f"{TAG_TECH} Gap RSI EMA backtest FAILED:\n{traceback.format_exc()[-1500:]}")
        raise
    lines = []
    for (side, gap, ex), t in runs.items():
        if ex in ("ema", "rr2", "rr3", "monthly"):
            s = G.stats(t, base.capital)
            lines.append(f"{side} gap {gap:g}% {ex}: {s['trades']} tr | win {s['win_pct'] or 0:.0f}% | net Rs {s['net']:,.0f} | PF {s['profit_factor'] or 0:.2f} | avg R {s['avg_r'] or 0:+.2f}")
    msg = f"{TAG_TRADING} GAP + RSI + 21-EMA BACKTEST DONE ({(time.time() - t0) / 60:.1f} min). Capital Rs 10 L, 1% risk.\n" + "\n".join(lines)
    tell(msg)
    print(msg)
    if not a.no_telegram:
        send_document(out / "report.html", "Gap + RSI + 21-EMA report (browser me kholo). Rules tab me rules, neeche Formulas; 'Trade viewer' tab me har trade ka chart.")
    print("done ->", out, f"{(time.time() - t0) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())

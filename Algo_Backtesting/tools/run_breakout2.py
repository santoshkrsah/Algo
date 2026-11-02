"""Volume breakout v2 - full rulebook spec on the top-50 liquid F&O stocks (point-in-time): type A stock long and type B put
SELL on the same breakouts (both Dec 2021 - Oct 2026: stock options from the NSE bhavcopy incl. expired contracts), a
post-sell-off variant for B, every listed value tested one at a time, a fit grid on the first 60% judged on the last 40%,
the claims ("~80% success", failed breakouts, "4-6% a month"), tail risk of the short puts; report + Telegram.

    python tools/run_breakout2.py
    python tools/run_breakout2.py --no-telegram --status-file status.txt
    python tools/run_breakout2.py --quick          # main tests + checks only (no grid), for testing
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest import breakout2 as B  # noqa: E402
from backtest.breakout_report import write_html  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
NAME = "breakout_ab_full"
TITLE = "Breakout A + B (poora test)"
P = B.Params()
W_ = B.with_params
SPLIT = "2024-11-01"                       # fit = Dec 2021 - Oct 2024 (35 of 59 months, ~60%), out-of-sample = Nov 2024 - Oct 2026
SETUP = ("top_n", "base_n", "max_height", "max_body", "vol_mult", "vol_n", "accum", "close_top", "red_mult", "body_mult", "morning_star", "per_month",
         "signal", "so_n", "so_drop", "so_hold", "so_bounce")
SO = W_(P, signal="selloff")

CHECKS = [
    ["Setup (breakout) ke numbers"],
    ("Main test", "A", P), ("Main test", "B", P),
    ("Base 40 din", "A", W_(P, base_n=40)), ("Base 40 din", "B", W_(P, base_n=40)), ("Base 60 din", "A", W_(P, base_n=60)), ("Base 60 din", "B", W_(P, base_n=60)),
    ("Range height 8% tak", "A", W_(P, max_height=0.08)), ("Range height 8% tak", "B", W_(P, max_height=0.08)),
    ("Range height 15% tak", "A", W_(P, max_height=0.15)), ("Range height 15% tak", "B", W_(P, max_height=0.15)),
    ("Volume 1.5x", "A", W_(P, vol_mult=1.5)), ("Volume 1.5x", "B", W_(P, vol_mult=1.5)), ("Volume 3x", "A", W_(P, vol_mult=3.0)), ("Volume 3x", "B", W_(P, vol_mult=3.0)),
    ("Chhoti candle filter band", "A", W_(P, max_body=None)), ("Volume badhna (accumulation) filter band", "A", W_(P, accum=False)),
    ("High ke paas close filter band", "A", W_(P, close_top=None)), ("Pichhle din badi laal candle filter band", "A", W_(P, red_mult=None)),
    ("Breakout candle badi ho (body 1.5x, report 18 jaisa)", "A", W_(P, body_mult=1.5)),
    ("Morning Star filter chalu", "A", W_(P, morning_star=True)), ("Morning Star filter chalu", "B", W_(P, morning_star=True)),
    ("Mahine me max 3 signal", "A", W_(P, per_month=3)), ("Mahine ki limit nahi", "A", W_(P, per_month=None)), ("Mahine ki limit nahi", "B", W_(P, per_month=None)),
    ["Type A: entry, stop, exit, size"],
    ("Entry breakout din ke close pe", "A", W_(P, entry="close")), ("Stop breakout candle ke low ke neeche", "A", W_(P, stop="low")),
    ("Target 1:3", "A", W_(P, rr=3.0)), ("Target 10%", "A", W_(P, exit="pct")), ("Time stop 5 din", "A", W_(P, max_days=5)), ("Time stop 20 din", "A", W_(P, max_days=20)),
    ("Trailing SL 5 din ka low (target nahi, time stop 20 din)", "A", W_(P, exit="trail5", max_days=20)),
    ("Trailing SL 10 din ka low (target nahi, time stop 20 din)", "A", W_(P, exit="trail10", max_days=20)),
    ("Add-on: naya high bane to 50% aur, stop entry pe", "A", W_(P, addon=True)),
    ("Risk 0.5% / trade", "A", W_(P, risk=0.005)), ("Risk 0.75% / trade", "A", W_(P, risk=0.0075)),
    ("Slippage 0", "A", W_(P, slip=0.0)), ("Slippage 2x (0.1%)", "A", W_(P, slip=0.001)),
    ("Futures me (poore lots), Rs 10 lakh", "A", W_(P, a_inst="fut")), ("Futures me (poore lots), Rs 1 crore", "A", W_(P, a_inst="fut", capital=10_000_000.0)),
    ["Type B: put selling"],
    ("Sell-off ke baad recovery (support = sell-off low)", "B", SO),
    ("Expiry 5 din door", "B", W_(P, dte=5)), ("Expiry 10 din door", "B", W_(P, dte=10)),
    ("Strike: support se 1 strike neeche", "B", W_(P, strike="below")), ("Strike: 5% OTM", "B", W_(P, strike="otm5")),
    ("SL: premium 2x (= loss 1x premium)", "B", W_(P, b_stop="prem2")), ("SL: loss 1.5x premium", "B", W_(P, b_stop="loss15")),
    ("50% premium kamate hi band", "B", W_(P, take=0.5)), ("70% premium kamate hi band", "B", W_(P, take=0.7)),
    ("ITM ho to bhi rakho, physical delivery lo", "B", W_(P, settle="physical")),
    ("Risk 0.5% / trade", "B", W_(P, risk=0.005)), ("Risk 0.75% / trade", "B", W_(P, risk=0.0075)),
    ("Slippage 0", "B", W_(P, opt_slip=0.0, opt_slip_min=0.0)), ("Slippage 2x (4%, min Rs 0.20)", "B", W_(P, opt_slip=0.04, opt_slip_min=0.20)),
    ("Margin 1.5 guna (andaze se zyada)", "B", W_(P, margin_mult=1.5)), ("Capital Rs 50 lakh (lot ka asar)", "B", W_(P, capital=5_000_000.0)),
]
GRID_SETUP = [dict(base_n=b, max_height=h, vol_mult=v) for b in (20, 40, 60) for h in (0.08, 0.12, 0.15) for v in (1.5, 2.0, 3.0)]
GRID_A = [dict(entry=e, stop=s, exit=x, rr=rr, max_days=m, addon=a) for e in ("next_open", "close") for s in ("res", "low")
          for x, rr in (("rr", 2.0), ("rr", 3.0), ("pct", 2.0), ("trail5", 2.0), ("trail10", 2.0)) for m in (5, 10, 20) for a in (False, True)]
GRID_B = [dict(strike=k, dte=d, b_stop=s, take=t) for k in ("support", "below", "otm5") for d in (5, 7, 10) for s in ("support", "prem2", "loss15") for t in (None, 0.5, 0.7)]
MIN_FIT = {"A": 20, "B": 10}

SETTINGS = [["Stock list", "top 50 F&O, har mahine (NSE bhavcopy, point-in-time)", 1], ["Data", "daily candles + NSE stock option bhavcopy (expired bhi)"],
            ["Capital", "Rs 10,00,000 fixed, compounding nahi", 1], ["Risk / trade", "1% = Rs 10,000", 1], ["Mahine me", "max 4 signal (zyada volume pehle)"],
            ["Slippage", "stock 0.05%, option max(2%, Rs 0.10) har fill", 1], ["Kharcha", "STT / exchange date ke hisaab se, GST, stamp, DP, Rs 20/order (F&O)"],
            ["Margin", "SPAN jaisa andaza, har din check", 1], ["Test", "fit Dec 2021 - Oct 2024, out-of-sample Nov 2024 - Oct 2026", 1]]
RULES = [
    "Stock list: har mahine ki pehli tarikh ko us din ke F&O stocks (NSE F&O bhavcopy se, Oct 2021 se, point-in-time) me se pichhle 60 din ke average futures turnover "
    "ke hisaab se top 50. Jo stock hamare stock data me nahi (merge / delist ho gaye, jaise HDFC), use chhod ke agla stock liya.",
    "Base (range): pichhle 20 trading din. Base top = un 20 din ka sabse uncha high, base low = sabse neecha low. Base ki unchai (top - low) / top 12% se kam ho. "
    "Candles chhoti: un 20 din me |close - open| ka average price ka 1.5% se kam.",
    "Volume badhna (accumulation): base ke aakhri 10 din ka average volume pehle 10 din se zyada.",
    "Breakout: din ka close base top ke upar; us din ka volume pichhle 20 din ke average ka kam se kam 2 guna; close din ki range ke upar wale 25% hisse me (high ke paas); "
    "pichhle din badi laal candle nahi (laal body base ki average body ke 2 guna se kam).",
    "Ek stock ka breakout aaya to agle 20 din us stock ka naya breakout nahi ginte (naya base banne do). Mahine me max 4 signal; ek din me zyada ho to jiska volume multiple sabse zyada, wo pehle.",
    "Type A (stock long): breakout ke agle din open pe kharido. Stop = base top se 0.5% neeche. Target = entry + 2 x (entry - stop). 10 trading din me na stop laga na target, "
    "to 10ve din close pe bahar. Ek hi candle me stop aur target dono chhue to stop maana; gap se paar khula to open pe fill.",
    "Type A size: qty = capital ka 1% / (entry - stop). Ek stock me max 50% capital, sab positions milake capital tak (cash, leverage nahi), ek stock me ek hi position.",
    "Type B (put SELL): wahi breakout. Put breakout ke agle din se (15 trading din ke andar) us din ke close pe becho, jab sabse paas wali expiry 2 se 7 trading din door ho, "
    "aur tab tak har din stock ka close support (base top) ke upar rahe. Strike = support ke barabar ya usse neeche wala sabse paas ka strike jisme us din trade hua. Premium Rs 1 se kam to trade nahi.",
    "Type B exit: stock ka close support ke neeche gaya to usi din close pe put wapas kharido (stop). Expiry se 1 din pehle put ITM ho (stock strike ke neeche) to usi din close pe band, "
    "taaki physical delivery na lena pade. Warna expiry tak rakho; expiry pe OTM ho to put bekaar khatam (wapas kharidna nahi padta).",
    "Type B size: risk har share = stop pe put ki keemat (stock support se 1% neeche, Black-76, entry din ki IV) - premium (kam se kam premium ka aadha) + slippage. "
    "Lots = capital ka 1% / (risk x lot size). 1 lot ka risk hi 1% se zyada ho to trade chhoda.",
    "Margin: har din har khule put ka margin (SPAN jaisa andaza). Naye put ke din sab khule puts ka us din ka margin + naye ka margin, capital + ab tak ke booked P&L se zyada ho to trade chhoda. "
    "Ek stock me ek hi put.",
    "Kharcha: stock delivery pe STT 0.1% dono taraf, exchange, SEBI, stamp, GST, DP. Option pe Rs 20 / order, STT sell pe (Oct 2024 se pehle 0.0625%, phir 0.1%, 1 Apr 2026 se 0.15%), "
    "exchange, stamp, GST. Slippage: stock 0.05% har fill, option max(price ka 2%, Rs 0.10) har fill.",
    "Capital Rs 10 lakh fixed (profit dobara nahi lagaya, compounding nahi). A aur B do alag test (har ek ke liye alag Rs 10 lakh): same signal, trade karne ke do tareeke.",
    "Sell-off variant (sirf B): pichhle 20 din ka sabse neecha low, usse pehle ke 20 din ke sabse uche high se 10% ya zyada neeche ho; 5 din se naya low nahi bana; aaj ka close low se 3% upar. "
    "Support = wo sell-off low (strike usi ke paas, stop = close us low ke neeche). Baaki sab B jaisa.",
    "Test ka tareeka: pehla ~60% samay (Dec 2021 - Oct 2024) = fit, aakhri ~40% (Nov 2024 - Oct 2026) = out-of-sample. Grid me har setting chalayi; sabse achhi setting sirf fit ke "
    "result se chuni, phir uska out-of-sample result dikhaya. Main test ke number pehle se tay the (result dekh ke nahi badle).",
]
NOTES = [
    "Spec me kai number 3 option me the (base 20/40/60, range 8/12/15%, volume 1.5/2/3x ...). Main test me beech wali value li; base 20 din liya kyunki 40 / 60 din pe signal "
    "bahut kam aate hain (spec: 3-4 trade / mahina). Baaki saari value 'Extra checks' aur grid me hain.",
    "Breakout ka volume poore din ka hai, wo close pe hi pata chalta hai - isliye signal din ke close pe, entry agle din. 'Breakout din close pe entry' variant thoda optimistic hai.",
    "Option data NSE bhavcopy hai (daily, expired contracts bhi, sirf trade hue strikes, 2 sabse paas wali expiry). Din ke andar ka order pata nahi: premium stop din ke high se, "
    "target din ke low se, support stop stock ke close se. Jis din strike me trade nahi hua, us din ki keemat Black-76 se andaza (pichhle trade ki IV).",
    "Jul 2024 se pehle ki NSE file (purana format) me lot size, underlying price nahi tha. Lot = turnover / (contracts x (strike + premium)) - asli lot size se mila ke check kiya. "
    "Underlying = sabse paas wale futures ka close.",
    "Stock ke daily candles split / bonus adjusted hain (Fyers poora itihaas adjust karta hai), par option strike us din ka asli price hai. Isliye factor = asli price / adjusted close "
    "(1, 2, 10, 20 jaise saaf ratio pe) se support aur spot ko option ke scale pe laaya. Is wajah se report 18 ke type B me split / bonus se pehle wale trades galat strike pe the ya chhoot gaye.",
    "Survivorship bias: stock data AMFI Jun-2026 list se hai. F&O top-50 point-in-time hai, par jo stocks merge / delist ho gaye (HDFC, MINDTREE, IDFC ...) unka stock data nahi, isliye unhe chhoda (Data card me ginti).",
    "Margin ek andaza hai (asli SPAN NSE ki file se aata hai): futures margin = 10 x stock ka daily sd (15% - 50%), put ka margin = max(scan x spot - OTM, scan ka aadha) + 3.5% exposure, "
    "ITM ho to expiry ke aakhri 4 din delivery margin. Isliye 'return on margin' bhi andaza hai.",
    "Physical settlement variant: expiry pe ITM put -> strike pe share lene pade (assignment), agle din open pe beche. STT 0.1% dono taraf, broker fee 0.25% (andaza). "
    "Rs 10 lakh me delivery ka paisa (strike x qty) aksar capital se zyada hota hai - asli me broker pehle hi band kar deta.",
    "Earnings / news dates ka data nahi hai. Isliye gap-down stress do tarah: (1) asli gap-down din jab put khula tha, (2) andaza: har trade pe agle din 5 / 10 / 15 / 20% gap.",
    "'~80% success' ka matlab saaf nahi tha - isliye kai matlab dikhaye: breakout tika (5 din base ke andar close nahi), +1R / +2R stop se pehle, type A trade profit me.",
    "'4-6% mahina = 18 mahine me double': 4% mahina compounding se 18 mahine me ~2x (1.04^18 = 2.03); bina compounding 18 x 4% = 72% hi. Hamare result se kitne mahine lagte, Daave tab me.",
    "Daily candle me ek hi din stop aur target dono chhue to stop maana (bura case). 'Premium 2x' aur 'loss 1x premium' ek hi rule hai. Expiry tak din = trading din.",
]
FORMULAS = [
    ["Base top / low", "max(high) / min(low) pichhle 20 din (aaj ko chhod ke)"], ["Range height", "(base top - base low) / base top <= 12%"],
    ["Chhoti candles", "average(|close - open| / close) pichhle 20 din <= 1.5%"], ["Volume multiple", "aaj ka volume / average(volume pichhle 20 din) >= 2"],
    ["Accumulation", "average(volume pichhle 10 din) > average(volume usse pehle ke 10 din)"],
    ["Breakout candle", "close > base top; close >= low + 0.75 x (high - low); kal ki (open - close) / close <= 2 x average body"],
    ["Sell-off (B variant)", "low = min(low, 20 din) <= 0.9 x max(high, us low se pehle 20 din); low >= 5 din purana; close >= 1.03 x low"],
    ["A: entry", "agle din ka open; fill = open x (1 + 0.05%)"], ["A: stop", "base top x (1 - 0.5%)"], ["A: target", "entry + 2 x (entry - stop)"],
    ["A: exit fill", "stop / target price (gap me open), time stop pe close; fill = price x (1 - 0.05%)"],
    ["A: qty", "floor(min(capital x 1% / (entry fill - stop), capital x 50% / entry fill, khali cash / entry fill))"],
    ["A futures: lots", "floor(capital x 1% / ((entry fill - stop) x lot)); margin = futures margin x qty x price <= capital + booked P&L - khule margin"],
    ["A: P&L / R", "P&L = qty x (exit fill - entry fill) - kharcha; R = P&L / (qty x (entry fill - stop))"],
    ["B: entry din", "pehla din (signal ke baad, 15 din ke andar) jab expiry tak trading din 2 se 7 ho aur ab tak har close >= support"],
    ["B: support / spot (option scale)", "support x factor, close x factor; factor = asli price / adjusted close"],
    ["B: strike", "max(strike) jahan strike <= support aur us din trade hua"],
    ["B: risk / share (support stop)", "max(Black76(put, spot = support x 0.99, T - 1 din, IV entry) - premium, 0.5 x premium) + slippage dono fill"],
    ["B: risk / share (premium stop)", "premium 2x: 1 x premium; loss 1.5x: 1.5 x premium (+ slippage)"],
    ["B: lots", "floor(capital x 1% / (risk x lot)); phir margin check"],
    ["B: put margin / share", "max((m - 3.5%) x spot - max(spot - strike, 0), 0.5 x (m - 3.5%) x spot) + 3.5% x spot; m = clip(10 x daily sd, 15%, 50%)"],
    ["B: delivery margin", "ITM ho to expiry se 4/3/2/1 din pehle + 10/25/45/70% x m x strike x qty"],
    ["B: fill", "sell = premium - max(2% x premium, Rs 0.10); buy back = price + max(2% x price, Rs 0.10); OTM expiry = 0"],
    ["B: P&L", "qty x (sell fill - buy fill) - kharcha"], ["B: R / loss multiple", "R = P&L / (risk x qty); loss multiple = P&L / premium mila"],
    ["B: return", "premium pe = net / premium; margin pe = net / entry din ka margin; notional pe = net / (strike x qty)"],
    ["Physical", "P&L = qty x (sell fill - (strike - agle din open)) - option kharcha - delivery kharcha (STT 0.1% dono, stamp, DP) - 0.25% x strike x qty"],
    ["Stock kharcha", "STT 0.1% x (buy + sell) + exchange 0.00322% (Oct 2024 se 0.00297%) + SEBI 0.0001% + stamp 0.015% x buy + GST 18% x (exchange + SEBI) + DP Rs 18.10"],
    ["Option kharcha", "Rs 20 x orders + STT sell (0.0625% / 0.1% / 0.15%) + exchange 0.05% (Oct 2024 se 0.03503%) + SEBI 0.0001% + stamp 0.003% x buy + GST 18% x (brokerage + exchange + SEBI)"],
    ["Futures kharcha", "Rs 20 x orders + STT sell (0.0125% / 0.02% / 0.05%) + exchange 0.0019% (Oct 2024 se 0.00173%) + SEBI + stamp 0.002% x buy + GST 18%"],
    ["Win rate", "net > 0 wale trade / kul trade x 100"], ["Average win / loss R", "jeete trades ka average R / hare trades ka average R"],
    ["Expectancy", "average R (aur Rs me average net / trade)"], ["Profit factor", "jeete trades ka kul profit / hare trades ka kul loss"],
    ["Max drawdown", "roz ke mark-to-market (khule trade bhi us din ke close pe) kul P&L curve ka sabse bada gir (peak se)"],
    ["Mahine ka return", "us mahine ka net (mark-to-market) / Rs 10,00,000 x 100 (compounding nahi)"],
    ["Double kab", "ln(2) / ln(1 + average mahina return) mahine (agar profit compound karo)"],
    ["Breakout fail (study)", "breakout ke baad 5 din me koi close base top ke neeche"], ["+1R / +2R (study)", "stop (base top - 0.5%) lagne se pehle high >= entry + 1 / 2 x (entry - stop), 20 din tak"],
    ["Rank correlation", "Spearman: grid ki settings ka fit-rank vs out-of-sample-rank (1 = poora mel, 0 = koi rishta nahi)"],
]


def months_between(a: str, b: str) -> int:
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7]) + 1


def inr(v: float) -> str:
    """Rs 12,34,567 (Indian grouping)."""
    s = f"{abs(v):.0f}"
    head, groups = s[:-3], [s[-3:]]
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ("-" if v < 0 else "") + "Rs " + ",".join(groups)


def chips_of(typ: str, p: B.Params) -> list:
    filt = ", ".join(x for x, on in (("chhoti candles", p.max_body is not None), ("volume badhna", p.accum), ("high ke paas close", p.close_top is not None),
                                     ("kal badi laal nahi", p.red_mult is not None), ("badi body", p.body_mult is not None), ("Morning Star", p.morning_star)) if on) or "koi nahi"
    if p.signal == "selloff":
        setup = [["Signal", f"sell-off {p.so_drop * 100:g}%+ ke baad recovery (low {p.so_hold} din tika, {p.so_bounce * 100:g}% upar)", 1]]
    else:
        setup = [["Base", f"{p.base_n} din, height <= {p.max_height * 100:g}%", 1], ["Volume", f">= {p.vol_mult:g}x (20 din avg)", 1], ["Filters", filt]]
    if typ == "A":
        ex = {"rr": f"target 1:{p.rr:g}", "pct": f"target {p.tgt_pct * 100:g}%", "trail5": "trailing 5 din ka low", "trail10": "trailing 10 din ka low"}[p.exit]
        return setup + [["Type", "A: stock long" + (" (futures lots)" if p.a_inst == "fut" else ""), 1], ["Entry", "agle din open" if p.entry == "next_open" else "breakout din close", 1],
                        ["Stop", "base top - 0.5%" if p.stop == "res" else "breakout candle low - 0.5%"], ["Exit", f"{ex}, time stop {p.max_days} din", 1],
                        ["Add-on", "chalu" if p.addon else "band"], ["Max / stock", f"{p.max_val * 100:g}% capital"]]
    st = {"support": "support pe / neeche", "below": "support se 1 strike neeche", "otm5": "5% OTM"}[p.strike]
    sl = {"support": "stock support ke neeche close", "prem2": "premium 2x", "loss15": "loss 1.5x premium"}[p.b_stop]
    return setup + [["Type", "B: put SELL", 1], ["Strike", st, 1], ["Expiry", f"2 - {p.dte} trading din door", 1], ["SL", sl, 1],
                    ["Exit", f"{p.take * 100:g}% premium kamate hi" if p.take else "expiry tak", 1],
                    ["ITM expiry ke paas", "1 din pehle band" if p.settle == "close" else "physical delivery"]]


def describe(typ: str, q: B.Params) -> str:
    """One line for a grid setting."""
    s = f"base {q.base_n}, height {q.max_height * 100:g}%, volume {q.vol_mult:g}x"
    if typ == "A":
        ex = {"rr": f"1:{q.rr:g}", "pct": "10%", "trail5": "trail 5 din", "trail10": "trail 10 din"}[q.exit]
        return s + f" | entry {'open' if q.entry == 'next_open' else 'close'}, stop {'base top' if q.stop == 'res' else 'candle low'}, {ex}, {q.max_days} din" + (", add-on" if q.addon else "")
    return s + f" | strike {q.strike}, expiry <= {q.dte} din, SL {q.b_stop}, " + (f"{q.take * 100:g}% pe band" if q.take else "expiry tak")


def peak_open(t: pd.DataFrame, col: str, cal: list) -> tuple[float, int]:
    if t is None or t.empty:
        return 0.0, 0
    idx = {d: i for i, d in enumerate(cal)}
    v, n = np.zeros(len(cal)), np.zeros(len(cal))
    for a, b, x in zip(t.entry_date, t.exit_date, t[col]):
        v[idx[a]:idx[b] + 1] += x
        n[idx[a]:idx[b] + 1] += 1
    return float(v.max()), int(n.max())


def inv_of(typ: str, t: pd.DataFrame, p: B.Params, cal: list) -> list:
    cap = f"{inr(p.capital)} (fixed, compounding nahi)"
    if typ == "A":
        pk, n = peak_open(t, "margin", cal)
        fut = p.a_inst == "fut"
        return [["Shuruaati capital", cap], ["Risk har trade", f"{p.risk * 100:g}% = {inr(p.capital * p.risk)}"],
                ["Qty ka rule", "capital x risk / (entry - stop)" + (", poore lots (futures)" if fut else ", ek stock me max 50% capital")],
                ["Max khule trade", f"jitna {'margin' if fut else 'cash'} ho; sabse zyada ek saath: {n}"],
                ["Average / sabse badi position", f"{inr(t.notional.mean())} / {inr(t.notional.max())}" if len(t) else "n/a"],
                ["Sabse zyada paisa ek din me laga", f"{inr(pk)} ({pk / p.capital * 100:.0f}% capital)"]]
    pk, n = peak_open(t, "margin", cal)
    return [["Shuruaati capital", cap], ["Risk har trade", f"{p.risk * 100:g}% = {inr(p.capital * p.risk)} (stop rule se)"],
            ["Lots ka rule", "capital x risk / (stop pe risk x lot size)"], ["Margin", "SPAN jaisa andaza, har din; sab milake capital + booked P&L tak"],
            ["Max khule puts ek saath", str(n)], ["Average premium mila / trade", inr(t.premium_rs.mean()) if len(t) else "n/a"],
            ["Average / sabse bada margin (entry din)", f"{inr(t.margin.mean())} / {inr(t.margin.max())}" if len(t) else "n/a"],
            ["Sabse zyada margin ek din me (entry ke hisaab se)", f"{inr(pk)} ({pk / p.capital * 100:.0f}% capital)"]]


def series_for(D: dict, trades: list[pd.DataFrame], before: int = 30, after: int = 6) -> dict:
    """Daily candles (adjusted) of every traded stock around its trades: {symbol: [[date, o, h, l, c], ...]}."""
    W, cal = D["W"], D["cal"]
    idx = D["pos"]
    spans: dict[str, list] = {}
    for t in trades:
        if t is None or t.empty:
            continue
        for sym, a, b in zip(t.symbol, t.signal_date, t.exit_date):
            spans.setdefault(sym, []).append((max(0, idx[a] - before), min(len(cal) - 1, idx[b] + after)))
    out = {}
    for sym, sp in spans.items():
        keep = np.zeros(len(cal), dtype=bool)
        for a, b in sp:
            keep[a:b + 1] = True
        rows = np.flatnonzero(keep)
        o, h, l, c = (W[k][sym].to_numpy()[rows] for k in ("open", "high", "low", "close"))
        out[sym] = [[cal[r], round(float(a), 2), round(float(b), 2), round(float(x), 2), round(float(y), 2)] for r, a, b, x, y in zip(rows, o, h, l, c) if y == y]
    return out


A_KEEP = ["symbol", "kind", "signal_date", "entry_date", "entry", "stop", "target", "exit_date", "exit", "reason", "hold", "qty", "add_qty", "add_date", "add_px",
          "notional", "margin", "entry_fill", "exit_fill", "risk_rs", "gross", "slip", "costs", "net", "R", "vm", "height", "res", "lo", "bull", "base_n"]
B_KEEP = ["symbol", "kind", "signal_date", "entry_date", "expiry", "dte", "strike", "prem", "lots", "lot", "qty", "support", "spot", "factor", "otm_pct", "iv", "exit_date",
          "exit", "reason", "hold", "est_days", "sell_fill", "buy_fill", "premium_rs", "margin", "peak_margin", "notional", "risk_rs", "gross", "slip", "costs", "net", "R",
          "loss_x_prem", "ret_prem", "ret_margin", "ret_notional", "deliver_rs", "vm", "height", "res", "lo", "bull", "base_n"]


def trade_dicts(t: pd.DataFrame, typ: str, D: dict) -> list:
    if t is None or t.empty:
        return []
    x = t[[c for c in (A_KEEP if typ == "A" else B_KEEP) if c in t]].copy()
    num = x.select_dtypes("number").columns
    x[num] = x[num].round(4)
    out = x.astype(object).where(x.notna(), None).to_dict("records")
    if typ == "B":
        cal = D["cal"]
        for r, days, vals in zip(out, t.days, t.vals):
            r["prem_series"] = [[cal[k], round(float(v), 2)] for k, v in zip(days, vals)]
    for i, r in enumerate(out):
        r["id"] = f"{typ}{i}"
    return out


def mtm_curve(D: dict, t: pd.DataFrame, frm: str, to: str) -> list:
    s = B.daily_mtm(D, t)
    s = s[(s.index >= frm) & (s.index <= to)].cumsum()
    return [[d, round(float(v), 0)] for d, v in s.items()]


def trade_log(views: list) -> pd.DataFrame:
    """The spec's trade log: one row per trade of every view (B: entry = premium sold, stop = stock close below support or the
    premium level of a premium stop)."""
    rows = []
    for key, label, typ, t, q in views:
        if t is None or t.empty:
            continue
        for r in t.itertuples():
            if typ == "A":
                entry, stop = r.entry, r.stop
            else:
                entry = r.prem
                stop = f"stock close < {r.support:.2f}" if q.b_stop == "support" else f"premium >= {r.prem * (2.0 if q.b_stop == 'prem2' else 2.5):.2f}"
            rows.append({"view": key, "date": r.signal_date, "stock": r.symbol, "type": f"{typ} ({r.kind})", "base_length": r.base_n, "range_pct": round(r.height * 100, 2),
                         "volume_multiple": round(r.vm, 2), "entry_date": r.entry_date, "entry": round(entry, 2), "stop": round(stop, 2) if typ == "A" else stop,
                         "strike": getattr(r, "strike", None), "expiry": getattr(r, "expiry", None), "exit_date": r.exit_date, "exit_reason": r.reason,
                         "R_multiple": round(r.R, 3), "gross_pnl": round(r.gross, 2), "net_pnl": round(r.net, 2), "days_held": r.hold})
    return pd.DataFrame(rows)


def spearman(a: pd.Series, b: pd.Series) -> float | None:
    if len(a) < 3:
        return None
    return float(a.rank().corr(b.rank()))


def run(args, status) -> int:
    t0 = time.time()
    say = (lambda m: None) if args.no_telegram else send
    status("Data load (daily store + NSE F&O bhavcopy Oct 2021 - Oct 2026)")
    D = B.load()
    cal = D["cal"]
    W = D["W"]
    a_from = next(d for d, n in zip(cal, W["uni"].sum(axis=1)) if n > 0)
    END = cal[-1]
    months = months_between(a_from, END)
    m_fit, m_oos = months_between(a_from, "2024-10-31"), months_between(SPLIT, END)
    print(f"load {time.time() - t0:.0f}s, universe from {a_from}, {len(W['close'].columns)} stocks ever in top-50, {len(D['missing'])} month-slots skipped (no stock data)", flush=True)
    out = result_dir(NAME)
    say(f"Run shuru: {TITLE}, top-50 F&O stocks (point-in-time). A (stock long) aur B (put SELL) dono {a_from} - {END}. Folder {out.name}.")

    cache = {}

    def sigs(q, cap=True):
        k = (tuple(getattr(q, f) for f in SETUP), cap)
        if k not in cache:
            cache[k] = B.selloffs(D, q, cap) if q.signal == "selloff" else B.signals(D, q, cap)
        return cache[k]

    def run_a(q):
        return B.book_a(D, B.path_a(D, sigs(q), q), q)

    def run_b(q):
        return B.book_b(D, B.path_b(D, sigs(q), q), q)

    def part(t, lo=None, hi=None):
        if t is None or t.empty:
            return t
        m = np.ones(len(t), dtype=bool)
        if lo:
            m &= (t.entry_date >= lo).to_numpy()
        if hi:
            m &= (t.entry_date < hi).to_numpy()
        return t[m]

    def check_row(label, typ, t, q):
        s = B.stats(t, q.capital, months)
        f, o = part(t, hi=SPLIT), part(t, lo=SPLIT)
        fn = float(f.net.sum()) if f is not None and len(f) else 0.0
        on = float(o.net.sum()) if o is not None and len(o) else 0.0
        return [label, typ, s["trades"], s["win_pct"], s["net"], s["profit_factor"], s["max_dd"], s["avg_r"], s["worst"], fn, on, s["month_ret"],
                q.capital != P.capital]

    # ---- main tests + one-at-a-time checks
    status("Main tests (A, B, sell-off, futures) + extra checks")
    pa, pb, pso = B.path_a(D, sigs(P), P), B.path_b(D, sigs(P), P), B.path_b(D, sigs(SO), SO)
    A, Bt, Bso = B.book_a(D, pa, P), B.book_b(D, pb, P), B.book_b(D, pso, SO)
    Afut = run_a(W_(P, a_inst="fut"))
    Bphys = run_b(W_(P, settle="physical"))
    print(f"main {time.time() - t0:.0f}s: A {len(A)}, B {len(Bt)}, B sell-off {len(Bso)}, A futures {len(Afut)}", flush=True)
    checks = []
    for i, c in enumerate(CHECKS):
        if len(c) == 1:
            checks.append(list(c))
            continue
        label, typ, q = c
        checks.append(check_row(label, typ, run_a(q) if typ == "A" else run_b(q), q))
        if i % 10 == 0:
            status(f"Extra checks {i}/{len(CHECKS)}")
            print(f"check {i}/{len(CHECKS)} {time.time() - t0:.0f}s", flush=True)

    # ---- fit grid: choose on the first 60%, judge on the last 40%
    grids = {}
    chosen = {}
    if not args.quick:
        for typ, inner, runner in (("A", GRID_A, run_a), ("B", GRID_B, run_b)):
            rows = []
            total = len(GRID_SETUP) * len(inner)
            for gi, g0 in enumerate(GRID_SETUP):
                for g1 in inner:
                    q = W_(P, **g0, **g1)
                    t = runner(q)
                    f, o = part(t, hi=SPLIT), part(t, lo=SPLIT)
                    sf, so = B.stats(f, q.capital, m_fit), B.stats(o, q.capital, m_oos)
                    rows.append({**g0, **g1, "fit_n": sf["trades"], "fit_net": sf["net"], "fit_pf": sf["profit_factor"], "fit_r": sf["avg_r"],
                                 "oos_n": so["trades"], "oos_net": so["net"], "oos_pf": so["profit_factor"], "oos_r": so["avg_r"]})
                status(f"Grid {typ}: {(gi + 1) * len(inner)}/{total} settings")
                print(f"grid {typ} {(gi + 1) * len(inner)}/{total} {time.time() - t0:.0f}s", flush=True)
            g = pd.DataFrame(rows)
            g.to_csv(out / f"grid_{typ}.csv", index=False)
            elig = g[g.fit_n >= MIN_FIT[typ]]
            best = elig.sort_values(["fit_net", "fit_n"], ascending=False).iloc[0] if len(elig) else None
            top = elig.sort_values("fit_net", ascending=False).head(10)
            g["oos_rank"] = g.oos_net.rank(ascending=False, method="min")
            qb = W_(P, **{k: (None if isinstance(best[k], float) and np.isnan(best[k]) else best[k]) for k in (*GRID_SETUP[0], *inner[0])}) if best is not None else None
            if qb is not None:
                qb = W_(qb, base_n=int(qb.base_n), max_days=int(qb.max_days) if typ == "A" else qb.max_days, dte=int(qb.dte))
            chosen[typ] = qb
            grids[typ] = {"n": int(len(g)), "eligible": int(len(elig)), "min_fit": MIN_FIT[typ],
                          "fit_pos": float((g.fit_net > 0).mean() * 100), "oos_pos": float((g.oos_net > 0).mean() * 100), "both_pos": float(((g.fit_net > 0) & (g.oos_net > 0)).mean() * 100),
                          "rank_corr": spearman(elig.fit_net, elig.oos_net), "best": describe(typ, qb) if qb is not None else None,
                          "best_fit": [int(best.fit_n), float(best.fit_net), best.fit_pf, best.fit_r] if best is not None else None,
                          "best_oos": [int(best.oos_n), float(best.oos_net), best.oos_pf, best.oos_r] if best is not None else None,
                          "best_oos_rank": int(g.loc[best.name, "oos_rank"]) if best is not None else None,
                          "top10_oos_median": float(top.oos_net.median()) if len(top) else None, "top10_oos_pos": int((top.oos_net > 0).sum()) if len(top) else 0,
                          "top": [[describe(typ, W_(P, **{k: (None if isinstance(r[k], float) and np.isnan(r[k]) else r[k]) for k in (*GRID_SETUP[0], *inner[0])})),
                                   int(r.fit_n), float(r.fit_net), r.fit_pf, int(r.oos_n), float(r.oos_net), r.oos_pf] for _, r in top.iterrows()],
                          "oos_all_median": float(g.oos_net.median())}

    # ---- views
    views = [("a", "A: stock long (main)", "A", A, P), ("b", "B: put SELL (main)", "B", Bt, P), ("bso", "B: sell-off ke baad put SELL", "B", Bso, SO),
             ("af", "A: futures (lots, Rs 10L)", "A", Afut, W_(P, a_inst="fut")), ("bp", "B: physical delivery variant", "B", Bphys, W_(P, settle="physical"))]
    if chosen.get("A") is not None:
        views.insert(1, ("ag", "A: fit pe chuni setting", "A", run_a(chosen["A"]), chosen["A"]))
    if chosen.get("B") is not None:
        views.insert(4, ("bg", "B: fit pe chuni setting", "B", run_b(chosen["B"]), chosen["B"]))

    # ---- study + claims
    status("Breakout study (80% daawa, fail rate) + tail risk")
    st = B.breakout_study(D, P)
    st = st[st.fail5.notna()]
    bins = [(1.5, 2, "1.5x - 2x"), (2, 3, "2x - 3x"), (3, 5, "3x - 5x"), (5, 1e9, "5x se zyada"), (1.5, 1e9, "Saare (1.5x+)")]
    study_rows = []
    for lo_, hi_, lb in bins:
        g = st[(st.vm >= lo_) & (st.vm < hi_)]
        r = g.r_ps.dropna()
        h1 = g.hit1R.dropna().astype(bool)
        h2 = g.hit2R.dropna().astype(bool)
        study_rows.append([lb, int(len(g)), float(g.fail5.astype(bool).mean() * 100) if len(g) else None, float(h1.mean() * 100) if len(h1) else None,
                           float(h2.mean() * 100) if len(h2) else None, float(g.fwd5.mean()) if len(g) else None, float(g.fwd10.mean()) if len(g) else None,
                           float(g.fwd20.mean()) if len(g) else None, float((r > 0).mean() * 100) if len(r) else None, float(r.mean()) if len(r) else None])
    study_reg = []
    for lb, m in (("Nifty 200 DMA ke upar", st.bull == True), ("Nifty 200 DMA ke neeche", st.bull == False)):  # noqa: E712
        g = st[m]
        r = g.r_ps.dropna()
        study_reg.append([lb, int(len(g)), float(g.fail5.astype(bool).mean() * 100) if len(g) else None, float(g.fwd10.mean()) if len(g) else None,
                          float((r > 0).mean() * 100) if len(r) else None, float(r.mean()) if len(r) else None])

    def month_claim(t, p):
        if t is None or t.empty:
            return None
        s = B.daily_mtm(D, t)
        s = s[(s.index >= a_from) & (s.index <= END)]
        mo = s.groupby(s.index.str[:7]).sum() / p.capital * 100
        mo = mo.reindex([m for m in sorted({d[:7] for d in cal if a_from <= d <= END})], fill_value=0.0)
        avg = float(mo.mean())
        return {"avg": avg, "median": float(mo.median()), "ge4": int((mo >= 4).sum()), "n": int(len(mo)), "pos": int((mo > 0).sum()), "best": float(mo.max()), "worst": float(mo.min()),
                "double": float(np.log(2) / np.log(1 + avg / 100)) if avg > 0 else None, "x18": float((1 + avg / 100) ** 18) if avg > -100 else None}

    claims = {k: month_claim(t, q) for k, _, _, t, q in views}

    # ---- tail risk (B views)
    tails = {}
    for key, label, typ, t, q in views:
        if typ != "B" or t is None or t.empty:
            continue
        mt = B.daily_mtm(D, t)
        cum = mt.cumsum()
        dd = cum - cum.cummax().clip(lower=0)
        open_n = pd.Series(0, index=cal)
        for r in t.itertuples():
            open_n.iloc[r.e:r.x + 1] += 1
        worst_days = mt.sort_values().head(10)
        wt = t.sort_values("net").head(10)
        gs = B.gap_stress(D, t, q)
        stress = []
        for gp, g in gs.groupby("gap"):
            stress.append([f"{gp * 100:g}% gap-down", float(g.x_prem.mean()), float(g.x_prem.min()), float(g.x_risk.mean()), float(g.loss.min()), float(g.pct_cap.min())])
        # all open puts hit together on the day with most open puts
        pk_day = int(open_n.to_numpy().argmax())
        pk_open = t[(t.e <= pk_day) & (t.x >= pk_day)]
        port = []
        for gp in (0.05, 0.10, 0.15, 0.20):
            loss = 0.0
            for r in pk_open.itertuples():
                T1 = max(B._years(cal[pk_day], r.expiry), 1 / 365)
                spot = W["close"].iloc[pk_day, r.j] * r.factor
                v = B.put_value(spot * (1 - gp), r.strike, T1, r.iv * 1.5)
                v0 = r.vals[r.days.index(pk_day)] if pk_day in r.days else r.vals[-1]
                loss -= r.qty * (v + max(q.opt_slip * v, q.opt_slip_min) - v0)
            port.append([f"{gp * 100:g}%", float(loss), float(loss / q.capital * 100)])
        # real gap-downs while a put was open
        O, C = W["open"].to_numpy(), W["close"].to_numpy()
        hits = []
        for r in t.itertuples():
            g = [(O[k, r.j] / C[k - 1, r.j] - 1) * 100 for k in range(r.e + 1, r.x + 1) if C[k - 1, r.j] == C[k - 1, r.j] and O[k, r.j] == O[k, r.j]]
            if g and min(g) <= -3:
                hits.append([r.symbol, r.entry_date, r.exit_date, round(min(g), 2), r.reason, float(r.net), float(r.loss_x_prem)])
        tails[key] = {"worst_trades": [[r.symbol, r.entry_date, r.exit_date, r.strike, r.reason, float(r.net), float(r.loss_x_prem), float(r.R)] for r in wt.itertuples()],
                      "worst_days": [[d, float(v), int(open_n[d])] for d, v in worst_days.items()], "max_dd": float(dd.min()), "dd_day": dd.idxmin(),
                      "stress": stress, "port": port, "port_day": cal[pk_day], "port_n": int(len(pk_open)), "gaps": hits,
                      "loss_mult": [float(x) for x in t.loss_x_prem.round(3)], "worst_mult": float(t.loss_x_prem.min()),
                      "nifty_worst": float((D["nifty"].close.pct_change() * 100).loc[a_from:END].min())}

    # ---- report data
    status("Report bana raha hoon")
    skip_counts = {}
    for k, lb, typ, t, q in views:
        skip_counts[k] = t.attrs.get("skips", {}) if t is not None else {}
    funnel_a = pa.skip.value_counts().to_dict() if len(pa) else {}
    funnel_b = pb.skip.value_counts().to_dict() if len(pb) else {}
    miss = D["missing"]
    miss_txt = ", ".join(f"{s} ({n} mahine)" for s, n in miss.groupby("symbol").size().sort_values(ascending=False).items()) if len(miss) else "koi nahi"
    lot_info = f"{sum(1 for k in D['lots'] if k[1] < '2024-07-08')} purane contracts ka lot turnover se nikala"
    data_info = [["Stock data", f"Daily OHLCV (fast store, split / bonus adjusted), {cal[0]} se {cal[-1]}; stock list {a_from} se (60 din turnover chahiye)"],
                 ["Option data", "NSE F&O bhavcopy (daily, expired contracts bhi), Oct 2021 - Oct 2026: stock puts, 2 sabse paas wali expiry, sirf trade hue strikes. "
                                 "Jul 2024 se pehle purana format (NSE archive), uske baad UDiFF."],
                 ["Lot size", lot_info + "; Jul 2024 se NSE file me diya hua"],
                 ["F&O list", "har mahine ki pehli tarikh ki futures list (point-in-time, Oct 2021 se); ticker badle to aaj ka naam (ZOMATO -> ETERNAL, LTI -> LTIM -> LTM)"],
                 ["Stock data nahi (chhode)", miss_txt],
                 ["Breakouts", f"saare filters ke baad {len(sigs(P, False))} breakout, mahine ki limit ke baad {len(sigs(P))}; sell-off signal {len(sigs(SO))}"],
                 ["Type A chhode", ", ".join(f"{k} {v}" for k, v in {**funnel_a, **skip_counts.get('a', {})}.items()) or "koi nahi"],
                 ["Type B chhode", ", ".join(f"{k} {v}" for k, v in {**funnel_b, **skip_counts.get('b', {})}.items()) or "koi nahi"],
                 ["Nifty regime", "Nifty 50 ka 200 din ka average (Jan - Sep 2021 NSE index file se, phir 1m data)"],
                 ["Survivorship", "stock data AMFI Jun-2026 list ka; merge / delist hue F&O stocks ka data nahi (upar list)"]]

    vdata = []
    for key, label, typ, t, q in views:
        sub = label + " | " + ("breakout (20 din base, volume 2x) | agle din open pe kharid, stop base top ke neeche, target 1:2, 10 din time stop" if key == "a" else describe(typ, q))
        vdata.append({"key": key, "label": label, "type": typ, "sub": sub, "chips": chips_of(typ, q), "inv": inv_of(typ, t, q, cal), "from": a_from, "to": END,
                      "months": months, "split": SPLIT, "capital": q.capital, "trades": trade_dicts(t, typ, D), "mtm": mtm_curve(D, t, a_from, END) if t is not None and len(t) else [],
                      "claim": claims.get(key), "tail": tails.get(key)})
    series = series_for(D, [v[3] for v in views])
    blob = {"title": TITLE, "capital": P.capital, "data_range": [cal[0], cal[-1]], "settings": SETTINGS, "views": vdata, "checks": checks, "grids": grids,
            "study": study_rows, "study_reg": study_reg, "rules": RULES, "notes": NOTES, "formulas": FORMULAS, "data_info": data_info, "series": series,
            "split": SPLIT, "m_fit": m_fit, "m_oos": m_oos, "data_note": "Daily stock candles (fast store) + NSE stock option bhavcopy (Oct 2021 - Oct 2026)",
            "variants": int(len(CHECKS) - sum(1 for c in CHECKS if len(c) == 1) + sum(g["n"] for g in grids.values()))}
    path = write_html(out / "report.html", blob, template="breakout2_template.html")
    for key, _, typ, t, _ in views:
        if t is not None and len(t):
            t.drop(columns=[c for c in ("days", "vals") if c in t]).to_csv(out / f"trades_{key}.csv", index=False)
    trade_log(views).to_csv(out / "trade_log.csv", index=False)
    pd.DataFrame([c[:12] for c in checks if len(c) > 1], columns=["check", "type", "trades", "win_pct", "net", "pf", "max_dd", "avg_r", "worst", "net_fit", "net_oos", "month_ret"]).to_csv(out / "checks.csv", index=False)
    st.to_csv(out / "study.csv", index=False)

    # ---- summary
    def line(lb, t, q):
        s = B.stats(t, q.capital, months)
        f, o = part(t, hi=SPLIT), part(t, lo=SPLIT)
        fn = f.net.sum() if f is not None and len(f) else 0
        on = o.net.sum() if o is not None and len(o) else 0
        return (f"{lb}: {s['trades']} trade ({s['per_month']:.1f}/mahina), win {s['win_pct'] or 0:.0f}%, net Rs {s['net']:,.0f} ({s['ret_pct']:.1f}%, {s['month_ret']:.2f}%/mahina), "
                f"PF {s['profit_factor'] or 0:.2f}, avg R {s['avg_r'] or 0:.2f}, sabse bura trade Rs {s['worst']:,.0f} | fit Rs {fn:,.0f}, out-of-sample Rs {on:,.0f}")
    lines = [line(lb, t, q) for _, lb, _, t, q in views]
    rb = []
    for key, lb, typ, t, q in views:
        if typ == "B" and t is not None and len(t):
            rb.append(f"{lb} return: premium pe {t.net.sum() / t.premium_rs.sum() * 100:.1f}%, margin pe {t.net.sum() / t.margin.sum() * 100:.2f}%, notional pe {t.net.sum() / t.notional.sum() * 100:.3f}%; "
                      f"sabse bura trade premium ka {t.loss_x_prem.min():.1f}x; max DD (MTM) Rs {tails[key]['max_dd']:,.0f}")
    allb = study_rows[-1]
    gl = []
    for typ, g in grids.items():
        gl.append(f"Grid {typ}: {g['n']} setting, fit me profit {g['fit_pos']:.0f}%, out-of-sample me profit {g['oos_pos']:.0f}%, rank corr {g['rank_corr'] if g['rank_corr'] is None else round(g['rank_corr'], 2)}; "
                  f"chuni setting [{g['best']}] fit Rs {g['best_fit'][1]:,.0f} -> out-of-sample Rs {g['best_oos'][1]:,.0f} (rank {g['best_oos_rank']}/{g['n']}); "
                  f"fit ki top 10 ka out-of-sample median Rs {g['top10_oos_median']:,.0f}, {g['top10_oos_pos']}/10 profit me")
    cl = [f"{k}: avg {c['avg']:.2f}%/mahina, {c['ge4']}/{c['n']} mahine 4%+, double " + ("kabhi nahi" if c["double"] is None else f"{c['double']:.0f} mahine")
          for k, c in claims.items() if c]
    summary = "\n".join(lines + rb + [f"Study: {allb[1]} breakout (1.5x+), 5 din me fail {allb[2]:.0f}%, +1R pehle {allb[3]:.0f}%, A jeeta {allb[8]:.0f}%"] + gl + cl + ["", "Checks:"]
                        + [f"{c[1]} {c[0]}: {c[2]} trade, net Rs {c[4]:,.0f}, PF {c[5] or 0:.2f}, avg R {c[7] or 0:.2f}, fit Rs {c[9]:,.0f}, oos Rs {c[10]:,.0f}" for c in checks if len(c) > 1]
                        + ["", "Study:"] + [f"{r[0]}: {r[1]} breakout, fail {r[2] or 0:.0f}%, +1R {r[3] or 0:.0f}%, +2R {r[4] or 0:.0f}%, 10 din {r[6] or 0:.2f}%, A win {r[8] or 0:.0f}%, avg R {r[9] or 0:.2f}" for r in study_rows])
    (out / "summary.txt").write_text(summary, encoding="utf-8")
    print(summary, flush=True)
    msg = (f"{TITLE} ka result {TAG_TRADING}\n" + "\n".join(lines[:4]) + "\n" + "\n".join(rb[:2]) + "\n"
           f"80% daawa: {allb[1]} breakout (volume 1.5x+) me {100 - (allb[2] or 0):.0f}% 5 din tike, +1R stop se pehle {allb[3]:.0f}%, type A trade {allb[8]:.0f}% jeeta.\n"
           + "\n".join(gl) + f"\nCapital Rs 10L fixed, compounding nahi, risk 1%/trade, slippage + kharcha chalu. Kul {blob['variants']} variant try kiye.")
    (out / "telegram.txt").write_text(msg, encoding="utf-8")
    if not args.no_telegram:
        send(msg)
        send_document(path, f"{TITLE} report ({out.name})")
    print(f"done {time.time() - t0:.0f}s -> {out}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--quick", action="store_true", help="no grid")
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

"""Breakout + put selling (Mitesh Patel interview rulebook) on the top-50 liquid F&O stocks: type A stock long (2022 - Oct 2026)
and type B put SELL (Nov 2024 - Oct 2026, stock option data starts there) on the same breakouts; every assumed number changed
one at a time; breakout study for the '~80% success' claim; report + Telegram.

    python tools/run_breakout.py
    python tools/run_breakout.py --no-telegram
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest import breakout as B  # noqa: E402
from backtest.breakout_report import write_html  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
NAME = "breakout_put_selling"
TITLE = "Breakout + Put Selling"
P = B.Params()
W_ = B.with_params
SPLIT_A, SPLIT_B = "2024-11-01", "2025-11-01"
SETUP = ("top_n", "base_n", "max_height", "max_body", "vol_mult", "vol_n", "accum", "candle", "close_top", "body_mult", "red_mult", "morning_star", "per_month")

CHECKS = [
    ["Setup (breakout) ke numbers - A aur B dono pe asar"],
    ("Main test", "A", P), ("Main test", "B", P),
    ("Base 40 din", "A", W_(P, base_n=40)), ("Base 60 din", "A", W_(P, base_n=60)),
    ("Range height 8% tak", "A", W_(P, max_height=0.08)), ("Range height 15% tak", "A", W_(P, max_height=0.15)),
    ("Volume 1.5x", "A", W_(P, vol_mult=1.5)), ("Volume 1.5x", "B", W_(P, vol_mult=1.5)), ("Volume 3x", "A", W_(P, vol_mult=3.0)), ("Volume 3x", "B", W_(P, vol_mult=3.0)),
    ("Chhoti candle filter band", "A", W_(P, max_body=None)), ("Accumulation (volume badhna) filter band", "A", W_(P, accum=False)),
    ("Breakout candle filter band", "A", W_(P, candle=False)), ("Morning Star filter chalu", "A", W_(P, morning_star=True)),
    ("Mahine me max 3 trade", "A", W_(P, per_month=3)), ("Mahine ki limit nahi", "A", W_(P, per_month=None)), ("Mahine ki limit nahi", "B", W_(P, per_month=None)),
    ["Type A: entry, stop, exit"],
    ("Entry breakout din ke close pe", "A", W_(P, entry="close")), ("Stop breakout candle ke low ke neeche", "A", W_(P, stop="low")),
    ("Target 10%", "A", W_(P, rr=None, tgt_pct=0.10)), ("Target 1:3", "A", W_(P, rr=3.0)),
    ("Time stop 5 din", "A", W_(P, max_days=5)), ("Time stop 20 din", "A", W_(P, max_days=20)),
    ("Trailing SL 5 din ka low (target nahi, max 60 din)", "A", W_(P, exit="trail5", rr=None, max_days=60)),
    ("Trailing SL 10 din ka low (target nahi, max 60 din)", "A", W_(P, exit="trail10", rr=None, max_days=60)),
    ("Add-on: naya high bane to 50% aur, stop entry pe", "A", W_(P, addon=True)), ("Risk 0.5% / trade", "A", W_(P, risk=0.005)),
    ("Slippage 0", "A", W_(P, slip=0.0)), ("Slippage 2x (0.1%)", "A", W_(P, slip=0.001)),
    ["Type B: put selling"],
    ("Expiry 5 din door", "B", W_(P, dte=5)), ("Expiry 10 din door", "B", W_(P, dte=10)),
    ("Strike: support se 1 strike neeche", "B", W_(P, strike="below")), ("Strike: 5% OTM", "B", W_(P, strike="otm5")),
    ("SL: premium 2x", "B", W_(P, b_stop="prem2")), ("SL: loss 1.5x premium", "B", W_(P, b_stop="loss15")),
    ("50% premium kamate hi band", "B", W_(P, take=0.5)), ("70% premium kamate hi band", "B", W_(P, take=0.7)),
    ("Risk 0.5% / trade", "B", W_(P, risk=0.005)), ("Slippage 0", "B", W_(P, opt_slip=0.0)), ("Slippage 2x (premium ka 4%)", "B", W_(P, opt_slip=0.04)),
]

SETTINGS = [["Stock list", "top 50 F&O (har mahine 60 din ka turnover)", 1], ["Data", "daily candles (split / bonus adjusted) + NSE stock option bhavcopy"],
            ["Capital", "Rs 10,00,000 fixed, compounding nahi", 1], ["Risk / trade", "1% = Rs 10,000", 1], ["Mahine me", "max 4 trade (sabse zyada volume pehle)"],
            ["Slippage", "stock 0.05%, option premium ka 2% (har fill)", 1], ["Kharcha", "STT, exchange, SEBI, stamp, GST, DP; option Rs 20/order"]]
RULES = [
    "Stock list: har mahine ki pehli tarikh ko F&O stocks me se pichhle 60 din ke average turnover (close x volume) ke hisaab se top 50. "
    "Nov 2024 se pehle ki F&O list hamare paas nahi, isliye us se pehle wo saare stocks liye jo kabhi F&O me rahe.",
    "Base (range): pichhle 20 trading din. Resistance = un 20 din ka sabse uncha high, range low = sabse neecha low. Range ki unchai (resistance - low) / resistance 12% se kam ho. "
    "Candles chhoti: un 20 din me |close - open| ka average price ka 1.5% se kam.",
    "Accumulation: base ke aakhri 10 din ka average volume pehle 10 din se zyada.",
    "Breakout: din ka close resistance ke upar, aur us din ka volume pichhle 20 din ke average ka kam se kam 2 guna. Breakout candle badi ho aur high ke paas band ho: "
    "close din ki range ke upar wale 25% hisse me, body (close - open) base ki average body ka 1.5 guna ya zyada. Breakout se pehle wala din badi laal candle na ho (body average ke 2 guna se kam).",
    "Ek stock ka breakout aaya to agle 20 din us stock ka naya breakout nahi ginte (naya base banne do). Mahine me max 4 trade; ek din me zyada signal ho to jiska volume multiple sabse zyada, wo pehle.",
    "Type A (stock long): breakout ke agle din open pe kharido. Stop = resistance se 0.5% neeche (toota hua resistance ab support). Target = entry + 2 x (entry - stop). "
    "10 trading din me na stop laga na target, to 10ve din close pe bahar. Ek hi candle me stop aur target dono chhue to stop maana; gap se paar khula to open pe fill.",
    "Type A size: qty = capital ka 1% / (entry - stop). Ek stock me capital ka max 50%. Sab positions milake capital se zyada nahi (cash, leverage nahi).",
    "Type B (put SELL): wahi breakout. Put tab becho jab sabse paas wali expiry 2 se 7 trading din door ho - breakout ke din, ya agle 15 trading din ke andar jab tak har din close support ke upar rahe. "
    "Strike = support (resistance) ke barabar ya usse neeche wala sabse paas ka strike jisme us din trade hua. Premium Rs 1 se kam to trade nahi.",
    "Type B exit: stock ka close support ke neeche gaya to usi din close pe put wapas kharido (stop). Warna expiry ke din close pe band (OTM aur trade nahi hua to bekaar expire, kharidna nahi padta).",
    "Type B size: lots = capital ka 1% / (premium x lot size) - maana ki stop pe premium double hoga. Margin = strike x qty ka 20%; sab khule puts ka margin capital se zyada nahi.",
    "Kharcha: stock delivery pe STT 0.1% dono taraf, exchange, SEBI, stamp, GST, DP charge; slippage 0.05% har fill. Option pe Rs 20 / order, STT sell pe 0.1% (1 Apr 2026 se 0.15%), "
    "exchange, stamp, GST; slippage premium ka 2% har fill.",
    "Capital Rs 10 lakh, fixed (profit dobara nahi lagaya, compounding nahi). A aur B do alag test hain: same breakouts, trade karne ke do tareeke.",
]
NOTES = [
    "Interview summary me koi exact number nahi tha (volume kitna guna, base kitne din, strike, stop). Jahan rulebook ne 3 value di wahan beech wali li; baaki value 'Extra checks' me hain.",
    "Base: beech wali value 40 din thi, par 40 din pe 5 saal me sirf 26 breakout aaye (0.4 / mahina), jabki summary kehta hai 3-4 trade / mahina. Isliye 20 din liya - "
    "ye trade ki ginti se milane ke liye chuna, P&L dekh ke nahi. 40 aur 60 din Extra checks me hain.",
    "Din ke beech resistance todna (intraday break) test nahi kiya: breakout ka volume poore din ka hai, wo close pe hi pata chalta hai. Data bhi daily hai.",
    "Stock options ka data Nov 2024 se hi hai, isliye type B sirf 2 saal (Nov 2024 - Oct 2026). Type A 2022 se; A ka Nov 2024 se wala view alag se diya hai taaki dono ki tulna same period pe ho.",
    "Option data daily bhavcopy hai: sirf us din trade hue strikes ka close. Stop din ke close pe hi check (intraday nahi). Jis din put me trade nahi hua us din stop ka price = "
    "max(intrinsic, pichhla close). Expiry din ITM put close pe wapas kharida (physical delivery se bachne ke liye).",
    "Margin 20% x strike x qty ek andaza hai (asli SPAN alag hota hai). Isliye 'return on margin' andaza hai.",
    "Tata Global example purana hai (date nahi, 280 vs 335 ka mismatch) - test me nahi liya. Infosys wala 'sell-off ke baad support' setup bhi nahi liya: rulebook kehta hai sirf ek setup; ye sirf breakout test hai. "
    "Bearish (call selling) bhi test nahi.",
    "'8% in 5 din' kis cheez pe tha, saaf nahi - isliye type B ka return premium, margin aur notional teeno pe dikhaya hai.",
    "'~80% success' check: Saransh me 'Breakout study' - har breakout (mahine ki limit ke bina, volume 1.5x ya zyada) kitni baar 5 din tak resistance ke upar tika, aur type A trade kitni baar jeeta.",
    "Nov 2024 se pehle stock list me wo stocks bhi hain jo baad me F&O me aaye (survivorship bias). Prices split / bonus adjusted hain; option strike asli (bina adjust) price pe milaye gaye.",
    "Daily candle me ek hi din stop aur target dono chhue to stop maana (bura case).",
]
FORMULAS = [
    ["Resistance", "max(high) pichhle 20 din (aaj ko chhod ke)"], ["Range low", "min(low) pichhle 20 din"], ["Range height", "(resistance - range low) / resistance <= 12%"],
    ["Chhoti candles", "average(|close - open| / close) pichhle 20 din <= 1.5%"], ["Volume multiple", "aaj ka volume / average(volume pichhle 20 din) >= 2"],
    ["Accumulation", "average(volume pichhle 10 din) > average(volume usse pehle ke 10 din)"],
    ["Breakout candle", "close > resistance; close >= low + 0.75 x (high - low); (close - open) / close >= 1.5 x average body; kal ki (open - close) / close <= 2 x average body"],
    ["A: entry", "agle din ka open; fill = open x (1 + 0.05%)"], ["A: stop", "resistance x (1 - 0.5%)"], ["A: target", "entry + 2 x (entry - stop)"],
    ["A: exit fill", "stop / target price (gap me open), time stop pe close; fill = price x (1 - 0.05%)"],
    ["A: qty", "floor(min(capital x 1% / (entry fill - stop), capital x 50% / entry fill, khali cash / entry fill))"],
    ["A: P&L", "qty x (exit fill - entry fill) - kharcha"],
    ["Stock kharcha", "STT 0.1% x (buy + sell) + exchange 0.00307% + SEBI 0.0001% + stamp 0.015% x buy + GST 18% x (exchange + SEBI) + DP Rs 15.34 + GST"],
    ["B: entry din", "pehla din (breakout se 15 din ke andar) jab trading din(expiry) - trading din(aaj) 2 se 7 ho aur close >= support"],
    ["B: strike", "max(strike) jahan strike <= support (asli price me) aur us din trade hua"],
    ["B: lots", "floor(capital x 1% / (premium fill x lot size)); margin = 20% x strike x lot x lots"],
    ["B: fill", "sell = premium x (1 - 2%), buy back = price x (1 + 2%)"], ["B: P&L", "qty x (sell fill - buy fill) - kharcha"],
    ["Option kharcha", "Rs 20 x orders + STT sell x 0.1% (1 Apr 2026 se 0.15%) + exchange 0.03503% + SEBI 0.0001% + stamp 0.003% x buy + GST 18% x (brokerage + exchange + SEBI)"],
    ["B: return", "premium pe = net / premium; margin pe = net / margin; notional pe = net / (strike x qty)"],
    ["R multiple", "A: net / (qty x (entry fill - stop)); B: net / (premium fill x qty)"], ["Win rate", "net > 0 wale trade / kul trade x 100"],
    ["Profit factor", "jeete trades ka kul profit / hare trades ka kul loss"], ["Max drawdown", "exit din ke hisaab se kul net P&L ki curve ka sabse bada gir (peak se)"],
    ["Mahine ka average return", "net / Rs 10,00,000 / mahine x 100 (compounding nahi)"], ["Return %", "net / Rs 10,00,000 x 100"],
    ["5 din tika (study)", "breakout ke baad agle 5 din har close >= resistance"],
]


def chips_of(typ: str, p: B.Params) -> list:
    setup = [["Base", f"{p.base_n} din, height <= {p.max_height * 100:g}%", 1], ["Volume", f">= {p.vol_mult:g}x (20 din avg)", 1],
             ["Filters", ", ".join(x for x, on in (("chhoti candles", p.max_body is not None), ("accumulation", p.accum), ("badi breakout candle", p.candle), ("Morning Star", p.morning_star)) if on) or "koi nahi"]]
    if typ == "A":
        ex = (f"target 1:{p.rr:g}" if p.rr else f"target {p.tgt_pct * 100:g}%") if p.exit == "target" else f"trailing {p.exit[5:]} din ka low"
        return setup + [["Type", "A: stock long", 1], ["Entry", "agle din open" if p.entry == "next_open" else "breakout din close", 1],
                        ["Stop", "resistance - 0.5%" if p.stop == "res" else "breakout candle low - 0.5%"], ["Exit", f"{ex}, time stop {p.max_days} din", 1],
                        ["Add-on", "chalu" if p.addon else "band"], ["Max / stock", f"{p.max_val * 100:g}% capital"]]
    st = {"support": "support pe / neeche", "below": "support se 1 strike neeche", "otm5": "5% OTM"}[p.strike]
    sl = {"support": "stock support ke neeche close", "prem2": "premium 2x", "loss15": "loss 1.5x premium"}[p.b_stop]
    return setup + [["Type", "B: put SELL", 1], ["Strike", st, 1], ["Expiry", f"2 - {p.dte} trading din door", 1], ["SL", sl, 1],
                    ["Exit", f"{p.take * 100:g}% premium kamate hi" if p.take else "expiry din close"], ["Margin", f"{p.margin_pct * 100:g}% x strike x qty"]]


def months_between(a: str, b: str) -> int:
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7]) + 1


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
    if typ == "A":
        pk, n = peak_open(t, "notional", cal)
        return [["Shuruaati capital", f"Rs {p.capital:,.0f} (fixed, compounding nahi)"], ["Risk har trade", f"{p.risk * 100:g}% = Rs {p.capital * p.risk:,.0f}"],
                ["Qty ka rule", "capital x risk / (entry - stop), ek stock me max 50% capital"], ["Max khule trade", f"jitna cash ho (leverage nahi); sabse zyada ek saath: {n}"],
                ["Average / sabse badi position", f"Rs {t.notional.mean():,.0f} / Rs {t.notional.max():,.0f}" if len(t) else "n/a"],
                ["Sabse zyada paisa ek din me laga", f"Rs {pk:,.0f} ({pk / p.capital * 100:.0f}% capital)"]]
    pk, n = peak_open(t, "margin", cal)
    return [["Shuruaati capital", f"Rs {p.capital:,.0f} (fixed, compounding nahi)"], ["Risk har trade", f"{p.risk * 100:g}% = Rs {p.capital * p.risk:,.0f} (andaza: premium double)"],
            ["Lots ka rule", "capital x risk / (premium x lot size)"], ["Margin", f"{p.margin_pct * 100:g}% x strike x qty (andaza); sab milake capital tak"],
            ["Max khule puts ek saath", str(n)], ["Average premium mila / trade", f"Rs {t.premium_rs.mean():,.0f}" if len(t) else "n/a"],
            ["Average / sabse bada margin", f"Rs {t.margin.mean():,.0f} / Rs {t.margin.max():,.0f}" if len(t) else "n/a"],
            ["Sabse zyada margin ek din me", f"Rs {pk:,.0f} ({pk / p.capital * 100:.0f}% capital)"]]


def check_row(label: str, typ: str, t: pd.DataFrame, months: int, split: str, capital: float) -> list:
    s = B.stats(t, capital, months)
    fit = float(t[t.entry_date < split].net.sum()) if len(t) else 0.0
    ver = float(t[t.entry_date >= split].net.sum()) if len(t) else 0.0
    return [label, typ, s["trades"], s["win_pct"], s["net"], s["profit_factor"], s["max_dd"], s["avg_r"], s["worst"], fit, ver]


def series_for(D: dict, trades: list[pd.DataFrame], before: int = 30, after: int = 6) -> dict:
    """Daily candles (adjusted) of every traded stock around its trades: {symbol: [[date, o, h, l, c], ...]}."""
    W = D["W"]
    cal = list(W["close"].index)
    idx = {d: i for i, d in enumerate(cal)}
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
        ok = ~np.isnan(c)
        out[sym] = [[cal[r], round(float(a), 2), round(float(b), 2), round(float(x), 2), round(float(y), 2)] for r, a, b, x, y, g in zip(rows, o, h, l, c, ok) if g]
    return out


def trade_dicts(t: pd.DataFrame, typ: str, D: dict) -> list:
    if t is None or t.empty:
        return []
    keep = {"A": ["symbol", "signal_date", "entry_date", "entry", "stop", "target", "exit_date", "exit", "reason", "hold", "qty", "add_qty", "add_date", "add_px", "notional",
                  "entry_fill", "exit_fill", "risk_rs", "gross", "slip", "costs", "net", "R", "vm", "height", "res", "lo"],
            "B": ["symbol", "signal_date", "entry_date", "expiry", "dte", "strike", "prem", "lots", "lot", "qty", "support", "spot", "factor", "exit_date", "exit", "reason", "hold",
                  "sell_fill", "buy_fill", "premium_rs", "margin", "notional", "risk_rs", "gross", "slip", "costs", "net", "R", "ret_prem", "ret_margin", "ret_notional", "vm",
                  "height", "res", "lo"]}[typ]
    x = t[[c for c in keep if c in t]].copy()
    num = x.select_dtypes("number").columns
    x[num] = x[num].round(4)
    out = x.astype(object).where(x.notna(), None).to_dict("records")
    if typ == "B":
        ch = D["chains"]
        for r in out:
            c = ch.get(r["symbol"], r["expiry"])[0]
            s = c.loc[(c.index >= r["entry_date"]) & (c.index <= r["exit_date"]), r["strike"]].dropna()
            r["prem_series"] = [[d, round(float(v), 2)] for d, v in s.items()]
    for i, r in enumerate(out):
        r["id"] = f"{typ}{i}"
    return out


def run(args, status) -> int:
    t0 = time.time()
    out = result_dir(NAME)
    say = (lambda m: None) if args.no_telegram else send
    say(f"Run shuru: {TITLE} (Mitesh Patel rulebook), top-50 F&O stocks. Type A 2022 - Oct 2026, type B (put sell) Nov 2024 - Oct 2026. Folder {out.name}.")
    status(f"Data load (daily store + stock option bhavcopy) | {out.name}")
    D = B.load()
    W = D["W"]
    cal = list(W["close"].index)
    a_from = next(d for d, n in zip(cal, W["uni"].sum(axis=1)) if n > 0)
    m_a, m_b, m_a2 = months_between(a_from, B.END), months_between(B.OPT_START, B.END), months_between(B.OPT_START, B.END)
    print(f"load {time.time() - t0:.0f}s, universe from {a_from}", flush=True)

    cache = {}

    def sigs(q):
        k = tuple(getattr(q, f) for f in SETUP)
        if k not in cache:
            cache[k] = B.signals(D, q)
        return cache[k]

    def run_a(q, start=None):
        t = B.path_a(D, sigs(q), q)
        if start and len(t):
            t = t[t.skip.notna() | (t.entry_date.fillna("") >= start)]
        return B.size_a(t, q)

    def run_b(q):
        return B.size_b(B.path_b(D, sigs(q), q), q)

    status("Main test + extra checks")
    pa, pb = B.path_a(D, sigs(P), P), B.path_b(D, sigs(P), P)
    A, Bt, A2 = B.size_a(pa, P), B.size_b(pb, P), run_a(P, B.OPT_START)
    checks = []
    for i, c in enumerate(CHECKS):
        if len(c) == 1:
            checks.append(list(c))
            continue
        label, typ, q = c
        t = run_a(q) if typ == "A" else run_b(q)
        checks.append(check_row(label, typ, t, m_a if typ == "A" else m_b, SPLIT_A if typ == "A" else SPLIT_B, q.capital))
        if i % 6 == 0:
            status(f"Extra checks {i}/{len(CHECKS)}")
            print(f"check {i}/{len(CHECKS)} {time.time() - t0:.0f}s", flush=True)

    status("Breakout study (80% claim)")
    st = B.study(D, P)
    st = st[st.held5.notna()]
    bins = [(1.5, 2, "1.5x - 2x"), (2, 3, "2x - 3x"), (3, 5, "3x - 5x"), (5, 1e9, "5x se zyada"), (1.5, 1e9, "Saare (1.5x+)")]
    study_rows = []
    for lo_, hi_, lb in bins:
        g = st[(st.vm >= lo_) & (st.vm < hi_)]
        r = g.r_ps.dropna()
        study_rows.append([lb, int(len(g)), float(g.held5.astype(bool).mean() * 100) if len(g) else None, float(g.fwd10.mean()) if len(g) else None,
                           float((r > 0).mean() * 100) if len(r) else None, float(r.mean()) if len(r) else None])

    # funnel for the Data card
    raw_n, cap_n = len(B.signals(D, P, cap=False)), len(sigs(P))
    skip_a = pa.skip.value_counts().to_dict() if len(pa) else {}
    skip_b = pb.skip.value_counts().to_dict() if len(pb) else {}
    nb_sig = int((sigs(P).date >= B.OPT_START).sum())
    data_info = [["Stock data", f"Daily OHLCV (fast store, split / bonus adjusted), {cal[0]} se {cal[-1]}; stock list {a_from} se (60 din turnover chahiye)"],
                 ["Option data", "NSE stock option bhavcopy (daily, expired contracts bhi), Nov 2024 - Oct 2026: sirf trade hue strikes"],
                 ["F&O list", "Nov 2024 se har mahine futures bhavcopy se (point-in-time); usse pehle saare kabhi-F&O stocks"],
                 ["Breakouts", f"saare filters ke baad {raw_n} breakout (20 din wala niyam laga ke), mahine ki limit ke baad {cap_n}"],
                 ["Type A", f"{len(A)} trade" + (f"; chhode: " + ", ".join(f"{k} {v}" for k, v in skip_a.items()) if skip_a else "") + f"; cash / same stock ki wajah se {len(pa) - sum(skip_a.values()) - len(A)}"],
                 ["Type B", f"Nov 2024 se {nb_sig} breakout -> {len(Bt)} put trade; chhode: " + ", ".join(f"{k} {v}" for k, v in skip_b.items())],
                 ["Survivorship", "Nov 2024 se pehle stock list me aaj ke F&O stocks (baad me aaye stocks bhi)"]]

    views = [("a", "A: stock long (poora period)", "A", A, a_from, m_a, SPLIT_A), ("b", "B: put SELL (Nov 2024 - Oct 2026)", "B", Bt, B.OPT_START, m_b, SPLIT_B),
             ("a2", "A: stock long, sirf Nov 2024 - Oct 2026", "A", A2, B.OPT_START, m_a2, SPLIT_B)]
    vdata = []
    for key, label, typ, t, frm, months, split in views:
        sub = (f"{label} | breakout (20 din base, volume 2x) | " + ("agle din open pe kharid, stop resistance ke neeche, target 1:2, 10 din time stop" if typ == "A"
               else "put SELL support pe / neeche, expiry 2-7 din door, stop = stock support ke neeche close, warna expiry din"))
        vdata.append({"key": key, "label": label, "type": typ, "sub": sub, "chips": chips_of(typ, P), "inv": inv_of(typ, t, P, cal), "from": frm, "to": B.END,
                      "months": months, "split": split, "trades": trade_dicts(t, typ, D)})
    series = series_for(D, [A, Bt])
    blob = {"title": TITLE, "capital": P.capital, "data_range": [cal[0], cal[-1]], "settings": SETTINGS, "views": vdata, "checks": checks, "study": study_rows,
            "rules": RULES, "notes": NOTES, "formulas": FORMULAS, "data_info": data_info, "series": series, "split_a": SPLIT_A, "split_b": SPLIT_B,
            "data_note": "Daily stock candles (fast store) + NSE stock option bhavcopy (Nov 2024 se)"}
    path = write_html(out / "report.html", blob)
    for key, _, _, t, *_ in views:
        if t is not None and len(t):
            t.to_csv(out / f"trades_{key}.csv", index=False)
    pd.DataFrame([c for c in checks if len(c) > 1], columns=["check", "type", "trades", "win_pct", "net", "pf", "max_dd", "avg_r", "worst", "net_fit", "net_verify"]).to_csv(out / "checks.csv", index=False)
    pd.DataFrame(study_rows, columns=["volume", "breakouts", "held5_pct", "fwd10_pct", "a_win_pct", "a_avg_r"]).to_csv(out / "study.csv", index=False)

    # ---- summary
    sa, sb, sa2 = B.stats(A, P.capital, m_a), B.stats(Bt, P.capital, m_b), B.stats(A2, P.capital, m_a2)

    def line(lb, s, t, split):
        fit = t[t.entry_date < split].net.sum() if len(t) else 0
        ver = t[t.entry_date >= split].net.sum() if len(t) else 0
        return (f"{lb}: {s['trades']} trade ({s['per_month']:.1f}/mahina), win {s['win_pct'] or 0:.0f}%, net Rs {s['net']:,.0f} ({s['ret_pct']:.1f}%, {s['month_ret']:.2f}%/mahina), "
                f"PF {s['profit_factor'] or 0:.2f}, avg R {s['avg_r'] or 0:.2f}, max DD Rs {s['max_dd']:,.0f}, sabse bura trade Rs {s['worst']:,.0f} | pehla hissa Rs {fit:,.0f}, doosra Rs {ver:,.0f}")
    lines = [line("A (poora)", sa, A, SPLIT_A), line("B put sell", sb, Bt, SPLIT_B), line("A (Nov 2024 se)", sa2, A2, SPLIT_B)]
    allb = study_rows[-1]
    rb = (f"B return: premium pe {Bt.net.sum() / Bt.premium_rs.sum() * 100:.1f}%, margin pe {Bt.net.sum() / Bt.margin.sum() * 100:.1f}%, notional pe {Bt.net.sum() / Bt.notional.sum() * 100:.2f}% "
          f"(kul net / kul premium, margin, notional)") if len(Bt) else "B: koi trade nahi"
    summary = "\n".join(lines + [rb, f"Study: {allb[1]} breakout, 5 din tike {allb[2]:.0f}%, A jeeta {allb[4]:.0f}%", "", "Checks:"]
                        + [f"{c[1]} {c[0]}: {c[2]} trade, net Rs {c[4]:,.0f}, PF {c[5] or 0:.2f}, avg R {c[7] or 0:.2f}, fit Rs {c[9]:,.0f}, verify Rs {c[10]:,.0f}" for c in checks if len(c) > 1]
                        + ["", "Study:"] + [f"{r[0]}: {r[1]} breakout, tike {r[2] or 0:.0f}%, 10 din return {r[3] or 0:.2f}%, A win {r[4] or 0:.0f}%, avg R {r[5] or 0:.2f}" for r in study_rows])
    (out / "summary.txt").write_text(summary, encoding="utf-8")
    print(summary, flush=True)
    msg = (f"{TITLE} ka result {TAG_TRADING}\n" + "\n".join(lines) + "\n" + rb + "\n"
           f"80% claim: {allb[1]} breakout (volume 1.5x+) me {allb[2]:.0f}% 5 din tak resistance ke upar tike, type A trade {allb[4]:.0f}% jeeta.\n"
           f"Capital Rs 10L fixed, compounding nahi, risk 1%/trade, slippage + kharcha chalu.")
    if not args.no_telegram:
        send(msg)
        send_document(path, f"{TITLE} report ({out.name})")
    print(f"done {time.time() - t0:.0f}s -> {out}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
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

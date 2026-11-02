"""Gap breakout LONG baseline on the Nifty 200 proxy (top 200 market cap each month), daily candles 2010 - 2026 (Fyers,
split / bonus adjusted): gap-up above the 60-day high + close above the 21 EMA + smoothed RSI(10) 60-80 + monthly close above
the 20-month EMA; entry next open, exit next open after a close below the 21 EMA or the gap-fill level.
Report + trade log CSV + Telegram. Short side, gap / RSI / exit variants and the 60/40 grid come after this baseline.

    python tools/run_gapbo.py
    python tools/run_gapbo.py --no-telegram

Needs the daily data:  (Algo_Historical_Data) python -m fyers_data.download_daily
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest import gapbo as G  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.gapbo_report import series_for, write_html  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
NAME = "gap_breakout_long"
TITLE = "Gap Breakout Long (EMA21 + RSI + monthly trend)"
P = G.Params()
W_ = G.with_params
TEST_START = "2011-09-01"      # data from Jan 2010: first month with 20 finished months for the 20-month EMA
FIT_SHARE = 0.6

CHECKS = [("Baseline (koi badlav nahi)", {}), ("Slippage 0 (sirf kharcha)", dict(slip=0.0)), ("Slippage 2x (0.3% har fill)", dict(slip=0.003)),
          ("Risk 0.5% har trade", dict(risk=0.005)), ("Max 5 position", dict(max_open=5)), ("Max 10 position", dict(max_open=10)),
          ("Compounding ON (booked profit se size)", dict(compound=True))]

SETTINGS_BASE = [["Stock list", "Nifty 200 jaisa: har mahine top 200 market cap (NSE list Jun 2026)", 1], ["Data", "daily candle 2010 se (Fyers), split / bonus adjusted"],
                 ["Capital", "Rs 10,00,000", 1], ["Risk / trade", "1% (Rs 10,000) = entry - stop level", 1],
                 ["Limit", "max 20% / stock, max 8 position, leverage nahi, qty <= 5% avg volume"], ["Slippage", "0.15% har fill (0.3% round trip)", 1],
                 ["Kharcha", "delivery: STT 0.1% x2, exchange, SEBI, stamp, GST, DP (~0.23% round trip)"], ["Fit / verify", "pehla 60% / aakhri 40%"]]
RULES = [
    "Stock list (Nifty 200 jaisa): har mahine ke shuru me, pichhle mahine ke aakhri din tak ke 120 din ke average market cap se top 200 stocks. Market cap = AMFI (Jun 2026) market cap "
    "x us din ka price / Jan-Jun 2026 ka average price. Sirf wo stocks jo aaj NSE pe hain (delisted nahi mile).",
    "Gap-up (din t): aaj ka open kal ke high se upar, aur open kal ke close se kam se kam 2% upar.",
    "Breakout: aaj ka open pichhle 60 trading din ke sabse uche high se upar (aaj ka din chhod ke).",
    "Trend: aaj ka close 21 EMA ke upar. Mahina: pichhle poore hue mahine ka close uske 20-month EMA ke upar (chalta hua mahina nahi gina).",
    "RSI: RSI(10) ka 3 din ka simple average 60 se upar aur 80 (overbought line) se neeche.",
    "Gap khula rahe: aaj ka close kal ke high (gap-fill level) se upar. Close tak gap bhar gaya to trade nahi.",
    "Entry: agle din (t+1) ke open pe kharido. Agle din open stop level ke neeche / barabar khule, ya stock circuit me lock ho (poora din ek hi price), to trade nahi.",
    "Stop level = kal ka high (gap-fill level) aur gap din ka 21 EMA, dono me jo upar ho. Risk har share = entry fill - stop level.",
    "Exit (baseline, 21 EMA trail): jis din close 21 EMA ke neeche ya gap-fill level ke neeche aaye, agle din open pe becho. Open stop ke neeche khule to usi open pe "
    "(nuksaan 1R se zyada ho sakta hai). Koi target nahi, koi time limit nahi.",
    "Size: qty = Rs 10 lakh x 1% / (entry fill - stop level). Ek stock me max 20% (Rs 2 lakh), max 8 position ek saath, leverage nahi (sab milake Rs 10 lakh tak), "
    "bacha paisa chahiye qty ke aadhe se kam ho to trade chhodo, qty <= 50 din ke average volume ka 5%. Ek din me kai signal = bada gap pehle. Ek stock me ek hi position. "
    "Capital fixed (profit dobara nahi lagaya, compounding nahi).",
    "Kharcha (delivery): STT 0.1% kharid aur bech dono pe, NSE 0.00307%, SEBI 0.0001%, stamp 0.015% (kharid), GST 18% (exchange + SEBI pe), DP Rs 15.34 + GST har bechne pe; "
    "brokerage 0. Slippage 0.15% har fill. Kul ~0.53% round trip.",
    "Test: 1 Sep 2011 se 2 Nov 2026 (data Jan 2010 se, pehle 20 mahine indicators bharne ke liye). Pehla 60% = fit, aakhri 40% = verify. Baseline ki koi setting data dekh ke nahi chuni.",
]
NOTES = [
    "Gap % kal ke close se naapa (spec: 'previous close ka %'), saath me open > kal ka high (asli gap-up, kal ki candle se upar).",
    "Baseline ke beech wale options pehle se tay: gap 2% (1/2/3 me se), overbought 80 (70/80/90), RSI smoothing 3 din (3/5), 60 din high (40/60/100), gap-fill level kal ka high, "
    "entry agle din open. Baaki options agle step me (60/40 fit-verify ke saath).",
    "RSI aur EMA TradingView jaise bane hain (pehli value simple average se, phir EMA / Wilder). Suspension wale din skip.",
    "Monthly trend: sirf poore hue mahine ka close (mahine ke aakhri din ke close ke baad wo mahina gina gaya). 'Monthly support' wala option abhi nahi (rule clear nahi).",
    "Close wala stop din ke close pe hi pata chalta hai, isliye exit agle din ke open pe. Isliye kai trades me asli nuksaan 1R se zyada hua - 'Daave ki jaanch' tab me alag dikhaya.",
    "20% cap ki wajah se chhote stop wale trade me asli risk 1% se kam hota hai. R = net / asli risk (qty x (entry fill - stop level)); bahut chhote stop pe R bahut bada ya bahut chhota dikhta hai.",
    "30% se bada gap (open) data galti / corporate action maana, trade nahi.",
    "Demerger: Fyers ka daily data demerger ke liye hamesha adjust nahi hota (jaise ITC Jan 2025 me -5%, Adani Ent Jun 2015 me -83% ka nakli girna). Isliye demerger ki ex-date "
    "(NSE list Oct 2021 se, purane haath se) pe khuli position pichhle close pe bech di (ex-date se pehle bechna mumkin hai), aur ex-date se 1 din pehle se 60 din baad tak naya setup nahi.",
    "Bull / bear = gap wale din Nifty 50 ka close uske 200 din ke simple average ke upar / neeche.",
    "Nifty 50 buy-hold price index hai (dividend ke bina, ~1.2-1.5% saal kam).",
    "Survivorship bias: Fyers delisted / merged stocks ka data nahi deta (HDFC Ltd, DHFL, Jet Airways, Cairn, RCOM ...). Stock list Jun 2026 ki hai; purane saalon me jo stocks gire "
    "aur band ho gaye wo test me nahi hain, isliye result asli se behtar dikh sakta hai. Har mahine market cap rank (us waqt ke price se) se kuch bias kam hua, khatam nahi.",
    "Market cap proxy aaj ke share count pe hai (rights issue / QIP / buyback nahi dikhte); rank sirf purane price se banta hai.",
    "Short side (gap-down after overbought), Nifty pe test, aur saare variants abhi NAHI chalaye - ye sirf long baseline hai.",
]
FORMULAS = [
    ["EMA n", "pehli value = pehle n close ka average; phir EMA = close x 2/(n+1) + EMA(kal) x (1 - 2/(n+1))   (n = 21 daily, 20 monthly)"],
    ["RSI(10)", "badlav = close - kal ka close; up / down = Wilder average (1/10 weight, pehla = 10 din ka average); RSI = 100 - 100 / (1 + up / down)"],
    ["Smoothed RSI", "pichhle 3 din ke RSI(10) ka average"], ["Gap %", "(open / kal ka close - 1) x 100; gap-up = open > kal ka high aur gap % >= 2"],
    ["60 din high", "max(high) pichhle 60 trading din (aaj ko chhod ke); breakout = open > 60 din high"],
    ["Monthly trend", "mahine ka close (aakhri trading din) > EMA20(monthly close); sirf poora hua mahina"],
    ["Market cap (proxy)", "AMFI market cap x close / average close (Jan-Jun 2026); rank = 120 din average, mahine ke aakhri din; agle mahine rank <= 200"],
    ["Gap-fill level", "kal (gap se pehle wale din) ka high"], ["Stop level", "max(gap-fill level, gap din ka 21 EMA)"],
    ["Entry fill", "agle din ka open x (1 + 0.15%)"],
    ["Qty", "floor(min(Rs 10,00,000 x 1% / (entry fill - stop level), Rs 2,00,000 / entry fill, bacha cash / entry fill, 5% x 50 din avg volume)); aadhe se kam mile to skip"],
    ["Exit signal", "close < 21 EMA ya close < gap-fill level -> agle din open pe exit (open hi fill)"], ["Exit fill", "exit price x (1 - 0.15%)"],
    ["Kharcha", "STT 0.1% x (kharid + bech) + NSE 0.00307% x (kharid + bech) + SEBI 0.0001% x (kharid + bech) + stamp 0.015% x kharid + GST 18% x (NSE + SEBI) + Rs 15.34 x 1.18"],
    ["P&L", "qty x (exit fill - entry fill) - kharcha"], ["R multiple (spec)", "net P&L / (qty x (entry fill - stop level))"],
    ["R (1% units)", "net P&L / (Rs 10,00,000 x 1%) = net P&L / Rs 10,000"], ["Average trade", "kul net / kul trade"],
    ["Stop ke paar nuksaan", "qty x max(0, stop level - exit price)"], ["Win rate", "net > 0 wale trade / kul trade x 100"],
    ["Average win / loss (R)", "jeete trades ka average R / hare trades ka average R"], ["Expectancy", "sab trades ka average R"],
    ["Profit factor", "jeete trades ka kul net / hare trades ka kul loss"],
    ["Equity", "Rs 10,00,000 + band trades ka net + khule trades ka (aaj ka close - entry fill) x qty"],
    ["Max drawdown", "min(equity / us din tak ka sabse uncha equity - 1)"], ["Total return", "net / Rs 10,00,000 x 100"],
    ["CAGR", "(aakhri equity / Rs 10,00,000)^(1 / saal) - 1"], ["Exposure", "average(lagaya paisa / equity) roz ka"],
    ["Top 5% ka hissa", "sabse acche 5% trades ka net / kul net"], ["Bull / bear", "gap din Nifty 50 close > / < SMA200(Nifty 50)"],
    ["Nifty 50 buy-hold", "close(aakhri din) / close(pehla din) - 1; CAGR = (1 + return)^(1 / saal) - 1"],
]
LOG_COLS = {"signal_date": "gap_date", "entry_date": "entry_date", "symbol": "stock", "gap_pct": "gap_pct", "rsi": "rsi_10", "rsi_s": "rsi_smoothed",
            "dist_ema": "price_vs_ema21_pct", "monthly_status": "monthly_status", "entry": "entry", "stop": "stop", "exit_date": "exit_date", "exit": "exit_price",
            "reason": "exit_reason", "R": "r_multiple", "hold": "holding_days", "costs": "costs", "net": "net_pnl"}


def inr(v: float) -> str:
    """Rs with Indian digit grouping: 1000000 -> 'Rs 10,00,000'."""
    s = str(int(round(abs(v))))
    if len(s) > 3:
        head, parts = s[:-3], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join(([head] if head else []) + parts) + "," + s[-3:]
    return ("-" if v < 0 else "") + "Rs " + s


def chips_of(q) -> list:
    return [["Gap", f">= {q.gap * 100:g}% (open vs kal ka close) aur open > kal ka high", 1], ["Breakout", f"open > pichhle {q.hh_n} din ka high", 1],
            ["Trend", f"close > {q.ema_n} EMA; mahina close > {q.month_ema}-month EMA"], ["RSI", f"RSI({q.rsi_n}) ka {q.rsi_sma} din SMA {q.rsi_lo:g} - {q.rsi_ob:g}", 1],
            ["Entry", "agle din open" if q.entry == "open" else "gap din ka close", 1],
            ["Stop", f"close 21 EMA ke neeche ya kal ke {'high' if q.fill == 'high' else 'close'} (gap-fill) ke neeche -> agle din open", 1], ["Exit", G.EXITS[q.exit]]]


def inv_of(q, t: pd.DataFrame, m: dict) -> list:
    if t is None or t.empty:
        return [["Shuruaati capital", inr(q.capital)], ["Trade", "koi nahi"]]
    return [["Shuruaati capital", f"{inr(q.capital)} ({'compounding ON' if q.compound else 'fixed, compounding nahi'})"],
            ["Risk har trade", f"{q.risk * 100:g}% = {inr(q.capital * q.risk)} (20% cap ki wajah se asli average {t.risk_pct.mean():.2f}%, median {t.risk_pct.median():.2f}%)"],
            ["Qty ka rule", "capital x risk / (entry fill - stop level)"], ["Max ek stock me", f"{q.max_pos_pct * 100:g}% capital = {inr(q.capital * q.max_pos_pct)}; qty <= {q.adv_pct * 100:g}% avg volume"],
            ["Max khuli position", f"{q.max_open} (sabse zyada ek saath: {int(m['n_open'].max())})"],
            ["Average / sabse badi position", f"{inr(t.notional.mean())} / {inr(t.notional.max())}"],
            ["Sabse zyada paisa ek din me laga", f"{inr(m['dep'].max())} ({m['dep'].max() / q.capital * 100:.0f}% capital)"]]


def trade_dicts(t: pd.DataFrame, key: str) -> list:
    if t is None or t.empty:
        return []
    keep = ["symbol", "signal_date", "entry_date", "exit_date", "gap_pct", "rsi", "rsi_s", "dist_ema", "mdist", "rank", "bull", "prev_high", "prev_close", "gap_open", "gap_close",
            "fill_lvl", "entry", "entry_fill", "stop", "stop_pct", "qty", "notional", "risk_rs", "risk_pct", "exit", "exit_fill", "reason", "hold", "beyond_rs",
            "gross", "slip", "c_stt", "c_exch", "c_stamp", "c_gst", "c_dp", "costs", "net", "R", "R_cap"]
    x = t[keep].copy()
    num = x.select_dtypes("number").columns
    x[num] = x[num].round(4)
    out = x.astype(object).where(x.notna(), None).to_dict("records")
    for i, r in enumerate(out):
        r["id"] = f"{key}{i}"
    return out


def trade_log(t: pd.DataFrame) -> pd.DataFrame:
    x = t.copy()
    x["monthly_status"] = x.mdist.map(lambda v: "n/a" if v is None or v != v else f"upar ({v:+.1f}% vs 20M EMA)")
    x = x.rename(columns=LOG_COLS)
    first = list(LOG_COLS.values())
    rest = [c for c in x.columns if c not in first and c not in ("i", "j", "k", "x", "mdist")]
    return x[first + rest]


def run(args, status) -> int:
    t0 = time.time()
    out = Path(args.out) if args.out else result_dir(NAME)
    out.mkdir(parents=True, exist_ok=True)
    say = (lambda m: None) if args.no_telegram else send
    say(f"Run shuru: {TITLE}, Nifty 200 jaisa (top 200 market cap har mahine), {TEST_START} se aaj tak, sirf LONG baseline + {len(CHECKS) - 1} paise wale checks. Folder {out.name}.")
    status(f"Data load + indicators | {out.name}")
    D = G.load(path=args.data)
    I = G.indicators(D, P)
    cal = D["cal"]
    END = cal[-1]
    a, b = int(np.searchsorted(cal, TEST_START)), len(cal) - 1
    s_i = a + int(round(FIT_SHARE * (b - a + 1)))
    SPLIT, END_IS = cal[s_i], cal[s_i - 1]
    print(f"load + indicators {time.time() - t0:.0f}s: {len(cal)} days {cal[0]} -> {END}, {len(D['syms'])} stocks; split {SPLIT}", flush=True)

    status("Signals + exits")
    S = G.signals(I, P)
    S = S[S.i >= a - 1].reset_index(drop=True)                     # first entries on the test start
    X = G.paths(I, S, P)
    X = X[X.k >= a].reset_index(drop=True)
    print(f"signals {len(S)}, entries {len(X)} ({time.time() - t0:.0f}s)", flush=True)

    def parts(q):
        t, m = G.portfolio(D, I, X, q)
        return t, m, {"all": G.stats(t, m, cal, q.capital, TEST_START, END), "is": G.stats(t, m, cal, q.capital, TEST_START, END_IS),
                      "oos": G.stats(t, m, cal, q.capital, SPLIT, END)}

    status("Portfolio + checks")
    checks = []
    for label, kw in CHECKS:
        _, _, s = parts(W_(P, **kw))
        al, i_, o_ = s["all"], s["is"], s["oos"]
        checks.append([label, al["trades"], al["win_pct"], al["net"], al["ret_pct"], al["cagr"], al["pf"], al["exp_rcap"], al["max_dd_pct"], i_["net"], i_["pf"], o_["net"], o_["pf"]])

    status("Views + report")
    bh = {k: G.buy_hold(I, cal, P.capital, s_, e_) for k, (s_, e_) in {"all": (TEST_START, END), "is": (TEST_START, END_IS), "oos": (SPLIT, END)}.items()}
    vdata, all_trades, main = [], [], None
    for key, label, q in (("main", "Baseline (fixed capital)", P), ("comp", "Baseline, compounding ON", W_(P, compound=True))):
        t, m, s = parts(q)
        if key == "main":
            main = (t, m, s)
        vdata.append({"key": key, "label": label, "sub": f"{label} | gap {q.gap * 100:g}%, {q.hh_n} din high, RSI {q.rsi_lo:g}-{q.rsi_ob:g}, exit: {G.EXITS[q.exit]}",
                      "chips": chips_of(q), "inv": inv_of(q, t, m), "stats": s, "eq": (q.capital + m["pnl"][a:b + 1]).round(0).tolist(),
                      "trades": trade_dicts(t, key), "compound": q.compound})
        all_trades.append(t)
        if t is not None and len(t):
            trade_log(t).to_csv(out / f"trades_{key}.csv", index=False)
    t_m, m_m, s_m = main
    n50 = I["n50"][a:b + 1]
    funnel = S.why.replace("", "entry").value_counts().to_dict()
    ever = int((I["uni"][a:b + 1]).any(axis=0).sum())
    data_info = [["Stock data", f"Fyers daily candles (split / bonus adjusted), {cal[0]} se {END}; {len(D['syms'])} stocks (NSE list Jun 2026, market cap >= Rs 1,000 Cr); "
                                f"{D['bad_rows']} galat candle (high < low waghera) hata diye"],
                 ["Stock list", f"har mahine top {P.top_n} market cap; test me kabhi na kabhi top {P.top_n} me rahe: {ever} stocks"],
                 ["Nifty 50", f"Fyers daily index (2005 se); 200 DMA ke upar {np.nanmean(I['bull'][a:b + 1]) * 100:.0f}% din (test period)"],
                 ["Setups (baseline)", f"{len(S)} gap din jo saare rules pass kiye: " + ", ".join(f"{k} {v}" for k, v in funnel.items())],
                 ["Trade (baseline)", f"{len(X)} entry -> {len(t_m)} trade; chhode: " + (", ".join(f"{k} {v}" for k, v in m_m['skipped'].items()) or "koi nahi")],
                 ["Survivorship", "Fyers delisted / merged stocks nahi deta; list Jun 2026 ki. Purane saalon ke gire / band stocks test me nahi."],
                 ["Demerger ex-dates", f"{len(D['ca_list'])} (NSE corporate actions Oct 2021 se + {len(G.CA_MANUAL)} purane haath se: "
                                       + ", ".join(f"{s} {d}" for s, d in G.CA_MANUAL) + "); in trades me position pichhle close pe band: "
                                       + str(int((t_m.reason.str.startswith('corporate')).sum()) if len(t_m) else 0)],
                 ["Fit / verify", f"{TEST_START} se {END_IS} (60%) / {SPLIT} se {END} (40%)"]]
    blob = {"title": TITLE, "capital": P.capital, "data_range": [TEST_START, END], "split": SPLIT, "settings": SETTINGS_BASE, "views": vdata, "checks": checks,
            "rules": RULES, "notes": NOTES, "formulas": FORMULAS, "data_info": data_info, "series": series_for(D, I, all_trades), "dates": cal[a:b + 1],
            "bh": (P.capital * n50 / n50[0]).round(0).tolist(), "bh_stats": bh, "data_note": "Daily stock candles (Fyers, adjusted) + Nifty 50 index"}
    path = write_html(out / "report.html", blob)
    pd.DataFrame(checks, columns=["check", "trades", "win_pct", "net", "ret_pct", "cagr", "pf", "exp_r", "max_dd_pct", "is_net", "is_pf", "oos_net", "oos_pf"]).to_csv(out / "checks.csv", index=False)
    S.assign(date=[cal[i] for i in S.i], stock=[D["syms"][j] for j in S.j]).drop(columns=["i", "j"]).to_csv(out / "setups.csv", index=False)

    # ---- summary
    def line(lb, s):
        return (f"{lb}: {s['trades']} trade, win {s['win_pct'] or 0:.0f}%, net Rs {s['net']:,.0f} ({s['ret_pct']:.0f}%), CAGR {s['cagr']:.1f}%, PF {s['pf'] or 0:.2f}, "
                f"avg trade Rs {s['avg_net'] or 0:,.0f} ({s['exp_rcap'] or 0:.2f}R of 1%), max DD {s['max_dd_pct']:.1f}%")
    reg = t_m.groupby("bull").net.agg(["size", lambda x: (x > 0).mean() * 100]) if len(t_m) else None
    reg_txt = ", ".join(f"Nifty 200 DMA ke {k}: {int(r.iloc[0])} trade win {r.iloc[1]:.0f}%" for k, r in reg.iterrows()) if reg is not None else ""
    sa = s_m["all"]
    wins = t_m[t_m.net > 0].R if len(t_m) else pd.Series(dtype=float)
    sc = vdata[1]["stats"]["all"]
    lines = [line("Poora (fixed capital)", sa), line("  fit 60%", s_m["is"]), line("  verify 40%", s_m["oos"]), line("Compounding ON", sc),
             f"Nifty 50 buy-hold: {bh['all']['ret_pct']:.0f}% (CAGR {bh['all']['cagr']:.1f}%, max DD {bh['all']['max_dd_pct']:.1f}%)",
             f"Daave: win {sa['win_pct'] or 0:.0f}% (daava ~50%); {reg_txt} (daava 80-85% / 40-45%)",
             f"R: jeete trades ka median {wins.median() if len(wins) else 0:.1f}R, 15R+ sirf {sa['r15']} trade, sabse bada {sa['max_r'] or 0:.1f}R (daava 1:15 - 1:100); "
             f"top 5% trades = kul net ka {sa['top5_share'] or 0:.0f}%",
             f"Stop ke paar: {sa['beyond_n']} trade, extra nuksaan Rs {sa['beyond_rs']:,.0f}; -1R se bura {sa['below_m1']} trade (sabse bura {sa['worst_r'] or 0:.1f}R)"]
    summary = "\n".join(lines + ["", "Checks:"] + [f"  {c[0]}: {c[1]} trade, win {c[2] or 0:.0f}%, net Rs {c[3]:,.0f}, CAGR {c[5]:.1f}%, PF {c[6] or 0:.2f}, DD {c[8]:.1f}%, "
                                                  f"fit Rs {c[9]:,.0f}, verify Rs {c[11]:,.0f}" for c in checks])
    (out / "summary.txt").write_text(summary, encoding="utf-8")
    print(summary, flush=True)
    print(f"report {path.stat().st_size / 1e6:.1f} MB", flush=True)
    if not args.no_telegram:
        send(f"{TITLE} ka result {TAG_TRADING}\n" + "\n".join(lines) + "\nCapital Rs 10L fixed, risk 1%, kharcha + slippage 0.15% har fill chalu. Ye sirf LONG baseline hai.")
        send_document(path, f"{TITLE} report ({out.name})")
    print(f"done {time.time() - t0:.0f}s -> {out}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--status-file")
    ap.add_argument("--data", help="daily parquet (default data/daily/equity_daily.parquet); for trial runs on part of the data")
    ap.add_argument("--out", help="output folder instead of results/NN_gap_breakout_long (trial runs)")
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

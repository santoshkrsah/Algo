"""RSI(14) swing on 15-minute candles, NIFTY 50 stocks, Jun 2023 - Jun 2026 (user request 18 Nov 2026).

Buy when RSI crosses back up through 30, sell when it crosses back down through 70; long only, cash delivery, no stop loss,
Rs 10 lakh capital (no margin, no compounding), Rs 1 lakh per trade, max 10 positions, Angel One charges + 0.05% slippage.
Writes results/NN_rsi15_swing_nifty50/: report.html, trades.csv, skipped.csv, equity.csv, checks.csv, summary.json.

    python tools/run_rsi15.py [--status-file s.txt] [--no-telegram]
"""
import argparse
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest import rsi15 as R  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.notify import send  # noqa: E402
from backtest.rsi15_report import series_for, write_html  # noqa: E402

TAG_TECH = "@albert_max"
NAME = "rsi15_swing_nifty50"
TITLE = "RSI 30/70 Swing (15-min) - NIFTY 50 stocks"
P = R.Params()

SETTINGS = [["Stock list", "NIFTY 50 ke 50 stocks (aaj ki list, Sep 2025 rebalance)", 1], ["Timeframe", "15-min candle", 1], ["Indicator", "RSI 14 (Wilder)", 1],
            ["Buy", "RSI 30 ke neeche se wapas 30 ke upar", 1], ["Sell", "RSI 70 ke upar se wapas 70 ke neeche", 1], ["Stop loss", "nahi"], ["Side", "sirf long (delivery)"],
            ["Capital", "Rs 10,00,000, margin nahi, compounding nahi", 1], ["Har trade", "Rs 1,00,000", 1], ["Max position", "10 ek saath"],
            ["Kharcha", "Angel One delivery (STT 0.1% x2, Rs 20 brokerage, DP, GST ...)", 1], ["Slippage", "0.05% har fill", 1],
            ["Fill", "signal candle ke baad agli 15m candle ka open"], ["Period", "1 Jun 2023 - 30 Jun 2026"]]

RULES = [
    "Stocks: NIFTY 50 ke 50 stocks (aaj wali list). Har stock ka alag RSI, alag signal.",
    "Candle: 15 minute (9:15, 9:30 ... 15:15). RSI 14 period, Wilder tareeke se (TradingView jaisa), din badalne pe bhi chalta rehta hai.",
    "Buy: pichhli candle pe RSI 30 se neeche tha aur is candle ke close pe RSI 30 ya upar aa gaya. Agli 15m candle ke open pe kharido.",
    "Sell: pichhli candle pe RSI 70 se upar tha aur is candle ke close pe RSI 70 ya neeche aa gaya. Agli 15m candle ke open pe becho. Koi stop loss nahi, koi target nahi.",
    "Sirf long (kharido phir becho). Ek stock me ek time pe ek hi position; position khuli hai to us stock ka naya buy signal chhod do.",
    "Paisa: Rs 10 lakh capital, margin nahi. Har trade me Rs 1 lakh (qty = 1,00,000 / entry price, neeche round). Ek saath max 10 position. Compounding nahi: profit ho ya loss, har trade Rs 1 lakh ka hi.",
    "Cash kam ho (pichhle loss ki wajah se) ya 10 position khuli hon to naya signal chhod do. Ek hi time pe kai signal aur jagah kam ho to jiska RSI sabse zyada neeche gaya tha wo pehle.",
    "Test 1 Jun 2023 se 30 Jun 2026. 30 Jun 2026 ko jo position khuli thi wo us din ke close pe band maani.",
    "Kharcha Angel One ke hisaab se (delivery; usi din buy-sell ho gaya to intraday rate) + 0.05% slippage har fill pe.",
]
NOTES = [
    "Aapne '30 pe buy, 70 pe sell' bola: hamne 'cross back' maana (RSI 30 ke neeche jaake wapas upar aaye tab buy, 70 ke upar jaake wapas neeche aaye tab sell). 'Touch' wala tareeka Extra checks me hai.",
    "Bina margin ke short overnight nahi rakh sakte, isliye 70 pe sell = exit (short nahi).",
    "Angel One ne delivery brokerage Nov 2024 se lena shuru kiya (pehle free). Hamne poore test pe Rs 20 / 0.1% lagaya (thoda zyada kharcha, safe side).",
    "Stock list aaj ki NIFTY 50 hai (survivorship bias): jo stock beech me NIFTY 50 se bahar hue (jaise BPCL, BRITANNIA, INDUSINDBK, HEROMOTOCO) wo test me nahi; jo baad me aaye (ETERNAL, JIOFIN, INDIGO, MAXHEALTH, TRENT ...) wo shuru se hain.",
    "JIOFIN Aug 2023 me list hua, uske trade uske baad se hi.",
    "RELIANCE (Jio Financial, 20 Jul 2023) aur ITC (ITC Hotels, 6 Jan 2025) demerger: purane price adjust kiye taaki naye share ki value ka girna loss na lage (jaise naya share bhi aapke paas ho).",
    "Signal candle ke close ke baad hi order: agli candle ka open (15:15 wali candle ka signal = agle din 9:15 ka open, gap ke saath).",
    "Usi din buy aur sell = intraday charges (exchange ka rule). Ye bahut kam trades me hua.",
]
FORMULAS = [
    ["RSI (14)", "change = close - pichhla close; avg gain / avg loss = Wilder average (alpha 1/14); RSI = 100 - 100 / (1 + avg gain / avg loss)"],
    ["Buy signal", "RSI[pichhli candle] < 30 AUR RSI[ye candle] >= 30"],
    ["Sell signal", "RSI[pichhli candle] > 70 AUR RSI[ye candle] <= 70"],
    ["Entry fill", "agli 15m candle ka open x (1 + 0.0005)"],
    ["Exit fill", "agli 15m candle ka open x (1 - 0.0005); test khatam = 30 Jun 2026 close x (1 - 0.0005)"],
    ["Qty", "floor(1,00,000 / entry fill)"],
    ["Slippage", "qty x (entry open + exit price) x 0.0005"],
    ["Gross P&L", "qty x (exit fill - entry fill)"],
    ["Brokerage (delivery)", "har order min(Rs 20, 0.1% x order value); intraday: min(Rs 20, 0.03%)"],
    ["STT", "delivery: 0.1% x (buy value + sell value); intraday: 0.025% x sell value"],
    ["Exchange + SEBI", "0.00297% + 0.0001% x (buy value + sell value)"],
    ["Stamp duty", "delivery 0.015% x buy value; intraday 0.003% x buy value"],
    ["GST", "18% x (brokerage + exchange + SEBI)"],
    ["DP charge", "Rs 20 + 18% GST = Rs 23.60 har delivery sell"],
    ["Net P&L", "gross P&L - (brokerage + STT + exchange + SEBI + stamp + GST + DP)"],
    ["Return % (trade)", "net / buy value x 100"],
    ["Equity (roz)", "Rs 10 lakh + band trades ka net + khuli position x aaj ka close - unki buy value - buy ka kharcha"],
    ["Max drawdown", "min(equity / ab tak ki sabse oonchi equity - 1)"],
    ["CAGR", "(1 + net / 10 lakh) ^ (1 / saal) - 1"],
    ["Win rate", "net > 0 wale trade / kul trade"],
    ["Profit factor", "jeete trades ka jod / haare trades ka jod (bina minus)"],
    ["NIFTY 50 buy-hold", "10 lakh x NIFTY close / pehle din ka NIFTY open (dividend nahi)"],
]


def trade_dicts(t: pd.DataFrame) -> list[dict]:
    keep = ["id", "symbol", "sig_time", "entry_time", "exit_time", "entry_raw", "entry", "exit_raw", "exit", "qty", "buy_val", "gross", "slip", "c_brok", "c_stt", "c_exch",
            "c_sebi", "c_stamp", "c_gst", "c_dp", "costs", "net", "ret_pct", "days", "same_day", "reason", "dip", "rsi_entry", "mae_pct", "mfe_pct"]
    x = t[keep].copy()
    for c in ("sig_time", "entry_time", "exit_time"):
        x[c] = x[c].dt.strftime("%Y-%m-%d %H:%M")
    num = x.select_dtypes("number").columns.difference(["id", "qty", "days"])
    x[num] = x[num].round(2)
    return x.to_dict("records")


def run(args, status) -> int:
    t0 = time.time()
    out = result_dir(NAME)
    status("RSI 15m swing: 50 stocks ka 15m data load (fast store)")
    D = R.load()
    print(f"loaded {len(D)} stocks {time.time() - t0:.0f}s", flush=True)

    status("RSI 15m swing: base run + extra checks")
    base = R.run(D, P)
    S = R.stats(base, P)
    variants = [("Aapke rules (base)", P), ("Slippage 2x (0.1% har fill)", R.with_params(P, slip=0.001)),
                ("Bina kharcha, bina slippage (sirf signal ki taakat)", R.with_params(P, costs=False, slip=0.0)),
                ("Touch rule: RSI 30 ke neeche jaate hi buy, 70 ke upar jaate hi sell", R.with_params(P, rule="touch")),
                ("Max 5 position x Rs 2 lakh", R.with_params(P, max_pos=5, per_trade=2_00_000))]
    checks = []
    for label, q in variants:
        r = base if q == P else R.run(D, q)
        s = R.stats(r, q)
        checks.append({"label": label, "s": s})
        print(f"  {label}: {s['trades']} trades net {s['net']:,.0f} pf {s['pf'] or 0:.2f} dd {s['max_dd_pct']:.1f}%", flush=True)

    t, e = base["trades"], base["equity"]
    dates = e.date.tolist()
    bh = R.nifty_bh(P, dates)
    bh_stats = _bh_stats(bh, P)
    bh_year = {}
    for y in sorted({d[:4] for d in dates}):
        g = bh[bh.date.str.startswith(y)]
        prev = bh[bh.date < g.date.iloc[0]]
        start = float(prev.equity.iloc[-1]) if len(prev) else P.capital
        bh_year[y] = (float(g.equity.iloc[-1]) / start - 1) * 100
    cost_sum = {k: float(t["c_" + k].sum()) for k in ("brok", "stt", "exch", "sebi", "stamp", "gst", "dp")}
    sk = base["skipped"].why.value_counts()
    skipped = [[f"Chhoota: {k}", int(v)] for k, v in sk.items()]
    inv = [["Shuruaati capital", "Rs 10,00,000 (fixed, compounding nahi, margin nahi)"], ["Risk har trade", "fixed risk nahi: har trade Rs 1 lakh, stop loss nahi"],
           ["Qty ka rule", "floor(Rs 1,00,000 / entry fill)"], ["Max ek stock me", "1 position = ~Rs 1 lakh (10% capital)"],
           ["Max khuli position", f"10 (sabse zyada ek saath: {S['max_positions']}, average {S['avg_positions']:.1f})"],
           ["Average / sabse badi position", f"Rs {t.buy_val.mean():,.0f} / Rs {t.buy_val.max():,.0f}"],
           ["Sabse zyada paisa ek din me laga", f"Rs {S['peak_deployed']:,.0f} ({S['peak_deployed'] / P.capital * 100:.0f}% capital)"],
           ["Average paisa laga", f"Rs {S['avg_deployed']:,.0f}; market me time {S['in_mkt']:.0f}% din"]]
    data_info = [["Stock data", f"Fyers 1-min candles (fast store) -> 15-min candles, {len(D)} stocks; RSI warm-up Jan 2023 se, test {R.START} se {R.END}"],
                 ["Adjust", "split / bonus adjusted (fast store); RELIANCE x" + f"{R.DEMERGERS['RELIANCE'][1]:.4f} (20 Jul 2023 se pehle), ITC x{R.DEMERGERS['ITC'][1]:.4f} (6 Jan 2025 se pehle) demerger ke liye"],
                 ["NIFTY 50", "daily index (Fyers) sirf buy-hold tulna ke liye"],
                 ["Signal", f"{base['signals']:,} buy signal -> {len(t):,} trade; baaki: " + ", ".join(f"{k} {v:,}" for k, v in sk.items())],
                 ["Survivorship", "stock list aaj ki NIFTY 50; beech me bahar hue stocks test me nahi"],
                 ["Dividend", "dividend nahi joda (na strategy me, na NIFTY 50 buy-hold me)"]]
    gross_pre = S["gross"] + S["slip"]
    verdict_note = ("Signal me thoda edge hai par kharcha usse zyada hai." if gross_pre > 0 > S["net"] else
                    "Kharche se pehle bhi loss: signal me edge nahi." if gross_pre <= 0 else "Kharche ke baad bhi profit.")
    sub = f"RSI(14) 15-min cross-back 30/70, sirf long, {len(D)} stocks"
    blob = {"title": TITLE, "sub": sub, "capital": P.capital, "data_range": [dates[0], dates[-1]], "data_note": "15-min stock candles (fast store, adjusted) + NIFTY 50 daily index",
            "settings": SETTINGS, "stats": S, "bh_stats": bh_stats, "bh_year": bh_year, "checks": checks, "cost_sum": cost_sum, "skipped": skipped,
            "n_signals": base["signals"], "inv": inv, "rules": RULES, "notes": NOTES, "formulas": FORMULAS, "data_info": data_info, "verdict_note": verdict_note,
            "dates": dates, "eq": e.equity.round(0).tolist(), "bh": bh.equity.round(0).tolist(), "trades": trade_dicts(t), "series": series_for(D, t)}
    status("RSI 15m swing: HTML report likh raha hoon")
    path = write_html(out / "report.html", blob)
    t.drop(columns=["si", "fi", "xi", "sell_side_cost"]).to_csv(out / "trades.csv", index=False)
    base["skipped"].to_csv(out / "skipped.csv", index=False)
    e.to_csv(out / "equity.csv", index=False)
    pd.DataFrame([{"check": c["label"], **c["s"]} for c in checks]).to_csv(out / "checks.csv", index=False)
    summary = {"title": TITLE, "folder": out.name, "stats": S, "nifty_bh": bh_stats, "checks": {c["label"]: c["s"] for c in checks}, "cost_sum": cost_sum,
               "skipped": dict(sk), "signals": base["signals"], "report_mb": round(path.stat().st_size / 1e6, 1)}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)), encoding="utf-8")
    print(json.dumps(summary, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)), flush=True)
    print(f"done {time.time() - t0:.0f}s -> {path} ({path.stat().st_size / 1e6:.1f} MB)", flush=True)
    return 0


def _bh_stats(bh: pd.DataFrame, p) -> dict:
    v = bh.equity.to_numpy()
    peak = np.maximum.accumulate(v)
    years = (pd.Timestamp(bh.date.iloc[-1]) - pd.Timestamp(bh.date.iloc[0])).days / 365.25
    return {"ret_pct": (v[-1] / p.capital - 1) * 100, "cagr": ((v[-1] / p.capital) ** (1 / years) - 1) * 100, "max_dd_pct": float((v / peak - 1).min() * 100)}


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

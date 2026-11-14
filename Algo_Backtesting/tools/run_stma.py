"""NIFTY 1-hour Supertrend + MA backtest (Big Bull Series Ep 94): Module A end to end + report.

    python tools/run_stma.py                 # run, write results/NN_stnifty_supertrend_ma/report.html
    python tools/run_stma.py --notify        # after the website update: finish message + HTML to Telegram
Data: ../Algo_Historical_Data/data/bars (python -m fyers_data.download_bars --only NIFTY50 NIFTY_F1 NIFTY_F2 INDIAVIX ...).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from backtest.config import result_dir  # noqa: E402
from backtest.podcast import data as pdata  # noqa: E402
from backtest.stma import quality, report, run  # noqa: E402
from backtest.stma.config import CONFIG, TITLE_SUFFIX  # noqa: E402

NAME = "stnifty_supertrend_ma"
SITE_URL = json.loads((Path(__file__).resolve().parents[1] / "site" / "site.json").read_text(encoding="utf-8"))["url"]  # reports website


def texts(res):
    P = res["P"]
    rules = [
        "Instrument: NIFTY near-month futures (apni continuous series: expiry se 3 trading din pehle agla contract; roll ka gap P&L me nahi, roll pe 2 order + slippage).",
        "Candle: 1 ghanta, 1-minute data se banayi, 09:15 se: 09:15, 10:15, 11:15, 12:15, 13:15, 14:15 aur aakhri chhoti candle 15:15-15:30. Signals NIFTY spot chart pe (L1).",
        f"Indicators: Supertrend (ATR {P['atr_n']}, multiplier {P['mult']:g}, Wilder ATR) aur {P['ma_type']} {P['ma_len']} - dono 1-ghanta close pe. Ye setting 54 settings ke grid me se sirf fit period (pehle 60%) dekh ke chuni.",
        "BUY: 1-ghanta candle Supertrend line AUR MA dono ke upar band ho, pichhli candle me dono shart poori na thi, aur koi position na ho. Order agle 1-minute bar ke open pe (futures).",
        "MA sirf entry ke liye; entry ke baad MA ko ignore karo.",
        "EXIT: Supertrend line trailing stop hai. Exit tabhi jab koi 1-ghanta candle Supertrend ke neeche BAND ho (sirf chhoona kaafi nahi); order agle 1-minute open pe.",
        "Exit ke baad nayi entry ke liye entry ki shart dobara se banni chahiye (pehle tootna, phir dobara dono ke upar).",
        "Short (alag variant): candle dono ke neeche band ho -> short (50% size), Supertrend ke upar close pe exit. Long aur short kabhi ek saath nahi.",
        f"Slippage {CONFIG['slippage_main']:g} point har fill pe (1 aur 5 bhi test). Charges aaj ke rates se: brokerage Rs 20/order, STT sell 0.05% futures, NSE, SEBI, stamp, GST.",
        f"Account: Rs {CONFIG['capital']:,} se, 2x leverage, lots = floor(equity x 2 / (price x lot size)); profit COMPOUND hota hai. Points ke result 1 unit ke hain (compounding nahi).",
        f"Period Feb 2018 - Nov 2026. Fit = pehla 60% ({res['split'].date()} se pehle), test = baaki 40%.",
    ]
    formulas = [
        ["ATR (Wilder)", "TR = max(H-L, |H-C_prev|, |L-C_prev|);  ATR = (ATR_prev x (n-1) + TR) / n"],
        ["Supertrend bands", "upper = (H+L)/2 + mult x ATR, lower = (H+L)/2 - mult x ATR; upper sirf neeche aata hai, lower sirf upar (jab tak close band na tode)"],
        ["Supertrend direction", "uptrend me line = lower band; close < lower band -> downtrend (line = upper band); close > upper band -> uptrend"],
        ["MA", "SMA(n) = n close ka average;  EMA = EMA_prev + 2/(n+1) x (close - EMA_prev)"],
        ["Entry", "close > Supertrend AND close > MA, aur pichhli candle pe ye dono saath nahi;  fill = agla 1-min futures open + slippage"],
        ["Exit (close)", "candle close Supertrend ke neeche (direction palti);  fill = agla 1-min open - slippage"],
        ["Exit (touch variant)", "pichhli candle close pe Supertrend line = stop; jis 1-min candle ne chhua uske agle 1-min open pe"],
        ["Points (per unit)", "side x (exit fill - entry fill) x size (short 0.5)"],
        ["Slippage", "slip x (2 + 2 x rolls) points"],
        ["Futures charges (per order)", "Rs 20 + STT (sell, 0.05% aaj) + NSE 0.00173% + SEBI 0.0001% + stamp (buy) 0.002% + GST 18% x (brokerage + NSE + SEBI); value = price x lot"],
        ["Synthetic charges", "4 option order (buy CE + sell PE, wapas) ; premium = Black-Scholes ATM, India VIX vol, 30 din; STT 0.15% sell premium, NSE 0.03503%, stamp 0.003%"],
        ["Charges in points", "charges Rs per lot / lot size"],
        ["Net points", "points - slippage - charges"],
        ["Lots (Rs account)", "floor(equity x leverage x dd_factor / (futures price x lot size))"],
        ["DD rule", "DD = (peak - equity)/peak; > 5% -> dd_factor 0.5, > 10% -> 0.25, naya high -> 1"],
        ["Win rate / PF / expectancy", "net > 0 trades %;  sab profit / sab loss;  net points / trades"],
        ["Max drawdown / time drawdown", "peak se sabse bada gir (roz ka mark-to-market); do equity high ke beech sabse lamba samay"],
        ["CAGR / Return %", "(aakhri equity / 7.5 lakh)^(1/saal) - 1;  saal ka Rs net / saal ki shuruaati equity"],
        ["Top 5% share", "sabse acchi 5% trades ke net points / kul net points"],
        ["Plateau", "setting + har padosi (ATR period, multiplier, MA length ek step) ka fit PF > 1"],
        ["Random control", "1,000 baar: random entry candle, wahi Supertrend exit, wahi trade count; p = (random avg >= rule avg ki ginti + 1) / 1001"],
    ]
    settings = [["Instrument", "NIFTY futures (near month, roll 3 din pehle)", 1], ["Candle", "1 ghanta, 09:15 se, 15:15 chhoti candle shamil (L1)", 1],
                ["Supertrend", f"ATR {P['atr_n']} x {P['mult']:g}", 1], ["MA", f"{P['ma_type']} {P['ma_len']} (sirf entry)", 1], ["Exit", "candle close Supertrend ke paar", 1],
                ["Side", "long-only (long+short alag)", 0], ["Fill", "agla 1-min futures open", 0], ["Slippage", "2 pt har fill (1 / 5 bhi)", 1],
                ["Charges", "aaj ke rates: STT 0.05% sell, Rs 20/order...", 1], ["Capital", "Rs 7.5 lakh, compounding", 0], ["Leverage", "2x (1 / 1.5 / 3 bhi)", 1],
                ["DD rule", "off (on alag)", 0], ["Lot size", "75 / 50 / 25 / 75 / 65 date se", 0], ["Fit / test", f"pehle 60%, {res['split'].date()} se test", 1],
                ["Grid", "ATR 7/10/14 x 2/3/4 x SMA/EMA x 40/50/60", 0]]
    return rules, formulas, settings


def data_card(q):
    cov = pd.read_csv(Path(CONFIG.get("bars", pdata.BARS)) / "coverage.csv", parse_dates=["first", "last"]) if (pdata.BARS / "coverage.csv").exists() else None
    rows = []
    want = ["NIFTY50", "NIFTY_F1", "NIFTY_F2", "INDIAVIX", "BANKNIFTY", "GOLD_F1", "GOLD_F2"]
    if cov is not None:
        for n in want:
            g = cov[cov.name == n].set_index("tf")
            if len(g):
                rows.append([n, f"{g.loc['1D', 'first']:%Y-%m-%d} -> {g.loc['1D', 'last']:%Y-%m-%d}" if "1D" in g.index else "-",
                             f"{g.loc['1m', 'first']:%Y-%m-%d} -> {g.loc['1m', 'last']:%Y-%m-%d} ({int(g.loc['1m', 'rows']):,})" if "1m" in g.index else "-"])
    qs = q[q["count"] > 0]
    return {"coverage_cols": ["Series", "Daily", "1-minute (bars)"], "coverage": rows,
            "quality": [[f"{r.dataset}: {r.check}", r["count"], str(r.examples)[:140], r.handling] for _, r in qs.iterrows()],
            "note": "Fyers se (python -m fyers_data.download_bars). _F1 = near month, _F2 = next month futures; inse apni continuous series (roll 3 din pehle)."}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args()
    out = result_dir(NAME)
    if a.notify:
        from backtest.notify import send, send_document
        s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        m, num = s["metrics"], int(out.name.split("_", 1)[0])
        P = s["P"]
        send(f"Backtest khatam: NIFTY 1-ghanta Supertrend + MA - {TITLE_SUFFIX} (@ayushanand440)\n"
             f"Chuni setting (sirf fit se): ATR {P['atr_n']} x {P['mult']}, {P['ma_type']} {P['ma_len']}, long-only futures\n"
             f"Poora 2018-2026: {m['full']['trades']} trade ({m['full']['trades_per_year']:.0f}/saal), net {m['full']['net_pts']:,.0f} pts/unit, PF {m['full']['pf']:.2f}\n"
             f"Test {s['split']} se: {m['oos']['trades']} trade, {m['oos']['net_pts']:,.0f} pts, PF {m['oos']['pf']:.2f}\n"
             f"Random se behtar? p = {s['random']['any']['p']:.3f} (uptrend random p = {s['random']['up']['p']:.3f})\n{s['verdict']['text']}\n"
             f"Reports website: {SITE_URL}\nYe report: {SITE_URL}#report-{num}")
        send_document(out / "report.html", caption=f"NIFTY Supertrend + MA - {TITLE_SUFFIX} (report {num:02d})")
        return 0
    q, _ = quality.report(save_to=pdata.BARS / "quality_report_stma.csv")
    res = run.module_a(out)
    rules, formulas, settings = texts(res)
    side_path = out / "side.json"
    side = json.loads(side_path.read_text(encoding="utf-8")) if side_path.exists() else {}
    p = report.write_html(res, out, settings=settings, rules=rules, formulas=formulas, data_card=data_card(q), side=side)
    print(p, round(p.stat().st_size / 1e6, 1), "MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

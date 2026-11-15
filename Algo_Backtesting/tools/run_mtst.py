"""NIFTY multi-timeframe Supertrend backtest (daily 7x3 trend filter + 1-hour 7x3 entries / exits, 1% fixed-fractional).

    python tools/run_mtst.py                 # data checks + validation + full test -> results/NN_mtst_nifty_supertrend/
    python tools/run_mtst.py --notify        # after the website update: finish message + HTML report to Telegram
Data: ../Algo_Historical_Data/data/bars (python -m fyers_data.download_bars --only NIFTY50 NIFTY_F1 NIFTY_F2 INDIAVIX).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backtest.config import result_dir  # noqa: E402
from backtest.mtst import data, report, run  # noqa: E402
from backtest.mtst.config import CONFIG, NAME, TITLE_SUFFIX  # noqa: E402

SITE = json.loads((Path(__file__).resolve().parents[1] / "site" / "site.json").read_text(encoding="utf-8"))["url"]


def texts(res):
    split = res["ctx"]["split"].date()
    rules = [
        "Chart: NIFTY 50 spot. Do Supertrend (ATR 7, multiplier 3, Wilder ATR jaise TradingView): ek daily candle pe, ek 1-ghanta candle pe.",
        "1-ghanta candle 1-minute data se banayi, 09:15 se: 09:15, 10:15, 11:15, 12:15, 13:15, 14:15 aur aakhri chhoti candle 15:15-15:30.",
        "Trend filter: daily Supertrend SELL mode me ho to sirf SHORT, BUY mode me ho to sirf LONG. Din ke beech hamesha PICHHLE poore daily candle ka mode (aaj ki adhuri candle nahi). 15:15 wali candle 15:30 pe din ke saath band hoti hai, isliye wo aaj ka daily mode dekhti hai.",
        "Entry: har 1-ghanta candle band hone pe dekho. Jab 1h Supertrend ki direction = daily direction ABHI bani (1h palta ya daily palta), aur koi position nahi, to usi direction me trade.",
        "Order: NIFTY near-month futures, agle 1-minute candle ke open pe (15:15 candle ka signal = agle din 09:15 open). Slippage 2 point har taraf.",
        "Stop-loss: entry ke waqt 1h Supertrend ki line (short me upper band, long me lower band). Exit: jab 1h candle line ke paar BAND ho (1h flip). Line profit ki taraf khisakti hai, to yahi trailing stop hai.",
        "Exit ke baad nayi entry ke liye nayi alignment chahiye. Choppy time me baar-baar signal aate hain: sab trade kiye.",
        "Size: risk = 1% x equity. Lots = round(risk / (stop points x lot size)), kam se kam 1. Lots <= equity / Rs 1.5 lakh margin. Profit COMPOUND hota hai.",
        "Futures roll: expiry se 3 trading din pehle agla contract; roll ka gap P&L me nahi; roll pe 2 order + slippage lagta hai.",
        f"Kharche aaj ke rates se (STT 0.05% sell, Rs 20/order, NSE, SEBI, stamp, GST). Data Feb 2018 - Nov 2026; fit = {split} se pehle, test = baad me.",
    ]
    formulas = [
        ["True range", "TR = max(H - L, |H - C_prev|, |L - C_prev|)"],
        ["ATR (Wilder / RMA)", "ATR = (ATR_prev x (n - 1) + TR) / n, shuru me pehle n TR ka average;  SMA variant: ATR = n TR ka average"],
        ["Supertrend bands", "upper = (H+L)/2 + 3 x ATR (sirf neeche aata hai jab tak close upar na tode), lower = (H+L)/2 - 3 x ATR (sirf upar jaata hai)"],
        ["Supertrend mode", "SELL mode me close > upper -> BUY mode (line = lower);  BUY mode me close < lower -> SELL mode (line = upper)"],
        ["Daily mode at a 1h candle", "aakhri daily candle jiska close (15:30) <= 1h candle ka close time"],
        ["Entry (E1)", "a = 1h mode agar 1h mode = daily mode, warna 0;  entry jab a != 0 aur a != pichhli candle ka a"],
        ["Fill", "agla 1-min futures open + slippage (long) / - slippage (short)"],
        ["Initial stop / stop distance", "stop = 1h Supertrend line entry candle pe;  stop distance = |signal close - stop| (spot points)"],
        ["Exit", "1h candle ka close line ke paar (mode palta) -> agla 1-min futures open"],
        ["Lots", "round(1% x equity / (stop distance x lot size)), min 1;  max floor(equity / 1.5 lakh)"],
        ["Points (per unit)", "side x (exit fill - entry fill)  (back-adjusted futures, roll gap nahi)"],
        ["Slippage", "2 pt x (2 + 2 x rolls)"],
        ["Charges per order", "Rs 20 + STT 0.05% (sell) + NSE 0.00173% + SEBI 0.0001% + stamp 0.002% (buy) + GST 18% x (brokerage + NSE + SEBI); value = price x lot"],
        ["Net points / Rs", "net = points - slippage - charges/lot;  Rs = lots x lot x (points - slippage) - lots x charges"],
        ["Win rate / PF / expectancy", "net > 0 trades %;  sab profit / sab loss;  net / trades"],
        ["Max drawdown / time drawdown", "roz ke mark-to-market equity me peak se sabse bada gir;  do naye high ke beech sabse lamba samay"],
        ["CAGR / Return %", "(aakhri equity / 25 lakh)^(1/saal) - 1;  saal ya mahine ka Rs net / us waqt ki shuruaati equity"],
        ["Top 5% share", "sabse acche 5% trades ke net points / kul net points"],
        ["Plateau", "setting + har padosi (1h ATR, 1h mult, daily ATR, daily mult me ek step) ka fit PF > 1"],
        ["Random control", "1,000 baar random candle pe entry, wahi exit, wahi trade count;  p = (random avg >= rule avg wale runs + 1) / 1001"],
    ]
    settings = [["Instrument", "NIFTY near-month futures (roll 3 din pehle)", 1], ["Signal chart", "NIFTY 50 spot (futures chart variant)", 0],
                ["Daily filter", "Supertrend 7 x 3 (pichhla poora din)", 1], ["1h entry / exit", "Supertrend 7 x 3, close pe flip", 1], ["ATR", "Wilder / RMA (SMA variant)", 0],
                ["Entry", "E1: alignment abhi bani (E2 variant)", 0], ["Side", "long + short", 1], ["15:15 candle", "shamil (defer / drop variant)", 0],
                ["Fill", "agla 1-min futures open", 0], ["Slippage", "2 pt har fill (1 / 5 bhi)", 1], ["Charges", "aaj ke rates: STT 0.05% sell, Rs 20/order...", 1],
                ["Capital", "Rs 25 lakh, compounding", 1], ["Risk", "1% / trade (0.5% / 2% bhi)", 1], ["Lots", "round, min 1; max equity / 1.5L margin", 0],
                ["Lot size", "75 / 50 / 25 / 75 / 65 date se", 0], ["Fit / test", f"pehle 60%, {split} se test", 1], ["Grid", "ATR 5/7/10/14 x 2/2.5/3/4 (1h aur daily)", 0]]
    return rules, formulas, settings


def data_card(cov, q):
    rows = [[r.dataset, r.symbol, r.first, r.last, f"{r.bars:,}", r.days, r.missing_days, r.short_days] for r in cov.itertuples()]
    qs = q[q["count"] > 0]
    return {"coverage_cols": ["Dataset", "Fyers symbol", "First", "Last", "Bars", "Trading days", "Missing days", "Short days"], "coverage": rows,
            "quality": [[f"{r.dataset}: {r.check}", r["count"], str(r.examples)[:160], r.handling] for _, r in qs.iterrows()],
            "note": "Fyers v3 history API (read-only). Fyers 1-minute history Dec 2017 / Jan 2018 se pehle nahi deta; daily NIFTY 2005 se. NIFTY_F1 = near, F2 = next month (Fyers continuous); apni roll series inse bani."}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args()
    out = result_dir(NAME)
    if a.notify:
        from backtest.notify import send, send_document
        s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        m, rs, num = s["metrics"], s["rs"], int(out.name.split("_", 1)[0])
        send(f"Backtest khatam: NIFTY multi-timeframe Supertrend (daily 7x3 + 1h 7x3) - {TITLE_SUFFIX} (@ayushanand440)\n"
             f"Feb 2018 - Nov 2026, long+short futures, Rs 25 lakh, 1% risk compounding\n"
             f"{m['full']['trades']} trade ({m['full']['trades_per_year']:.0f}/saal), net {m['full']['net_pts']:,.0f} pts/unit, PF {m['full']['pf']:.2f}, win {m['full']['win_pct']:.0f}%\n"
             f"Rs: {rs['net_rs']:,.0f} net, CAGR {rs['cagr_pct']:.1f}%, max DD {rs['max_dd_pct']:.1f}%\n"
             f"Test {s['split']} se: {m['oos']['net_pts']:,.0f} pts, PF {m['oos']['pf']:.2f}\n"
             f"Random se behtar? p = {s['random']['trend']['p']:.2f}. Buy-and-hold CAGR {s['buy_hold']['rs_curve']['cagr_pct']:.1f}%\n{s['verdict']['text']}\n"
             f"Reports website: {SITE}\nYe report: {SITE}#report-{num}")
        send_document(out / "report.html", caption=f"NIFTY MTF Supertrend - {TITLE_SUFFIX} (report {num:02d})")
        return 0
    ctx = run.context()
    cov = data.coverage(ctx["M"])
    q = data.quality_rows(ctx["M"])
    print(cov.to_string(index=False))
    out.mkdir(parents=True, exist_ok=True)
    cov.to_csv(out / "coverage.csv", index=False)
    q.to_csv(out / "data_quality.csv", index=False)
    res = run.everything(ctx, out)
    report.pngs(res, out)
    rules, formulas, settings = texts(res)
    p = report.write_html(res, out, settings=settings, rules=rules, formulas=formulas, data_card=data_card(cov, q))
    print(p, round(p.stat().st_size / 1e6, 1), "MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

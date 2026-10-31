"""VMU ORB V4: prepare 2024 -> Oct 2026, test 54 combinations on TRAIN, run the chosen one on TRAIN and TEST, report, send.

    python tools/run_orb_v4.py
    python tools/run_orb_v4.py --cached --no-telegram      # reuse results/<name>/candidates.pkl
"""
import argparse
import itertools
import pickle
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from backtest import orb  # noqa: E402
from backtest.config import RESULTS  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402
from backtest.orb_report import write_html  # noqa: E402
from backtest.vmu_report import totals  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
PERIODS = {"TRAIN": ("2024-01-01", "2025-12-31"), "TEST": ("2026-01-01", "2026-10-23")}
RULES = [
    "TRAIN 1 Jan 2024 to 31 Dec 2025 (parameters chosen here). TEST 1 Jan 2026 to 23 Oct 2026 (chosen parameters only run, never changed). NSE 1-minute candles.",
    "Universe each day: stocks NOT in F&O that day and allowed for MIS (point-in-time, from NSE's daily price band list).",
    "Capital Rs 70,000; max 2 trades a day; Rs 35,000 margin each, 5x MIS = Rs 1,75,000 exposure; qty = floor(175000 / entry price); no compounding.",
    "Market filter: Nifty gap % = (today's open - yesterday's close) / yesterday's close x 100 must be between -1.5% and +1.5%. "
    "BUY trades only if Nifty at the end of the opening range is above its 9:15 open; SELL trades only if below.",
    "Opening range (OR) = 9:15 to 9:30 (15 min) or 9:15 to 9:45 (30 min). OR volume >= N x average OR volume of the previous 5 days; OR turnover >= Rs 5 crore.",
    "Stock price Rs 50 to Rs 5,000. OR range % = (OR high - OR low) / OR open x 100 between 1% and 5%. Price at least 3% away from the upper and lower circuit.",
    "Ranking: by volume multiple, top 2 stocks of the day.",
    "Entry between the end of the OR and 11:00: BUY when price goes above OR high, entry = OR high + 0.05%; SELL when price goes below OR low, entry = OR low - 0.05%. "
    "If the candle opens beyond the trigger, entry = that candle's open. One trade per stock per day; no breakout by 11:00 = stock dropped.",
    "Stop-loss: BUY at OR low, SELL at OR high, but at most X% from entry (X = 2%, 2.5% or 3%). R = distance from entry to SL.",
    "At 1R profit the SL moves to entry (breakeven). At P x R profit half the quantity is booked (P = 1.5, 2 or 3). "
    "The other half trails: SL = low (BUY) / high (SELL) of the last 2 completed 15-minute candles.",
    "Exit on SL / breakeven / trailing SL, or square-off at 15:10. Daily loss limit: once the day's loss reaches Rs 6,000 no new trade that day.",
    "If one candle touches both the SL and the target, the SL counts. A candle that opens beyond the SL fills at its open.",
    "Costs 0.10% per trade. Slippage 0.3% (main), also run at 0% and 0.5%.",
    "Parameter test on TRAIN only: volume 3x / 5x / 7x, max SL 2% / 2.5% / 3%, partial booking 1.5R / 2R / 3R, opening range 15 / 30 min = 54 combinations.",
]
NOTES = [
    "Best combination = highest TRAIN net profit at 0.3% slippage among combinations with at least 100 TRAIN trades.",
    "F&O membership each day comes from NSE's price band list for that day (sec_list): stocks marked 'No Band' are the F&O stocks.",
    "MIS allowed is not archived anywhere (each broker keeps its own list). Proxy used: EQ series, price band not 2%, not under GSM. ASM lists were not applied.",
    "Circuit limits = previous day's close x (1 +- band %), band from that day's NSE list. The 3% distance and the Rs 50 - 5,000 price check use the price at the end of the OR.",
    "Nifty 'at 9:30' = close of the last 1-minute candle inside the OR (9:29 for 15 min, 9:44 for 30 min).",
    "The top 2 are picked at the end of the OR. If a picked stock never breaks out (or breaks out the wrong way for the Nifty direction) it is not replaced.",
    "The SL is also checked on the breakout candle itself (worst case). Breakeven and trailing moves apply from the next candle; the trailing SL only tightens.",
    "Half quantity = floor(qty / 2). The trailing SL starts after the half is booked (breakeven is already on by then).",
    "Daily loss limit: a second trade is skipped if trades already closed before its entry lost Rs 6,000 or more in total.",
    "The 0.10% cost is on the entry trade value. Slippage is charged on every fill (entry, half booking, final exit).",
    "Turnover = sum of typical price ((high + low + close) / 3) x volume for each OR candle. Prices are as supplied by Fyers (adjusted for corporate actions).",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="orb_v4")
    ap.add_argument("--cached", action="store_true")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    tell = (lambda m: None) if a.no_telegram else send
    out = RESULTS / a.name
    out.mkdir(parents=True, exist_ok=True)
    t0 = last = time.time()
    tell("VMU ORB V4 BACKTEST STARTED. TRAIN 2024-2025 (54 combinations), then TEST Jan-Oct 2026 with the chosen one. Progress every 10 min.")

    def on_day(done, total, n):
        nonlocal last
        if time.time() - last >= 600:
            last = time.time()
            tell(f"VMU ORB V4 progress: reading day {done}/{total}, {n} candidate stocks so far, {(time.time() - t0) / 60:.0f} min elapsed")

    try:
        pkl = out / "candidates.pkl"
        if a.cached and pkl.exists():
            cands, skipped = pickle.load(open(pkl, "rb"))
        else:
            cands, skipped = orb.prepare(PERIODS["TRAIN"][0], PERIODS["TEST"][1], on_day)
            pickle.dump((cands, skipped), open(pkl, "wb"))
        print("candidates", len(cands), "skipped days", skipped, flush=True)
        runner = orb.Runner(cands)
        slip = orb.SLIPPAGES[orb.SELECT_SLIP]
        rows = []
        for L, vm, sl, pr in itertools.product(orb.OR_MINS, orb.VOL_MULTS, orb.MAX_SLS, orb.PARTIALS):
            sm = orb.summary(runner.run(vm, sl, pr, L, slip, PERIODS["TRAIN"]))
            rows.append({"or_min": L, "vol_mult": vm, "max_sl": sl, "partial_r": pr, **sm})
        grid = pd.DataFrame(rows)
        ok = grid[grid.trades >= orb.MIN_TRAIN_TRADES]
        bi = (ok if len(ok) else grid).net.idxmax()
        grid["chosen"] = grid.index == bi
        best = grid.loc[bi, ["or_min", "vol_mult", "max_sl", "partial_r"]].to_dict()
        grid.to_csv(out / "train_combinations.csv", index=False)
        runs = {p: {k: runner.run(best["vol_mult"], best["max_sl"], best["partial_r"], int(best["or_min"]), s, d) for k, s in orb.SLIPPAGES.items()}
                for p, d in PERIODS.items()}
        for p, r in runs.items():
            for k, t in r.items():
                t.to_csv(out / f"trades_{p.lower()}_slip_{k.rstrip('%')}pct.csv", index=False)
        write_html(out / "report.html", runs, grid, best, RULES, NOTES, {p: f"{d[0]} to {d[1]}" for p, d in PERIODS.items()})
    except Exception:
        tell(f"{TAG_TECH} VMU ORB V4 backtest FAILED:\n{traceback.format_exc()[-1500:]}")
        raise
    lines = []
    for p, r in runs.items():
        for k, t in r.items():
            if t.empty:
                lines.append(f"{p} slip {k}: no trades")
                continue
            s = totals(t)
            lines.append(f"{p} slip {k}: {s['trades']} trades | win {s['win_pct']:.1f}% | net Rs {s['net']:,.0f} ({s['ret_pct']:.0f}%) | "
                         f"PF {s['profit_factor'] or 0:.2f} | max DD Rs {s['max_dd']:,.0f}")
    msg = (f"{TAG_TRADING} VMU ORB V4 BACKTEST DONE ({(time.time() - t0) / 60:.1f} min)\n"
           f"Chosen on TRAIN (best net at 0.3% slippage): OR {int(best['or_min'])} min, volume {best['vol_mult']}x, max SL {best['max_sl']:.1%}, "
           f"half booked at {best['partial_r']}R\n" + "\n".join(lines) + "\nFull details, all 54 combinations and every trade in the report file.")
    tell(msg)
    print(msg)
    if not a.no_telegram:
        send_document(out / "report.html", "VMU ORB V4 report (open in a browser)")
    print("done ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

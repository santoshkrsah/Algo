"""VMU Equity V3: prepare the data, run all 243 parameter combinations, write results/<name>/report.html and send it.

    python tools/run_vmu.py --start 2026-01-01 --end 2026-10-23 --name vmu_v3
    python tools/run_vmu.py --name vmu_v3 --cached --no-telegram    # reuse the prepared data
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

from backtest import vmu  # noqa: E402
from backtest.config import RESULTS  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402
from backtest.vmu_report import write_html  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
RULES = [
    "Period 1 Jan 2026 to 23 Oct 2026, NSE 1-minute candles. Universe: stocks that were not in F&O on that day.",
    "Capital Rs 70,000; max 2 trades a day; Rs 35,000 margin each, 5x leverage = Rs 1,75,000 exposure; qty = floor(175000 / entry); no compounding.",
    "Market filter: Nifty gap % = (today's open - yesterday's close) / yesterday's close. Trade only if the gap is between -1.5% and +1.5%.",
    "Signal candle = 9:15 to 9:25 (10 minutes). Needs volume >= N x average 9:15-9:25 volume of the last 3 trading days, and turnover >= Rs 5 crore.",
    "Green candle = BUY, red = SELL (short), flat = skip. Rank by range % ((high - low) / open) and take the top 2.",
    "Entry = open of the 9:25 one-minute candle. Initial SL: candle low/high (max 4% from entry), or fixed 3% / 4%.",
    "Breakeven: profit reaches the trigger -> SL moves to entry. Trailing: after +2% profit SL trails the best price by the gap. SL only moves in the profit direction.",
    "Exit on any stop, or at 15:15 square-off. If one candle touches both SL and a new high/low, SL is assumed first. A gap beyond the SL fills at that candle's open.",
    "Costs 0.10% per trade. Slippage 0%, 0.3% and 0.5% each run separately. Stops move after a candle closes and apply from the next candle.",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-10-23")
    ap.add_argument("--name", default="vmu_v3")
    ap.add_argument("--cached", action="store_true")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    tell = (lambda m: None) if a.no_telegram else send
    out = RESULTS / a.name
    out.mkdir(parents=True, exist_ok=True)
    t0 = last = time.time()
    tell(f"VMU V3 BACKTEST STARTED ({a.start} to {a.end}). Progress every 10 min.")

    def on_day(done, total, n):
        nonlocal last
        if time.time() - last >= 600:
            last = time.time()
            tell(f"VMU V3 progress: day {done}/{total}, {n} candidates, {(time.time() - t0) / 60:.0f} min elapsed")

    try:
        pkl = out / "candidates.pkl"
        if a.cached and pkl.exists():
            cands = pickle.load(open(pkl, "rb"))
        else:
            cands = vmu.prepare(a.start, a.end, on_day)
            pickle.dump(cands, open(pkl, "wb"))
        rows = []
        for vm, sl, be, tg, sp in itertools.product(vmu.VOL_MULTS, vmu.SL_MODES, vmu.BE_TRIGGERS, vmu.TRAIL_GAPS, vmu.SLIPPAGES):
            sm = vmu.summary(vmu.simulate(cands, vm, sl, be, tg, sp))
            sm.update(vol_mult=vm, sl_mode=sl, be=be, trail=tg, slip=sp)
            sm["score"] = round(vmu.match_score(sm), 4)
            rows.append(sm)
        grid = pd.DataFrame(rows).reset_index(drop=True)
        best = grid.loc[grid.score.idxmin()].to_dict()
        best["idx"] = int(grid.score.idxmin())
        trades = vmu.simulate(cands, best["vol_mult"], best["sl_mode"], best["be"], best["trail"], best["slip"])
        grid.drop(columns=[]).to_csv(out / "combinations.csv", index=False)
        trades.to_csv(out / "trades_best_match.csv", index=False)
        write_html(out / "report.html", trades, grid, best, RULES, f"{a.start} to {a.end}")
    except Exception:
        tell(f"{TAG_TECH} VMU V3 backtest FAILED:\n{traceback.format_exc()[-1500:]}")
        raise
    tell(f"{TAG_TRADING} VMU V3 BACKTEST DONE ({(time.time() - t0) / 60:.1f} min). 243 combinations tested.\n"
         f"Closest to expected: volume {best['vol_mult']}x, SL {best['sl_mode']}, breakeven {best['be']:.1%}, trail {best['trail']:.1%}, slippage {best['slip']:.1%}\n"
         f"Trades {int(best['trades'])} | win {best['win_pct']}% | net Rs {int(best['net']):,} | PF {best['profit_factor']}\n"
         f"Expected: ~200 trades, 67.5% win, Rs 5.7 lakh. Result does not match; details in the report file.")
    if not a.no_telegram:
        send_document(out / "report.html", "VMU Equity V3 report (open in a browser)")
    print("done ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

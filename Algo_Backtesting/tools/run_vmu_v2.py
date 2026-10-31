"""VMU Equity V2 (7x volume, fixed 3% SL, no breakeven/trailing): run at 0%, 0.3% and 0.5% slippage, write the report, send it.

    python tools/run_vmu_v2.py                                   # prepares the data (reads every day file)
    python tools/run_vmu_v2.py --cached results/vmu_v3/candidates.pkl   # reuse candidates prepared for the same period
"""
import argparse
import dataclasses
import pickle
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from backtest import vmu  # noqa: E402
from backtest.config import RESULTS, UNIVERSE_CSV  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402
from backtest.vmu_report import totals, write_html_v2  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
VOL_MULT, SL_MODE = 7, "fixed3"
SLIPS = {"0%": 0.0, "0.3%": 0.003, "0.5%": 0.005}
EXPECTED = {"trades": 154, "win_pct": 68.8, "net": 280_000, "worst_trade": -5485}
RULES = [
    "Period 1 Jan 2026 to 23 Oct 2026, NSE 1-minute candles. Universe: each day, the NSE stocks that were NOT in F&O on that day.",
    "Capital Rs 70,000; max 2 trades a day; Rs 35,000 margin each, 5x MIS leverage = Rs 1,75,000 exposure; qty = floor(175000 / entry price); no compounding.",
    "Market filter: Nifty gap % = (today's open - yesterday's close) / yesterday's close x 100. No trades that day unless the gap is between -1.5% and +1.5%.",
    "Signal candle (checked at 9:25) = 9:15 to 9:25: open of 9:15, close of the 9:24 candle, 10-minute high/low, total volume.",
    "Volume filter: signal volume >= 7 x average 9:15-9:25 volume of the previous 3 trading days. Turnover filter: 9:15-9:25 turnover >= Rs 5 crore.",
    "Direction: close > open = BUY, close < open = SELL (short), close = open = skip.",
    "Ranking: range % = (high - low) / open x 100, largest first; take the top 2.",
    "Entry: market order at 9:25 = open of the 9:25 one-minute candle.",
    "Stop-loss: BUY at entry x 0.97, SELL at entry x 1.03. If a candle opens beyond the SL (gap), the fill is that candle's open.",
    "Exit: SL hit, or square-off at 15:15 (open of the 15:15 candle). No target, no trailing SL, no breakeven.",
    "Costs: 0.10% of turnover per trade. Slippage run separately at 0%, 0.3% and 0.5%.",
]
NOTES = [
    "Slippage is charged on both sides (entry and exit) at the chosen %. The 0.10% cost is on the entry trade value.",
    "Turnover = sum over the ten 1-minute candles of typical price ((high + low + close) / 3) x volume.",
    "Point-in-time F&O list: estimated from the date each stock's futures data starts (only current contracts are stored). "
    "Stocks that LEFT F&O before Nov 2026 are therefore counted as non-F&O for the whole period.",
    "The SL can trigger on the entry candle itself (9:25). The 15:15 candle is the exit candle, so the SL is checked up to 15:14.",
    "Max drawdown % = deepest fall from a peak, divided by the account value at that peak (Rs 70,000 + profit so far).",
    "A stock needs a 9:25 candle (it traded at 9:25) and 3 previous days of 9:15-9:25 volume to qualify.",
]


def checks(cands: list) -> list[list]:
    """Same data, one change at a time (0% slippage), to look for the cause of the gap to the expected result."""
    def row(name, cs, key=lambda c: -c.range_pct, vm=VOL_MULT):
        by: dict[str, list] = {}
        for c in cs:
            if c.ratio >= vm:
                by.setdefault(c.day, []).append(c)
        t = pd.DataFrame([vmu.simulate_trade(c, SL_MODE, None, None, 0.0) for d in sorted(by) for c in sorted(by[d], key=key)[:vmu.MAX_TRADES]])
        return [name, len(t), round((t.net_pnl > 0).mean() * 100, 1), round(t.net_pnl.sum())]

    u = pd.read_csv(UNIVERSE_CSV)
    u["sym"] = u.symbol.str.replace("NSE:", "").str.replace(r"-(EQ|BE)$", "", regex=True)
    fno = vmu.fno_first_dates()
    big = set(u[~u.sym.isin(fno)].sort_values("mcap_cr", ascending=False).sym.head(556))
    flipped = [dataclasses.replace(c, side=-c.side) for c in cands]
    return [
        row("As specified (7x volume, top 2 by range)", cands),
        row("Volume filter 5x instead of 7x", cands, vm=5),
        row("Volume filter 10x instead of 7x", cands, vm=10),
        row("Universe cut to the 556 largest non-F&O stocks by market cap", [c for c in cands if c.symbol in big]),
        row("Direction flipped (green candle = SELL, red = BUY)", flipped),
        row("Ranking by smallest range instead of largest", cands, key=lambda c: c.range_pct),
        row("Ranking by volume ratio instead of range", cands, key=lambda c: -c.ratio),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-10-23")
    ap.add_argument("--name", default="vmu_v2")
    ap.add_argument("--cached", help="candidates.pkl prepared for the same period")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    tell = (lambda m: None) if a.no_telegram else send
    out = RESULTS / a.name
    out.mkdir(parents=True, exist_ok=True)
    t0 = last = time.time()
    tell(f"VMU V2 BACKTEST STARTED ({a.start} to {a.end}). Volume 7x, fixed 3% SL, slippage 0 / 0.3 / 0.5%. Progress every 10 min.")

    def on_day(done, total, n):
        nonlocal last
        if time.time() - last >= 600:
            last = time.time()
            tell(f"VMU V2 progress: day {done}/{total}, {n} candidates, {(time.time() - t0) / 60:.0f} min elapsed")

    try:
        if a.cached:
            cands = pickle.load(open(a.cached, "rb"))
        else:
            cands = vmu.prepare(a.start, a.end, on_day)
            pickle.dump(cands, open(out / "candidates.pkl", "wb"))
        cands = [c for c in cands if a.start <= c.day <= a.end]
        runs = {k: vmu.simulate(cands, VOL_MULT, SL_MODE, None, None, s) for k, s in SLIPS.items()}
        for k, t in runs.items():
            t.to_csv(out / f"trades_slip_{k.rstrip('%')}pct.csv", index=False)
        write_html_v2(out / "report.html", runs, EXPECTED, RULES, NOTES, checks(cands), f"{a.start} to {a.end}")
    except Exception:
        tell(f"{TAG_TECH} VMU V2 backtest FAILED:\n{traceback.format_exc()[-1500:]}")
        raise
    lines = []
    for k, t in runs.items():
        s = totals(t)
        lines.append(f"Slippage {k}: {s['trades']} trades | win {s['win_pct']:.1f}% | net Rs {s['net']:,.0f} ({s['ret_pct']:.0f}%) | "
                     f"PF {s['profit_factor']:.2f} | max DD Rs {s['max_dd']:,.0f} | worst trade Rs {s['worst_trade']:,.0f}")
    msg = (f"{TAG_TRADING} VMU V2 BACKTEST DONE ({(time.time() - t0) / 60:.1f} min)\n" + "\n".join(lines)
           + f"\nExpected: ~{EXPECTED['trades']} trades, {EXPECTED['win_pct']}% win, net Rs {EXPECTED['net']:,}, worst trade Rs {EXPECTED['worst_trade']:,}.\n"
           "Checks done to find the difference are in the report's Rules used tab.")
    tell(msg)
    print(msg)
    if not a.no_telegram:
        send_document(out / "report.html", "VMU Equity V2 report (open in a browser)")
    print("done ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

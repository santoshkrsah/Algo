"""Run the strategy and write results/<name>/ (trades.csv, report.html), with Telegram updates.

    python tools/run_backtest.py --start 2026-01-01 --end 2026-10-31 --name jan_oct_2026
    python tools/run_backtest.py --start 2026-10-01 --end 2026-10-31 --name oct_2026 --no-telegram

A progress message goes to Telegram every 10 minutes while it runs; on finish the HTML report is sent (Ayush tagged),
on error Almax is tagged.
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backtest.config import RESULTS  # noqa: E402
from backtest.engine import run  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402
import pandas as pd  # noqa: E402
from backtest.report import stats, write_html  # noqa: E402
from backtest.strategy import Params  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
PROGRESS_EVERY = 600  # seconds
RULES = [
    "Buy when the first 15-minute candle of the day (09:15-09:30) closes above the Fibonacci R3 pivot of the previous day.",
    "R3 = Pivot + (High - Low) of yesterday, Pivot = (High + Low + Close) / 3. Long trades only.",
    "Buy at that candle close (about 09:30). Stop-loss 3% below the buy price. Otherwise sell at 15:00.",
    "Rs 2 crore per trade, same amount for every trade, at most 5 stocks per day; small caps first, then mid, then large.",
    "Skip stocks priced below Rs 30. Costs: standard Fyers/NSE charges plus slippage.",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--from-trades", action="store_true", help="rebuild the report from an existing trades.csv")
    a = ap.parse_args()
    tell = (lambda m: None) if a.no_telegram else send
    out = RESULTS / a.name
    out.mkdir(parents=True, exist_ok=True)
    p = Params()
    title = f"Fibonacci R3 first-candle breakout ({a.name})"
    sub = (f"{a.start} to {a.end} | NSE equity intraday long | 15m | Rs {p.trade_value / 1e7:g} cr per trade, max "
           f"{p.max_trades_per_day}/day | SL {p.stop_loss_pct:.0%} | exit 15:00 | costs: standard + {p.slippage_pct:.2%} slippage/side")
    t0 = last = time.time()
    tell(f"BACKTEST STARTED: {title}\n{a.start} to {a.end}. Progress every 10 min.")

    def on_day(done, total, n):
        nonlocal last
        if time.time() - last >= PROGRESS_EVERY:
            last = time.time()
            tell(f"Backtest progress {a.name}: day {done}/{total} ({done / total:.0%}), {n} trades, "
                 f"{(time.time() - t0) / 60:.0f} min elapsed")

    try:
        trades = pd.read_csv(out / "trades.csv") if a.from_trades else run(a.start, a.end, p, on_day)
        if trades.empty:
            tell(f"{TAG_TRADING} Backtest {a.name} finished: no trades in this period.")
            return 0
        trades.to_csv(out / "trades.csv", index=False)
        capital = p.trade_value * p.max_trades_per_day
        s = stats(trades, capital)
        write_html(out / "report.html", title, sub, s, trades, RULES, capital)
    except Exception:
        tell(f"{TAG_TECH} Backtest {a.name} FAILED:\n{traceback.format_exc()[-1500:]}")
        raise
    tell(f"{TAG_TRADING} BACKTEST DONE: {a.name} ({(time.time() - t0) / 60:.1f} min)\n"
         f"Trades {s['Total trades']:,} | Net profit Rs {s['Net profit (Rs)']:,.0f} | Win rate {s['Win rate']:.1%} | "
         f"Profit factor {s['Profit factor']:.2f} | Max drawdown Rs {s['Max drawdown (Rs)']:,.0f}\nReport file follows.")
    if not a.no_telegram:
        send_document(out / "report.html", f"Backtest report: {a.name} (open the file in a browser)")
    print(f"done -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

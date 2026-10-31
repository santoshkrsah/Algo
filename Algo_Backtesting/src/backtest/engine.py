"""Day-by-day backtest: reads one daily file at a time (keeps memory low on 8 GB) and returns the trade list.

    from backtest.engine import run
    trades = run("2026-01-01", "2026-10-31")
"""
import math
from typing import Callable

import pandas as pd

from backtest.config import EQUITY, UNIVERSE_CSV
from backtest.data import COLUMNS, trading_days
from backtest.strategy import Params, costs, fib_r3

FIRST_CANDLE = pd.Timedelta(hours=9, minutes=15)


def _read_day(day: str) -> pd.DataFrame:
    df = pd.read_csv(EQUITY / f"{day}.csv", usecols=COLUMNS, parse_dates=["datetime"])
    return df[df["timeframe"].isin(["15m", "1m"])]


def _day_hlc(m15: pd.DataFrame) -> pd.DataFrame:
    g = m15.sort_values("datetime").groupby("symbol")
    return pd.DataFrame({"high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last()})


def _categories() -> dict[str, str]:
    u = pd.read_csv(UNIVERSE_CSV)
    return dict(zip(u["symbol"].str.replace(r"^NSE:|-EQ$", "", regex=True), u["category"]))


def _exit(bars: pd.DataFrame, entry: float, p: Params):
    """bars: 1m candles from the entry minute on, sorted. Returns (exit_price, exit_time, reason)."""
    sl = entry * (1 - p.stop_loss_pct)
    day = bars["datetime"].dt.normalize().iloc[0]
    sq = day + pd.Timedelta(hours=int(p.squareoff[:2]), minutes=int(p.squareoff[3:]))
    live = bars[bars["datetime"] < sq]
    hit = live[live["low"] <= sl]
    if len(hit):
        r = hit.iloc[0]
        return min(r["open"], sl), r["datetime"], "stop-loss"
    late = bars[bars["datetime"] >= sq]
    if len(late):
        r = late.iloc[0]
        return r["open"], r["datetime"], "3pm square-off"
    r = live.iloc[-1]
    return r["close"], r["datetime"], "last candle"


def run(start, end, params: Params | None = None, on_day: Callable[[int, int, int], None] | None = None) -> pd.DataFrame:
    """Backtest start..end (inclusive). on_day(done, total, trades_so_far) is called after each day."""
    p = params or Params()
    cats = _categories()
    rank = {c: i for i, c in enumerate(p.category_order)}
    all_days = trading_days()
    days = trading_days(start, end)
    if not days:
        raise ValueError("no data files in that date range")
    prev = _day_hlc(_read_day(all_days[all_days.index(days[0]) - 1]).query("timeframe == '15m'"))
    trades = []
    for i, day in enumerate(days, 1):
        df = _read_day(day)
        m15 = df[df["timeframe"] == "15m"]
        first = m15[m15["datetime"] == pd.Timestamp(day) + FIRST_CANDLE].set_index("symbol")
        j = first.join(prev, rsuffix="_prev", how="inner")
        if len(j):
            j["r3"] = fib_r3(j["high_prev"], j["low_prev"], j["close_prev"])
            sig = j[(j["close"] > j["r3"]) & (j["close"] >= p.min_price)].copy()
            sig["cat"] = sig.index.map(lambda s: cats.get(s, "Small"))
            sig["rank"] = sig["cat"].map(rank)
            sig = sig.sort_values(["rank", "volume"], ascending=[True, False]).head(p.max_trades_per_day)
            m1 = df[(df["timeframe"] == "1m") & df["symbol"].isin(sig.index)]
            for sym, row in sig.iterrows():
                bars = m1[(m1["symbol"] == sym) & (m1["datetime"] >= pd.Timestamp(day) + pd.Timedelta(hours=9, minutes=30))]
                if bars.empty:
                    continue
                bars = bars.sort_values("datetime")
                entry = row["close"] * (1 + p.slippage_pct)
                qty = math.floor(p.trade_value / entry)
                if qty < 1:
                    continue
                px, t, why = _exit(bars, row["close"], p)
                sell = px * (1 - p.slippage_pct)
                gross = qty * (sell - entry)
                fees = costs(p, qty, entry, sell)
                trades.append({
                    "date": day, "symbol": sym, "category": row["cat"], "qty": qty,
                    "r3": round(row["r3"], 2), "entry": round(entry, 2), "exit": round(sell, 2),
                    "exit_time": t.strftime("%H:%M"), "exit_reason": why,
                    "gross_pnl": round(gross, 2), "costs": round(fees, 2), "net_pnl": round(gross - fees, 2),
                    "first_candle_volume": int(row["volume"]),
                })
        prev = _day_hlc(m15)
        if on_day:
            on_day(i, len(days), len(trades))
    return pd.DataFrame(trades)

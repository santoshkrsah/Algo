"""Load equity candles from the daily files written by Algo_Historical_Data.

    from backtest.data import load_equity
    df = load_equity("RELIANCE", "5m", "2024-01-01", "2024-03-31")   # datetime-indexed OHLCV
"""
import pandas as pd

from backtest.config import EQUITY

TIMEFRAMES = ("1m", "5m", "15m", "30m", "60m")
COLUMNS = ["symbol", "timeframe", "datetime", "open", "high", "low", "close", "volume"]


def trading_days(start=None, end=None) -> list[str]:
    """Dates (YYYY-MM-DD) that have a data file, optionally limited to start..end."""
    days = sorted(p.stem for p in EQUITY.glob("????-??-??.csv"))
    if start:
        days = [d for d in days if d >= str(pd.Timestamp(start).date())]
    if end:
        days = [d for d in days if d <= str(pd.Timestamp(end).date())]
    return days


def load_equity(symbol: str | list[str], timeframe: str, start=None, end=None) -> pd.DataFrame:
    """Candles for one symbol (or a list) on one timeframe. Index = datetime; 'symbol' column kept."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {TIMEFRAMES}")
    symbols = {symbol} if isinstance(symbol, str) else set(symbol)
    parts = []
    for day in trading_days(start, end):
        df = pd.read_csv(EQUITY / f"{day}.csv", usecols=COLUMNS, parse_dates=["datetime"])
        parts.append(df[df["timeframe"].eq(timeframe) & df["symbol"].isin(symbols)])
    if not parts:
        return pd.DataFrame(columns=COLUMNS).set_index("datetime")
    out = pd.concat(parts, ignore_index=True).drop(columns="timeframe")
    return out.sort_values(["symbol", "datetime"]).set_index("datetime")

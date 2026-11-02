"""Fast reads from the Parquet store that Algo_Historical_Data builds (python -m fyers_data.store build).
When the store is missing, every function falls back to the daily CSVs, so code using this module never breaks.

    from backtest import fast
    df  = fast.read_day("2026-10-28", ("1m", "15m"))   # SAME columns and order as pd.read_csv(EQUITY / f"{day}.csv") -> drop-in
    m1  = fast.day_1m("2026-10-28")                     # compact: symbol, minute (555 = 9:15), open, high, low, close, volume
    one = fast.symbol_1m("RELIANCE", "2024-01-01", "2024-12-31")   # date, minute, OHLCV for one stock
    pan = fast.panel("2021-10-01", "2026-10-31")        # one row per stock-day (daily OHLCV, value, vwap, first 5/15/30 min, 15:15 open)
    pan = fast.add_features(pan)                        # prev day levels, gap, ATR14, avg value 20d, RVOL (previous days only)

Pick stocks on the panel first, then load minutes only for those stocks - that is what makes a backtest fast.
Prices pass through data/store/adjustments.csv (split/bonus factors); adjust=False returns the raw download.
"""
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.config import DATA, EQUITY

STORE = DATA / "store"
DAY, SYM, PANEL, DAILY, ADJ = STORE / "day", STORE / "sym", STORE / "panel", STORE / "daily.parquet", STORE / "adjustments.csv"
OPEN_MIN = 555
TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60}
TIMEFRAMES = tuple(TF_MIN)
COLUMNS = ["symbol", "timeframe", "datetime", "open", "high", "low", "close", "volume"]
PRICE = ["open", "high", "low", "close"]
PANEL_PRICE = PRICE + ["vwap", "open_1515"] + [f"o{k}_{x}" for k in (5, 15, 30) for x in ("open", "high", "low", "close")]
PANEL_VOL = ["volume"] + [f"o{k}_vol" for k in (5, 15, 30)]
HHMMSS = np.array([f"{m // 60:02d}:{m % 60:02d}:00" for m in range(24 * 60)], dtype=object)
_ADJ_CACHE: dict = {}


def read_pq(path: Path, columns=None, filters=None) -> pd.DataFrame:
    return pd.read_parquet(path, columns=columns, filters=filters)


def available() -> bool:
    """True once the store has been built."""
    return DAY.exists() and next(DAY.glob("????-??-??.parquet"), None) is not None


def days(start=None, end=None) -> list[str]:
    """Trading days in the store (YYYY-MM-DD), optionally limited to start..end."""
    ds = sorted(p.stem for p in DAY.glob("????-??-??.parquet"))
    if start:
        ds = [d for d in ds if d >= str(pd.Timestamp(start).date())]
    if end:
        ds = [d for d in ds if d <= str(pd.Timestamp(end).date())]
    return ds


# -------------------------------------------------------------------------------------------------- adjustments
def adjustments() -> pd.DataFrame:
    """symbol, before, factor, ...: rows dated before `before` get price / factor and volume x factor."""
    key = ADJ.stat().st_mtime_ns if ADJ.exists() else None
    if _ADJ_CACHE.get("key") != key or "df" not in _ADJ_CACHE:
        if key is None:
            a = pd.DataFrame({"symbol": pd.Series(dtype=object), "before": pd.Series(dtype=object), "factor": pd.Series(dtype=float)})
        else:
            a = pd.read_csv(ADJ, dtype={"symbol": str, "before": str})
            a["factor"] = pd.to_numeric(a["factor"], errors="coerce")
            a = a[a["factor"] > 0]
        _ADJ_CACHE.update(key=key, df=a)
    return _ADJ_CACHE["df"]


def _scale(df: pd.DataFrame, f: pd.Series, price_cols, vol_cols) -> pd.DataFrame:
    """Divide prices / multiply volumes by f (NaN = untouched), keeping integer volumes integer."""
    has = f.notna().to_numpy()
    if not has.any():
        return df
    df = df.copy()
    fv = f.to_numpy(dtype=np.float64)
    for c in price_cols:
        if c in df:
            x = df[c].to_numpy(dtype=np.float64, copy=True)
            x[has] = x[has] / fv[has]
            df[c] = x
    for c in vol_cols:
        if c in df:
            is_int = df[c].dtype.kind in "iu"
            x = df[c].to_numpy(dtype=np.float64, copy=True)
            x[has] = x[has] * fv[has]
            df[c] = np.round(x).astype(np.int64) if is_int else x
    return df


def _apply_day(df: pd.DataFrame, day: str) -> pd.DataFrame:
    a = adjustments()
    a = a[a["before"] > day]
    if a.empty or df.empty:
        return df
    return _scale(df, df["symbol"].map(a.groupby("symbol")["factor"].prod()), PRICE, ["volume"])


def _apply_rows(df: pd.DataFrame, symbol_col: str | None, symbol: str | None, price_cols, vol_cols) -> pd.DataFrame:
    """Per-row factors for frames that span many days (date column)."""
    a = adjustments()
    if a.empty or df.empty:
        return df
    sym = df[symbol_col].to_numpy(dtype=object) if symbol_col else np.full(len(df), symbol, dtype=object)
    a = a[a["symbol"].isin(set(sym))]
    if a.empty:
        return df
    f = np.ones(len(df))
    dates = df["date"].astype(str).to_numpy(dtype=object)
    for r in a.itertuples(index=False):
        f[(sym == r.symbol) & (dates < r.before)] *= r.factor
    return _scale(df, pd.Series(np.where(f != 1.0, f, np.nan), index=df.index), price_cols, vol_cols)


# ------------------------------------------------------------------------------------------------------- minutes
def _bounds(codes: np.ndarray) -> np.ndarray:
    return np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])


def to_timeframes(m1: pd.DataFrame, day: str, timeframes=TIMEFRAMES, parse_dates: bool = False) -> pd.DataFrame:
    """1m rows (symbol, minute, OHLCV; sorted by symbol, minute) -> daily-CSV format for these timeframes, ordered like the
    CSV (timeframe, symbol, datetime). Buckets anchored at 9:15, exactly like the downloader's resample."""
    sym = m1["symbol"].to_numpy(dtype=object)
    mins = m1["minute"].to_numpy().astype(np.int64)
    o, h, l, c = (m1[k].to_numpy(dtype=np.float64) for k in PRICE)
    v = m1["volume"].to_numpy()
    codes = pd.factorize(sym)[0].astype(np.int64)
    out = []
    for tf in [t for t in TF_MIN if t in timeframes]:
        k = TF_MIN[tf]
        if k == 1 or not len(m1):
            part = pd.DataFrame({"symbol": sym, "minute": mins, "open": o, "high": h, "low": l, "close": c, "volume": v})
        else:
            b = (mins - OPEN_MIN) // k
            s = _bounds(codes * 100_000 + (b + 50_000))
            e = np.r_[s[1:], len(b)] - 1
            part = pd.DataFrame({"symbol": sym[s], "minute": OPEN_MIN + b[s] * k, "open": o[s], "high": np.maximum.reduceat(h, s),
                                 "low": np.minimum.reduceat(l, s), "close": c[e], "volume": np.add.reduceat(v, s)})
        part.insert(1, "timeframe", tf)
        out.append(part)
    if not out:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.concat(out, ignore_index=True)
    dt = (day + " ") + pd.Series(HHMMSS[df["minute"].to_numpy(dtype=np.int64)], index=df.index, dtype=object)
    df.insert(2, "datetime", pd.to_datetime(dt, format="%Y-%m-%d %H:%M:%S") if parse_dates else dt)
    return df.drop(columns="minute")


def _csv_1m(day: str) -> pd.DataFrame:
    raw = pd.read_csv(EQUITY / f"{day}.csv", usecols=COLUMNS)
    raw = raw[raw["timeframe"] == "1m"]
    codes, uniq = pd.factorize(raw["datetime"])
    mins = np.array([int(str(u)[11:13]) * 60 + int(str(u)[14:16]) for u in uniq], dtype=np.int16)[codes]
    df = pd.DataFrame({"symbol": raw["symbol"].to_numpy(dtype=object), "minute": mins, **{k: raw[k].to_numpy() for k in PRICE},
                       "volume": raw["volume"].to_numpy()})
    return df.sort_values(["symbol", "minute"], kind="stable").reset_index(drop=True)


def day_1m(day: str, symbols=None, adjust: bool = True) -> pd.DataFrame:
    """1m candles of one day: symbol, minute, open, high, low, close, volume (sorted by symbol, minute)."""
    pq = DAY / f"{day}.parquet"
    if pq.exists():
        flt = [("symbol", "in", sorted(set(symbols)))] if symbols is not None else None
        df = read_pq(pq, filters=flt)
    else:
        df = _csv_1m(day)
        if symbols is not None:
            df = df[df["symbol"].isin(set(symbols))].reset_index(drop=True)
    return _apply_day(df, day) if adjust else df


def read_day(day: str, timeframes=TIMEFRAMES, parse_dates: bool = False, adjust: bool = True, symbols=None) -> pd.DataFrame:
    """Drop-in for pd.read_csv(EQUITY / f"{day}.csv", usecols=COLUMNS) restricted to `timeframes`."""
    if isinstance(timeframes, str):
        timeframes = (timeframes,)
    if not (DAY / f"{day}.parquet").exists():
        df = pd.read_csv(EQUITY / f"{day}.csv", usecols=COLUMNS, parse_dates=["datetime"] if parse_dates else False)
        df = df[df["timeframe"].isin(timeframes)]
        if symbols is not None:
            df = df[df["symbol"].isin(set(symbols))]
        return _apply_day(df, day) if adjust else df
    return to_timeframes(day_1m(day, symbols, adjust), day, timeframes, parse_dates)


def symbol_1m(symbol: str, start=None, end=None, adjust: bool = True) -> pd.DataFrame:
    """All 1m candles of one stock: date, minute, open, high, low, close, volume (sorted)."""
    s = str(pd.Timestamp(start).date()) if start else None
    e = str(pd.Timestamp(end).date()) if end else None
    years = sorted(p.name for p in SYM.iterdir() if p.is_dir() and p.name.isdigit()) if SYM.exists() else []
    years = [y for y in years if (not s or y >= s[:4]) and (not e or y <= e[:4])]
    parts = [read_pq(SYM / y / f"{symbol}.parquet") for y in years if (SYM / y / f"{symbol}.parquet").exists()]
    if not parts:
        return pd.DataFrame({"date": pd.Series(dtype=object), "minute": pd.Series(dtype=np.int16),
                             **{k: pd.Series(dtype=np.float64) for k in PRICE}, "volume": pd.Series(dtype=np.int64)})
    df = pd.concat(parts, ignore_index=True)
    if s:
        df = df[df["date"] >= s]
    if e:
        df = df[df["date"] <= e]
    df = df.reset_index(drop=True)
    return _apply_rows(df, None, symbol, PRICE, ["volume"]) if adjust else df


def load_equity(symbol, timeframe: str, start=None, end=None) -> pd.DataFrame:
    """Same result as backtest.data.load_equity (datetime index, symbol + OHLCV), read from sym/ instead of every CSV."""
    syms = [symbol] if isinstance(symbol, str) else list(symbol)
    k = TF_MIN[timeframe]
    parts = []
    for sname in syms:
        m = symbol_1m(sname, start, end)
        if m.empty:
            continue
        if k > 1:
            dcode = pd.factorize(m["date"])[0].astype(np.int64)
            mins = m["minute"].to_numpy().astype(np.int64)
            b = (mins - OPEN_MIN) // k
            s = _bounds(dcode * 100_000 + (b + 50_000))
            e = np.r_[s[1:], len(b)] - 1
            o, h, l, c = (m[x].to_numpy(dtype=np.float64) for x in PRICE)
            v = m["volume"].to_numpy()
            m = pd.DataFrame({"date": m["date"].to_numpy(dtype=object)[s], "minute": OPEN_MIN + b[s] * k, "open": o[s],
                              "high": np.maximum.reduceat(h, s), "low": np.minimum.reduceat(l, s), "close": c[e],
                              "volume": np.add.reduceat(v, s)})
        dt = m["date"].astype(str).to_numpy(dtype=object) + " " + HHMMSS[m["minute"].to_numpy(dtype=np.int64)]
        parts.append(pd.DataFrame({"datetime": pd.to_datetime(pd.Series(dt), format="%Y-%m-%d %H:%M:%S"), "symbol": sname,
                                   **{x: m[x].to_numpy() for x in PRICE}, "volume": m["volume"].to_numpy()}))
    if not parts:
        return pd.DataFrame(columns=["datetime", "symbol"] + PRICE + ["volume"]).set_index("datetime")
    out = pd.concat(parts, ignore_index=True).sort_values(["symbol", "datetime"], kind="stable")
    return out.set_index("datetime")


# --------------------------------------------------------------------------------------------------------- panel
def panel(start=None, end=None, columns=None, adjust: bool = True) -> pd.DataFrame:
    """One row per stock-day from data/store/daily.parquet (see fyers_data.store.PANEL_DOC), sorted by date, symbol."""
    if not DAILY.exists():
        raise FileNotFoundError(f"{DAILY} missing - run `python -m fyers_data.store build` in Algo_Historical_Data")
    flt = []
    if start:
        flt.append(("date", ">=", str(pd.Timestamp(start).date())))
    if end:
        flt.append(("date", "<=", str(pd.Timestamp(end).date())))
    cols = None if columns is None else list(dict.fromkeys(["date", "symbol"] + list(columns)))
    df = read_pq(DAILY, columns=cols, filters=flt or None)
    df = df.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
    return _apply_rows(df, "symbol", None, PANEL_PRICE, PANEL_VOL) if adjust else df


def _roll_prev(x: pd.Series, by: pd.Series, n: int) -> pd.Series:
    """Rolling mean of the previous n rows of the same stock (today excluded)."""
    r = x.groupby(by, sort=False).rolling(n, min_periods=n).mean().reset_index(level=0, drop=True)
    return r.groupby(by, sort=False).shift(1)


def add_features(p: pd.DataFrame, atr_n: int = 14, avg_n: int = 20, rvol_n: int = 14) -> pd.DataFrame:
    """Adds, per stock and using only earlier days: prev_close/prev_high/prev_low, gap_pct, atr (simple average of the true
    range of the previous atr_n days), atr_pct, avg_value_cr (avg traded value, Rs crore, avg_n days), avg_vol, and
    rvol5/rvol15/rvol30 = today's first 5/15/30-min volume / its average of the previous rvol_n days
    (rvolK uses today's first K minutes, so it can only be used for decisions after 9:15 + K)."""
    p = p.sort_values(["symbol", "date"], kind="stable").copy()
    by = p["symbol"]
    g = p.groupby(by, sort=False)
    p["prev_close"], p["prev_high"], p["prev_low"] = g["close"].shift(1), g["high"].shift(1), g["low"].shift(1)
    p["gap_pct"] = (p["open"] / p["prev_close"] - 1) * 100
    tr = pd.concat([p["high"] - p["low"], (p["high"] - p["prev_close"]).abs(), (p["low"] - p["prev_close"]).abs()], axis=1).max(axis=1)
    p["atr"] = _roll_prev(tr, by, atr_n)
    p["atr_pct"] = p["atr"] / p["prev_close"] * 100
    if "value" in p:
        p["avg_value_cr"] = _roll_prev(p["value"], by, avg_n) / 1e7
    p["avg_vol"] = _roll_prev(p["volume"].astype(np.float64), by, avg_n)
    for k in (5, 15, 30):
        if f"o{k}_vol" in p:
            p[f"rvol{k}"] = p[f"o{k}_vol"] / _roll_prev(p[f"o{k}_vol"], by, rvol_n)
    return p.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)

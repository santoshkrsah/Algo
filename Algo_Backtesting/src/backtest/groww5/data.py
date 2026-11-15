"""Data for the 5 Groww intraday strategies.

    syms = universe()                       # AMFI Jun-2026 large caps (store names)
    G = grid(fast.symbol_1m(sym, a, b), sym)  # one stock as (days x 375 minutes) arrays, NaN where a minute is missing
    Z = zone_daily(syms)                    # long daily history per stock (2010 -> , joined to the store panel)
    F = nifty()                             # NIFTY near-month futures 1m (back-adjusted continuous, podcast data layer)
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import fast
from backtest.config import DATA
from backtest.groww5.config import CONFIG

OPEN = 555
N_MIN = 375


def universe(cat: str | None = None) -> list[str]:
    u = pd.read_csv(DATA / "reference" / "equity_universe.csv")
    u = u[u["category"] == (cat or CONFIG["universe"])]
    return [s.split(":", 1)[1].rsplit("-", 1)[0] for s in u["symbol"]]


@dataclass
class Grid:
    syms: np.ndarray        # stock of each row
    days: np.ndarray        # day ('YYYY-MM-DD') of each row
    o: np.ndarray           # (rows, 375) 1m candles 09:15 .. 15:29, NaN where a minute is missing
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray


def grid(df: pd.DataFrame, sym: str) -> Grid:
    """All 1m candles of ONE stock (fast.symbol_1m) -> one row per day."""
    days = np.array(sorted(df["date"].astype(str).unique()))
    D = len(days)
    out = {k: np.full((D, N_MIN), np.nan) for k in "ohlcv"}
    if D:
        code = pd.Categorical(df["date"].astype(str), categories=days).codes
        t = df["minute"].to_numpy().astype(int) - OPEN
        ok = (code >= 0) & (t >= 0) & (t < N_MIN)
        for k, col in zip("ohlcv", ["open", "high", "low", "close", "volume"]):
            out[k][code[ok], t[ok]] = df[col].to_numpy(float)[ok]
    return Grid(np.full(D, sym, dtype=object), days, out["o"], out["h"], out["l"], out["c"], out["v"])


def resample_grid(G: Grid, m: int):
    """(S, 375) 1m -> (S, 375/m) m-minute candles (o of first, h max, l min, c of last). Missing minutes are ignored."""
    S, n = G.c.shape[0], N_MIN // m
    sl = lambda a: a[:, : n * m].reshape(S, n, m)
    with np.errstate(all="ignore"):
        h = np.nanmax(sl(G.h), axis=2)
        l = np.nanmin(sl(G.l), axis=2)
    o, c = sl(G.o), sl(G.c)
    first = np.argmax(~np.isnan(o), axis=2)
    last = m - 1 - np.argmax(~np.isnan(c[:, :, ::-1]), axis=2)
    oo = np.take_along_axis(o, first[..., None], 2)[..., 0]
    cc = np.take_along_axis(c, last[..., None], 2)[..., 0]
    return oo, h, l, cc


def zone_daily(syms) -> pd.DataFrame:
    """date, symbol, high, low, close per stock: Fyers daily (2010 -> 2021-09) scaled to the store panel's price basis
    (median ratio over Oct 2021), then the panel (same basis as the 1m candles) from 2021-10-01."""
    start = CONFIG["data"]["start"]
    p = fast.panel(start, CONFIG["data"]["end"], columns=["open", "high", "low", "close", "n_bars"])
    p = p[p["symbol"].isin(syms)]
    d = pd.read_parquet(DATA / "daily" / "equity_daily.parquet")
    d = d[d["symbol"].isin(syms)].copy()
    d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    p["date"] = pd.to_datetime(p["date"]).dt.strftime("%Y-%m-%d")
    ov = d[(d["date"] >= start) & (d["date"] <= "2021-10-31")].merge(p[["date", "symbol", "close"]], on=["date", "symbol"], suffixes=("_d", "_p"))
    f = (ov["close_p"] / ov["close_d"]).groupby(ov["symbol"]).median()
    old = d[d["date"] < start].copy()
    old = old[old["symbol"].isin(f.index)]
    k = old["symbol"].map(f).to_numpy()
    for col in ("open", "high", "low", "close"):
        old[col] = old[col].to_numpy() * k
    z = pd.concat([old[["date", "symbol", "open", "high", "low", "close"]], p[["date", "symbol", "open", "high", "low", "close"]]])
    return z.sort_values(["symbol", "date"]).reset_index(drop=True)


def nifty():
    """NIFTY near-month futures 1m (back-adjusted; real price = price - off)."""
    from backtest.podcast import data as pdata
    return pdata.bars("NIFTY", "1m", start=CONFIG["data"]["fut_start"], end=CONFIG["data"]["end"])


def lot_size(day) -> int:
    lot = CONFIG["lot_sizes"][0][1]
    for d0, n in CONFIG["lot_sizes"]:
        if str(day)[:10] >= d0:
            lot = n
    return lot


def coverage(syms, F) -> pd.DataFrame:
    days = fast.days(CONFIG["data"]["start"], CONFIG["data"]["end"])
    p = fast.panel(CONFIG["data"]["start"], CONFIG["data"]["end"], columns=["n_bars"])
    p = p[p["symbol"].isin(syms)]
    z = pd.read_parquet(DATA / "daily" / "equity_daily.parquet", columns=["symbol", "date"])
    z = z[z["symbol"].isin(syms)]
    rows = [["Stocks 1-min (fast store)", f"{p['symbol'].nunique()} / {len(syms)} large cap", days[0], days[-1], len(days),
             f"{(p['n_bars'] < 370).sum()} stock-din me 370 se kam candle"],
            ["Stocks daily (zones ke liye, Fyers adjusted)", f"{z['symbol'].nunique()} stocks", str(pd.to_datetime(z['date']).min().date()),
             str(pd.to_datetime(z['date']).max().date()), "", "2021-10 se pehle ka part store ke price basis pe scale kiya"],
            ["NIFTY near-month futures 1-min", "NIFTY_F1 / F2 (apna roll)", str(F.days[0]), str(F.days[-1]), len(F.days), "roll expiry se 3 din pehle"]]
    return pd.DataFrame(rows, columns=["Data", "Kya", "Se", "Tak", "Din", "Note"])

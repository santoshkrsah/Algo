#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rulebook.py - NSE intraday rulebook strategies (reference engine), wired to the Algo data store.

Strategies (strategies/RULEBOOK.md has the exact rules in Hinglish):
  orb    ORB-5 "Stocks in Play"             noise  Noise-band intraday momentum
  vwap   VWAP trend (India version)         fh     First half-hour -> last half-hour

Normal use is through tools/run_rulebook.py (result_dir, Telegram, run log, DEV/FINAL guard).
Direct use:
  python src/backtest/rulebook.py --store --strategy orb --start 2021-10-01 --end 2023-12-31 --oos-start 2023-01-01
  python src/backtest/rulebook.py --make-demo-data demo_data          # synthetic data, only for code checks
  python src/backtest/rulebook.py --data-dir demo_data --strategy orb --start 2021-01-01 --end 2021-12-31 --oos-start 2021-07-01
--store reads ../Algo_Historical_Data/data/store/sym (fast, built by `python -m fyers_data.store build`);
--data-dir reads one CSV per stock (datetime,open,high,low,close,volume; 1-minute, IST, candle start time).

Rules of the engine (do not change silently - update strategies/RULEBOOK.md in the same edit):
  * day-level features use only earlier days; a decision on candle m fills at the open of candle m+1
    (ORB stop orders fill at the trigger, or at the candle open if it gapped through)
  * costs (Zerodha/Fyers style equity intraday) + slippage on every fill; stress run = slippage x2
  * stop touched in the entry candle = stopped; exit by 15:15 at the latest

Educational tool only - not investment advice. A backtest profit is not a promise of future profit.
"""

import argparse
import itertools
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace

import numpy as np
import pandas as pd

MINUTES = 375                 # 09:15 .. 15:29 (1-min candles)
SESSION_START = 9 * 60 + 15
DEMO_MARKER = "DEMO_DATA_README.txt"
STORE_PREFIX = "store:"


def m_of(hhmm: str) -> int:
    """'14:30' -> minute index from 09:15 (09:15 -> 0)."""
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m - SESSION_START


def hhmm(m: int) -> str:
    t = SESSION_START + int(m)
    return f"{t // 60:02d}:{t % 60:02d}"


# --------------------------------------------------------------------------------------
# Parameters
# --------------------------------------------------------------------------------------
@dataclass
class Params:
    strategy: str = "orb"
    start: str = "2021-10-01"            # first day of the Algo data
    end: str = "2099-12-31"              # = up to the newest day in the data
    oos_start: str = "2024-01-01"
    capital: float = 500000.0
    # universe (har din, point-in-time)
    min_price: float = 100.0
    min_turnover_cr: float = 50.0      # pichhle 20 din ka avg turnover, Rs crore
    min_atr_pct: float = None          # ATR14 % of price (strategy default)
    top_n: int = None                  # strategy default
    # ORB
    or_minutes: int = 5
    rvol_min: float = 1.0
    stop_atr: float = 0.25             # paper = 0.10 ; India start = 0.25
    entry_cutoff: str = "14:30"
    risk_pct: float = 1.0
    max_leverage: float = None         # strategy default (5x; fh 3x)
    # noise band
    vm: float = 1.0
    lookback: int = 14
    sigma_target: float = 0.02
    # vwap
    vwap_buffer_pct: float = 0.2
    vwap_bar: int = 5
    vwap_max_entries: int = 3
    vwap_rvol_min: float = 1.5
    # first half-hour
    fh_k_sigma: float = 0.5
    fh_entry: str = "14:45"
    # exit + costs (Zerodha, Nov 2026)
    exit_time: str = "15:15"
    slippage_pct: float = 0.03         # per side
    brokerage_pct: float = 0.03
    brokerage_cap: float = 20.0
    stt_pct: float = 0.025             # sell side
    exch_pct: float = 0.00307          # NSE, both sides
    sebi_per_cr: float = 10.0          # both sides
    stamp_pct: float = 0.003           # buy side
    gst_pct: float = 18.0              # on brokerage + exch + sebi
    cost_mult: float = 1.0
    # risk
    daily_loss_limit_pct: float = 3.0
    liq_cap_pct: float = 1.0           # order <= 1% of 20-day avg daily volume
    gap_skip_pct: float = 25.0         # isse bada overnight gap = shayad unadjusted split
    # optional filters (Section 8) - comma list: nr4,nr7,cpr,gap,noexpiry,vixhigh,trendshort
    filters: str = ""
    gap_min_pct: float = 0.5           # 'gap' filter: |gap| >= 0.5%
    cpr_max_pct: float = 0.2           # 'cpr' filter: kal ka CPR width < 0.2%
    vix_file: str = None               # India VIX daily CSV (date, close) - 'vixhigh' ke liye
    nifty_file: str = None             # Nifty 50 daily/minute CSV (date, close) - 'trendshort' ke liye
    skip_dates_file: str = None        # event days (Budget, RBI, results...) ek line mein ek date


STR_FIELDS = {"strategy", "start", "end", "oos_start", "entry_cutoff", "exit_time", "fh_entry",
              "filters", "vix_file", "nifty_file", "skip_dates_file"}
KNOWN_FILTERS = {"nr4", "nr7", "cpr", "gap", "noexpiry", "vixhigh", "trendshort"}
INT_FIELDS = {"or_minutes", "top_n", "lookback", "vwap_bar", "vwap_max_entries"}

STRATEGY_DEFAULTS = {
    "orb": dict(top_n=5, min_atr_pct=1.0, max_leverage=5.0),
    "noise": dict(top_n=10, min_atr_pct=1.5, max_leverage=5.0),
    "vwap": dict(top_n=5, min_atr_pct=1.0, max_leverage=5.0),
    "fh": dict(top_n=10, min_atr_pct=1.0, max_leverage=3.0),
}


def with_defaults(p: Params) -> Params:
    d = STRATEGY_DEFAULTS[p.strategy]
    upd = {k: v for k, v in d.items() if getattr(p, k) is None}
    return replace(p, **upd)


# --------------------------------------------------------------------------------------
# Data loading (CSV -> per-day 2D arrays, cached as .npz)
# --------------------------------------------------------------------------------------
def _ffill_rows(a: np.ndarray) -> np.ndarray:
    mask = np.isnan(a)
    idx = np.where(~mask, np.arange(a.shape[1])[None, :], 0)
    np.maximum.accumulate(idx, axis=1, out=idx)
    return a[np.arange(a.shape[0])[:, None], idx]


def parse_csv(path: str):
    df = pd.read_csv(path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "date" in df.columns and "time" in df.columns and "datetime" not in df.columns:
        df["datetime"] = df["date"].astype(str) + " " + df["time"].astype(str)
    dtcol = next((c for c in ("datetime", "timestamp", "date", "time") if c in df.columns), None)
    if dtcol is None:
        raise ValueError(f"{path}: datetime column nahi mila")
    need = ["open", "high", "low", "close", "volume"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise ValueError(f"{path}: columns missing {miss}")
    ts = pd.to_datetime(df[dtcol])
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df = df.assign(ts=ts)[["ts"] + need].dropna(subset=["open", "high", "low", "close"])
    df = df.drop_duplicates("ts").sort_values("ts")
    mins = (df.ts.dt.hour * 60 + df.ts.dt.minute - SESSION_START).to_numpy()
    df = df[(mins >= 0) & (mins < MINUTES)]
    mins = (df.ts.dt.hour * 60 + df.ts.dt.minute - SESSION_START).to_numpy()
    day = df.ts.dt.normalize()
    udays = pd.Index(day.unique())
    return _day_arrays(udays.get_indexer(day), udays, mins, df)


def _day_arrays(di, udays, mins, df):
    """Rows (day index di, minute index mins, OHLCV) -> per-day (D x 375) arrays + the quality filter. Shared by CSV and store."""
    D = len(udays)
    arrs = {}
    for c in ("open", "high", "low", "close"):
        a = np.full((D, MINUTES), np.nan, dtype=np.float64)
        a[di, mins] = df[c].to_numpy(dtype=np.float64)
        arrs[c] = a
    V = np.zeros((D, MINUTES), dtype=np.float64)
    V[di, mins] = df["volume"].fillna(0).to_numpy(dtype=np.float64)
    present = ~np.isnan(arrs["close"])
    # quality: first candle must exist, >= 95% minutes present (Muhurat/special/broken days drop)
    good = present[:, 0] & (present.mean(axis=1) >= 0.95)
    C = _ffill_rows(arrs["close"][good])
    pres = present[good]
    O = np.where(pres, arrs["open"][good], C)
    H = np.where(pres, arrs["high"][good], C)
    L = np.where(pres, arrs["low"][good], C)
    V = V[good]
    dates = pd.DatetimeIndex(udays)[good].to_numpy().astype("datetime64[D]")
    return dates, O.astype(np.float32), H.astype(np.float32), L.astype(np.float32), C.astype(np.float32), V.astype(np.float32)


def arrays_from_store(df: pd.DataFrame):
    """fast.symbol_1m() rows (date, minute, OHLCV) -> the same arrays parse_csv() makes from a CSV."""
    mins = df["minute"].to_numpy().astype(np.int64) - SESSION_START
    keep = (mins >= 0) & (mins < MINUTES)
    df = df[keep].dropna(subset=["open", "high", "low", "close"])
    mins = df["minute"].to_numpy().astype(np.int64) - SESSION_START
    di, udays = pd.factorize(df["date"].astype(str), sort=True)
    return _day_arrays(di, pd.to_datetime(pd.Index(udays)), mins, df)


def store_symbols() -> list:
    from backtest import fast
    return sorted({f.stem for f in fast.SYM.glob("*/*.parquet")})


def prefilter_symbols(symbols, p: Params, verbose=True):
    """Drop stocks that can never pass the liquidity/price rules in start..end (checked on the daily panel with a safety
    margin), so they are not loaded at all. Same result, ~3x less work. --no-prefilter switches it off."""
    from backtest import fast
    s0 = (pd.Timestamp(p.start) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    pan = fast.panel(s0, p.end, columns=["open", "close", "volume"])
    pan = pan.sort_values(["symbol", "date"], kind="stable")
    to = (pan["close"] * pan["volume"]).groupby(pan["symbol"], sort=False).transform(
        lambda x: x.rolling(20, min_periods=20).mean().shift(1))
    inside = pan["date"] >= str(pd.Timestamp(p.start).date())
    ok = inside & (to >= 0.7 * p.min_turnover_cr * 1e7) & (pan["open"] >= 0.9 * p.min_price)
    keep = set(pan.loc[ok, "symbol"])
    out = [s for s in symbols if s in keep]
    if verbose:
        print(f"  prefilter: {len(out)}/{len(symbols)} stocks can meet turnover >= Rs {p.min_turnover_cr:g} cr and price >= "
              f"Rs {p.min_price:g} in {p.start}..{p.end}")
    return out


def load_symbol(path: str, cache_dir: str):
    if path.startswith(STORE_PREFIX):
        from backtest import fast
        sym = path[len(STORE_PREFIX):]
        return (sym,) + arrays_from_store(fast.symbol_1m(sym))
    sym = os.path.splitext(os.path.basename(path))[0].upper()
    cpath = os.path.join(cache_dir, sym + ".npz")
    if os.path.exists(cpath) and os.path.getmtime(cpath) >= os.path.getmtime(path):
        z = np.load(cpath)
        return sym, z["dates"].astype("datetime64[D]"), z["O"], z["H"], z["L"], z["C"], z["V"]
    dates, O, H, L, C, V = parse_csv(path)
    os.makedirs(cache_dir, exist_ok=True)
    np.savez(cpath, dates=dates.astype("datetime64[D]").astype("int64"), O=O, H=H, L=L, C=C, V=V)
    return sym, dates, O, H, L, C, V


def list_csvs(data_dir: str):
    files = sorted(f for f in os.listdir(data_dir)
                   if f.lower().endswith(".csv") and not f.startswith(("_", ".")))
    if not files:
        sys.exit(f"'{data_dir}' mein koi .csv file nahi mili.")
    return [os.path.join(data_dir, f) for f in files]


# --------------------------------------------------------------------------------------
# Features (sirf pichhle din tak ka data -> no lookahead)
# --------------------------------------------------------------------------------------
def roll_mean_prev(x, n):
    return pd.Series(np.asarray(x, dtype=np.float64)).rolling(n, min_periods=n).mean().shift(1).to_numpy()


def roll_std_prev(x, n):
    return pd.Series(np.asarray(x, dtype=np.float64)).rolling(n, min_periods=n).std(ddof=1).shift(1).to_numpy()


def roll_mean_prev_2d(X, n):
    return pd.DataFrame(np.asarray(X, dtype=np.float64)).rolling(n, min_periods=n).mean().shift(1).to_numpy()


def daily_features(O, H, L, C, V, p: Params):
    d_open = O[:, 0].astype(np.float64)
    d_high = H.max(axis=1).astype(np.float64)
    d_low = L.min(axis=1).astype(np.float64)
    d_close = C[:, -1].astype(np.float64)
    d_vol = V.sum(axis=1).astype(np.float64)
    prev_close = np.r_[np.nan, d_close[:-1]]
    tr = np.fmax(d_high - d_low, np.fmax(np.abs(d_high - prev_close), np.abs(d_low - prev_close)))
    atr = roll_mean_prev(tr, 14)
    ret = d_close / prev_close - 1.0
    sigma = roll_std_prev(ret, 14)
    avg_to = roll_mean_prev(d_close * d_vol, 20)
    adv = roll_mean_prev(d_vol, 20)
    or_vol = V[:, :5].sum(axis=1).astype(np.float64)
    rvol = or_vol / roll_mean_prev(or_vol, 14)
    # unadjusted split/bonus guard: bade gap ke baad features kharab -> window skip
    gap = d_open / prev_close - 1.0
    ok = np.ones(len(d_open), dtype=bool)
    bad = np.nonzero(np.abs(gap) > p.gap_skip_pct / 100.0)[0]
    span = max(20, p.lookback) + 1
    for b in bad:
        ok[b: b + span] = False
    # Section 8 filters - sab kal ke data se (aaj trade karne layak hai ya nahi)
    rng_ = d_high - d_low

    def narrowest(k):
        mn = pd.Series(rng_).rolling(k, min_periods=k).min().to_numpy()
        flag = rng_ <= mn                        # din ki range pichhle k din (us din samet) mein sabse chhoti
        return np.r_[False, flag[:-1]]           # kal NR-k tha -> aaj ka flag
    piv = (d_high + d_low + d_close) / 3.0
    bc = (d_high + d_low) / 2.0
    cpr_w = np.r_[np.nan, (2 * np.abs(piv - bc) / piv * 100)[:-1]]
    return dict(d_open=d_open, prev_close=prev_close, atr=atr, sigma=sigma, avg_to=avg_to,
                adv=adv, rvol=rvol, ok=ok, n_bad_gaps=len(bad), nr4=narrowest(4), nr7=narrowest(7),
                cpr_w=cpr_w, gap_abs_pct=np.abs(gap) * 100)


def session_vwap(H, L, C, V):
    tp = (H.astype(np.float64) + L + C) / 3.0
    cpv = np.cumsum(tp * V, axis=1)
    cv = np.cumsum(V.astype(np.float64), axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        vw = np.where(cv > 0, cpv / np.where(cv > 0, cv, 1), C)
    return vw


# --------------------------------------------------------------------------------------
# Strategy trade generators (per symbol, saare din ek saath)
# Har trade ka result us din ke apne data par depend karta hai, selection baad mein hota hai.
# --------------------------------------------------------------------------------------
def gen_orb(O, H, L, C, V, f, p: Params):
    D = O.shape[0]
    n = p.or_minutes
    cut = m_of(p.entry_cutoff)
    ex = m_of(p.exit_time)
    rows = np.arange(D)
    cols = np.arange(MINUTES)[None, :]
    or_open = O[:, 0].astype(np.float64)
    or_close = C[:, n - 1].astype(np.float64)
    or_high = H[:, :n].max(axis=1).astype(np.float64)
    or_low = L[:, :n].min(axis=1).astype(np.float64)
    direction = np.sign(or_close - or_open)
    atr = f["atr"]
    win = (cols >= n) & (cols < cut)
    out = []
    for side in (1, -1):
        if side == 1:
            trig = (H >= or_high[:, None]) & win
        else:
            trig = (L <= or_low[:, None]) & win
        has = trig.any(axis=1)
        em = trig.argmax(axis=1)
        bar_open = O[rows, em].astype(np.float64)
        if side == 1:
            entry = np.maximum(or_high, bar_open)
            stop = entry - p.stop_atr * atr
            hitm = (L <= stop[:, None])
        else:
            entry = np.minimum(or_low, bar_open)
            stop = entry + p.stop_atr * atr
            hitm = (H >= stop[:, None])
        swin = (cols >= em[:, None]) & (cols < ex)
        hit = hitm & swin
        h_has = hit.any(axis=1)
        hm = hit.argmax(axis=1)
        h_open = O[rows, hm].astype(np.float64)
        if side == 1:
            stop_fill = np.where(hm == em, stop, np.minimum(stop, h_open))
        else:
            stop_fill = np.where(hm == em, stop, np.maximum(stop, h_open))
        exit_px = np.where(h_has, stop_fill, O[:, ex].astype(np.float64))
        exit_m = np.where(h_has, hm, ex)
        take = has & (direction == side) & np.isfinite(atr) & (atr > 0) & f["ok"]
        idx = np.nonzero(take)[0]
        if len(idx):
            out.append(pd.DataFrame(dict(
                day=idx, side=side, entry_m=em[idx], entry_px=entry[idx], stop_px=stop[idx],
                exit_m=exit_m[idx], exit_px=exit_px[idx],
                reason=np.where(h_has[idx], "stop", "eod"))))
    return pd.concat(out, ignore_index=True) if out else None


def gen_noise(O, H, L, C, V, f, p: Params):
    D = O.shape[0]
    ex = m_of(p.exit_time)
    cps = [m for m in range(29, MINUTES, 30) if m + 1 < ex]      # 09:45, 10:15 ... 14:45
    O64 = O.astype(np.float64)
    day_open = O64[:, 0]
    move = np.abs(C[:, cps].astype(np.float64) / day_open[:, None] - 1.0)
    sig = roll_mean_prev_2d(move, p.lookback)
    base_up = np.fmax(day_open, f["prev_close"])
    base_dn = np.fmin(day_open, f["prev_close"])
    UB = base_up[:, None] * (1 + p.vm * sig)
    LB = base_dn[:, None] * (1 - p.vm * sig)
    vw = session_vwap(H, L, C, V)
    pos = np.zeros(D, dtype=np.int8)
    e_px = np.full(D, np.nan)
    e_m = np.zeros(D, dtype=np.int64)
    usable = f["ok"] & np.isfinite(f["prev_close"])
    trades = []

    def close(mask, xm, xpx, reason):
        idx = np.nonzero(mask)[0]
        if len(idx):
            trades.append(pd.DataFrame(dict(day=idx, side=pos[idx].astype(int), entry_m=e_m[idx],
                                            entry_px=e_px[idx], stop_px=np.nan, exit_m=xm,
                                            exit_px=xpx[idx], reason=reason)))
        pos[idx] = 0

    for j, m in enumerate(cps):
        price = C[:, m].astype(np.float64)
        nxt = O64[:, m + 1]
        ub, lb, v = UB[:, j], LB[:, j], vw[:, m]
        close((pos == 1) & (price < np.fmax(ub, v)), m + 1, nxt, "trail")
        close((pos == -1) & (price > np.fmin(lb, v)), m + 1, nxt, "trail")
        can = (pos == 0) & usable & np.isfinite(ub)
        gl = can & (price > ub)
        gs = can & (price < lb)
        pos[gl] = 1
        pos[gs] = -1
        e_px[gl | gs] = nxt[gl | gs]
        e_m[gl | gs] = m + 1
    close(pos != 0, ex, O64[:, ex], "eod")
    return pd.concat(trades, ignore_index=True) if trades else None


def gen_vwap(O, H, L, C, V, f, p: Params):
    D = O.shape[0]
    B = int(p.vwap_bar)
    ex = m_of(p.exit_time)
    cut = m_of(p.entry_cutoff) - 1           # 14:30 par close hone wala last bar
    first = 14                                # 09:29 candle -> 09:30 close
    ends = [m for m in range(B - 1, MINUTES, B) if m >= first and m + 1 < ex]
    O64 = O.astype(np.float64)
    vw = session_vwap(H, L, C, V)
    buf = p.vwap_buffer_pct / 100.0
    pos = np.zeros(D, dtype=np.int8)
    n_ent = np.zeros(D, dtype=np.int64)
    e_px = np.full(D, np.nan)
    e_m = np.zeros(D, dtype=np.int64)
    usable = f["ok"]
    trades = []

    def close(mask, xm, xpx, reason):
        idx = np.nonzero(mask)[0]
        if len(idx):
            trades.append(pd.DataFrame(dict(day=idx, side=pos[idx].astype(int), entry_m=e_m[idx],
                                            entry_px=e_px[idx], stop_px=np.nan, exit_m=xm,
                                            exit_px=xpx[idx], reason=reason)))
        pos[idx] = 0

    for m in ends:
        price = C[:, m].astype(np.float64)
        v = vw[:, m]
        nxt = O64[:, m + 1]
        close((pos == 1) & (price < v), m + 1, nxt, "vwap")
        close((pos == -1) & (price > v), m + 1, nxt, "vwap")
        if m <= cut:
            can = (pos == 0) & usable & (n_ent < p.vwap_max_entries)
            gl = can & (price > v * (1 + buf))
            gs = can & (price < v * (1 - buf))
            pos[gl] = 1
            pos[gs] = -1
            new = gl | gs
            e_px[new] = nxt[new]
            e_m[new] = m + 1
            n_ent[new] += 1
    close(pos != 0, ex, O64[:, ex], "eod")
    return pd.concat(trades, ignore_index=True) if trades else None


def gen_fh(O, H, L, C, V, f, p: Params):
    ex = m_of(p.exit_time)
    em = m_of(p.fh_entry)                     # 14:45 candle open = 14:45 price
    r_fh = C[:, 29].astype(np.float64) / f["prev_close"] - 1.0   # 09:44 candle close = 09:45
    vol30 = V[:, :30].sum(axis=1).astype(np.float64)
    rvol30 = vol30 / roll_mean_prev(vol30, 14)
    side = np.sign(r_fh)
    take = (np.abs(r_fh) >= p.fh_k_sigma * f["sigma"]) & (rvol30 >= 1.0) & (side != 0) & f["ok"]
    idx = np.nonzero(take)[0]
    if not len(idx):
        return None
    return pd.DataFrame(dict(day=idx, side=side[idx].astype(int), entry_m=em,
                             entry_px=O[idx, em].astype(np.float64), stop_px=np.nan, exit_m=ex,
                             exit_px=O[idx, ex].astype(np.float64), reason="eod"))


GENERATORS = {"orb": gen_orb, "noise": gen_noise, "vwap": gen_vwap, "fh": gen_fh}


def _tables(files, cache_dir, p: Params, progress=None):
    """Per-stock features + trade candidates for a list of stocks. Returns (uni parts, trade parts, gap warnings)."""
    unis, trs, warn = [], [], []
    gen = GENERATORS[p.strategy]
    for k, path in enumerate(files, 1):
        sym, dates, O, H, L, C, V = load_symbol(path, cache_dir)
        if len(dates) < 30:
            continue
        f = daily_features(O, H, L, C, V, p)
        if f["n_bad_gaps"]:
            warn.append((sym, f["n_bad_gaps"]))
        uni = pd.DataFrame(dict(
            date=dates, symbol=sym, price=f["d_open"], avg_to_cr=f["avg_to"] / 1e7,
            atr_pct=f["atr"] / f["d_open"] * 100, rvol=f["rvol"], sigma=f["sigma"],
            adv=f["adv"], atr=f["atr"], ok=f["ok"], nr4=f["nr4"], nr7=f["nr7"],
            cpr_w=f["cpr_w"], gap_abs_pct=f["gap_abs_pct"]))
        unis.append(uni)
        t = gen(O, H, L, C, V, f, p)
        if t is not None and len(t):
            t["date"] = dates[t["day"].to_numpy()]
            t["symbol"] = sym
            trs.append(t.drop(columns=["day"]))
        if progress:
            progress(1)
    return unis, trs, warn


def _tables_job(args):
    return _tables(*args)


def build_tables(files, cache_dir, p: Params, verbose=True, workers=1, progress=None):
    """All stocks -> (uni, tr). workers > 1 splits the stocks over processes; the result is identical (sorted)."""
    t0 = time.time()
    done = [0]

    def tick(n):
        done[0] += n
        if verbose and (done[0] % 100 == 0 or done[0] == len(files)):
            print(f"  {done[0]}/{len(files)} symbols processed ({time.time() - t0:.0f}s)", flush=True)
        if progress:
            progress(done[0], len(files))

    if workers > 1 and len(files) > 1:
        n_chunks = min(len(files), workers * 4)
        chunks = [files[i::n_chunks] for i in range(n_chunks)]
        unis, trs, warn = [], [], []
        with ProcessPoolExecutor(workers) as ex:
            futs = {ex.submit(_tables_job, (c, cache_dir, p)): len(c) for c in chunks}
            for f in as_completed(futs):
                u, t, w = f.result()
                unis += u
                trs += t
                warn += w
                tick(futs[f])
    else:
        unis, trs, warn = _tables(files, cache_dir, p, progress=tick)
    if warn and verbose:
        print(f"  WARNING: {len(warn)} symbols mein >{p.gap_skip_pct:.0f}% overnight gap mila "
              f"(shayad unadjusted split/bonus). Un dino ke aas-paas trading skip ki gayi. "
              f"Pehle 5: {sorted(warn)[:5]}")
    if not unis:
        sys.exit("Koi stock load nahi hua (data folder / store khaali?).")
    uni = pd.concat(unis, ignore_index=True)
    uni["date"] = pd.to_datetime(uni["date"])
    uni = uni.sort_values(["date", "symbol"], kind="mergesort").reset_index(drop=True)
    tr = pd.concat(trs, ignore_index=True) if trs else pd.DataFrame(
        columns=["side", "entry_m", "entry_px", "stop_px", "exit_m", "exit_px", "reason", "date", "symbol"])
    tr["date"] = pd.to_datetime(tr["date"])
    tr = tr.sort_values(["date", "symbol", "entry_m", "side"], kind="mergesort").reset_index(drop=True)
    return uni, tr


# --------------------------------------------------------------------------------------
# Portfolio layer: selection, sizing, costs, daily loss limit
# --------------------------------------------------------------------------------------
def trade_costs(buy_val, sell_val, p: Params):
    brk = min(p.brokerage_cap, p.brokerage_pct / 100 * buy_val) + min(p.brokerage_cap, p.brokerage_pct / 100 * sell_val)
    stt = p.stt_pct / 100 * sell_val
    exch = p.exch_pct / 100 * (buy_val + sell_val)
    sebi = p.sebi_per_cr / 1e7 * (buy_val + sell_val)
    stamp = p.stamp_pct / 100 * buy_val
    gst = p.gst_pct / 100 * (brk + exch + sebi)
    return (brk + stt + exch + sebi + stamp + gst) * p.cost_mult


def filter_set(p: Params):
    flt = {x.strip().lower() for x in (p.filters or "").split(",") if x.strip()}
    unknown = flt - KNOWN_FILTERS
    if unknown:
        sys.exit(f"Unknown filter {sorted(unknown)}. Allowed: {sorted(KNOWN_FILTERS)}")
    if "vixhigh" in flt and not p.vix_file:
        sys.exit("'vixhigh' filter ke liye --vix-file (India VIX daily CSV: date,close) do")
    if "trendshort" in flt and not p.nifty_file:
        sys.exit("'trendshort' filter ke liye --nifty-file (Nifty 50 CSV: date,close) do")
    return flt


def nifty_expiry_flag(dates):
    """Nifty weekly expiry ka din: 31 Aug 2025 tak Thursday, 1 Sep 2025 se Tuesday (holiday shift ignore)."""
    d = pd.to_datetime(pd.Series(dates))
    wd = d.dt.weekday
    return np.where(d < pd.Timestamp("2025-09-01"), wd == 3, wd == 1)


def load_daily_close(path):
    df = pd.read_csv(path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    dcol = next((c for c in ("date", "datetime", "timestamp") if c in df.columns), None)
    if dcol is None or "close" not in df.columns:
        sys.exit(f"{path}: 'date' aur 'close' columns chahiye")
    s = pd.DataFrame(dict(date=pd.to_datetime(df[dcol]).dt.normalize(),
                          close=pd.to_numeric(df["close"], errors="coerce"))).dropna()
    return s.groupby("date")["close"].last().sort_index()      # minute file ho to din ka last close


def attach_day_flags(uni, p: Params):
    uni = uni.copy()
    uni["expiry"] = nifty_expiry_flag(uni["date"])
    if p.vix_file:
        v = load_daily_close(p.vix_file)
        vh = (v.shift(1) > v.rolling(252, min_periods=60).median().shift(1)).rename("vix_high")
        uni = uni.merge(vh, left_on="date", right_index=True, how="left")
    if p.nifty_file:
        n = load_daily_close(p.nifty_file)
        nb = (n.shift(1) < n.rolling(50, min_periods=50).mean().shift(1)).rename("nifty_below")
        uni = uni.merge(nb, left_on="date", right_index=True, how="left")
    for c in ("vix_high", "nifty_below"):
        if c in uni:
            uni[c] = uni[c].astype("boolean").fillna(False).astype(bool)
    if p.skip_dates_file:
        with open(p.skip_dates_file) as fh:
            skip = pd.to_datetime([x.strip() for x in fh if x.strip() and not x.startswith("#")])
        uni["skip_day"] = uni["date"].isin(skip)
    return uni


def select_universe(uni, p: Params):
    s, e = pd.Timestamp(p.start), pd.Timestamp(p.end)
    u = uni[(uni.date >= s) & (uni.date <= e) & uni.ok]
    elig = (u.price >= p.min_price) & (u.avg_to_cr >= p.min_turnover_cr) & (u.atr_pct >= p.min_atr_pct)
    flt = filter_set(p)
    if "nr4" in flt:
        elig &= u.nr4
    if "nr7" in flt:
        elig &= u.nr7
    if "cpr" in flt:
        elig &= u.cpr_w < p.cpr_max_pct
    if "gap" in flt:
        elig &= u.gap_abs_pct >= p.gap_min_pct
    if "noexpiry" in flt:
        elig &= ~u.expiry
    if "vixhigh" in flt:
        elig &= u.vix_high
    if "skip_day" in u:
        elig &= ~u.skip_day
    if p.strategy == "orb":
        elig &= u.rvol >= p.rvol_min
        key = "rvol"
    elif p.strategy == "vwap":
        elig &= u.rvol >= p.vwap_rvol_min
        key = "rvol"
    else:
        key = "avg_to_cr"
    sel = u[elig].sort_values(["date", key], ascending=[True, False], kind="mergesort").groupby("date").head(p.top_n)
    cols = ["date", "symbol", "sigma", "adv", "atr"] + (["nifty_below"] if "nifty_below" in sel else [])
    return sel[cols]


def simulate(uni, tr, p: Params):
    s, e = pd.Timestamp(p.start), pd.Timestamp(p.end)
    all_days = np.sort(uni.loc[(uni.date >= s) & (uni.date <= e), "date"].unique())
    sel = select_universe(uni, p)
    t = tr.merge(sel, on=["date", "symbol"], how="inner")
    if "trendshort" in filter_set(p):                 # short sirf jab Nifty kal 50-DMA ke neeche tha
        t = t[(t.side == 1) | t.nifty_below]
    t = t.sort_values(["date", "entry_m", "symbol"], kind="mergesort").reset_index(drop=True)
    slip = p.slippage_pct / 100.0
    E = p.capital
    eq_rows, out = [], []
    groups = {d: g for d, g in t.groupby("date")}
    for d in all_days:
        E0 = E
        day_pnl = 0.0
        closed = []          # (exit_m, pnl)
        n_tr = 0
        g = groups.get(pd.Timestamp(d))
        if g is not None:
            for r in g.itertuples(index=False):
                realized = sum(x[1] for x in closed if x[0] <= r.entry_m)
                if realized <= -p.daily_loss_limit_pct / 100 * E0:
                    continue
                side = int(r.side)
                ent = float(r.entry_px)
                if not (np.isfinite(ent) and ent > 0 and np.isfinite(r.exit_px)):
                    continue
                if p.strategy == "orb":
                    risk_ps = abs(ent - float(r.stop_px))
                    if not (risk_ps > 0):
                        continue
                    sh = min(p.risk_pct / 100 * E0 / risk_ps, p.max_leverage * E0 / (p.top_n * ent))
                else:
                    sg = float(r.sigma)
                    if not (np.isfinite(sg) and sg > 0):
                        continue
                    notional = E0 * min(p.max_leverage, p.sigma_target / sg) / p.top_n
                    sh = notional / ent
                if np.isfinite(r.adv) and r.adv > 0:
                    sh = min(sh, p.liq_cap_pct / 100 * r.adv)
                sh = math.floor(sh)
                if sh <= 0:
                    continue
                if side == 1:
                    e_fill, x_fill = ent * (1 + slip), float(r.exit_px) * (1 - slip)
                    buy_val, sell_val = e_fill * sh, x_fill * sh
                else:
                    e_fill, x_fill = ent * (1 - slip), float(r.exit_px) * (1 + slip)
                    buy_val, sell_val = x_fill * sh, e_fill * sh
                gross = side * (float(r.exit_px) - ent) * sh
                slip_cost = abs(e_fill - ent) * sh + abs(x_fill - float(r.exit_px)) * sh
                fees = trade_costs(buy_val, sell_val, p)
                net = gross - slip_cost - fees
                R = net / (abs(ent - float(r.stop_px)) * sh) if p.strategy == "orb" else np.nan
                closed.append((int(r.exit_m), net))
                day_pnl += net
                n_tr += 1
                out.append(dict(date=pd.Timestamp(d).date(), symbol=r.symbol, side="LONG" if side == 1 else "SHORT",
                                entry_time=hhmm(r.entry_m), entry_px=round(ent, 2), exit_time=hhmm(r.exit_m),
                                exit_px=round(float(r.exit_px), 2), stop_px=round(float(r.stop_px), 2) if np.isfinite(r.stop_px) else None,
                                shares=sh, notional=round(ent * sh, 2), gross_pnl=round(gross, 2),
                                slippage=round(slip_cost, 2), fees=round(fees, 2), net_pnl=round(net, 2),
                                ret_pct=net / (ent * sh) * 100, gross_pct=gross / (ent * sh) * 100,
                                R=R, reason=r.reason))
        E = E0 + day_pnl
        eq_rows.append(dict(date=pd.Timestamp(d), equity=E, day_pnl=day_pnl, ret=day_pnl / E0 if E0 > 0 else 0.0, trades=n_tr))
        if E <= 0:
            print("Equity zero ho gayi - simulation band.")
            break
    eq = pd.DataFrame(eq_rows)
    trades = pd.DataFrame(out)
    if len(trades):
        trades["date"] = pd.to_datetime(trades["date"])
    return eq, trades


# --------------------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------------------
def metrics(eq, trades):
    m = {}
    if eq is None or not len(eq):
        return m
    start_eq = eq.equity.iloc[0] - eq.day_pnl.iloc[0]
    end_eq = eq.equity.iloc[-1]
    n = len(eq)
    m["days"] = n
    m["total_return_pct"] = (end_eq / start_eq - 1) * 100
    m["cagr_pct"] = ((end_eq / start_eq) ** (252 / n) - 1) * 100 if end_eq > 0 and n > 0 else -100.0
    sd = eq.ret.std(ddof=1)
    m["sharpe"] = eq.ret.mean() / sd * math.sqrt(252) if sd and sd > 0 else 0.0
    curve = np.r_[start_eq, eq.equity.to_numpy()]
    peak = np.maximum.accumulate(curve)
    m["max_dd_pct"] = float(np.max(1 - curve / peak) * 100)
    if trades is None or not len(trades):
        m["trades"] = 0
        return m
    pnl = trades.net_pnl
    m["trades"] = int(len(trades))
    m["win_rate_pct"] = float((pnl > 0).mean() * 100)
    gp, gl = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    m["profit_factor"] = float(gp / gl) if gl > 0 else float("inf")
    m["avg_net_pnl_rs"] = float(pnl.mean())
    m["avg_net_pct"] = float(trades.ret_pct.mean())
    m["avg_gross_pct"] = float(trades.gross_pct.mean())
    cost_pct = ((trades.fees + trades.slippage) / trades.notional * 100).mean()
    m["avg_cost_pct"] = float(cost_pct)
    m["gross_to_cost"] = float(m["avg_gross_pct"] / cost_pct) if cost_pct > 0 else float("nan")
    r = trades.ret_pct
    m["t_stat"] = float(r.mean() / r.std(ddof=1) * math.sqrt(len(r))) if len(r) > 1 and r.std(ddof=1) > 0 else 0.0
    if trades.R.notna().any():
        m["expectancy_R"] = float(trades.R.mean())
    m["total_fees_rs"] = float(trades.fees.sum())
    m["total_slippage_rs"] = float(trades.slippage.sum())
    for sd_name in ("LONG", "SHORT"):
        sub = trades[trades.side == sd_name]
        m[f"{sd_name.lower()}_trades"] = int(len(sub))
        m[f"{sd_name.lower()}_net_rs"] = float(sub.net_pnl.sum())
    return m


def split_period(eq, trades, start=None, end=None):
    e = eq
    t = trades
    if start is not None:
        e = e[e.date >= pd.Timestamp(start)]
        t = t[t.date >= pd.Timestamp(start)] if len(t) else t
    if end is not None:
        e = e[e.date < pd.Timestamp(end)]
        t = t[t.date < pd.Timestamp(end)] if len(t) else t
    return e, t


def yearly_table(eq, trades):
    rows = []
    for y in sorted(eq.date.dt.year.unique()):
        e = eq[eq.date.dt.year == y]
        t = trades[trades.date.dt.year == y] if len(trades) else trades
        mm = metrics(e, t)
        rows.append(dict(year=y, return_pct=mm.get("total_return_pct"), sharpe=mm.get("sharpe"),
                         max_dd_pct=mm.get("max_dd_pct"), trades=mm.get("trades", 0),
                         win_rate_pct=mm.get("win_rate_pct"), profit_factor=mm.get("profit_factor"),
                         avg_net_pct=mm.get("avg_net_pct")))
    return pd.DataFrame(rows)


def breakdown_table(trades):
    """Section 8/11 ke tags: weekday, Nifty expiry din, long/short, exit reason."""
    if trades is None or not len(trades):
        return pd.DataFrame()
    t = trades.copy()
    t["weekday"] = t.date.dt.weekday.astype(str) + "-" + t.date.dt.day_name().str[:3]
    t["expiry_day"] = nifty_expiry_flag(t.date)
    rows = []
    for col in ("weekday", "expiry_day", "side", "reason"):
        for k, g in t.groupby(col):
            rows.append(dict(group=col, value=str(k), trades=len(g), net_pnl_rs=g.net_pnl.sum(),
                             win_rate_pct=(g.net_pnl > 0).mean() * 100, avg_net_pct=g.ret_pct.mean()))
    return pd.DataFrame(rows)


def pass_fail(m_is, m_oos, m_stress):
    def g(d, k, default=float("nan")):
        v = d.get(k, default)
        return default if v is None else v
    checks = [
        ("Sharpe >= 1.0", g(m_oos, "sharpe") >= 1.0),
        ("Max drawdown <= 20%", g(m_oos, "max_dd_pct") <= 20),
        ("Profit factor >= 1.3", g(m_oos, "profit_factor") >= 1.3),
        ("Trades >= 300 aur t-stat >= 2", g(m_oos, "trades", 0) >= 300 and g(m_oos, "t_stat") >= 2),
        ("Avg gross trade / cost >= 2", g(m_oos, "gross_to_cost") >= 2),
        ("OOS Sharpe / IS Sharpe >= 0.5", g(m_is, "sharpe") > 0 and g(m_oos, "sharpe") / g(m_is, "sharpe") >= 0.5),
        ("Stress (slippage 2x) profitable", g(m_stress, "total_return_pct") > 0),
    ]
    if "expectancy_R" in m_oos:
        checks.insert(3, ("Expectancy >= +0.10R", g(m_oos, "expectancy_R") >= 0.10))
    return checks


def fmt(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, float):
        if math.isnan(v):
            return "-"
        if math.isinf(v):
            return "inf"
        return f"{v:,.{nd}f}"
    return str(v)


def print_block(title, m):
    keys = ["total_return_pct", "cagr_pct", "sharpe", "max_dd_pct", "trades", "win_rate_pct", "profit_factor",
            "expectancy_R", "avg_net_pct", "avg_gross_pct", "avg_cost_pct", "gross_to_cost", "t_stat",
            "long_trades", "long_net_rs", "short_trades", "short_net_rs", "total_fees_rs", "total_slippage_rs"]
    print(f"\n--- {title} ---")
    for k in keys:
        if k in m:
            print(f"  {k:<20} {fmt(m[k])}")


# --------------------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------------------
def run_once(files, cache_dir, p: Params, verbose=True, workers=1, progress=None):
    if verbose:
        print(f"[{p.strategy}] trade candidates bana raha hoon ({len(files)} stocks)...")
    filter_set(p)
    uni, tr = build_tables(files, cache_dir, p, verbose=verbose, workers=workers, progress=progress)
    uni = attach_day_flags(uni, p)
    eq, trades = simulate(uni, tr, p)
    p_stress = replace(p, slippage_pct=p.slippage_pct * 2)
    eq_s, tr_s = simulate(uni, tr, p_stress)
    return uni, eq, trades, eq_s, tr_s


def report(eq, trades, eq_s, tr_s, p: Params, out_dir, is_demo):
    os.makedirs(out_dir, exist_ok=True)
    e_is, t_is = split_period(eq, trades, None, p.oos_start)
    e_oos, t_oos = split_period(eq, trades, p.oos_start, None)
    es_oos, ts_oos = split_period(eq_s, tr_s, p.oos_start, None)
    m_all, m_is, m_oos, m_st = metrics(eq, trades), metrics(e_is, t_is), metrics(e_oos, t_oos), metrics(es_oos, ts_oos)
    if not len(e_oos) or not len(e_is):
        print(f"\nNOTE: --oos-start {p.oos_start} se pehle ya baad ka data nahi hai. "
              f"In-sample/out-of-sample split ke liye --oos-start apne data ke beech ki date do.")
    if is_demo:
        print("\n" + "!" * 78 + "\n  DEMO (synthetic) DATA - ye numbers sirf code check ke liye hain, market ka sach NAHI.\n" + "!" * 78)
    last = eq.date.max().date() if len(eq) else p.end
    print_block(f"FULL {eq.date.min().date() if len(eq) else p.start} -> {last}", m_all)
    print_block(f"IN-SAMPLE  (< {p.oos_start})", m_is)
    print_block(f"OUT-OF-SAMPLE (>= {p.oos_start})", m_oos)
    print_block("OUT-OF-SAMPLE, STRESS (slippage 2x)", m_st)
    yt = yearly_table(eq, trades)
    print("\n--- Saal-wise ---")
    print(yt.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
    bd = breakdown_table(trades)
    if len(bd):
        print("\n--- Breakdown (weekday / Nifty expiry din / side / exit reason) ---")
        print(bd.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
        bd.to_csv(os.path.join(out_dir, "breakdown.csv"), index=False)
    checks = pass_fail(m_is, m_oos, m_st)
    print("\n--- Pass / Fail (out-of-sample, rulebook Section 11) ---")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    verdict = all(ok for _, ok in checks)
    span = f"OOS {p.oos_start} -> {eq.date.max().date() if len(eq) else p.end}"
    if verdict:
        print(f"\n  RESULT: Saare checks PASS ({span}). Agar ye ek-baar wala FINAL test tha to agla step paper trading; "
              f"agar DEV/validation tha to ab final test karo.")
    else:
        print(f"\n  RESULT: Kam se kam ek FAIL ({span}) - live mat jao.")
    trades.to_csv(os.path.join(out_dir, "trades.csv"), index=False)
    eq.to_csv(os.path.join(out_dir, "equity.csv"), index=False)
    yt.to_csv(os.path.join(out_dir, "yearly.csv"), index=False)
    with open(os.path.join(out_dir, "summary.json"), "w") as fh:
        json.dump(dict(params=asdict(p), full=m_all, in_sample=m_is, out_of_sample=m_oos, stress_oos=m_st,
                       checks={k: bool(v) for k, v in checks}, demo_data=is_demo), fh, indent=2, default=str)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 4.5))
        ax.plot(eq.date, eq.equity, color="#2563eb", lw=1.4, label="Strategy (net)")
        if len(eq_s):
            ax.plot(eq_s.date, eq_s.equity, color="#9ca3af", lw=1.0, label="Stress: slippage 2x")
        oos = pd.Timestamp(p.oos_start)
        if eq.date.min() < oos <= eq.date.max():
            ax.axvline(oos, color="#6b7280", ls="--", lw=1)
            ax.text(oos, ax.get_ylim()[1], "  out-of-sample", va="top", color="#6b7280", fontsize=9)
        ax.set_title(f"{p.strategy.upper()} equity (Rs)" + ("  [DEMO DATA]" if is_demo else ""))
        ax.grid(alpha=0.25)
        ax.legend(frameon=False)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "equity.png"), dpi=130)
        plt.close(fig)
    except Exception:
        pass
    print(f"\nFiles saved: {os.path.abspath(out_dir)}")
    return m_is, m_oos, m_st, verdict


def parse_grid(s: str):
    spec = {}
    for part in s.split(";"):
        part = part.strip()
        if not part:
            continue
        k, vals = part.split("=")
        k = k.strip()
        if k not in Params.__dataclass_fields__:
            sys.exit(f"Grid parameter '{k}' galat hai.")
        if k in STR_FIELDS:
            conv = str
        elif k in INT_FIELDS:
            conv = int
        else:
            conv = float
        spec[k] = [conv(v.strip()) for v in vals.split(",")]
    return spec


def run_grid(files, cache_dir, p: Params, grid: str, out_dir, workers=1, progress=None):
    spec = parse_grid(grid)
    keys = list(spec)
    rows = []
    for combo in itertools.product(*[spec[k] for k in keys]):
        q = replace(p, **dict(zip(keys, combo)))
        print(f"\nGrid: {dict(zip(keys, combo))}", flush=True)
        _, eq, trades, eq_s, tr_s = run_once(files, cache_dir, q, verbose=False, workers=workers, progress=progress)
        e_is, t_is = split_period(eq, trades, None, q.oos_start)
        e_oos, t_oos = split_period(eq, trades, q.oos_start, None)
        es, ts = split_period(eq_s, tr_s, q.oos_start, None)
        mi, mo, ms = metrics(e_is, t_is), metrics(e_oos, t_oos), metrics(es, ts)
        rows.append({**dict(zip(keys, combo)),
                     "IS_sharpe": mi.get("sharpe"), "IS_return_pct": mi.get("total_return_pct"),
                     "IS_maxdd_pct": mi.get("max_dd_pct"), "IS_trades": mi.get("trades", 0),
                     "OOS_sharpe": mo.get("sharpe"), "OOS_return_pct": mo.get("total_return_pct"),
                     "OOS_maxdd_pct": mo.get("max_dd_pct"), "OOS_trades": mo.get("trades", 0),
                     "OOS_stress_return_pct": ms.get("total_return_pct"),
                     "all_checks_pass": all(ok for _, ok in pass_fail(mi, mo, ms))})
    df = pd.DataFrame(rows).sort_values("IS_sharpe", ascending=False)
    os.makedirs(out_dir, exist_ok=True)
    df.attrs["keys"] = keys
    df.to_csv(os.path.join(out_dir, "grid.csv"), index=False)
    print("\n--- Grid results (IN-SAMPLE Sharpe se sorted) ---")
    print(df.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
    print("\nNiyam: parameters IN-SAMPLE se chuno (plateau dekho), OOS sirf ek baar confirm karne ke liye.")
    print(f"Saved: {os.path.abspath(os.path.join(out_dir, 'grid.csv'))}")
    return df


# --------------------------------------------------------------------------------------
# Demo data (synthetic) - sirf code test karne ke liye
# --------------------------------------------------------------------------------------
def make_demo_data(out_dir, n_symbols=15, start="2020-09-01", end="2021-12-31", seed=7):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, end)
    t = np.linspace(0, 1, MINUTES)
    prof = 1 + 6 * (t - 0.5) ** 2
    prof = prof / prof.sum()
    os.makedirs(out_dir, exist_ok=True)
    for s in range(n_symbols):
        sym = f"DEMO{s + 1:02d}"
        px = rng.uniform(150, 3000)
        dvol = rng.uniform(0.012, 0.03)
        base_vol = rng.uniform(60e7, 400e7) / px          # turnover ~Rs 60-400 crore/din
        frames = []
        for d in days:
            news = rng.random() < 0.06
            gap = rng.normal(0, dvol * (1.2 if news else 0.35))
            drift = (np.sign(gap) if gap != 0 else 1) * abs(rng.normal(dvol, dvol * 0.5)) if news else 0.0
            vmult = rng.uniform(2.5, 6.0) if news else rng.lognormal(0, 0.25)
            o = px * (1 + gap)
            sm = dvol * 0.9 / math.sqrt(MINUTES)
            rets = rng.normal(drift / MINUTES, sm, MINUTES)
            c = o * np.cumprod(1 + rets)
            op = np.r_[o, c[:-1]]
            hi = np.maximum(op, c) * (1 + np.abs(rng.normal(0, sm * 0.5, MINUTES)))
            lo = np.minimum(op, c) * (1 - np.abs(rng.normal(0, sm * 0.5, MINUTES)))
            vol = np.maximum(1, base_vol * vmult * prof * rng.lognormal(0, 0.3, MINUTES)).astype(np.int64)
            ts = pd.Timestamp(d) + pd.Timedelta(hours=9, minutes=15) + pd.to_timedelta(np.arange(MINUTES), unit="m")
            frames.append(pd.DataFrame(dict(datetime=ts, open=op, high=hi, low=lo, close=c, volume=vol)))
            px = c[-1]
        df = pd.concat(frames, ignore_index=True)
        df["datetime"] = df["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
        df.to_csv(os.path.join(out_dir, f"{sym}.csv"), index=False, float_format="%.2f")
        print(f"  {sym}.csv likh di ({len(df):,} rows)")
    with open(os.path.join(out_dir, DEMO_MARKER), "w") as fh:
        fh.write("Synthetic demo data. Sirf code test ke liye. Inke results ka market se koi lena-dena nahi.\n")
    print(f"Demo data ready: {os.path.abspath(out_dir)}")


# --------------------------------------------------------------------------------------
def sources(data_dir, use_store, p: Params, prefilter=True, verbose=True):
    """(list of stocks to load, cache dir, is_demo). Store entries look like 'store:RELIANCE'."""
    if use_store:
        syms = store_symbols()
        if not syms:
            sys.exit("Store khaali hai - Algo_Historical_Data mein `python -m fyers_data.store build` chalao.")
        if prefilter:
            syms = prefilter_symbols(syms, p, verbose=verbose)
        return [STORE_PREFIX + s for s in syms], None, False
    return list_csvs(data_dir), os.path.join(data_dir, ".cache"), os.path.exists(os.path.join(data_dir, DEMO_MARKER))


def main():
    ap = argparse.ArgumentParser(description="NSE intraday rulebook backtester", formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("--make-demo-data", metavar="DIR", help="synthetic demo data banao aur exit")
    ap.add_argument("--data-dir", help="1-min CSV files wala folder (one file per stock)")
    ap.add_argument("--store", action="store_true", help="Algo data store se padho (fyers_data.store build)")
    ap.add_argument("--workers", type=int, default=1, help="processes (e.g. 4-6 on an 8-core laptop)")
    ap.add_argument("--no-prefilter", action="store_true", help="store: load every stock, even illiquid ones")
    ap.add_argument("--out-dir", default=None, help="results folder (default results/<strategy>_<time>)")
    ap.add_argument("--grid", default=None, help='jaise "stop_atr=0.1,0.25;rvol_min=1,1.5"')
    for fld in Params.__dataclass_fields__.values():
        default = getattr(Params(), fld.name)
        typ = str if fld.name in STR_FIELDS else (int if fld.name in INT_FIELDS else float)
        kw = dict(type=typ, default=None, help=f"default: {default}")
        if fld.name == "strategy":
            kw["choices"] = list(GENERATORS)
        ap.add_argument("--" + fld.name.replace("_", "-"), **kw)
    a = ap.parse_args()

    if a.make_demo_data:
        make_demo_data(a.make_demo_data)
        return
    if not a.data_dir and not a.store:
        ap.error("--store ya --data-dir chahiye (ya --make-demo-data)")
    vals = {k: getattr(a, k) for k in Params.__dataclass_fields__ if getattr(a, k) is not None}
    p = with_defaults(replace(Params(), **vals))
    files, cache_dir, is_demo = sources(a.data_dir, a.store, p, prefilter=not a.no_prefilter)
    out_dir = a.out_dir or os.path.join("results", f"rulebook_{p.strategy}_{time.strftime('%Y%m%d_%H%M%S')}")
    if a.grid:
        run_grid(files, cache_dir, p, a.grid, out_dir, workers=a.workers)
        return
    _, eq, trades, eq_s, tr_s = run_once(files, cache_dir, p, workers=a.workers)
    if not len(eq):
        sys.exit("Is date range mein koi data nahi mila.")
    report(eq, trades, eq_s, tr_s, p, out_dir, is_demo)


if __name__ == "__main__":
    main()

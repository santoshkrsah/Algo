"""Data loading, bar building and data checks for the multi-timeframe Supertrend backtest.

    M = data.load()                       # all 1m / daily series (cached by the podcast data layer)
    H = data.hourly(M, "spot", "include") # 1-hour signal candles (+ fill indices on the futures 1m series)
    D = data.daily(M, "spot")             # daily candles of the signal chart

1-hour candles: 09:15-anchored buckets 09:15, 10:15, 11:15, 12:15, 13:15, 14:15 and the part candle 15:15-15:29, built
from session-only 1-minute candles. A candle's close is known at its end (start + 60 min; 15:30 for the part candle).
Orders always fill on the continuous back-adjusted NIFTY futures 1-minute series (backtest.podcast.data.bars("NIFTY")).
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest.podcast import data as pdata
from backtest.podcast import quality as pq
from backtest.mtst.config import CONFIG

OPEN = 555          # 09:15 as minute of the day
PART = 6            # bucket of the 15:15-15:30 part candle
CLOSE_MIN = 930     # 15:30
NS_MIN = 60_000_000_000


@dataclass
class Hourly:
    chart: str
    last: str
    start: np.ndarray       # candle start, datetime64[ns]
    end: np.ndarray         # time its close is known
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    bucket: np.ndarray      # 0..6 (6 = part candle)
    day: np.ndarray         # datetime64[D]
    lo: np.ndarray          # first / last 1m index of the candle in the signal chart's 1m series
    hi: np.ndarray
    fill_next: np.ndarray   # futures 1m index of the first bar starting at / after `end` (-1: none)
    fill_close: np.ndarray  # futures 1m index of the candle's last minute (-1: none that day)
    src: object             # 1m Bars of the signal chart

    @property
    def n(self) -> int:
        return len(self.c)


@dataclass
class Daily:
    chart: str
    day: np.ndarray         # datetime64[D]
    close_time: np.ndarray  # day + 15:30 (datetime64[ns])
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray


def load() -> dict:
    d = CONFIG["data"]
    fut = pdata.bars("NIFTY", "1m", start=d["start"], end=d["end"])
    spot = pdata.bars("NIFTY50", "1m", kind="index", start=d["spot_start"], end=d["end"])
    sd = pdata.bars("NIFTY50", "1D", kind="index", start=d["daily_start"], end=d["end"])
    v = pdata.read("INDIAVIX", "1D")
    v = v[v["close"] > 0]
    vix = v.set_index(v["datetime"].dt.normalize())["close"]
    return {"fut": fut, "spot": spot, "spot_daily": sd, "vix": vix}


def hourly(M: dict, chart: str, last: str) -> Hourly:
    """1-hour candles of the signal chart (spot or fut). last='drop' leaves out the 15:15-15:30 part candle."""
    b = M["spot"] if chart == "spot" else M["fut"]
    fut = M["fut"]
    bucket = (b.minute.astype(np.int64) - OPEN) // 60
    key = b.di.astype(np.int64) * 8 + bucket
    brk = np.r_[0, np.nonzero(np.diff(key))[0] + 1]
    hi_idx = np.r_[brk[1:] - 1, b.n - 1]
    o, c = b.o[brk], b.c[hi_idx]
    h, l = np.maximum.reduceat(b.h, brk), np.minimum.reduceat(b.l, brk)
    bk = bucket[brk]
    day = b.dt[brk].astype("datetime64[D]")
    day0 = day.astype("datetime64[ns]")
    start = day0 + ((OPEN + 60 * bk) * NS_MIN).astype("timedelta64[ns]")
    end = day0 + (np.where(bk >= PART, CLOSE_MIN, OPEN + 60 * (bk + 1)) * NS_MIN).astype("timedelta64[ns]")
    keep = np.ones(len(brk), bool) if last != "drop" else bk < PART
    end_k = end[keep]
    fn = np.searchsorted(fut.dt, end_k)
    fn = np.where(fn < fut.n, fn, -1)
    fc = np.searchsorted(fut.dt, end_k) - 1                                  # last futures minute before the close time
    same_day = (fc >= 0) & (fut.dt[np.clip(fc, 0, fut.n - 1)].astype("datetime64[D]") == day[keep])
    fc = np.where(same_day, fc, -1)
    return Hourly(chart, last, start[keep], end_k, o[keep], h[keep], l[keep], c[keep], bk[keep], day[keep], brk[keep], hi_idx[keep],
                  fn, fc, b)


def daily(M: dict, chart: str) -> Daily:
    """Daily candles: spot = Fyers daily download (official OHLC, from 2005); fut = built from the back-adjusted 1m futures."""
    if chart == "spot":
        s = M["spot_daily"]
        day, o, h, l, c = s.days, s.o, s.h, s.l, s.c
    else:
        f = M["fut"]
        day = f.days
        o, c = f.o[f.first], f.c[f.last]
        h = np.maximum.reduceat(f.h, f.first)
        l = np.minimum.reduceat(f.l, f.first)
    ct = day.astype("datetime64[ns]") + np.timedelta64(CLOSE_MIN, "m")
    return Daily(chart, day, ct, o, h, l, c)


def daily_index(H: Hourly, D: Daily) -> np.ndarray:
    """For each 1-hour candle: index of the LAST COMPLETED daily candle at the candle's close time (-1: none)."""
    return np.searchsorted(D.close_time, H.end, "right") - 1


# ------------------------------------------------------------------------------------------------------- data checks
def coverage(M: dict) -> pd.DataFrame:
    """Per dataset: symbol, timeframe, first / last date, bars, trading days, missing trading days vs the NSE calendar
    (NIFTY 50 daily), short days."""
    import json
    meta = json.loads((pdata.BARS / "meta.json").read_text(encoding="utf-8"))
    cal = pd.DatetimeIndex(pdata.read("NIFTY50", "1D")["datetime"].dt.normalize())
    rows = []
    for name, tf in [("NIFTY50", "1m"), ("NIFTY50", "1D"), ("NIFTY_F1", "1m"), ("NIFTY_F2", "1m"), ("NIFTY_F1", "1D"), ("INDIAVIX", "1D"), ("INDIAVIX", "1m")]:
        df = pdata.read(name, tf)
        days = pd.DatetimeIndex(df["datetime"].dt.normalize().unique())
        c = cal[(cal >= days.min()) & (cal <= days.max())]
        miss = c.difference(days)
        short = 0
        if tf == "1m":
            n = df[(df.datetime.dt.hour * 60 + df.datetime.dt.minute).between(555, 929)].groupby(df["datetime"].dt.normalize()).size()
            short = int((n < CONFIG.get("min_day_bars", 300)).sum())
        rows.append({"dataset": f"{name}_{tf}", "symbol": meta.get(f"{name}_{tf}", {}).get("symbol", ""), "tf": tf,
                     "first": str(days.min().date()), "last": str(days.max().date()), "bars": len(df), "days": len(days),
                     "missing_days": len(miss), "missing_examples": ", ".join(d.strftime("%Y-%m-%d") for d in miss[:4]), "short_days": short})
    rows.append({"dataset": "NIFTY continuous (built)", "symbol": "F1/F2, roll 3 days before expiry, back-adjusted", "tf": "1m",
                 "first": str(M["fut"].days[0]), "last": str(M["fut"].days[-1]), "bars": M["fut"].n, "days": len(M["fut"].days),
                 "missing_days": 0, "missing_examples": "", "short_days": 0})
    return pd.DataFrame(rows)


def daily_crosscheck(M: dict) -> dict:
    """Downloaded spot daily candles vs candles aggregated from the spot 1m session (2018 on)."""
    s = M["spot"]
    agg = pd.DataFrame({"o": s.o[s.first], "h": np.maximum.reduceat(s.h, s.first), "l": np.minimum.reduceat(s.l, s.first), "c": s.c[s.last]},
                       index=pd.DatetimeIndex(s.days))
    d = M["spot_daily"]
    dl = pd.DataFrame({"o": d.o, "h": d.h, "l": d.l, "c": d.c}, index=pd.DatetimeIndex(d.days))
    j = dl.join(agg, rsuffix="_1m", how="inner")
    out = {"days": len(j), "only_daily": int(len(dl[dl.index >= agg.index[0]].index.difference(agg.index))),
           "only_1m": int(len(agg.index.difference(dl.index)))}
    for k in "ohlc":
        diff = (j[k] - j[k + "_1m"]).abs() / j[k] * 100
        out[k] = {"median_pct": float(diff.median()), "p99_pct": float(diff.quantile(0.99)), "max_pct": float(diff.max()),
                  "n_over_0_05pct": int((diff > 0.05).sum()), "worst": str(diff.idxmax().date())}
    return out


def quality_rows(M: dict) -> pd.DataFrame:
    rows = []
    rows += pq.check_1m("NIFTY50", "index") + pq.check_daily("NIFTY50", "index") + pq.check_daily("INDIAVIX", "index")
    for k in (1, 2):
        rows += pq.check_1m(f"NIFTY_F{k}", "fut")
    rows += pq.check_futures("NIFTY")
    cal = pdata.read("NIFTY50", "1D")
    have = set(cal["datetime"].dt.normalize())
    hol = [x for x in pd.bdate_range(CONFIG["data"]["start"], CONFIG["data"]["end"]) if x not in have]
    rows.append({"dataset": "NIFTY50_1D", "check": "weekday holidays (no session)", "count": len(hol), "examples": pq._ex(d.strftime("%Y-%m-%d") for d in hol[-5:]),
                 "handling": "no trading on those days (the data has none)"})
    for name in ("NIFTY50_1m", "NIFTY_F1_1m"):
        df = pdata.read(name.rsplit("_", 1)[0], "1m")
        m = df.datetime.dt.hour * 60 + df.datetime.dt.minute
        first = df.groupby(df.datetime.dt.normalize())["datetime"].min()
        mode = (first.dt.hour * 60 + first.dt.minute).mode().iloc[0]
        rows.append({"dataset": name, "check": "time zone (most common first candle of the day)", "count": int((m == 555).sum()),
                     "examples": f"first candle {mode // 60:02d}:{mode % 60:02d} on most days -> IST, no tz suffix", "handling": "used as IST"})
    vix = pdata.read("INDIAVIX", "1D")
    bad = vix[(vix["close"] <= 5) | (vix["close"] > 100)]
    rows.append({"dataset": "INDIAVIX_1D", "check": "VIX outside 5-100", "count": len(bad), "examples": pq._ex(bad["datetime"].dt.strftime("%Y-%m-%d")),
                 "handling": "rows dropped" if len(bad) else "none"})
    x = daily_crosscheck(M)
    for k, name in zip("ohlc", ("open", "high", "low", "close")):
        r = x[k]
        rows.append({"dataset": "NIFTY50 1D vs 1m", "check": f"daily {name} vs 1-minute aggregate", "count": r["n_over_0_05pct"],
                     "examples": f"median {r['median_pct']:.3f}%, p99 {r['p99_pct']:.3f}%, max {r['max_pct']:.2f}% ({r['worst']}); {x['days']} days",
                     "handling": "daily file used for the daily Supertrend (official prices); differences are the official open / close calculation"})
    rows.append({"dataset": "NIFTY50 1D vs 1m", "check": "days only in one of the two (2018 on)", "count": x["only_daily"] + x["only_1m"],
                 "examples": f"only daily {x['only_daily']}, only 1m {x['only_1m']}", "handling": "short / special sessions: not in the intraday test"})
    return pd.DataFrame(rows)


def roll_log() -> pd.DataFrame:
    r = pdata.rolls("NIFTY")
    rc = pdata.roll_check("NIFTY")
    rc = rc[rc["kind"] == "roll"].set_index("date")
    r["fut_vs_spot_pct_on_roll_day"] = r["roll_day"].map(rc["diff_pct"])
    f1 = pdata.daily_clean(pdata.read("NIFTY_F1", "1D")).set_index("datetime")["close"]
    f2 = pdata.daily_clean(pdata.read("NIFTY_F2", "1D")).set_index("datetime")["close"]
    gaps = []
    for d in r["roll_day"]:
        prev = f1.index[f1.index < d]
        gaps.append(float(f2.get(prev[-1], np.nan) - f1.get(prev[-1], np.nan)) if len(prev) else np.nan)
    r["roll_gap_pts"] = gaps
    return r

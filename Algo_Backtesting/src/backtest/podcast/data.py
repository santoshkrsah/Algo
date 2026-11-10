"""Bars for the podcast framework, read from ../Algo_Historical_Data/data/bars (python -m fyers_data.download_bars).

    from backtest.podcast import data
    b = data.bars("NIFTY", "5m")             # continuous NIFTY futures, 5-minute, back-adjusted, own roll
    b = data.bars("RELIANCE", "1m")          # RELIANCE cash, 1-minute
    b = data.bars("RELIANCE", "1D", kind="sfut")   # RELIANCE stock futures (continuous), daily
    b = data.bars("NIFTY50", "1D", kind="index")   # NIFTY 50 index, daily (long history)
    exp = data.expiries("NIFTY")             # detected monthly expiry days + checks

Intraday bars: only 09:15-15:29 candle starts are kept (Fyers' futures files also hold 15:30-15:39 and Muhurat-session
candles); days with fewer than CONFIG data.min_day_bars 1-minute candles are special sessions and are dropped.
3m / 5m / 60m candles are rebuilt here from the session-only 1-minute candles (09:15-anchored buckets).

Continuous futures: Fyers' near (F1) and next (F2) series switch contract after each expiry. Expiry days are found in
the data (day after which F1's basis to spot jumps to where F2's basis was), checked against the NSE rule. The next
contract (F2) is used for the last `roll_days` trading days of each contract, F1 otherwise. Prices are back-adjusted
by differences: every segment is shifted by the later roll gaps, measured at the close of the day before each roll
(F2 close - F1 close, same candle), so the newest segment is the real price. `off` = adjusted - real price.
"""
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.config import ROOT
from backtest.podcast.config import CONFIG, INDEX_FUT, SPOT_OF, STOCKS

BARS = Path(CONFIG["data"]["bars_dir"])
CACHE = ROOT / ".cache" / "podcast"
TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "60m": 60}
PRICE = ["open", "high", "low", "close"]


@dataclass
class Bars:
    """One instrument, one timeframe. Prices are adjusted (futures: back-adjusted); real price = price - off."""
    name: str
    kind: str                  # fut (index futures), sfut (stock futures), cash (stock), index
    tf: str
    dt: np.ndarray             # candle start, datetime64[ns]
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    off: np.ndarray            # adjusted - real (0 for cash / index)
    seg: np.ndarray            # contract number (futures), 0 otherwise
    di: np.ndarray             # day number of each candle (index into days)
    days: np.ndarray           # trading days, datetime64[D]
    minute: np.ndarray         # minute of the day of the candle start (555 = 09:15; 0 for daily)
    first: np.ndarray          # first candle of each day
    last: np.ndarray           # last candle of each day

    @property
    def n(self) -> int:
        return len(self.c)

    @property
    def bar_minutes(self) -> int:
        """Length of one candle (a daily candle counts as the whole day, so exits inside it come after its open)."""
        return 1440 if self.tf == "1D" else TF_MIN[self.tf]

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame({"datetime": self.dt, "open": self.o, "high": self.h, "low": self.l, "close": self.c, "volume": self.v,
                             "off": self.off, "seg": self.seg})

    def window(self, start=None, end=None) -> "Bars":
        """Candles of the days start..end (inclusive)."""
        d = self.days
        a = 0 if start is None else int(np.searchsorted(d, np.datetime64(start, "D"), "left"))
        b = len(d) if end is None else int(np.searchsorted(d, np.datetime64(end, "D"), "right"))
        if a >= b:
            return _from_frame(self.frame().iloc[:0], self.name, self.kind, self.tf)
        i0, i1 = self.first[a], self.last[b - 1] + 1
        return _from_frame(self.frame().iloc[i0:i1], self.name, self.kind, self.tf)


def _from_frame(df: pd.DataFrame, name: str, kind: str, tf: str) -> Bars:
    dt = df["datetime"].to_numpy("datetime64[ns]")
    day = dt.astype("datetime64[D]")
    days, di = np.unique(day, return_inverse=True)
    first = np.searchsorted(di, np.arange(len(days)), "left")
    last = np.searchsorted(di, np.arange(len(days)), "right") - 1
    minute = ((dt - day.astype("datetime64[ns]")) // np.timedelta64(1, "m")).astype(np.int32) if tf != "1D" else np.zeros(len(dt), np.int32)
    f = lambda k, default=0.0: df[k].to_numpy(np.float64) if k in df else np.full(len(df), default)
    return Bars(name, kind, tf, dt, f("open"), f("high"), f("low"), f("close"), f("volume"), f("off"),
                df["seg"].to_numpy(np.int32) if "seg" in df else np.zeros(len(df), np.int32), di.astype(np.int32), days, minute,
                first.astype(np.int64), last.astype(np.int64))


# ------------------------------------------------------------------------------------------------------- raw files
def read(name: str, tf: str) -> pd.DataFrame:
    """A file from data/bars as written by the downloader (1D or 1m; 3m/5m/60m are rebuilt from 1m here)."""
    p = BARS / f"{name}_{tf}.parquet"
    if not p.exists():
        raise FileNotFoundError(f"{p} missing - run in Algo_Historical_Data: python -m fyers_data.download_bars")
    df = pd.read_parquet(p)
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df.drop_duplicates("datetime", keep="last").sort_values("datetime").reset_index(drop=True)


def regular_days() -> pd.DatetimeIndex:
    """Normal trading days from 2018: NIFTY 50 index days with >= min_day_bars 1-minute candles in 09:15-15:29."""
    p = CACHE / "regular_days.parquet"
    src = BARS / "NIFTY50_1m.parquet"
    if p.exists() and p.stat().st_mtime >= src.stat().st_mtime:
        return pd.DatetimeIndex(pd.read_parquet(p)["date"])
    df = session(read("NIFTY50", "1m"))
    n = df.groupby(df["datetime"].dt.normalize()).size()
    days = pd.DatetimeIndex(n[n >= CONFIG["data"]["min_day_bars"]].index)
    _save(pd.DataFrame({"date": days}), p)
    return days


def session(df: pd.DataFrame) -> pd.DataFrame:
    """Only 09:15-15:29 candle starts with positive prices."""
    m = df["datetime"].dt.hour * 60 + df["datetime"].dt.minute
    lo, hi = CONFIG["data"]["session"]
    ok = (m >= lo) & (m <= hi) & (df[PRICE] > 0).all(axis=1)
    return df[ok].reset_index(drop=True)


def split_factors(name: str, root: str) -> pd.Series:
    """Per day: whole-number factor between a stock-futures series and the stock's cash price (1 = adjusted). Fyers did
    not adjust some stock futures for splits / bonuses (HDFCBANK before its Sep 2019 split, TCS before its Jun 2018
    bonus: futures = 2 x cash). Factor = round(F / S) when F / S > 1.5 (or 1 / round(S / F) when < 0.67)."""
    if root in INDEX_FUT:
        return pd.Series(dtype=float)
    f = daily_clean(read(name, "1D")).set_index("datetime")["close"]
    s = daily_clean(read(root, "1D")).set_index("datetime")["close"]
    r = (f / s).dropna()
    fac = pd.Series(1.0, index=r.index)
    fac[r > 1.5] = r[r > 1.5].round()
    fac[r < 0.67] = 1 / (1 / r[r < 0.67]).round()
    return fac[fac != 1.0]


def apply_split_factors(df: pd.DataFrame, name: str, root: str) -> pd.DataFrame:
    fac = split_factors(name, root)
    if not len(fac):
        return df
    f = df["datetime"].dt.normalize().map(fac).fillna(1.0).to_numpy()
    df = df.copy()
    df[PRICE] = df[PRICE].div(f, axis=0)
    return df


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Session-only 1m candles -> tf candles anchored at 09:15 (3m: 09:15-09:17 ...; 60m: 09:15-10:14 ... 15:15-15:29)."""
    if tf == "1m":
        return df
    r = df.set_index("datetime").resample(f"{TF_MIN[tf]}min", origin="start_day", offset="9h15min")
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    extra = {k: "first" for k in ("off", "seg") if k in df}
    out = r.agg({**agg, **extra}).dropna(subset=["open"]).reset_index()
    return out


def corp_fix(df: pd.DataFrame, root: str, daily: pd.DataFrame | None = None) -> pd.DataFrame:
    """Demergers Fyers did not adjust (CONFIG data.corp_fixes): prices before the day x (open that day / close the day before),
    taken from the stock's cash daily candles."""
    day = CONFIG["data"]["corp_fixes"].get(root)
    if not day:
        return df
    d = read(root, "1D") if daily is None else daily
    d = d.set_index(d["datetime"].dt.normalize())
    t = pd.Timestamp(day)
    prev = d.index[d.index < t][-1]
    f = float(d.loc[t, "open"] / d.loc[prev, "close"])
    df = df.copy()
    m = df["datetime"] < t
    df.loc[m, PRICE] = df.loc[m, PRICE] * f
    return df


def corp_factor(root: str) -> float | None:
    day = CONFIG["data"]["corp_fixes"].get(root)
    if not day:
        return None
    d = read(root, "1D")
    d = d.set_index(d["datetime"].dt.normalize())
    t = pd.Timestamp(day)
    prev = d.index[d.index < t][-1]
    return float(d.loc[t, "open"] / d.loc[prev, "close"])


def daily_clean(df: pd.DataFrame) -> pd.DataFrame:
    """Daily candles on normal trading days only: from 2018 the days with a full 1-minute session (drops Muhurat and short
    special sessions); before 2018 weekends are dropped (Muhurat sessions were often on a Sunday)."""
    df = df.copy()
    df["datetime"] = df["datetime"].dt.normalize()
    reg = regular_days()
    cut = reg.min()
    keep = np.where(df["datetime"] >= cut, df["datetime"].isin(reg), df["datetime"].dt.weekday < 5)
    return df[keep].reset_index(drop=True)


# ------------------------------------------------------------------------------------------------- expiries + roll
def _rule_expiry(year: int, month: int, trading: pd.DatetimeIndex) -> pd.Timestamp | None:
    """NSE monthly expiry by rule: last Thursday (last Tuesday from Sep 2025), moved back to a trading day."""
    end = pd.Timestamp(year, month, 1) + pd.offsets.MonthEnd(0)
    wd = 1 if pd.Timestamp(year, month, 1) >= pd.Timestamp("2025-09-01") else 3
    d = end - pd.Timedelta(days=(end.weekday() - wd) % 7)
    if d > trading[-1]:
        return None                                      # this month's expiry is after the data ends
    prior = trading[trading <= d]
    return prior[-1] if len(prior) and prior[-1].month == month else None


def nse_futures_expiries() -> pd.Series:
    """NSE monthly futures expiry per month (Oct 2021 on) from the stock futures rows (STF) of the NSE bhavcopy files on
    disk (fno/bhavcopy_hist, fno/bhavcopy; every 5th day is enough: each lists 3 months). Stock futures always expire
    with NIFTY's monthly contract (other index futures - FINNIFTY Tuesday, MIDCPNIFTY Monday - are left out on purpose).
    Index: 'YYYY-MM'. Read once, cached."""
    p = CACHE / "nse_futures_expiries.parquet"
    if p.exists():
        s = pd.read_parquet(p)
        return pd.Series(pd.to_datetime(s["expiry"]).to_numpy(), index=s["month"])
    fno = BARS.parent / "fno"
    seen = set()
    for folder in ("bhavcopy_hist", "bhavcopy"):
        for f in sorted((fno / folder).glob("*.csv"))[::5]:
            try:
                df = pd.read_csv(f, usecols=["FinInstrmTp", "XpryDt"])
            except (ValueError, OSError):
                continue
            seen.update(df.loc[df["FinInstrmTp"] == "STF", "XpryDt"].astype(str).unique())
    ex = pd.Series(pd.to_datetime(sorted(seen)))
    m = ex.groupby(ex.dt.strftime("%Y-%m")).max()
    CACHE.mkdir(parents=True, exist_ok=True)
    _save(pd.DataFrame({"month": m.index, "expiry": m.to_numpy()}), p)
    return m


def nse_monthly_expiries(index_root: str) -> pd.Series:
    """Last option expiry date of each month for an index (fno/bhavcopy_idx_hist, fno/bhavcopy_idx; Oct 2021 on). Equals
    the monthly expiry when weeklies share its weekday (BANKNIFTY's last-Wednesday monthlies, Mar - Dec 2024); can be a
    later weekly otherwise (e.g. BANKNIFTY Wednesday weeklies in Jan 2024), so expiries() cross-checks it. Cached."""
    p = CACHE / f"nse_expiries_{index_root}.parquet"
    if p.exists():
        s = pd.read_parquet(p)
        return pd.Series(pd.to_datetime(s["expiry"]).to_numpy(), index=s["month"])
    fno = BARS.parent / "fno"
    seen = set()
    for folder in ("bhavcopy_idx_hist", "bhavcopy_idx"):
        for f in sorted((fno / folder).glob("*.csv")):
            try:
                df = pd.read_csv(f, usecols=["TckrSymb", "XpryDt"])
            except (ValueError, OSError):
                continue
            seen.update(df.loc[df["TckrSymb"] == index_root, "XpryDt"].astype(str).unique())
    ex = pd.Series(pd.to_datetime(sorted(seen)))
    m = ex.groupby(ex.dt.strftime("%Y-%m")).max()
    CACHE.mkdir(parents=True, exist_ok=True)
    _save(pd.DataFrame({"month": m.index, "expiry": m.to_numpy()}), p)
    return m


def expiries(root: str) -> pd.DataFrame:
    """Monthly expiries of <root> futures and the last day Fyers' near series (F1) still held each old contract.

    nse_expiry: real NSE date from the bhavcopy (Oct 2021 on): the futures monthly date (STF / IDF rows); for an index
    also the last option expiry of the month; if the two differ (BANKNIFTY's last-Wednesday months in 2024, or a weekly
    after the monthly) the one where Fyers' F2 volume collapses (one-day drop on that day or the next) wins. Before Oct
    2021 the NSE rule (last Thursday, moved back to a trading day). Fyers switches F1 / F2 either the day after the expiry (normal) or
    on the expiry day itself (seen Feb 2024 - Mar 2025): on the expiry day the next month (F2) still carries the rollover
    volume if Fyers has not switched yet, and its volume collapses (far month) if it has. So
        F2 volume on expiry day / day before >= 0.4  -> old contract held through the expiry day (expiry = nse_expiry)
        otherwise                                    -> Fyers switched on the expiry day (expiry = trading day before)
    No futures candle on the expiry day (e.g. BANKNIFTY 29 Feb 2024) -> the trading day before.
    Columns: expiry (last day on the old contract; used for the roll), nse_expiry, source, regime, v2_ratio, conv_pct."""
    p = CACHE / f"expiries_{root}.parquet"
    srcs = [BARS / f"{root}_F1_1D.parquet", BARS / f"{root}_F2_1D.parquet"]
    if p.exists() and all(p.stat().st_mtime >= s.stat().st_mtime for s in srcs):
        return pd.read_parquet(p)
    spot = SPOT_OF.get(root, root)
    d2 = daily_clean(read(f"{root}_F2", "1D")).set_index("datetime")
    f1, s = (daily_clean(read(n, "1D")).set_index("datetime")["close"] for n in (f"{root}_F1", spot))
    j = pd.concat({"f1": f1, "s": s, "v2": d2["volume"].astype(float)}, axis=1, sort=True).dropna()
    trading = j.index
    fut_m = nse_futures_expiries()
    opt_m = nse_monthly_expiries(root) if root in INDEX_FUT else pd.Series(dtype="datetime64[ns]")
    drop = j.v2.shift() / j.v2.clip(lower=1.0)

    def collapse(e: pd.Timestamp) -> float:
        after = trading[trading >= e][:2]
        return float(max([drop.get(d, 0.0) for d in after] or [0.0]))

    rows = []
    for y, m in sorted({(d.year, d.month) for d in trading}):
        key = f"{y}-{m:02d}"
        cands = [pd.Timestamp(x[key]) for x in (fut_m, opt_m) if key in x.index]
        cands = sorted(set(cands))
        if len(cands) == 1:
            e, src = cands[0], "NSE bhavcopy"
        elif len(cands) > 1:
            e = max(cands, key=collapse)
            src = "NSE bhavcopy (futures vs option date: " + " / ".join(f"{c:%d %b}" for c in cands) + ", Fyers volume picked)"
        else:
            e, src = _rule_expiry(y, m, trading), "NSE rule (last Thursday)"
        if e is None or e > trading[-1]:
            continue
        prev = trading[trading < e]
        if e not in trading:
            rows.append({"expiry": prev[-1], "nse_expiry": e, "source": src, "regime": "no futures candle on expiry day", "v2_ratio": np.nan, "conv_pct": np.nan})
            continue
        ratio = float(j.v2.loc[e] / max(j.v2.loc[prev[-1]], 1.0)) if len(prev) else np.nan
        held = not (ratio < 0.4)
        last_old = e if held else prev[-1]
        rows.append({"expiry": last_old, "nse_expiry": e, "source": src, "regime": "switch after expiry" if held else "switch on expiry day",
                     "v2_ratio": ratio, "conv_pct": float(abs(j.f1.loc[last_old] / j.s.loc[last_old] - 1) * 100)})
    out = pd.DataFrame(rows).drop_duplicates("expiry").sort_values("expiry").reset_index(drop=True)
    _save(out, p)
    return out


def roll_table(root: str, trading: pd.DatetimeIndex) -> pd.DataFrame:
    """Per trading day: which source (1 = F1, 2 = F2) and which contract (number of the held contract's expiry)."""
    ex = expiries(root)["expiry"].to_numpy("datetime64[ns]")
    k = CONFIG["data"]["roll_days"]
    t = trading.to_numpy("datetime64[ns]")
    pos = np.searchsorted(t, ex)                                     # index of each expiry in trading days
    roll_day = t[np.clip(pos - (k - 1), 0, len(t) - 1)]              # first day on the next contract
    j = np.searchsorted(ex, t, "left")                               # first expiry >= day
    j_ok = j < len(ex)
    on_next = j_ok & (t >= roll_day[np.minimum(j, len(ex) - 1)])
    src = np.where(on_next, 2, 1)
    contract = j + on_next.astype(int)
    return pd.DataFrame({"date": trading, "src": src, "contract": contract})


def continuous(root: str, tf: str) -> pd.DataFrame:
    """Back-adjusted continuous futures for tf (1D / 1m / 3m / 5m / 60m) with columns datetime, OHLCV, off, seg, real_close."""
    if tf == "1D":
        f1, f2 = (daily_clean(read(f"{root}_F{k}", "1D")) for k in (1, 2))
    else:
        f1, f2 = (session(read(f"{root}_F{k}", "1m")) for k in (1, 2))
    f1, f2 = apply_split_factors(f1, f"{root}_F1", root), apply_split_factors(f2, f"{root}_F2", root)
    if root in CONFIG["data"]["corp_fixes"]:
        f1, f2 = corp_fix(f1, root), corp_fix(f2, root)
    d1, d2 = f1["datetime"].dt.normalize(), f2["datetime"].dt.normalize()
    if tf != "1D":
        n1 = d1.value_counts()
        good = n1[n1 >= CONFIG["data"]["min_day_bars"]].index.intersection(regular_days())
        f1, f2 = f1[d1.isin(good)], f2[d2.isin(good)]
        d1, d2 = f1["datetime"].dt.normalize(), f2["datetime"].dt.normalize()
    trading = pd.DatetimeIndex(sorted(set(d1)))
    rt = roll_table(root, trading).set_index("date")
    use1 = d1.map(rt["src"]).eq(1)
    use2 = d2.map(rt["src"]).eq(2)
    df = pd.concat([f1[use1.to_numpy()], f2[use2.to_numpy()]]).sort_values("datetime").reset_index(drop=True)
    day = df["datetime"].dt.normalize()
    df["seg"] = day.map(rt["contract"]).astype(int).to_numpy()
    # roll gaps: F2 close - F1 close on the last candle of the day before each roll
    last1 = f1.groupby(d1.to_numpy())["close"].last()
    last2 = f2.groupby(d2.to_numpy())["close"].last()
    segs = rt["contract"]
    change = segs.ne(segs.shift()) & segs.shift().notna()
    gaps = {}
    for d in segs.index[change.to_numpy()]:
        prev = segs.index[segs.index.get_loc(d) - 1]
        a, b = last1.get(prev, np.nan), last2.get(prev, np.nan)
        gaps[int(segs.loc[d])] = float(b - a) if np.isfinite(a) and np.isfinite(b) else 0.0
    # off(segment) = sum of the gaps of all later rolls
    order = sorted(set(df["seg"]))
    off, acc = {}, 0.0
    for sgm in reversed(order):
        off[sgm] = acc
        acc += gaps.get(sgm, 0.0)
    df["off"] = df["seg"].map(off).astype(float)
    df["real_close"] = df["close"]
    df[PRICE] = df[PRICE].add(df["off"], axis=0)
    if tf not in ("1D", "1m"):
        df = resample(df, tf)
    return df


def roll_check(root: str) -> pd.DataFrame:
    """Continuity test of the daily continuous series: on every roll day and every Fyers switch day the back-adjusted
    close-to-close move must match the spot's move (a wrong contract shows up as a jump of about one month's carry).
    Columns: date, kind (roll / switch), fut_pct, spot_pct, diff_pct."""
    c = continuous(root, "1D").set_index("datetime")
    s = daily_clean(read(SPOT_OF.get(root, root), "1D")).set_index("datetime")["close"]
    j = pd.concat({"f": c["close"], "real": c["close"] - c["off"], "s": s, "seg": c["seg"]}, axis=1, sort=True).dropna()
    rf, rs = j.f.diff() / j.real.shift() * 100, j.s.pct_change() * 100      # % moves always on the real price
    ex = set(expiries(root)["expiry"])
    idx = list(j.index)
    rows = []
    for i in range(1, len(idx)):
        kind = "roll" if j.seg.iloc[i] != j.seg.iloc[i - 1] else ("switch" if idx[i - 1] in ex else None)
        if kind:
            rows.append({"date": idx[i], "kind": kind, "fut_pct": rf.iloc[i], "spot_pct": rs.iloc[i], "diff_pct": rf.iloc[i] - rs.iloc[i]})
    out = pd.DataFrame(rows)
    normal = (rf - rs).drop(out["date"]) if len(out) else rf - rs
    out.attrs["normal_abs_p95"] = float(normal.abs().quantile(0.95))
    return out


def rolls(root: str) -> pd.DataFrame:
    """Roll dates of the continuous series: first day on the new contract, gap (points), contract expiries."""
    f1 = daily_clean(read(f"{root}_F1", "1D"))
    trading = pd.DatetimeIndex(f1["datetime"])
    rt = roll_table(root, trading)
    ex = expiries(root)
    ch = rt[rt["contract"].ne(rt["contract"].shift()) & rt["contract"].shift().notna()]
    out = []
    exps = ex["expiry"].tolist()
    for _, r in ch.iterrows():
        k = int(r["contract"])
        out.append({"roll_day": r["date"], "old_expiry": exps[k - 1] if 0 < k <= len(exps) else None,
                    "new_expiry": exps[k] if k < len(exps) else None})
    return pd.DataFrame(out)


# ----------------------------------------------------------------------------------------------------- public reader
def _cache_ok(p: Path, srcs: list[Path]) -> bool:
    return p.exists() and p.stat().st_size > 0 and all(s.exists() and p.stat().st_mtime >= s.stat().st_mtime for s in srcs)


def _save(df: pd.DataFrame, p: Path):
    """Atomic cache write (temp file + rename): parallel workers never see a half-written file."""
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.stem}.{os.getpid()}.tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, p)


def bars(name: str, tf: str, kind: str | None = None, start: str | None = None, end: str | None = None) -> Bars:
    """name: NIFTY / BANKNIFTY (futures), a stock (cash by default, kind='sfut' = its futures), NIFTY50 / BANKNIFTY index
    (kind='index'). tf: 1D, 1m, 3m, 5m, 60m. Window defaults to CONFIG data.start .. data.end."""
    if kind is None:
        kind = "fut" if name in INDEX_FUT else "cash"
    start = start or CONFIG["data"]["start"]
    end = end or CONFIG["data"]["end"]
    key = f"{name}_{kind}_{tf}"
    p = CACHE / f"{key}.parquet"
    if kind in ("fut", "sfut"):
        srcs = [BARS / f"{name}_F{k}_{'1D' if tf == '1D' else '1m'}.parquet" for k in (1, 2)]
    else:
        srcs = [BARS / f"{SPOT_OF.get(name, name) if kind == 'index' else name}_{'1D' if tf == '1D' else '1m'}.parquet"]
    df = None
    if _cache_ok(p, srcs):
        try:
            df = pd.read_parquet(p)
        except Exception:      # noqa: BLE001  (being replaced by another process right now)
            df = None
    if df is None:
        if kind in ("fut", "sfut"):
            df = continuous(name, tf)
        else:
            src = SPOT_OF.get(name, name) if kind == "index" else name
            if tf == "1D":
                df = daily_clean(read(src, "1D"))
            else:
                df = session(read(src, "1m"))
                n = df.groupby(df["datetime"].dt.normalize()).size()
                good = n[n >= CONFIG["data"]["min_day_bars"]].index.intersection(regular_days())
                df = df[df["datetime"].dt.normalize().isin(good)].reset_index(drop=True)
            if kind == "cash" and name in CONFIG["data"]["corp_fixes"]:
                df = corp_fix(df, name)
            if tf not in ("1D", "1m"):
                df = resample(df, tf)
            df["off"], df["seg"] = 0.0, 0
        _save(df, p)
    df = df[(df["datetime"] >= pd.Timestamp(start)) & (df["datetime"] < pd.Timestamp(end) + pd.Timedelta(days=1))]
    return _from_frame(df.reset_index(drop=True), name, kind, tf)


def universe(module: str) -> list[tuple[str, str]]:
    """[(name, kind)] traded by a module: index futures + stocks (cash for intraday modules, futures for B)."""
    m = CONFIG["modules"][module]
    out = []
    for n in m["instruments"]:
        if n in INDEX_FUT:
            out.append((n, "fut"))
        elif n in STOCKS:
            out.append((n, "sfut" if module == "B" else "cash"))
    return out

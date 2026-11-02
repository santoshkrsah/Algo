"""Fast columnar copy of data/equity/*.csv (Parquet) plus a daily panel, so a backtest reads one day in ~0.05 s instead
of ~1 s and can pick stocks from the panel before touching any minute data.
The daily CSVs stay the source of truth: everything under data/store/ can be deleted and rebuilt.

    python -m fyers_data.store build                 # first time ~10-20 min; afterwards only new or changed days
    python -m fyers_data.store verify --days 5       # store vs CSV, every timeframe -> must end with "OK"
    python -m fyers_data.store info                  # days, stocks, size on disk
    python -m fyers_data.store health [--apply]      # one-day bad prints + big overnight jumps -> store/health/*.csv
    python -m fyers_data.store selftest              # tiny synthetic run in a temp folder (checks pyarrow etc.)

Needs pyarrow (pip install pyarrow).

Layout (data/store/, git-ignored like the rest of data/):
    day/<YYYY-MM-DD>.parquet     1m candles of every stock that day: symbol, minute, open, high, low, close, volume
    sym/<YYYY>/<SYMBOL>.parquet  the same candles per stock per year: date, minute, open, high, low, close, volume
    panel/<YYYY-MM-DD>.parquet   one row per stock per day (columns: see PANEL_DOC)
    daily.parquet                every panel row in one file
    adjustments.csv              price factors applied by the readers (split/bonus the download did not adjust):
                                 symbol,before,factor,source,note -> rows dated before `before`: price / factor, volume x factor
    health/                      bad_days.csv, suggested_adjustments.csv (written by `health`)
    manifest.json                size + mtime of every converted CSV (incremental builds)
minute = minute of the day (555 = 9:15). Only 1m candles are stored; 5/15/30/60m are rebuilt on read with the same
9:15-anchored buckets the downloader used, so what the readers return equals the CSV rows (`verify` checks it).
"""
import argparse
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from fyers_data.paths import EQUITY, REFERENCE, STORE

OPEN_MIN = 555                     # 9:15 as minute of the day
TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60}
COLS = ["symbol", "timeframe", "datetime", "open", "high", "low", "close", "volume"]
M1 = ["symbol", "minute", "open", "high", "low", "close", "volume"]
OR_WINDOWS = (5, 15, 30)
ROW_GROUP = 65_536
SYM_BATCH = 120                    # stocks per task when (re)building sym/<year>/ (memory ~0.5 GB per task)
VERSION = 1
HHMMSS = np.array([f"{m // 60:02d}:{m % 60:02d}:00" for m in range(24 * 60)], dtype=object)
PANEL_DOC = ("date, symbol, open, high, low, close, volume, value (sum of typical price x volume, Rs), vwap, n_bars, "
             "first_min, last_min, o5_/o15_/o30_ + open/high/low/close/vol/value (candles before 9:20 / 9:30 / 9:45), "
             "open_1515 (open of the 15:15 candle)")


@dataclass(frozen=True)
class Layout:
    equity: Path = EQUITY
    store: Path = STORE

    @property
    def day(self) -> Path:
        return self.store / "day"

    @property
    def sym(self) -> Path:
        return self.store / "sym"

    @property
    def panel(self) -> Path:
        return self.store / "panel"

    @property
    def daily(self) -> Path:
        return self.store / "daily.parquet"

    @property
    def adjustments(self) -> Path:
        return self.store / "adjustments.csv"

    @property
    def health(self) -> Path:
        return self.store / "health"

    @property
    def manifest(self) -> Path:
        return self.store / "manifest.json"


def default_workers() -> int:
    return max(1, min(6, (os.cpu_count() or 2) - 1))


# ---------------------------------------------------------------------------------------------------------------- I/O
def write_pq(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False, compression="zstd", row_group_size=ROW_GROUP)
    os.replace(tmp, path)


def read_pq(path: Path, columns=None, filters=None) -> pd.DataFrame:
    return pd.read_parquet(path, columns=columns, filters=filters)


def read_csv_day(path: Path) -> pd.DataFrame:
    """One daily CSV, every timeframe. pyarrow's parser if available (several times faster), else pandas'."""
    dt = {"symbol": str, "timeframe": str, "datetime": str}
    try:
        return pd.read_csv(path, engine="pyarrow", dtype=dt)
    except Exception:
        return pd.read_csv(path, dtype=dt)


def _load_manifest(lay: Layout) -> dict:
    if lay.manifest.exists():
        m = json.loads(lay.manifest.read_text(encoding="utf-8"))
        if m.get("version") == VERSION:
            return m
    return {"version": VERSION, "days": {}, "sym_last": {}}


def _save_manifest(lay: Layout, m: dict) -> None:
    lay.store.mkdir(parents=True, exist_ok=True)
    tmp = lay.manifest.with_name("manifest.json.tmp")
    tmp.write_text(json.dumps(m, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, lay.manifest)


# ------------------------------------------------------------------------------------------------- day -> 1m + panel
def _minutes(dt: pd.Series, day: str) -> tuple[np.ndarray, np.ndarray]:
    """'YYYY-MM-DD HH:MM:SS' -> minute of day; parses each distinct time once (~375 a day). Also: row is on `day`."""
    codes, uniq = pd.factorize(dt)
    uniq = [str(u) for u in uniq]
    mins = np.array([int(u[11:13]) * 60 + int(u[14:16]) for u in uniq], dtype=np.int16)
    same = np.array([u[:10] == day for u in uniq], dtype=bool)
    return mins[codes], same[codes]


def _bounds(codes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """First and last row of every run of equal codes (rows sorted by symbol)."""
    s = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
    return s, np.r_[s[1:], len(codes)] - 1


def make_panel(day: str, m1: pd.DataFrame) -> pd.DataFrame:
    """One row per stock for one day; m1 sorted by symbol, minute."""
    sym = m1["symbol"].to_numpy(dtype=object)
    mins = m1["minute"].to_numpy()
    o, h, l, c = (m1[k].to_numpy(dtype=np.float64) for k in ("open", "high", "low", "close"))
    v = m1["volume"].to_numpy()
    vf = v.astype(np.float64)
    tpv = (h + l + c) / 3.0 * vf
    codes = pd.factorize(sym)[0]
    n = int(codes.max()) + 1 if len(codes) else 0
    s, e = _bounds(codes)
    p = pd.DataFrame({"date": day, "symbol": sym[s], "open": o[s], "high": np.maximum.reduceat(h, s) if n else h[:0],
                      "low": np.minimum.reduceat(l, s) if n else l[:0], "close": c[e],
                      "volume": np.add.reduceat(v, s) if n else v[:0], "value": np.add.reduceat(tpv, s) if n else tpv[:0],
                      "n_bars": (e - s + 1).astype(np.int16), "first_min": mins[s], "last_min": mins[e]})
    p["vwap"] = p["value"] / p["volume"].where(p["volume"] > 0)
    for k in OR_WINDOWS:
        w = mins < OPEN_MIN + k
        cols = {x: np.full(n, np.nan) for x in ("open", "high", "low", "close", "vol", "value")}
        if w.any():
            cw = codes[w]
            s2, e2 = _bounds(cw)
            at = cw[s2]
            cols["open"][at] = o[w][s2]
            cols["high"][at] = np.maximum.reduceat(h[w], s2)
            cols["low"][at] = np.minimum.reduceat(l[w], s2)
            cols["close"][at] = c[w][e2]
            cols["vol"][at] = np.add.reduceat(vf[w], s2)
            cols["value"][at] = np.add.reduceat(tpv[w], s2)
        for x, arr in cols.items():
            p[f"o{k}_{x}"] = arr
    x = np.full(n, np.nan)
    w = mins == 15 * 60 + 15
    x[codes[w]] = o[w]
    p["open_1515"] = x
    return p


def _convert_day(task) -> tuple[str, dict]:
    day, csv_path, lay = task
    df = read_csv_day(csv_path)
    df = df[df["timeframe"] == "1m"]
    mins, same = _minutes(df["datetime"], day)
    vol = df["volume"].to_numpy()
    if vol.dtype.kind == "f" and np.isfinite(vol).all() and (vol == np.round(vol)).all():
        vol = vol.astype(np.int64)
    m1 = pd.DataFrame({"symbol": df["symbol"].to_numpy(dtype=object), "minute": mins,
                       "open": df["open"].to_numpy(dtype=np.float64), "high": df["high"].to_numpy(dtype=np.float64),
                       "low": df["low"].to_numpy(dtype=np.float64), "close": df["close"].to_numpy(dtype=np.float64),
                       "volume": vol})[same]
    m1 = m1.sort_values(["symbol", "minute"], kind="stable")
    dup = m1.duplicated(["symbol", "minute"], keep="last")
    m1 = m1[~dup].reset_index(drop=True)
    write_pq(m1, lay.day / f"{day}.parquet")
    write_pq(make_panel(day, m1), lay.panel / f"{day}.parquet")
    st = Path(csv_path).stat()
    return day, {"size": st.st_size, "mtime": st.st_mtime_ns, "rows": int(len(m1)), "stocks": int(m1["symbol"].nunique()),
                 "dups": int(dup.sum()), "other_day_rows": int((~same).sum())}


# ------------------------------------------------------------------------------------------------- sym/<year>/<SYM>
def _sym_batch(task) -> int:
    """Write sym files for the stocks lo..hi (sorted names) from every day file of one year into out_dir."""
    days, lo, hi, out_dir, lay = task
    parts = []
    for d in days:
        t = read_pq(lay.day / f"{d}.parquet", filters=[("symbol", ">=", lo), ("symbol", "<=", hi)])
        if len(t):
            t.insert(0, "date", d)
            parts.append(t)
    if not parts:
        return 0
    df = pd.concat(parts, ignore_index=True).sort_values(["symbol", "date", "minute"], kind="stable")
    n = 0
    for sym, g in df.groupby("symbol", sort=False):
        write_pq(g.drop(columns="symbol").reset_index(drop=True), Path(out_dir) / f"{sym}.parquet")
        n += 1
    return n


def _year_symbols(lay: Layout, days: list[str]) -> list[str]:
    syms: set[str] = set()
    for d in days:
        syms.update(read_pq(lay.panel / f"{d}.parquet", columns=["symbol"])["symbol"].astype(str))
    return sorted(syms)


def _rebuild_year(lay: Layout, year: str, workers: int, log) -> None:
    days = sorted(p.stem for p in lay.day.glob(f"{year}-??-??.parquet"))
    tmp = lay.sym / f"_tmp_{year}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    syms = _year_symbols(lay, days)
    tasks = [(days, b[0], b[-1], str(tmp), lay) for b in (syms[i:i + SYM_BATCH] for i in range(0, len(syms), SYM_BATCH))]
    t0 = time.time()
    if workers > 1 and len(tasks) > 1:
        with ProcessPoolExecutor(min(workers, len(tasks))) as ex:
            done = sum(ex.map(_sym_batch, tasks))
    else:
        done = sum(_sym_batch(t) for t in tasks)
    final = lay.sym / year
    shutil.rmtree(final, ignore_errors=True)
    os.replace(tmp, final)
    log(f"  sym/{year}: {done} stocks, {len(days)} days ({time.time() - t0:.0f}s)")


def _append_one(args) -> None:
    path, new = args
    if path.exists():
        old = read_pq(path)
        new = pd.concat([old, new], ignore_index=True).drop_duplicates(["date", "minute"], keep="last")
    write_pq(new.sort_values(["date", "minute"], kind="stable").reset_index(drop=True), path)


def _append_days(lay: Layout, year: str, days: list[str], log) -> None:
    t0 = time.time()
    new = pd.concat([read_pq(lay.day / f"{d}.parquet").assign(date=d) for d in days], ignore_index=True)
    new = new[["date"] + [c for c in new.columns if c != "date"]]
    jobs = [(lay.sym / year / f"{sym}.parquet", g.drop(columns="symbol")) for sym, g in new.groupby("symbol", sort=True)]
    (lay.sym / year).mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(_append_one, jobs))
    log(f"  sym/{year}: +{len(days)} day(s) for {len(jobs)} stocks ({time.time() - t0:.0f}s)")


def _update_sym(lay: Layout, man: dict, changed: list[str], workers: int, log) -> None:
    years = sorted({d[:4] for d in changed} | {p.stem[:4] for p in lay.day.glob("????-??-??.parquet")
                                               if not (lay.sym / p.stem[:4]).exists()})
    for y in years:
        new = sorted(d for d in changed if d[:4] == y)
        last = man["sym_last"].get(y)
        if last is None or not (lay.sym / y).exists() or (new and new[0] <= last):
            _rebuild_year(lay, y, workers, log)
        elif new:
            _append_days(lay, y, new, log)
        days = sorted(p.stem for p in lay.day.glob(f"{y}-??-??.parquet"))
        if days:
            man["sym_last"][y] = days[-1]


def rebuild_daily(lay: Layout) -> int:
    files = sorted(lay.panel.glob("????-??-??.parquet"))
    if not files:
        return 0
    with ThreadPoolExecutor(8) as ex:
        parts = list(ex.map(read_pq, files))
    d = pd.concat(parts, ignore_index=True)
    write_pq(d, lay.daily)
    return len(d)


# ---------------------------------------------------------------------------------------------------------- build
def build(lay: Layout | None = None, workers: int | None = None, since: str | None = None, sym: bool = True,
          quiet: bool = False) -> dict:
    """Convert new/changed daily CSVs, refresh daily.parquet and sym/. Returns a small summary dict."""
    lay = lay or Layout()
    workers = workers or default_workers()
    log = (lambda *a: None) if quiet else (lambda *a: print(*a, flush=True))
    man = _load_manifest(lay)
    csvs = {p.stem: p for p in lay.equity.glob("????-??-??.csv")}

    def needs(d: str) -> bool:
        info, st = man["days"].get(d), csvs[d].stat()
        return (info is None or info["size"] != st.st_size or info["mtime"] != st.st_mtime_ns
                or not (lay.day / f"{d}.parquet").exists() or not (lay.panel / f"{d}.parquet").exists())

    todo = [d for d in sorted(csvs) if (since is None or d >= since) and needs(d)]
    log(f"store: {len(csvs)} CSV days, {len(todo)} to convert, workers {workers}")
    t0 = time.time()
    changed = []
    if todo:
        tasks = [(d, csvs[d], lay) for d in todo]
        if workers > 1 and len(tasks) > 1:
            ex = ProcessPoolExecutor(workers)
            futs = [ex.submit(_convert_day, t) for t in tasks]
            results = (f.result() for f in as_completed(futs))
        else:
            ex = None
            results = (_convert_day(t) for t in tasks)
        try:
            for i, (d, info) in enumerate(results, 1):
                man["days"][d] = info
                changed.append(d)
                if i % 25 == 0 or i == len(tasks):
                    el = time.time() - t0
                    log(f"  converted {i}/{len(tasks)} days, {el / 60:.1f} min, ~{el / i * (len(tasks) - i) / 60:.1f} min left")
                    _save_manifest(lay, man)
        finally:
            if ex:
                ex.shutdown()
    if changed or not lay.daily.exists():
        n = rebuild_daily(lay)
        log(f"  daily.parquet: {n:,} stock-days")
    if sym and (changed or not lay.sym.exists()):
        _update_sym(lay, man, changed, workers, log)
    _save_manifest(lay, man)
    dups = sum(man["days"][d].get("dups", 0) for d in changed)
    log(f"store ready in {(time.time() - t0) / 60:.1f} min ({len(changed)} day(s) converted"
        + (f", {dups} duplicate rows dropped" if dups else "") + ")")
    return {"converted": len(changed), "days": len(man["days"]), "minutes": round((time.time() - t0) / 60, 1)}


# ------------------------------------------------------------------------------------- reading back (CSV-identical)
def adjustments(lay: Layout | None = None) -> pd.DataFrame:
    lay = lay or Layout()
    if not lay.adjustments.exists():
        return pd.DataFrame(columns=["symbol", "before", "factor", "source", "note"])
    a = pd.read_csv(lay.adjustments, dtype={"symbol": str, "before": str})
    return a[pd.to_numeric(a["factor"], errors="coerce") > 0]


def to_timeframes(m1: pd.DataFrame, day: str, timeframes=tuple(TF_MIN), parse_dates: bool = False) -> pd.DataFrame:
    """1m rows (symbol, minute, OHLCV; sorted by symbol, minute) -> rows in the daily-CSV format for the given timeframes,
    ordered like the CSV (timeframe, symbol, datetime). Buckets are anchored at 9:15 like the downloader's resample."""
    sym = m1["symbol"].to_numpy(dtype=object)
    mins = m1["minute"].to_numpy().astype(np.int64)
    o, h, l, c = (m1[k].to_numpy(dtype=np.float64) for k in ("open", "high", "low", "close"))
    v = m1["volume"].to_numpy()
    codes = pd.factorize(sym)[0].astype(np.int64)
    out = []
    for tf in [t for t in TF_MIN if t in timeframes]:
        k = TF_MIN[tf]
        if k == 1 or not len(m1):
            part = pd.DataFrame({"symbol": sym, "minute": mins, "open": o, "high": h, "low": l, "close": c, "volume": v})
        else:
            b = (mins - OPEN_MIN) // k
            s, e = _bounds(codes * 100_000 + (b + 50_000))
            part = pd.DataFrame({"symbol": sym[s], "minute": OPEN_MIN + b[s] * k, "open": o[s], "high": np.maximum.reduceat(h, s),
                                 "low": np.minimum.reduceat(l, s), "close": c[e], "volume": np.add.reduceat(v, s)})
        part.insert(1, "timeframe", tf)
        out.append(part)
    df = pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["symbol", "timeframe", "minute"] + COLS[3:])
    dt = (day + " ") + pd.Series(HHMMSS[df["minute"].to_numpy(dtype=np.int64)], index=df.index, dtype=object)
    df.insert(2, "datetime", pd.to_datetime(dt, format="%Y-%m-%d %H:%M:%S") if parse_dates else dt)
    return df.drop(columns="minute")


def day_frame(day: str, timeframes=tuple(TF_MIN), lay: Layout | None = None) -> pd.DataFrame:
    lay = lay or Layout()
    return to_timeframes(read_pq(lay.day / f"{day}.parquet"), day, timeframes)


# --------------------------------------------------------------------------------------------------------- verify
def verify(lay: Layout | None = None, days: int = 5, seed: int = 1) -> bool:
    lay = lay or Layout()
    both = sorted(set(p.stem for p in lay.equity.glob("????-??-??.csv")) & set(p.stem for p in lay.day.glob("????-??-??.parquet")))
    if not both:
        print("verify: no day has both a CSV and a store file - run `build` first")
        return False
    rest = both[1:-1]
    pick = sorted(set([both[0], both[-1]] + random.Random(seed).sample(rest, min(max(days - 2, 0), len(rest)))))
    order = {t: i for i, t in enumerate(TF_MIN)}
    ok = True
    for d in pick:
        a = pd.read_csv(lay.equity / f"{d}.csv", usecols=COLS)          # exactly what the engines read today
        b = day_frame(d, lay=lay)
        for x in (a, b):
            x["_o"] = x["timeframe"].map(order)
        a = a.sort_values(["_o", "symbol", "datetime"], kind="stable").reset_index(drop=True)
        b = b.sort_values(["_o", "symbol", "datetime"], kind="stable").reset_index(drop=True)
        msg = []
        if len(a) != len(b):
            msg.append(f"rows CSV {len(a):,} vs store {len(b):,}")
        else:
            for col in ("symbol", "timeframe", "datetime"):
                if not (a[col].astype(str).to_numpy() == b[col].astype(str).to_numpy()).all():
                    msg.append(f"{col} differs")
            if not (a["volume"].to_numpy(dtype=np.float64) == b["volume"].to_numpy(dtype=np.float64)).all():
                msg.append("volume differs")
            diff = max(float(np.max(np.abs(a[c].to_numpy(dtype=np.float64) - b[c].to_numpy(dtype=np.float64)), initial=0))
                       for c in ("open", "high", "low", "close"))
            if diff > 1e-9:
                msg.append(f"price differs (max {diff:g})")
            elif diff > 0:
                print(f"  {d}: note - last-digit float rounding only (max {diff:.1e})")
        print(f"  {d}: {len(a):,} rows " + ("OK" if not msg else "MISMATCH: " + "; ".join(msg)))
        ok &= not msg
    print("verify: OK - the store returns the same candles as the CSVs" if ok else "verify: FAILED - do not use the store yet")
    return ok


# --------------------------------------------------------------------------------------------------------- health
def _corp_factors(path: Path) -> pd.DataFrame:
    """(symbol, ex_date, factor, kind) for splits and bonuses in NSE corporate actions (same parsing as gaprsi_data)."""
    if not path.exists():
        return pd.DataFrame(columns=["symbol", "ex_date", "factor", "kind"])
    c = pd.read_csv(path)
    if "series" in c:
        c = c[c["series"].isin(["EQ", "BE", "BZ", "SM", "ST"])]
    c = c[c["ex_date"].notna()]
    rows = []
    for r in c.itertuples():
        p = str(r.purpose).lower()
        if "split" in p or "sub-division" in p or "sub division" in p:
            m = re.search(r"from\s+(?:rs\.?|re\.?|inr)?\s*([\d.]+)\s*/?-?.*?to\s+(?:rs\.?|re\.?|inr)?\s*([\d.]+)", p)
            if m and float(m[2]) > 0 and float(m[1]) > float(m[2]):
                rows.append((r.symbol, str(r.ex_date)[:10], float(m[1]) / float(m[2]), "split"))
        elif "bonus" in p:
            m = re.search(r"bonus\s+(\d+)\s*:\s*(\d+)", p)
            if m and int(m[2]) > 0:
                rows.append((r.symbol, str(r.ex_date)[:10], (int(m[1]) + int(m[2])) / int(m[2]), "bonus"))
    return pd.DataFrame(rows, columns=["symbol", "ex_date", "factor", "kind"]).drop_duplicates(["symbol", "ex_date"])


def health(lay: Layout | None = None, apply: bool = False, corp_csv: Path = REFERENCE / "corporate_actions.csv") -> dict:
    """bad_days.csv: one-day bad prints (close jumps > 30% and the next close is back within 15%) and overnight gaps > 25%.
    suggested_adjustments.csv: gaps that match an NSE split/bonus (implied factor within 10% of the announced one).
    --apply merges the suggestions into adjustments.csv, which every reader then applies."""
    lay = lay or Layout()
    d = read_pq(lay.daily, columns=["date", "symbol", "open", "close"]).sort_values(["symbol", "date"], kind="stable")
    d = d.reset_index(drop=True)
    g = d.groupby("symbol", sort=False)["close"]
    prev, nxt = g.shift(1), g.shift(-1)
    spike = ((d["close"] / prev - 1).abs() > 0.30) & ((nxt / prev - 1).abs() < 0.15)
    after_spike = spike.groupby(d["symbol"]).shift(1, fill_value=False).astype(bool)
    gap = d["open"] / prev - 1
    jump = (gap.abs() > 0.25) & ~spike & ~after_spike
    bad = d.assign(prev_close=prev, next_close=nxt, gap_pct=(gap * 100).round(2))
    bad = pd.concat([bad[spike].assign(kind="spike (one-day bad print)"), bad[jump].assign(kind="jump (overnight gap > 25%)")])
    bad = bad.sort_values(["date", "symbol"])[["symbol", "date", "kind", "open", "close", "prev_close", "next_close", "gap_pct"]]
    lay.health.mkdir(parents=True, exist_ok=True)
    bad.to_csv(lay.health / "bad_days.csv", index=False)
    cf = _corp_factors(corp_csv)
    sug = bad[bad["kind"].str.startswith("jump")].merge(cf, left_on=["symbol", "date"], right_on=["symbol", "ex_date"])
    sug["implied"] = sug["prev_close"] / sug["open"]
    sug = sug[(sug["implied"] >= 0.9 * sug["factor"]) & (sug["implied"] <= 1.1 * sug["factor"])]
    sug = pd.DataFrame({"symbol": sug["symbol"], "before": sug["date"], "factor": sug["factor"], "source": "corp_action",
                        "note": sug["kind_y"] + " implied " + sug["implied"].round(3).astype(str)})
    sug.to_csv(lay.health / "suggested_adjustments.csv", index=False)
    added = 0
    if apply and len(sug):
        cur = adjustments(lay)
        merged = pd.concat([cur, sug], ignore_index=True).drop_duplicates(["symbol", "before"], keep="first")
        added = len(merged) - len(cur)
        merged.to_csv(lay.adjustments, index=False)
    print(f"health: {int(spike.sum())} bad prints, {int(jump.sum())} big gaps, {len(sug)} match a split/bonus"
          + (f" ({added} added to adjustments.csv)" if apply else " (run with --apply to use them)")
          + f" -> {lay.health}")
    return {"spikes": int(spike.sum()), "jumps": int(jump.sum()), "suggested": len(sug), "added": added}


# ----------------------------------------------------------------------------------------------------------- info
def _size(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e9 if p.exists() else 0.0


def info(lay: Layout | None = None) -> None:
    lay = lay or Layout()
    days = sorted(p.stem for p in lay.day.glob("????-??-??.parquet"))
    csv = sorted(p.stem for p in lay.equity.glob("????-??-??.csv"))
    print(f"CSV days {len(csv)} ({csv[0] if csv else '-'} -> {csv[-1] if csv else '-'}), {_size(lay.equity):.1f} GB")
    print(f"store days {len(days)} ({days[0] if days else '-'} -> {days[-1] if days else '-'})")
    for name in ("day", "sym", "panel"):
        print(f"  {name}/ {_size(lay.store / name):.2f} GB")
    if lay.daily.exists():
        d = read_pq(lay.daily, columns=["date", "symbol"])
        print(f"  daily.parquet {len(d):,} stock-days, {d['symbol'].nunique()} stocks, last day {d['date'].max()}")
    print(f"  adjustments.csv {len(adjustments(lay))} rows; missing in store: {len(set(csv) - set(days))} day(s)")


# ------------------------------------------------------------------------------------------------------- selftest
def write_synthetic(equity_dir: Path, days: list[str], symbols: list[str], seed: int = 3, px: dict | None = None) -> dict:
    """Daily CSVs exactly like download_equity writes them (1m + 5/15/30/60m resampled from 1m, same sort).
    Returns the last prices, so a later call can continue the same price path."""
    rng = np.random.default_rng(seed)
    equity_dir.mkdir(parents=True, exist_ok=True)
    rules = {"5m": "5min", "15m": "15min", "30m": "30min", "60m": "60min"}
    order = {t: i for i, t in enumerate(TF_MIN)}
    px = dict(px) if px else {s: rng.uniform(80, 3000) for s in symbols}
    for d in days:
        frames = []
        for s in symbols:
            n = 375 - int(rng.integers(0, 3))                     # a few missing minutes at the end sometimes
            ts = pd.Timestamp(d) + pd.Timedelta(minutes=OPEN_MIN) + pd.to_timedelta(np.arange(n), unit="min")
            c = np.round(px[s] * np.cumprod(1 + rng.normal(0, 0.0012, n)), 2)
            op = np.r_[np.round(px[s], 2), c[:-1]]
            hi = np.round(np.maximum(op, c) * (1 + np.abs(rng.normal(0, 0.0005, n))), 2)
            lo = np.round(np.minimum(op, c) * (1 - np.abs(rng.normal(0, 0.0005, n))), 2)
            vol = rng.integers(100, 50_000, n)
            m1 = pd.DataFrame({"datetime": ts, "open": op, "high": hi, "low": lo, "close": c, "volume": vol})
            px[s] = float(c[-1])
            parts = [m1.assign(timeframe="1m")]
            idx = m1.set_index("datetime")
            for tf, rule in rules.items():
                r = idx.resample(rule, origin="start_day", offset="9h15min").agg(
                    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
                parts.append(r.dropna(subset=["open"]).reset_index().assign(timeframe=tf))
            f = pd.concat(parts, ignore_index=True)
            f["symbol"] = s
            frames.append(f[COLS])
        df = pd.concat(frames, ignore_index=True)
        df["_o"] = df["timeframe"].map(order)
        df = df.sort_values(["_o", "symbol", "datetime"]).drop(columns="_o")
        df.to_csv(equity_dir / f"{d}.csv", index=False)
    return px


def selftest() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="store_selftest_"))
    try:
        lay = Layout(equity=tmp / "equity", store=tmp / "store")
        px = write_synthetic(lay.equity, ["2025-12-30", "2025-12-31", "2026-01-01"], ["AAA", "BAJAJ-AUTO", "M&M"])
        build(lay, workers=1, quiet=True)
        ok = verify(lay, days=3)
        write_synthetic(lay.equity, ["2026-01-02"], ["AAA", "BAJAJ-AUTO", "M&M"], seed=4, px=px)   # one more day -> append path
        build(lay, workers=1, quiet=True)
        s = read_pq(lay.sym / "2026" / "M&M.parquet")
        ok &= sorted(s["date"].astype(str).unique()) == ["2026-01-01", "2026-01-02"]
        ok &= verify(lay, days=4)
        health(lay)
        print("SELFTEST OK" if ok else "SELFTEST FAILED")
        return ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------------------------------------------------ CLI
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--workers", type=int, default=None)
    b.add_argument("--since", help="only look at CSV days from this date (YYYY-MM-DD)")
    b.add_argument("--no-sym", action="store_true", help="skip sym/ (per stock files)")
    b.add_argument("--notify", action="store_true", help="Telegram message when finished / failed")
    v = sub.add_parser("verify")
    v.add_argument("--days", type=int, default=5)
    sub.add_parser("info")
    h = sub.add_parser("health")
    h.add_argument("--apply", action="store_true")
    sub.add_parser("selftest")
    a = ap.parse_args()
    if a.cmd == "build":
        try:
            r = build(workers=a.workers, since=a.since, sym=not a.no_sym)
        except Exception as e:
            if a.notify:
                from fyers_data.notify import send
                send(f"Store build FAILED: {type(e).__name__}: {str(e)[:300]}")
            raise
        if a.notify:
            from fyers_data.notify import send
            send(f"Store build done: {r['converted']} day(s) converted, {r['days']} days in store, {r['minutes']} min.")
        return 0
    if a.cmd == "verify":
        return 0 if verify(days=a.days) else 1
    if a.cmd == "info":
        info()
        return 0
    if a.cmd == "health":
        health(apply=a.apply)
        return 0
    return 0 if selftest() else 1


if __name__ == "__main__":
    sys.exit(main())

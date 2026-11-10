"""Fyers bars for the podcast strategy framework (Algo_Backtesting/src/backtest/podcast): daily + 1-minute candles of the
NIFTY 50 and Bank Nifty indices, their near- and next-month futures (Fyers continuous series), 10 liquid stocks (cash)
and the near- / next-month futures of those 10 stocks (positional short trades in stocks need futures).
3m / 5m / 60m files are resampled from 1m (09:15-anchored buckets, the same ones Fyers and the rest of this project use).

    python -m fyers_data.download_bars                  # everything in the settings below; reruns fetch only what is missing
    python -m fyers_data.download_bars --only NIFTY_F1 RELIANCE
    python -m fyers_data.download_bars --report         # coverage table from the saved files, no requests

Read-only: only the public symbol master and the /data/history endpoint are used (no order, position or funds calls).
Credentials come from .env through fyers_data.auth (headless TOTP login, token cached for the day); nothing secret is
printed or logged. If Fyers keeps rejecting the token: run `python -m fyers_data.auth` (fresh login) and check the
FYERS_* entries in .env, then run this again - it resumes.

Output, one file per series and timeframe:  data/bars/<NAME>_<TF>.parquet   (TF = 1D, 1m, 3m, 5m, 60m)
    columns: datetime (IST candle start, no tz suffix; daily rows at 00:00), open, high, low, close, volume
    <ROOT>_F1 = near-month futures, <ROOT>_F2 = next-month futures (ROOT = NIFTY, BANKNIFTY or a stock, e.g. RELIANCE_F1;
    the stock name alone, e.g. RELIANCE, is the cash series). cont_flag=1 makes Fyers follow the near / next
    contract and switch after each expiry; prices are raw (not back-adjusted). The backtest builds its own continuous
    series from F1 + F2 (roll a few days before expiry, roll dates recorded) and never books a roll gap as P&L.
data/bars/meta.json      per file: where each piece came from (seed file / Fyers), whether the start of Fyers' history
                         was reached, and the seed-vs-Fyers price check on the overlap days.
data/bars/coverage.csv   per file: first, last, rows, trading days.

Data already on disk is reused, never downloaded again: index 1m CSVs (data/index), the equity store (stocks 1m from
Oct 2021), the daily CSVs (NIFTY 50 from 2005, stocks from 2010) and the Nov-2026 futures 1m files. Fyers only fills the
gaps (older history, days after the seed) plus ~10 overlap days that prove seed and Fyers agree (adjustment check).

Fyers limits seen on 10 Nov 2026: 1m history from Jan 2018 ("Invalid input" before), continuous futures from Jan 2018
(daily and 1m), index daily from 2005 (Bank Nifty from 18 Oct 2005). At most 100 days per 1m request, 366 per daily one.
"""
import argparse
import json
import random
import sys
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import requests

from fyers_data.auth import auth_header, login
from fyers_data.download_fno import load_master
from fyers_data.paths import DAILY, DATA, FNO, INDEX, STORE

# ----------------------------------------------------------------------------------------------------------- settings
OUT = DATA / "bars"
DAILY_START = date(2005, 1, 1)          # walk back to here (or to the first empty chunk = start of Fyers' history)
INTRADAY_START = date(2017, 6, 1)
STOCKS = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "ITC", "LT", "AXISBANK", "BHARTIARTL"]
INDICES = {"NIFTY50": "NSE:NIFTY50-INDEX", "BANKNIFTY": "NSE:NIFTYBANK-INDEX", "INDIAVIX": "NSE:INDIAVIX-INDEX"}
FUT_ROOTS = ("NIFTY", "BANKNIFTY")      # -> NIFTY_F1 / NIFTY_F2 / BANKNIFTY_F1 / BANKNIFTY_F2
FUT_STOCKS = tuple(STOCKS)              # stock futures (positional shorts need futures): RELIANCE_F1 / RELIANCE_F2 ...
MCX_FUT = ("GOLD",)                     # MCX futures (GOLD = 1 kg contract, bi-monthly expiry): GOLD_F1 / GOLD_F2
MCX_MASTER = "https://public.fyers.in/sym_details/MCX_COM.csv"
RESAMPLE = {"3m": "3min", "5m": "5min", "60m": "60min"}
CHUNK_DAYS = {"1": 99, "D": 365}        # Fyers: 100 days per 1m request, 366 per daily request
OVERLAP_DAYS = 10                       # re-fetched days next to a seed, to prove both sources agree
MIN_INTERVAL = 0.35                     # seconds between requests (Fyers allows ~200/min) -> ~170/min
MAX_RETRIES = 6
BACKOFF_BASE, BACKOFF_CAP = 2.0, 64.0   # exponential backoff: base x 2^attempt seconds (+-25% jitter), capped
SCALE_TOL = 2e-4                        # seed vs Fyers median price ratio beyond this -> seed rescaled (recorded)
HISTORY_URL = "https://api-t1.fyers.in/data/history"
COLS = ["datetime", "open", "high", "low", "close", "volume"]
PRICE = ["open", "high", "low", "close"]


class FyersError(RuntimeError):
    pass


class _Retry(Exception):
    pass


STATS = {"requests": 0, "retries": 0}
_last = [0.0]


def last_complete_day(now: datetime | None = None) -> date:
    now = now or datetime.now()
    return now.date() if now.hour * 60 + now.minute >= 16 * 60 else now.date() - timedelta(days=1)


def _wait():
    dt = time.time() - _last[0]
    if dt < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - dt)
    _last[0] = time.time()


def _backoff(attempt: int):
    STATS["retries"] += 1
    time.sleep(min(BACKOFF_CAP, BACKOFF_BASE * 2 ** attempt) * (0.75 + 0.5 * random.random()))


def fetch(symbol: str, res: str, a: date, b: date, cont: str) -> tuple[list, str]:
    """Candles for the dates a..b. status: ok / empty (no candles) / invalid (range starts before Fyers' history)."""
    relogged = False
    for attempt in range(MAX_RETRIES):
        _wait()
        STATS["requests"] += 1
        try:
            r = requests.get(HISTORY_URL, headers=auth_header(), timeout=60,
                             params=dict(symbol=symbol, resolution=res, date_format="1", range_from=a.isoformat(),
                                         range_to=b.isoformat(), cont_flag=cont))
            if r.status_code == 429 or r.status_code >= 500:
                raise _Retry(f"HTTP {r.status_code}")
            j = r.json()
        except (requests.RequestException, ValueError, _Retry):
            _backoff(attempt)
            continue
        s, code, msg = j.get("s"), j.get("code"), str(j.get("message", "")).lower()
        if s == "ok":
            return j.get("candles") or [], "ok"
        if s == "no_data" or code == -300 or "no data" in msg:
            return [], "empty"
        if code == -50 or "invalid input" in msg:
            return [], "invalid"
        if "invalid symbol" in msg:
            raise FyersError(f"{symbol}: Fyers says invalid symbol")
        if code in (-8, -15, -16, -17) or "token" in msg or "authenticat" in msg:
            if relogged:
                raise FyersError("Fyers rejected the token twice. Run `python -m fyers_data.auth` for a fresh login "
                                 "(check the FYERS_* entries in .env), then rerun - the download resumes.")
            login()
            relogged = True
            continue
        if code == 429 or "limit" in msg or "many" in msg:
            _backoff(attempt + 2)
            continue
        _backoff(attempt)
    raise FyersError(f"giving up on {symbol} {res} {a}..{b} after {MAX_RETRIES} tries")


def to_frame(candles: list, daily: bool) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"])
    dt = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df["datetime"] = dt.dt.normalize() if daily else dt
    return df[COLS]


def fetch_back(symbol: str, res: str, start: date, end: date, cont: str) -> tuple[pd.DataFrame, bool]:
    """Walk newest -> oldest in chunks from `end` down to `start`. Stops at the first empty chunk (the series has no
    older history). A chunk that starts before Fyers' history ('Invalid input') is shrunk from its old side until
    Fyers accepts it. Returns (candles, True if the start of the available history was reached)."""
    parts, hi = [], end
    while hi >= start:
        lo = max(start, hi - timedelta(days=CHUNK_DAYS[res] - 1))
        c, st = fetch(symbol, res, lo, hi, cont)
        while st == "invalid" and (hi - lo).days > 1:
            lo = lo + timedelta(days=max(1, (hi - lo).days // 2))
            c, st = fetch(symbol, res, lo, hi, cont)
        if c:
            parts.append(to_frame(c, res == "D"))
        if st != "ok" or not c or lo > max(start, hi - timedelta(days=CHUNK_DAYS[res] - 1)):
            return _concat(parts), True
        hi = lo - timedelta(days=1)
    return _concat(parts), False


def fetch_fwd(symbol: str, res: str, start: date, end: date, cont: str) -> pd.DataFrame:
    parts, lo = [], start
    while lo <= end:
        hi = min(end, lo + timedelta(days=CHUNK_DAYS[res] - 1))
        c, _ = fetch(symbol, res, lo, hi, cont)
        if c:
            parts.append(to_frame(c, res == "D"))
        lo = hi + timedelta(days=1)
    return _concat(parts)


def _concat(parts: list) -> pd.DataFrame:
    parts = [p for p in parts if len(p)]
    if not parts:
        return pd.DataFrame(columns=COLS)
    df = pd.concat(parts, ignore_index=True)
    return df.drop_duplicates("datetime", keep="last").sort_values("datetime").reset_index(drop=True)


# ------------------------------------------------------------------------------------------------------------- seeds
def _read_csv(path, date_col="datetime") -> pd.DataFrame | None:
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df = df.rename(columns={date_col: "datetime"})
    df["datetime"] = pd.to_datetime(df["datetime"])
    if "volume" not in df:
        df["volume"] = 0
    return df[COLS]


def seed_store_1m(sym: str) -> pd.DataFrame | None:
    """Stock 1m candles from the equity store (data/store/sym/<year>/<SYM>.parquet) with adjustments.csv applied."""
    files = sorted((STORE / "sym").glob(f"*/{sym}.parquet"))
    if not files:
        return None
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    adj_path = STORE / "adjustments.csv"
    if adj_path.exists():
        adj = pd.read_csv(adj_path)
        for _, a in adj[adj["symbol"] == sym].iterrows():
            m = df["date"].astype(str) < str(a["before"])
            df.loc[m, PRICE] = df.loc[m, PRICE] / float(a["factor"])
            df.loc[m, "volume"] = (df.loc[m, "volume"] * float(a["factor"])).round().astype("int64")
    df["datetime"] = pd.to_datetime(df["date"].astype(str)) + pd.to_timedelta(df["minute"].astype(int), unit="m")
    return df[COLS].sort_values("datetime").reset_index(drop=True)


def seeds(near_codes: dict) -> dict:
    """{(name, res): callable returning the seed frame or None}. Only files that already exist on disk."""
    s = {("NIFTY50", "1"): lambda: _read_csv(INDEX / "NIFTY50_FROM2020_1m.csv"),
         ("BANKNIFTY", "1"): lambda: _read_csv(INDEX / "BANKNIFTY_1m.csv"),
         ("NIFTY50", "D"): lambda: _read_csv(DAILY / "index" / "NIFTY50.csv", "date"),
         ("INDIAVIX", "1"): lambda: _read_csv(INDEX / "INDIAVIX_1m.csv")}
    for root, code in near_codes.items():
        s[(f"{root}_F1", "1")] = (lambda c=code: _read_csv(FNO / "1m" / "futures" / f"{c}.csv"))
    for sym in STOCKS:
        s[(sym, "1")] = (lambda x=sym: seed_store_1m(x))
        s[(sym, "D")] = (lambda x=sym: _read_csv(DAILY / "equity" / f"{x}.csv", "date"))
    return s


# ------------------------------------------------------------------------------------------------------------ series
def series_list() -> tuple[list, dict]:
    """[(name, fyers symbol, cont_flag, daily start, intraday start)], {root: near contract code} from the symbol master."""
    m = load_master("NSE")
    near, fut = {}, {}
    today = date.today()
    for root in FUT_ROOTS + FUT_STOCKS:
        ftype = 11 if root in FUT_ROOTS else 13          # 11 = index futures, 13 = stock futures in the Fyers master
        f = m[(m["type"] == ftype) & (m["under"].astype(str).str.split(":").str[-1] == root) & (m["expiry"] >= today)]
        f = f.sort_values("expiry")
        if len(f) < 2:
            raise FyersError(f"symbol master has fewer than 2 live {root} futures")
        syms = f["symbol"].tolist()
        near[root] = syms[0].split(":")[-1]
        fut[root] = [(f"{root}_F1", syms[0], "1", DAILY_START, INTRADAY_START), (f"{root}_F2", syms[1], "1", DAILY_START, INTRADAY_START)]
    out = [(n, s, "0", DAILY_START, INTRADAY_START) for n, s in INDICES.items()]
    out += [x for r in FUT_ROOTS for x in fut[r]]
    out += [(s, f"NSE:{s}-EQ", "0", DAILY_START, INTRADAY_START) for s in STOCKS]
    out += [x for r in FUT_STOCKS for x in fut[r]]
    mcx = pd.read_csv(MCX_MASTER, header=None, low_memory=False)
    mcx["exp"] = pd.to_datetime(mcx[8], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    for root in MCX_FUT:
        f = mcx[(mcx[13].astype(str) == root) & mcx[9].astype(str).str.endswith("FUT") & (mcx["exp"] >= today)].sort_values("exp")
        if len(f) >= 2:
            syms = f[9].tolist()
            out += [(f"{root}_F1", syms[0], "1", DAILY_START, INTRADAY_START), (f"{root}_F2", syms[1], "1", DAILY_START, INTRADAY_START)]
    return out, near


def scale_check(base: pd.DataFrame, fresh: pd.DataFrame) -> dict:
    """Compare closes on common timestamps: median ratio fresh / base and the share of exact-ish matches."""
    j = base[["datetime", "close"]].merge(fresh[["datetime", "close"]], on="datetime", suffixes=("_b", "_f"))
    j = j[(j.close_b > 0) & (j.close_f > 0)]
    if not len(j):
        return {"common": 0}
    r = j.close_f / j.close_b
    return {"common": int(len(j)), "median_ratio": float(r.median()), "match_share": float((abs(r - 1) < 1e-4).mean()),
            "max_abs_diff_pct": float((abs(r - 1)).max() * 100)}


def build(name: str, symbol: str, cont: str, res: str, start: date, end: date, seed_fn, meta: dict) -> pd.DataFrame:
    tf = "1D" if res == "D" else "1m"
    key = f"{name}_{tf}"
    path = OUT / f"{key}.parquet"
    m = meta.setdefault(key, {"symbol": symbol, "cont_flag": cont, "pieces": []})
    base = pd.read_parquet(path) if path.exists() else (seed_fn() if seed_fn else None)
    base_src = "cache" if path.exists() else ("seed" if base is not None else None)
    if base is not None and len(base):
        base = base.drop_duplicates("datetime", keep="last").sort_values("datetime").reset_index(drop=True)
        if res == "D":
            base["datetime"] = base["datetime"].dt.normalize()
        first, last = base["datetime"].min().date(), base["datetime"].max().date()
        old = new = pd.DataFrame(columns=COLS)
        if not m.get("history_start_reached") and first > start:
            old, reached = fetch_back(symbol, res, start, min(end, first + timedelta(days=OVERLAP_DAYS)), cont)
            m["history_start_reached"] = bool(reached)
        if last < end:
            new = fetch_fwd(symbol, res, max(start, last - timedelta(days=OVERLAP_DAYS)), end, cont)
        fresh = _concat([old, new])
        chk = scale_check(base, fresh) if len(fresh) else {"common": 0}
        if chk.get("common") and abs(chk["median_ratio"] - 1) > SCALE_TOL:
            f = chk["median_ratio"]
            base[PRICE] = base[PRICE] * f
            base["volume"] = (base["volume"] / f).round()
            chk["rescaled_by"] = f
        if base_src == "seed" or len(fresh):
            m["pieces"].append({"run": datetime.now().strftime("%Y-%m-%d %H:%M"), "base": base_src, "base_rows": int(len(base)),
                                "fyers_older_rows": int((fresh["datetime"] < base["datetime"].min()).sum()) if len(fresh) else 0,
                                "fyers_newer_rows": int((fresh["datetime"] > base["datetime"].max()).sum()) if len(fresh) else 0,
                                "overlap_check": chk})
        out = _concat([fresh[fresh["datetime"] < base["datetime"].min()], base, fresh[fresh["datetime"] > base["datetime"].max()]])
    else:
        out, reached = fetch_back(symbol, res, start, end, cont)
        m["history_start_reached"] = bool(reached)
        m["pieces"].append({"run": datetime.now().strftime("%Y-%m-%d %H:%M"), "base": None, "fyers_rows": int(len(out))})
    out = out[COLS].copy()
    for c in PRICE:
        out[c] = out[c].astype("float64")
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0).astype("int64")
    OUT.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)
    if res == "1":
        anchor = "9h0min" if symbol.startswith("MCX:") else "9h15min"       # MCX session opens 09:00, NSE 09:15
        for tf2, rule in RESAMPLE.items():
            resample(out, rule, anchor).to_parquet(OUT / f"{name}_{tf2}.parquet", index=False)
    return out


def resample(df: pd.DataFrame, rule: str, anchor: str = "9h15min") -> pd.DataFrame:
    """1m -> N-minute candles anchored at the session open each day (NSE 09:15: 09:15-09:17 ... for 3m; 60m: 09:15-10:14 ...
    15:15-15:29; MCX 09:00)."""
    r = df.set_index("datetime").resample(rule, origin="start_day", offset=anchor)
    out = r.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["open"])
    return out.reset_index()[COLS]


def coverage() -> pd.DataFrame:
    rows = []
    for p in sorted(OUT.glob("*_*.parquet")):
        name, tf = p.stem.rsplit("_", 1)
        df = pd.read_parquet(p, columns=["datetime"])
        if not len(df):
            continue
        d = df["datetime"].dt.normalize()
        rows.append({"name": name, "tf": tf, "first": df["datetime"].min(), "last": df["datetime"].max(), "rows": len(df),
                     "days": int(d.nunique()), "years": round((d.max() - d.min()).days / 365.25, 1)})
    cov = pd.DataFrame(rows)
    if len(cov):
        order = {"1D": 0, "60m": 1, "5m": 2, "3m": 3, "1m": 4}
        cov = cov.sort_values(["name", "tf"], key=lambda s: s.map(order) if s.name == "tf" else s).reset_index(drop=True)
        cov.to_csv(OUT / "coverage.csv", index=False)
    return cov


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", help="series names (NIFTY50 BANKNIFTY NIFTY_F1 ... RELIANCE ...)")
    ap.add_argument("--report", action="store_true", help="coverage table only, no requests")
    ap.add_argument("--end", help="last day (default: last complete trading day)")
    a = ap.parse_args()
    if a.report:
        print(coverage().to_string(index=False))
        return 0
    end = date.fromisoformat(a.end) if a.end else last_complete_day()
    meta_path = OUT / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    series, near = series_list()
    seed_fns = seeds(near)
    meta["_near_contracts"] = near
    t0 = time.time()
    for name, sym, cont, d0, i0 in series:
        if a.only and name not in a.only:
            continue
        for res, start in (("D", d0), ("1", i0)):
            t1 = time.time()
            df = build(name, sym, cont, res, start, end, seed_fns.get((name, res)), meta)
            meta_path.write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
            print(f"{name:12s} {'1D' if res == 'D' else '1m'}: {len(df):>9,} rows  {df['datetime'].min()} -> {df['datetime'].max()}"
                  f"  ({time.time() - t1:.0f}s, requests so far {STATS['requests']})", flush=True)
    print(f"done in {(time.time() - t0) / 60:.1f} min, {STATS['requests']} requests, {STATS['retries']} retries")
    print(coverage().to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Daily candles (Fyers resolution D) for every stock of the equity list and a few indices, 2010 onward - the long
history that swing / positional backtests need (the 1-minute data only starts in Oct 2021).

    python -m fyers_data.download_daily                          # indices, then stocks (biggest market cap first), then the parquet
    python -m fyers_data.download_daily --update                 # new days only (saved stocks), then the parquet
    python -m fyers_data.download_daily --only RELIANCE TCS
    python -m fyers_data.download_daily --indices-only
    python -m fyers_data.download_daily --combine-only           # rebuild daily/equity_daily.parquet from the CSVs

Fyers returns split / bonus adjusted prices (and volumes), at most 366 days per request. Delisted or merged stocks are
not served ('Invalid symbol'), so the history only covers stocks of the AMFI Jun 2026 list (survivorship bias).
Each stock walks one-year chunks newest -> oldest and stops at the first empty chunk (its listing date).
Resumable: a stock whose CSV exists is not asked again (an empty CSV = Fyers has no daily data for it).
New days: `--update` adds only the missing days to every saved stock (one request per stock, ~10 min; if the overlap
days no longer match, Fyers re-adjusted that stock for a split / bonus and its full history is downloaded again),
refreshes the index files, downloads stocks that have no CSV yet and rebuilds the parquet.
Output (IST dates): daily/equity/<SYMBOL>.csv and daily/index/<NAME>.csv with date, open, high, low, close, volume,
plus daily/equity_daily.parquet (every stock in long format: symbol, date, OHLCV).
Never run two Fyers downloads at the same time.
"""
import argparse
import re
import time
from datetime import date, datetime, timedelta

import pandas as pd

from fyers_data.download_fno import fetch_chunk
from fyers_data.notify import send
from fyers_data.paths import DAILY, UNIVERSE_CSV

START = date(2010, 1, 1)
INDEX_START = date(2005, 1, 1)
CHUNK_DAYS = 365                # Fyers rejects daily ranges over 366 days
INDICES = {"NIFTY50": "NSE:NIFTY50-INDEX", "NIFTY200": "NSE:NIFTY200-INDEX", "NIFTY500": "NSE:NIFTY500-INDEX"}
COLS = ["date", "open", "high", "low", "close", "volume"]
OVERLAP = 7                     # --update: re-ask this many calendar days before the last saved day (adjustment check)
TOL = 0.003                     # --update: saved vs new close differ by more than 0.3% -> Fyers re-adjusted the history


def last_complete_day(now: datetime | None = None) -> date:
    now = now or datetime.now()
    return now.date() if now.hour * 60 + now.minute >= 16 * 60 else now.date() - timedelta(days=1)


def short_name(symbol: str) -> str:
    return re.sub(r"^NSE:|-(EQ|BE|BZ|SM|ST|INDEX)$", "", symbol)


def history(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Daily candles start..end. Stops at the first empty chunk once data was found (before the listing date);
    two empty chunks before any data = Fyers has nothing."""
    rows, b, misses = [], end, 0
    while b >= start and misses < (1 if rows else 2):
        a = max(start, b - timedelta(days=CHUNK_DAYS - 1))
        c = fetch_chunk(symbol, a, b, resolution="D")
        rows.extend(c)
        misses = 0 if c else misses + 1
        b = a - timedelta(days=1)
    if not rows:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df.ts, unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.strftime("%Y-%m-%d")
    return df.drop_duplicates("date").sort_values("date")[COLS].reset_index(drop=True)


def save(df: pd.DataFrame, path) -> None:
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(path)                       # a half-written file never looks finished


def indices(start: date, end: date) -> None:
    out = DAILY / "index"
    out.mkdir(parents=True, exist_ok=True)
    for name, sym in INDICES.items():
        df = history(sym, start, end)
        save(df, out / f"{name}.csv")
        print(f"{name}: {len(df)} days {df.date.min() if len(df) else '-'} -> {df.date.max() if len(df) else '-'}", flush=True)


def stocks(start: date, end: date, only: set | None = None) -> list[str]:
    out = DAILY / "equity"
    out.mkdir(parents=True, exist_ok=True)
    u = pd.read_csv(UNIVERSE_CSV).sort_values("mcap_cr", ascending=False)
    have = {p.stem for p in out.glob("*.csv")}
    todo = [s for s in u.symbol if (only is None or short_name(s) in only) and short_name(s) not in have]
    failed, t0 = [], time.time()
    print(f"{len(todo)} stocks to download ({len(have)} already saved), {start} -> {end}", flush=True)
    for n, s in enumerate(todo, 1):
        try:
            save(history(s, start, end), out / f"{short_name(s)}.csv")
        except RuntimeError as e:           # fetch_chunk gave up after its retries; a rerun asks again
            failed.append(short_name(s))
            print(f"FAILED {s}: {e}", flush=True)
        if n % 25 == 0 or n == len(todo):
            print(f"{n}/{len(todo)} stocks, {(time.time() - t0) / 60:.1f} min, last {short_name(s)}", flush=True)
    return failed


def update(end: date, start: date = START, only: set | None = None) -> tuple[list[str], list[str]]:
    """Add the missing days to every saved stock CSV: one request per stock (last saved day - OVERLAP .. end).
    The overlap days must match the saved closes (TOL); if they don't, Fyers re-adjusted the history (split / bonus
    after the first download), so that stock's full history is downloaded again. Empty CSVs (no Fyers data) are skipped.
    Returns (failed, re-downloaded)."""
    out = DAILY / "equity"
    files = sorted(p for p in out.glob("*.csv") if only is None or p.stem in only)
    sym = {short_name(s): s for s in pd.read_csv(UNIVERSE_CSV).symbol}
    failed, redone, added, t0 = [], [], 0, time.time()
    print(f"update: {len(files)} saved stocks -> {end}", flush=True)
    for n, p in enumerate(files, 1):
        try:
            old = pd.read_csv(p, dtype={"date": str})
            if old.empty or old.date.max() >= str(end):
                continue
            a = date.fromisoformat(old.date.max()) - timedelta(days=OVERLAP)
            s = sym.get(p.stem, f"NSE:{p.stem}-EQ")
            rows = fetch_chunk(s, a, end, resolution="D")
            if not rows:
                continue
            new = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
            new["date"] = pd.to_datetime(new.ts, unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.strftime("%Y-%m-%d")
            new = new.drop_duplicates("date")[COLS]
            both = old.merge(new, on="date", suffixes=("", "_n"))
            if len(both) and ((both.close_n / both.close - 1).abs() > TOL).any():
                save(history(s, start, end), p)          # re-adjusted by Fyers -> fresh full history
                redone.append(p.stem)
            else:
                df = pd.concat([old, new[new.date > old.date.max()]], ignore_index=True)
                added += len(df) - len(old)
                save(df, p)
        except RuntimeError as e:           # fetch_chunk gave up after its retries; a rerun asks again
            failed.append(p.stem)
            print(f"FAILED {p.stem}: {e}", flush=True)
        if n % 100 == 0 or n == len(files):
            print(f"{n}/{len(files)} stocks, {(time.time() - t0) / 60:.1f} min, +{added} rows, re-downloaded {len(redone)}", flush=True)
    return failed, redone


def combine() -> pd.DataFrame:
    parts = [pd.read_csv(p).assign(symbol=p.stem) for p in sorted((DAILY / "equity").glob("*.csv"))]
    df = pd.concat([p for p in parts if len(p)], ignore_index=True)[["symbol"] + COLS]
    df.to_parquet(DAILY / "equity_daily.parquet", index=False)
    return df


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=START.isoformat())
    ap.add_argument("--index-start", default=INDEX_START.isoformat())
    ap.add_argument("--end", help="default: last complete trading day")
    ap.add_argument("--only", nargs="*", help="stock names without NSE: / -EQ")
    ap.add_argument("--indices-only", action="store_true")
    ap.add_argument("--combine-only", action="store_true")
    ap.add_argument("--update", action="store_true", help="add only the missing days to the saved stock CSVs")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    end = date.fromisoformat(a.end) if a.end else last_complete_day()
    t0 = time.time()
    failed, redone = [], []
    if not a.combine_only:
        indices(date.fromisoformat(a.index_start), end)
        if a.indices_only:
            return 0
        only = set(a.only) if a.only else None
        if a.update:
            failed, redone = update(end, date.fromisoformat(a.start), only)
        failed += stocks(date.fromisoformat(a.start), end, only)
    df = combine()
    msg = (f"Daily data (Fyers, adjusted) ready in {(time.time() - t0) / 60:.0f} min: {df.symbol.nunique()} stocks, {len(df):,} rows, "
           f"{df.date.min()} -> {df.date.max()} -> data/daily/equity_daily.parquet"
           + (f"\nRe-adjusted by Fyers (full history downloaded again) {len(redone)}: {' '.join(redone[:40])}" if redone else "")
           + (f"\nFAILED {len(failed)}: {' '.join(failed[:40])} (run again to retry)" if failed else ""))
    print(msg, flush=True)
    if not (a.combine_only or a.no_telegram):
        send(msg)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

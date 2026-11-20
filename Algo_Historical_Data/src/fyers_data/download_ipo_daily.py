"""Daily candles (Fyers resolution D, split / bonus adjusted) for every NSE IPO since 2016 that is NOT in the equity list
(mainboard IPOs that are smaller than the AMFI cut-off today, and SME IPOs), plus the Nifty Smallcap 100 index and the
gold / liquid ETFs - the data the IPO-base swing test needs beyond daily/equity.

    python -m fyers_data.download_ipo_daily                 # missing files only (resumable), then daily/ipo_daily.parquet
    python -m fyers_data.download_ipo_daily --update        # saved files: add the missing days (one request each)
    python -m fyers_data.download_ipo_daily --combine-only

Needs reference/ipo_past_issues.csv (python -m fyers_data.download_reference ipos). The IPO symbol is followed through
reference/symbol_changes.csv to today's ticker, and matched to the Fyers symbol master (NSE_CM.csv: -EQ / -BE / -SM / -ST
series). Delisted or merged companies are not served by Fyers ('Invalid symbol'): they stay missing (survivorship bias,
written to daily/ipo/_missing.csv with the reason).
Output: daily/ipo/<SYMBOL>.csv (date, open, high, low, close, volume, like daily/equity), daily/ipo_daily.parquet
(symbol, date, OHLCV), daily/ipo/_list.csv (every IPO since 2016: NSE symbol, today's ticker `now`, type Equity / SME,
listing date, issue price, in_equity = data is in daily/equity), daily/index/NIFTYSMLCAP100.csv, daily/etf/GOLDBEES.csv, daily/etf/LIQUIDBEES.csv.
Never run two Fyers downloads at the same time.
"""
import argparse
import time
from datetime import date, timedelta

import pandas as pd

from fyers_data.download_daily import COLS, history, last_complete_day, save
from fyers_data.paths import DATA, REFERENCE

DAILY = DATA / "daily"
OUT = DAILY / "ipo"
MASTER = "https://public.fyers.in/sym_details/NSE_CM.csv"
EXTRA = {"index/NIFTYSMLCAP100": ("NSE:NIFTYSMLCAP100-INDEX", date(2005, 1, 1)),
         "etf/GOLDBEES": ("NSE:GOLDBEES-EQ", date(2007, 1, 1)),
         "etf/LIQUIDBEES": ("NSE:LIQUIDBEES-EQ", date(2007, 1, 1))}


def current_symbol(sym: str, since: str, changes: pd.DataFrame) -> str:
    """Follow old -> new ticker changes made on / after the listing date (chains like A -> B -> C)."""
    seen = {sym}
    while True:
        c = changes[(changes.old == sym) & (changes.date >= since)]
        if c.empty:
            return sym
        sym = str(c.sort_values("date").new.iloc[-1])
        if sym in seen:
            return sym
        seen.add(sym)


def todo_list() -> tuple[pd.DataFrame, pd.DataFrame]:
    ipo = pd.read_csv(REFERENCE / "ipo_past_issues.csv", dtype=str)
    ipo = ipo[ipo.type.isin(["Equity", "SME"]) & ipo.listing_date.notna() & (ipo.listing_date != "nan")]
    ipo = ipo[ipo.listing_date >= "2016-01-01"].drop_duplicates("symbol", keep="last")
    changes = pd.read_csv(REFERENCE / "symbol_changes.csv", dtype=str)
    ipo["now"] = [current_symbol(s, d, changes) for s, d in zip(ipo.symbol, ipo.listing_date)]
    m = pd.read_csv(MASTER, header=None, usecols=[9, 13], names=["fyers", "short"], dtype=str)
    m = m[m.fyers.str.match(r"^NSE:.+-(EQ|BE|BZ|SM|ST)$", na=False)]
    m["sym"] = m.fyers.str.replace(r"^NSE:|-(EQ|BE|BZ|SM|ST)$", "", regex=True)
    fy = m.drop_duplicates("sym").set_index("sym").fyers
    have = {p.stem for p in (DAILY / "equity").glob("*.csv")}
    ipo["in_equity"] = ipo.now.isin(have)
    ipo["fyers"] = ipo.now.map(fy)
    todo = ipo[~ipo.in_equity & ipo.fyers.notna()]
    missing = ipo[~ipo.in_equity & ipo.fyers.isna()].assign(reason="not in Fyers symbol master today (delisted / merged / renamed)")
    OUT.mkdir(parents=True, exist_ok=True)
    ipo.to_csv(OUT / "_list.csv", index=False)   # every IPO: NSE symbol -> today's ticker, type, listing date
    return todo, missing


def update_file(sym: str, path, start: date, end: date) -> str:
    old = pd.read_csv(path, dtype={"date": str})
    if old.empty or old.date.max() >= str(end):
        return "same"
    new = history(sym, date.fromisoformat(old.date.max()) - timedelta(days=7), end)
    if new.empty:
        return "same"
    both = old.merge(new, on="date", suffixes=("", "_n"))
    if len(both) and ((both.close_n / both.close - 1).abs() > 0.003).any():
        save(history(sym, start, end), path)          # Fyers re-adjusted (split / bonus): full history again
        return "full"
    save(pd.concat([old, new[new.date > old.date.max()]], ignore_index=True)[COLS], path)
    return "new"


def combine() -> pd.DataFrame:
    parts = [pd.read_csv(p, dtype={"date": str}).assign(symbol=p.stem) for p in sorted(OUT.glob("*.csv")) if not p.stem.startswith("_")]
    df = pd.concat([p for p in parts if len(p)], ignore_index=True)[["symbol"] + COLS]
    df.to_parquet(DAILY / "ipo_daily.parquet", index=False)
    return df


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="saved files: add the missing days")
    ap.add_argument("--combine-only", action="store_true")
    ap.add_argument("--end", help="default: last complete trading day")
    a = ap.parse_args()
    end = date.fromisoformat(a.end) if a.end else last_complete_day()
    t0, failed, counts = time.time(), [], {}
    if not a.combine_only:
        for name, (sym, start) in EXTRA.items():
            p = DAILY / f"{name}.csv"
            p.parent.mkdir(parents=True, exist_ok=True)
            r = update_file(sym, p, start, end) if p.exists() else (save(history(sym, start, end), p) or "full")
            print(f"{name}: {r}", flush=True)
        todo, missing = todo_list()
        OUT.mkdir(parents=True, exist_ok=True)
        missing.to_csv(OUT / "_missing.csv", index=False)
        print(f"{len(todo)} IPO stocks outside daily/equity with a Fyers symbol, {len(missing)} without (delisted / merged)", flush=True)
        for n, r in enumerate(todo.itertuples(index=False), 1):
            p = OUT / f"{r.now}.csv"
            start = date.fromisoformat(r.listing_date) - timedelta(days=10)
            try:
                if p.exists():
                    k = update_file(r.fyers, p, start, end) if a.update else "skip"
                else:
                    save(history(r.fyers, start, end), p)
                    k = "full"
                counts[k] = counts.get(k, 0) + 1
            except RuntimeError as e:      # fetch_chunk gave up after its retries; a rerun asks again
                failed.append(r.now)
                print(f"FAILED {r.fyers}: {e}", flush=True)
            if n % 50 == 0 or n == len(todo):
                print(f"{n}/{len(todo)} stocks, {(time.time() - t0) / 60:.1f} min, {counts}", flush=True)
    if a.combine_only:
        OUT.mkdir(parents=True, exist_ok=True)
        todo_list()                        # refreshes daily/ipo/_list.csv (no Fyers request)
    df = combine()
    print(f"ipo daily ready in {(time.time() - t0) / 60:.0f} min: {df.symbol.nunique()} stocks, {len(df):,} rows, {df.date.min()} -> {df.date.max()}"
          + (f"; FAILED {len(failed)}: {' '.join(failed[:30])} (run again)" if failed else ""), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

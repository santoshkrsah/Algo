"""Bring data/equity up to date by downloading ONLY the missing days (one Fyers request per stock for up to 95 days,
~10 min for 1,386 stocks), then refresh the fast store. Re-running download_equity.py would fetch 5 years again (~3 h).

    python -m fyers_data.update_equity                    # newest saved day -> last complete trading day
    python -m fyers_data.update_equity --end 2026-11-20
    python -m fyers_data.update_equity --only RELIANCE TCS --dry-run
    python -m fyers_data.update_equity --nifty            # also refresh index/NIFTY50_1m.csv afterwards

How it works
1. last = newest data/equity/<date>.csv. For every stock in equity_universe.csv: 1m candles last..end through
   download_fno.fetch_chunk (same login, pacing ~150/min and retries as the main downloader).
2. Overlap check: the candles of `last` are compared with the saved ones. If Fyers now returns them scaled (a split or
   bonus after the first download makes Fyers re-adjust history), the factor goes into data/store/adjustments.csv and
   the fast readers apply it to the older days, so the price series stays continuous.
3. New days are written as data/equity/<date>.csv in exactly the downloader's format (1m + 5/15/30/60m) with
   download_equity.with_timeframes / write_parts / stage2. Resumable: data/equity/_update_progress.json.
4. fyers_data.store build converts the new days.
Never run two downloads at the same time (Fyers: 200 requests/min; repeated breaches block the account for the day).
"""
import argparse
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from fyers_data import store
from fyers_data.paths import UNIVERSE_CSV

CHUNK_DAYS = 95             # Fyers rejects 1m ranges over 100 days
BATCH = 25
TOL = 0.003                 # saved vs new overlap prices differ by more than 0.3% -> history was re-adjusted
MIN_MATCH = 30              # minutes that must match for the overlap check


def last_complete_day(now: datetime | None = None) -> date:
    now = now or datetime.now()
    return now.date() if now.hour * 60 + now.minute >= 16 * 60 else now.date() - timedelta(days=1)


def to_frame(candles: list) -> pd.DataFrame:
    """Fyers candles [epoch UTC, o, h, l, c, v] -> IST naive datetime frame (same as download_equity.download_stock)."""
    df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"])
    df["datetime"] = (pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
    df = df.drop(columns="ts").drop_duplicates("datetime").sort_values("datetime")
    return df[["datetime", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def download_range(fetch, ticker: str, a: date, b: date) -> pd.DataFrame:
    rows, s = [], a
    while s <= b:
        e = min(b, s + timedelta(days=CHUNK_DAYS - 1))
        rows.extend(fetch(ticker, s, e))
        s = e + timedelta(days=1)
    return to_frame(rows) if rows else pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])


def saved_1m(day: str, lay: store.Layout) -> dict[str, pd.DataFrame]:
    """Saved 1m closes of `day` per stock (minute, close): from the store if built, else from the CSV."""
    pq = lay.day / f"{day}.parquet"
    if pq.exists():
        df = store.read_pq(pq, columns=["symbol", "minute", "close"])
    else:
        raw = store.read_csv_day(lay.equity / f"{day}.csv")
        raw = raw[raw["timeframe"] == "1m"]
        mins, same = store._minutes(raw["datetime"], day)
        df = pd.DataFrame({"symbol": raw["symbol"].to_numpy(dtype=object), "minute": mins,
                           "close": raw["close"].to_numpy(dtype=np.float64)})[same]
    return {s: g[["minute", "close"]] for s, g in df.groupby("symbol", sort=False)}


def overlap_factor(fresh: pd.DataFrame, saved: pd.DataFrame) -> tuple[float | None, int]:
    """Median of saved close / new close over the minutes both have (None if fewer than MIN_MATCH)."""
    f = pd.DataFrame({"minute": fresh["datetime"].dt.hour * 60 + fresh["datetime"].dt.minute, "new": fresh["close"].to_numpy()})
    m = f.merge(saved.rename(columns={"close": "old"}), on="minute")
    m = m[(m["new"] > 0) & (m["old"] > 0)]
    if len(m) < MIN_MATCH:
        return None, len(m)
    return float(np.median(m["old"] / m["new"])), len(m)


def run(end: date | None = None, only: list[str] | None = None, dry_run: bool = False, nifty: bool = False,
        build_store: bool = True, fetch=None, lay: store.Layout | None = None, universe_csv: Path = UNIVERSE_CSV,
        tell=print) -> dict:
    lay = lay or store.Layout()
    from fyers_data.download_equity import stage2, with_timeframes, write_parts
    if fetch is None:
        from fyers_data.download_fno import fetch_chunk as fetch
    days = sorted(p.stem for p in lay.equity.glob("????-??-??.csv"))
    if not days:
        raise SystemExit("no data/equity/<date>.csv found - run download_equity first")
    last = days[-1]
    today, lcd = date.today(), last_complete_day()
    end = min(end or lcd, lcd)
    uni = pd.read_csv(universe_csv)
    uni["ticker"] = uni["symbol"].str.split(":").str[1].str.removesuffix("-EQ")
    if only:
        uni = uni[uni["ticker"].isin(only)]
    uni = uni.sort_values("symbol").reset_index(drop=True)
    windows = -(-((end - date.fromisoformat(last)).days + 1) // CHUNK_DAYS)
    print(f"update: last saved day {last}, up to {end}, {len(uni)} stocks, ~{len(uni) * max(windows, 0)} requests "
          f"(~{len(uni) * max(windows, 0) / 150:.0f} min)", flush=True)
    if end <= date.fromisoformat(last):
        print("already up to date", flush=True)
        if build_store and not dry_run:
            store.build(lay)
        return {"new_days": 0, "adjusted": 0}
    if dry_run:
        return {"new_days": None, "adjusted": None}
    parts = lay.equity / "_parts"
    prog_file = lay.equity / "_update_progress.json"
    if parts.exists() and not prog_file.exists():
        raise SystemExit(f"{parts} exists from an unfinished download_equity run - finish or clean it first")
    prog = json.loads(prog_file.read_text()) if prog_file.exists() else {}
    if prog.get("last") != last or prog.get("end") != end.isoformat():
        prog = {"last": last, "end": end.isoformat(), "done": [], "adjust": [], "empty": []}
        prog_file.write_text(json.dumps(prog))         # marks _parts as ours from the first batch on
    saved = saved_1m(last, lay)
    before = (date.fromisoformat(last) + timedelta(days=1)).isoformat()
    rows = list(uni.itertuples(index=False))
    batches = [rows[i:i + BATCH] for i in range(0, len(rows), BATCH)]
    tell(f"Equity update STARTED: {last} -> {end}, {len(uni)} stocks.")
    t0 = time.time()
    for bi, batch in enumerate(batches):
        bid = f"u{bi:04d}"
        if bid in prog["done"]:
            continue
        frames, adj = [], []
        for c in batch:
            try:
                m1 = download_range(fetch, c.symbol, date.fromisoformat(last), end)
            except Exception as e:                      # batch stays undone; retried on the next run
                print(f"[batch {bid}] FAIL {c.symbol}: {e}", flush=True)
                frames = None
                break
            if m1.empty:
                prog["empty"].append(c.ticker)
                continue
            d = m1["datetime"].dt.strftime("%Y-%m-%d")
            if c.ticker in saved and (d == last).any():
                f, n = overlap_factor(m1[d == last], saved[c.ticker])
                if f is not None and abs(f - 1) > TOL:
                    adj.append({"symbol": c.ticker, "before": before, "factor": round(f, 6), "source": "update_overlap",
                                "note": f"{last}: saved/new close, median of {n} minutes, found {today}"})
            new = m1[d > last]
            if len(new):
                frames.append(with_timeframes(c.ticker, new.reset_index(drop=True)))
        if frames is None:
            continue
        if frames:
            write_parts(pd.concat(frames, ignore_index=True), parts / bid)
        prog["adjust"] += adj
        prog["done"].append(bid)
        prog_file.write_text(json.dumps(prog))
        print(f"[batch {bi + 1}/{len(batches)}] done, {(time.time() - t0) / 60:.1f} min", flush=True)
    if len(prog["done"]) < len(batches):
        left = len(batches) - len(prog["done"])
        tell(f"Equity update STOPPED: {left} batch(es) failed. Run update_equity again to retry them.")
        return {"new_days": None, "adjusted": None, "failed_batches": left}
    new_days = sorted({p.stem for p in parts.glob("*/*.csv")}) if parts.exists() else []
    if new_days:
        stage2(lay.equity)
    if prog["adjust"]:
        cur = store.adjustments(lay)
        add = pd.DataFrame(prog["adjust"])
        merged = pd.concat([cur, add], ignore_index=True).drop_duplicates(["symbol", "before"], keep="first")
        lay.store.mkdir(parents=True, exist_ok=True)
        merged.to_csv(lay.adjustments, index=False)
    prog_file.unlink(missing_ok=True)
    msg = (f"Equity update DONE: {len(new_days)} new day(s) {new_days[0] if new_days else ''}"
           f"{' -> ' + new_days[-1] if len(new_days) > 1 else ''}, {len(uni)} stocks, {(time.time() - t0) / 60:.0f} min."
           + (f" Re-adjusted by Fyers (factor saved): {', '.join(a['symbol'] for a in prog['adjust'])}." if prog["adjust"] else "")
           + (f" No data: {len(set(prog['empty']))} stocks." if prog["empty"] else ""))
    if build_store:
        store.build(lay)
    if nifty:
        from fyers_data.download_reference import nifty as refresh_nifty
        refresh_nifty()
    tell(msg)
    return {"new_days": len(new_days), "adjusted": len(prog["adjust"]), "empty": len(set(prog["empty"]))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--end", type=date.fromisoformat, default=None, help="last day to fetch (default: last complete day)")
    ap.add_argument("--only", nargs="*", help="only these stocks (names without NSE: / -EQ)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--nifty", action="store_true", help="also refresh index/NIFTY50_1m.csv")
    ap.add_argument("--no-store", action="store_true", help="do not run the store build afterwards")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    if a.no_telegram:
        tell = print
    else:
        from fyers_data.notify import send

        def tell(m: str) -> None:
            print(m, flush=True)
            send(m)
    try:
        run(a.end, a.only, a.dry_run, a.nifty, not a.no_store, tell=tell)
    except Exception as e:
        tell(f"Equity update CRASHED: {type(e).__name__}: {str(e)[:200]}")
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

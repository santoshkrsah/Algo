"""Download NSE equity 1m candles (1 Oct 2021 -> today), resample to 5/15/30/60m, and write
one CSV per trading day containing all stocks and all timeframes.

    python -m fyers_data.download_equity                 # full run (resumable)
    python -m fyers_data.download_equity --only RELIANCE TCS --out equity_test   # --out is a folder under data/

Stage 1: stocks (from equity_universe.csv) in batches -> parts/<batch>/<date>.csv
Stage 2: merge parts of every date -> <out>/<date>.csv (sorted timeframe, symbol, datetime), parts deleted
"""
import argparse
import json
import shutil
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from fyers_data.download_fno import fetch_chunk
from fyers_data.notify import send as notify
from fyers_data.paths import DATA, UNIVERSE_CSV

START = date(2021, 10, 1)
END = date(2026, 10, 28)          # fixed so a resumed run covers the same period as earlier batches
CHUNK_DAYS = 95                 # Fyers rejects 1m ranges over 100 days
BATCH = 25
TF_ORDER = ["1m", "5m", "15m", "30m", "60m"]
RULES = {"5m": "5min", "15m": "15min", "30m": "30min", "60m": "60min"}
COLS = ["symbol", "timeframe", "datetime", "open", "high", "low", "close", "volume"]


def download_stock(ticker: str) -> pd.DataFrame:
    """1m candles, walking chunks newest -> oldest; stops at the first empty chunk (pre-listing)."""
    rows, end = [], END
    while end >= START:
        begin = max(START, end - timedelta(days=CHUNK_DAYS - 1))
        c = fetch_chunk(ticker, begin, end)
        rows.extend(c)
        if not c:
            break
        end = begin - timedelta(days=1)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["datetime"] = (pd.to_datetime(df["ts"], unit="s", utc=True)
                      .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
    df = df.drop(columns="ts").drop_duplicates("datetime").sort_values("datetime")
    return df[["datetime", "open", "high", "low", "close", "volume"]]


def with_timeframes(sym: str, m1: pd.DataFrame) -> pd.DataFrame:
    frames = [m1.assign(timeframe="1m")]
    idx = m1.set_index("datetime")
    for tf, rule in RULES.items():
        r = idx.resample(rule, origin="start_day", offset="9h15min").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        frames.append(r.dropna(subset=["open"]).reset_index().assign(timeframe=tf))
    out = pd.concat(frames, ignore_index=True)
    out["symbol"] = sym
    return out[COLS]


def write_parts(batch_df: pd.DataFrame, part_dir: Path):
    part_dir.mkdir(parents=True, exist_ok=True)
    day = batch_df["datetime"].dt.strftime("%Y-%m-%d")
    for d, g in batch_df.groupby(day, sort=True):
        g.to_csv(part_dir / f"{d}.csv", index=False, header=False)


def stage1(universe: pd.DataFrame, out: Path, prog_file: Path):
    prog = json.loads(prog_file.read_text()) if prog_file.exists() else {"done": [], "empty": []}
    syms = list(universe.itertuples(index=False))
    batches = [syms[i:i + BATCH] for i in range(0, len(syms), BATCH)]
    t0 = time.time()
    for bi, batch in enumerate(batches):
        bid = f"{bi:04d}"
        if bid in prog["done"]:
            continue
        frames = []
        for c in batch:
            name = c.symbol.split(":")[1].removesuffix("-EQ")
            try:
                m1 = download_stock(c.symbol)
            except Exception as e:      # leave batch undone; it is retried on the next run
                print(f"[batch {bid}] FAIL {c.symbol}: {e}", flush=True)
                frames = None
                break
            if m1.empty:
                prog["empty"].append(name)
                continue
            frames.append(with_timeframes(name, m1))
        if frames is None:
            continue
        if frames:
            write_parts(pd.concat(frames, ignore_index=True), out / "_parts" / bid)
        prog["done"].append(bid)
        prog_file.write_text(json.dumps(prog))
        el = (time.time() - t0) / 60
        print(f"[batch {bi + 1}/{len(batches)}] done, elapsed {el:.1f}m, empty so far {len(prog['empty'])}",
              flush=True)
    return len(prog["done"]) == len(batches)


def stage2(out: Path):
    parts = out / "_parts"
    dates = sorted({p.stem for b in parts.iterdir() for p in b.glob("*.csv")})
    print(f"merging {len(dates)} trading days", flush=True)
    order = {t: i for i, t in enumerate(TF_ORDER)}
    for i, d in enumerate(dates, 1):
        files = sorted(parts.glob(f"*/{d}.csv"))
        df = pd.concat([pd.read_csv(f, header=None, names=COLS) for f in files], ignore_index=True)
        df["_o"] = df["timeframe"].map(order)
        df = df.sort_values(["_o", "symbol", "datetime"]).drop(columns="_o")
        tmp = out / f"{d}.csv.tmp"
        df.to_csv(tmp, index=False)
        tmp.replace(out / f"{d}.csv")
        for f in files:
            f.unlink()
        if i % 100 == 0:
            print(f"  merged {i}/{len(dates)}", flush=True)
    shutil.rmtree(parts, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="equity", help="folder name under data/")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    out = DATA / args.out
    out.mkdir(parents=True, exist_ok=True)

    uni = pd.read_csv(UNIVERSE_CSV)
    if args.only:
        uni = uni[uni.symbol.str.split(":").str[1].str.removesuffix("-EQ").isin(args.only)]
    uni = uni.sort_values("symbol")[["symbol"]]
    print(f"{len(uni)} stocks, {START} -> {END}", flush=True)
    try:
        if stage1(uni, out, out / "_progress.json"):
            stage2(out)
            files = len(list(out.glob("2*.csv")))
            gb = sum(p.stat().st_size for p in out.glob("2*.csv")) / 1e9
            print("done", flush=True)
            notify(f"Equity download FINISHED. {files} daily files in {args.out}/ ({gb:.1f} GB).")
            if args.out == "equity":       # the report scans the main data folder
                try:
                    from fyers_data.reports.final_report import main as final_report
                    final_report()
                except Exception as e:
                    notify(f"Final summary PDF failed: {type(e).__name__}: {str(e)[:200]}. The data itself is complete.")
        else:
            left = len(uni) // BATCH + (len(uni) % BATCH > 0) - len(json.loads((out / "_progress.json").read_text())["done"])
            print("some batches failed; rerun to retry them", flush=True)
            notify(f"Equity download STOPPED: {left} batch(es) failed. Re-run download_equity.py to retry them.")
    except Exception as e:
        notify(f"Equity download CRASHED: {type(e).__name__}: {str(e)[:200]}")
        raise


if __name__ == "__main__":
    main()

"""Snapshot statistics of the equity download in progress -> equity_stats.json, equity_per_stock.csv.

Reads data/equity/_parts/<batch>/<date>.csv (headerless) for the batches already marked done.
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from fyers_data.paths import EQUITY as OUT, REPORTS, UNIVERSE_CSV
COLS = ["symbol", "timeframe", "datetime", "open", "high", "low", "close", "volume"]
TFS = ["1m", "5m", "15m", "30m", "60m"]
FINAL = "--final" in sys.argv             # scan the merged daily files instead of the temporary parts
SUFFIX = os.environ.get("EQ_SUFFIX", "")  # lets a test run write *_test outputs
LIMIT = int(os.environ.get("EQ_LIMIT", "0")) or None   # scan only the first N daily files (testing)

t0 = time.time()
prog = json.loads((OUT / "_progress.json").read_text())
done = sorted(prog["done"])
uni = pd.read_csv(UNIVERSE_CSV)
uni["name"] = uni["symbol"].str.split(":").str[1].str.removesuffix("-EQ")
uni = uni.sort_values("symbol").reset_index(drop=True)

per_stock = {}
tf_rows = {t: 0 for t in TFS}
tf_rows_by_year = {}
q = dict(rows=0, dup=0, bad_ohlc=0, neg_vol=0, zero_vol=0, outside=0, files=0)
trading_days = set()
rows_per_day_1m = {}

CAT = {"symbol": "category", "timeframe": "category"}


def iter_days():
    """Yield (date, DataFrame) from the merged daily files (--final) or the saved batch parts."""
    if FINAL:
        for f in sorted(OUT.glob("2*.csv"))[:LIMIT]:
            yield f.stem, pd.read_csv(f, dtype=CAT)
    else:
        for bid in done:
            bdir = OUT / "_parts" / bid
            if bdir.exists():
                for f in sorted(bdir.glob("*.csv")):
                    yield f.stem, pd.read_csv(f, header=None, names=COLS, dtype=CAT)


for day, d in iter_days():
    q["files"] += 1
    q["rows"] += len(d)
    trading_days.add(day)
    q["dup"] += int(d.duplicated(["symbol", "timeframe", "datetime"]).sum())
    q["bad_ohlc"] += int(((d.high < d.low) | (d.high < d[["open", "close"]].max(axis=1) - 1e-9)
                          | (d.low > d[["open", "close"]].min(axis=1) + 1e-9)).sum())
    q["neg_vol"] += int((d.volume < 0).sum())
    q["zero_vol"] += int((d.volume == 0).sum())
    hhmm = d["datetime"].str[11:16]
    q["outside"] += int(((hhmm < "09:15") | (hhmm > "15:39")).sum())
    vc = d["timeframe"].value_counts()
    for t in TFS:
        tf_rows[t] += int(vc.get(t, 0))
    yr = day[:4]
    tf_rows_by_year.setdefault(yr, 0)
    tf_rows_by_year[yr] += len(d)
    m1 = d[d.timeframe == "1m"]
    g = m1.groupby("symbol", observed=True).size()
    rows_per_day_1m[day] = int(g.sum())
    for s, n in g.items():
        r = per_stock.setdefault(str(s), {"rows_1m": 0, "first": day, "last": day, "days": 0,
                                          "close_first": None, "close_last": None})
        r["rows_1m"] += int(n)
        r["days"] += 1
        r["first"] = min(r["first"], day)
        r["last"] = max(r["last"], day)

ps = pd.DataFrame.from_dict(per_stock, orient="index").reset_index().rename(columns={"index": "name"})
ps = ps.merge(uni[["name", "symbol", "mcap_cr", "category"]], on="name", how="left")
REPORTS.mkdir(parents=True, exist_ok=True)
ps.to_csv(REPORTS / f"equity_per_stock{SUFFIX}.csv", index=False)

size_files = list(OUT.glob("2*.csv")) if FINAL else list((OUT / "_parts").rglob("*.csv"))
size_bytes = sum(p.stat().st_size for p in size_files)
days_sorted = sorted(trading_days)
stats = {
    "snapshot": datetime.now().strftime("%d %b %Y %H:%M"),
    "final": FINAL,
    "last_file_mb": round(max(size_files, key=lambda p: p.name).stat().st_size / 1e6, 1) if FINAL and size_files else None,
    "batches_done": len(done), "batches_total": (len(uni) + 24) // 25,
    "stocks_done": len(ps), "stocks_total": len(uni),
    "empty": prog.get("empty", []),
    "size_gb": round(size_bytes / 1e9, 2),
    "files": q["files"], "days": len(days_sorted),
    "first_day": days_sorted[0], "last_day": days_sorted[-1],
    "tf_rows": tf_rows, "quality": q,
    "avg_rows_1m_per_stock": float(ps.rows_1m.mean()),
    "full_history_stocks": int((ps["first"] <= "2021-10-05").sum()),
    "late_listed": ps[ps["first"] > "2021-10-05"].sort_values("first")[["name", "first", "rows_1m"]].values.tolist(),
    "start_by_year": ps["first"].str[:4].value_counts().sort_index().to_dict(),
    "rows_per_day_1m_first": rows_per_day_1m[days_sorted[0]],
    "rows_per_day_1m_last": rows_per_day_1m[days_sorted[-1]],
    "stocks_per_day_first": int(ps[ps["first"] <= days_sorted[0]].shape[0]),
    "downloaded_first_name": ps.name.min(), "downloaded_last_name": ps.name.max(),
    "uni": {
        "n": len(uni), "cat": uni.category.value_counts().to_dict(),
        "mcap_buckets": pd.cut(uni.mcap_cr, [999, 2000, 5000, 10000, 50000, 100000, 1e9],
                               labels=["1k-2k", "2k-5k", "5k-10k", "10k-50k", "50k-1L", ">1L Cr"])
        .value_counts().sort_index().to_dict(),
        "top": uni.nlargest(10, "mcap_cr")[["name", "mcap_cr", "category"]].values.tolist(),
        "bottom": uni.nsmallest(5, "mcap_cr")[["name", "mcap_cr", "category"]].values.tolist(),
        "median_mcap": float(uni.mcap_cr.median()),
    },
    "scan_seconds": round(time.time() - t0),
}
(REPORTS / f"equity_stats{SUFFIX}.json").write_text(json.dumps(stats, indent=1, default=str))
print("ok", stats["batches_done"], stats["stocks_done"], stats["size_gb"], "GB", stats["scan_seconds"], "s", q)

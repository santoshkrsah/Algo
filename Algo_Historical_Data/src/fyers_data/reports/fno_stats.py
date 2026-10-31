"""Scan data/fno/ and write summary_stats.json used by fno_pdf.py."""
import glob
import json
import os
from pathlib import Path

import pandas as pd

from fyers_data.paths import FNO as DATA, REPORTS, SELECTED_CONTRACTS
TFS = ["1m", "5m", "15m", "30m", "60m"]
INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"]

sel = pd.read_csv(SELECTED_CONTRACTS)
sel["name"] = sel["symbol"].str.split(":").str[1]
sel["is_index"] = sel["under"].isin(INDICES)

stats = {"timeframes": {}, "sel": {}}

# ---- per-timeframe totals (rows counted by newline count, no parsing)
for tf in TFS:
    d = {}
    for kind in ["futures", "options"]:
        files = glob.glob(str(DATA / tf / kind / "*.csv"))
        rows = size = nonempty = 0
        for f in files:
            sz = os.path.getsize(f)
            size += sz
            if sz > 60:
                nonempty += 1
                with open(f, "rb") as fh:
                    rows += sum(1 for _ in fh) - 1
        d[kind] = {"files": len(files), "nonempty": nonempty, "rows": rows,
                   "mb": round(size / 1e6, 1)}
    stats["timeframes"][tf] = d

# ---- scan every 1m file once
recs, quality = [], {"dup_ts": 0, "bad_ohlc": 0, "neg_vol": 0, "zero_vol_rows": 0, "rows": 0,
                     "outside_session": 0}
for _, r in sel.iterrows():
    folder = "futures" if r["kind"] == "FUT" else "options"
    f = DATA / "1m" / folder / f"{r['name']}.csv"
    if not f.exists():
        continue
    rec = {"name": r["name"], "under": r["under"], "kind": r["kind"], "is_index": bool(r["is_index"]),
           "opt": r["opt"] if r["kind"] == "OPT" else "", "expiry": str(r["expiry"]),
           "strike": r["strike"] if r["kind"] == "OPT" else None,
           "spot": r.get("spot"), "atm": r.get("atm"), "rows": 0, "start": None, "end": None,
           "days": 0}
    if f.stat().st_size > 60:
        d = pd.read_csv(f, parse_dates=["datetime"])
        rec["rows"] = len(d)
        rec["start"] = str(d.datetime.iloc[0].date())
        rec["end"] = str(d.datetime.iloc[-1].date())
        rec["days"] = int(d.datetime.dt.date.nunique())
        quality["rows"] += len(d)
        quality["dup_ts"] += int(d.datetime.duplicated().sum())
        quality["bad_ohlc"] += int(((d.high < d.low) | (d.high < d[["open", "close"]].max(axis=1) - 1e-9)
                                    | (d.low > d[["open", "close"]].min(axis=1) + 1e-9)).sum())
        quality["neg_vol"] += int((d.volume < 0).sum())
        quality["zero_vol_rows"] += int((d.volume == 0).sum())
        t = d.datetime.dt.time
        quality["outside_session"] += int(((t < pd.Timestamp("09:15").time()) |
                                           (t > pd.Timestamp("15:29").time())).sum())
    recs.append(rec)

df = pd.DataFrame(recs)
REPORTS.mkdir(parents=True, exist_ok=True)
df.to_csv(REPORTS / "summary_per_contract.csv", index=False)

fut = df[df.kind == "FUT"]
opt = df[df.kind == "OPT"]
opt_data = opt[opt.rows > 0]

stats["quality"] = quality
stats["fut"] = {
    "n": len(fut), "start_min": fut.start.min(), "start_max": fut.start.max(),
    "end_min": fut.end.min(), "end_max": fut.end.max(),
    "start_jan": int((fut.start == fut.start.min()).sum()),
    "late": fut[fut.start != fut.start.min()].sort_values("start")[["under", "start", "rows"]].values.tolist(),
    "rows_total": int(fut.rows.sum()),
    "days_median": int(fut.days.median()), "days_max": int(fut.days.max()),
    "idx": fut[fut.is_index][["under", "name", "start", "end", "rows", "days"]].values.tolist(),
}
stats["opt"] = {
    "selected": len(opt), "with_data": len(opt_data), "empty": int((opt.rows == 0).sum()),
    "start_min": opt_data.start.min(), "start_max": opt_data.start.max(),
    "end_min": opt_data.end.min(), "end_max": opt_data.end.max(),
    "rows_total": int(opt.rows.sum()),
    "ce": int((opt_data.opt == "CE").sum()), "pe": int((opt_data.opt == "PE").sum()),
    "start_by_month": opt_data.start.str[:7].value_counts().sort_index().to_dict(),
    "end_by_month": opt_data.end.str[:7].value_counts().sort_index().to_dict(),
    "expiry_counts": opt.groupby("expiry").size().sort_index().to_dict(),
    "days_median": int(opt_data.days.median()), "days_max": int(opt_data.days.max()),
    "stock_n": int((~opt.is_index).sum()), "index_n": int(opt.is_index.sum()),
    "empty_by_under_top": opt[opt.rows == 0].groupby("under").size().sort_values(ascending=False).head(12).to_dict(),
    "top_rows": opt.groupby("under").rows.sum().sort_values(ascending=False).head(10).to_dict(),
}
idx_rows = []
for u in INDICES:
    g = opt[opt.under == u]
    gd = g[g.rows > 0]
    idx_rows.append([u, len(g), len(gd), g.expiry.nunique(), f"{g.expiry.min()} - {g.expiry.max()}",
                     f"{g.strike.min():.0f} - {g.strike.max():.0f}", float(g.spot.iloc[0]) if len(g) else None,
                     gd.start.min() if len(gd) else "-", int(g.rows.sum())])
stats["idx_opts"] = idx_rows
stats["n_under"] = int(sel.under.nunique())
stats["n_stock_under"] = int(sel[~sel.is_index].under.nunique())
stats["unders_stock"] = sorted(sel[~sel.is_index].under.unique().tolist())

(REPORTS / "summary_stats.json").write_text(json.dumps(stats, indent=1, default=str))
print("ok", quality)

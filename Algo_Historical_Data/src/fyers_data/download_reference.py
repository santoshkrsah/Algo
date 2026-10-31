"""Reference data for backtests that need point-in-time lists.

    python -m fyers_data.download_reference nifty     # Nifty 50 index 1m, 1 Oct 2021 -> today (replaces index/NIFTY50_1m.csv)
    python -m fyers_data.download_reference bands [--start 2023-12-01]   # NSE price band list (sec_list) for every equity trading day (resumable)

The NSE price band list gives, per day: series (EQ/BE/...), circuit band (2/5/10/20%, "No Band" = stock is in F&O)
and the GSM stage in Remarks. One file per trading day in reference/price_bands/.
"""
import argparse
import time
from datetime import date, timedelta

import pandas as pd
import requests

from fyers_data.download_fno import fetch_chunk
from fyers_data.paths import EQUITY, INDEX, PRICE_BANDS

START = date(2021, 10, 1)
CHUNK_DAYS = 95
NSE_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
BAND_URL = "https://nsearchives.nseindia.com/content/equities/sec_list_{:%d%m%Y}.csv"


def nifty():
    rows, end = [], date.today()
    while end >= START:
        begin = max(START, end - timedelta(days=CHUNK_DAYS - 1))
        rows.extend(fetch_chunk("NSE:NIFTY50-INDEX", begin, end))
        end = begin - timedelta(days=1)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df = df.drop(columns="ts").drop_duplicates("datetime").sort_values("datetime")
    INDEX.mkdir(parents=True, exist_ok=True)
    df[["datetime", "open", "high", "low", "close", "volume"]].to_csv(INDEX / "NIFTY50_1m.csv", index=False)
    print(f"nifty: {len(df)} candles, {df.datetime.min()} -> {df.datetime.max()}")


def bands(start: str = ""):
    PRICE_BANDS.mkdir(parents=True, exist_ok=True)
    days = sorted(p.stem for p in EQUITY.glob("????-??-??.csv") if p.stem >= start)
    missing = []
    for d in days:
        out = PRICE_BANDS / f"{d}.csv"
        if out.exists():
            continue
        for attempt in range(4):
            try:
                r = requests.get(BAND_URL.format(date.fromisoformat(d)), headers=NSE_UA, timeout=30)
            except requests.RequestException:
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code == 200 and r.content.startswith(b"Symbol"):
                out.write_bytes(r.content)
                break
            if r.status_code == 404:
                break
            time.sleep(3 * (attempt + 1))
        if not out.exists():
            missing.append(d)
        time.sleep(0.3)
    print(f"bands: {len(days) - len(missing)} of {len(days)} days saved; missing: {missing[:20]}{' ...' if len(missing) > 20 else ''}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["nifty", "bands"])
    ap.add_argument("--start", default="", help="bands: only days from this date (YYYY-MM-DD)")
    a = ap.parse_args()
    nifty() if a.what == "nifty" else bands(a.start)

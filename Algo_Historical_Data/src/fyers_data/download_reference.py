"""Reference data for backtests that need point-in-time lists.

    python -m fyers_data.download_reference nifty     # Nifty 50 index 1m, 1 Oct 2021 -> today (replaces index/NIFTY50_1m.csv)
    python -m fyers_data.download_reference index NSE:NIFTYBANK-INDEX BANKNIFTY --start 2024-07-01   # any index 1m -> index/<NAME>_1m.csv
    python -m fyers_data.download_reference bands [--start 2023-12-01]   # NSE price band list (sec_list) for every equity trading day (resumable)
    python -m fyers_data.download_reference corp --start 2024-11-01 [--end 2026-12-31]   # NSE corporate actions -> reference/corporate_actions.csv
    python -m fyers_data.download_reference niftyday --start 2021-01-01 --end 2021-09-30   # Nifty 50 daily OHLC from NSE ind_close_all -> index/NIFTY50_daily.csv
    python -m fyers_data.download_reference symbols   # NSE symbol changes (old -> new ticker, effective date) -> reference/symbol_changes.csv

The NSE price band list gives, per day: series (EQ/BE/...), circuit band (2/5/10/20%, "No Band" = stock is in F&O)
and the GSM stage in Remarks. One file per trading day in reference/price_bands/.
Corporate actions (NSE website API, needs the cookie from the page first): one row per action with ex-date and subject
("Dividend - Rs 22 Per Share", "Bonus 1:1", "Face Value Split ..."), fetched month by month.
"""
import argparse
import io
import time
from datetime import date, timedelta

import pandas as pd
import requests

from fyers_data.download_fno import fetch_chunk
from fyers_data.paths import CORP_ACTIONS, EQUITY, INDEX, PRICE_BANDS, SYMBOL_CHANGES

START = date(2021, 10, 1)
CHUNK_DAYS = 95
NSE_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
BAND_URL = "https://nsearchives.nseindia.com/content/equities/sec_list_{:%d%m%Y}.csv"
CORP_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-actions"
CORP_API = "https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date={:%d-%m-%Y}&to_date={:%d-%m-%Y}"
INDCLOSE_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{:%d%m%Y}.csv"
SYMCHG_URL = "https://nsearchives.nseindia.com/content/equities/symbolchange.csv"


def nifty():
    index_1m("NSE:NIFTY50-INDEX", "NIFTY50")


def index_1m(symbol: str, name: str, start: date = START):
    rows, end = [], date.today()
    while end >= start:
        begin = max(start, end - timedelta(days=CHUNK_DAYS - 1))
        rows.extend(fetch_chunk(symbol, begin, end))
        end = begin - timedelta(days=1)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df = df.drop(columns="ts").drop_duplicates("datetime").sort_values("datetime")
    INDEX.mkdir(parents=True, exist_ok=True)
    df[["datetime", "open", "high", "low", "close", "volume"]].to_csv(INDEX / f"{name}_1m.csv", index=False)
    print(f"{name}: {len(df)} candles, {df.datetime.min()} -> {df.datetime.max()}")


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


def corp(start: date, end: date):
    s = requests.Session()
    head = {**NSE_UA, "Accept": "application/json, text/plain, */*", "Referer": CORP_PAGE}
    rows, failed, a = [], [], start.replace(day=1)
    while a <= end:
        b = min(end, (a.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1))
        for attempt in range(4):
            try:
                if attempt or not s.cookies:
                    s.get(CORP_PAGE, headers=NSE_UA, timeout=30)
                r = s.get(CORP_API.format(a, b), headers=head, timeout=60)
                if r.status_code == 200 and r.text.startswith("["):
                    rows.extend(r.json())
                    break
            except (requests.RequestException, ValueError):
                pass
            time.sleep(3 * (attempt + 1))
        else:
            failed.append(f"{a:%Y-%m}")
        time.sleep(0.5)
        a = b + timedelta(days=1)
    df = pd.DataFrame(rows)
    df["ex_date"] = pd.to_datetime(df.exDate, format="%d-%b-%Y", errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.rename(columns={"subject": "purpose", "comp": "company", "recDate": "record_date"})
    df = df[["symbol", "series", "ex_date", "purpose", "company", "record_date", "faceVal"]].drop_duplicates().sort_values(["ex_date", "symbol"])
    CORP_ACTIONS.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(CORP_ACTIONS, index=False)
    print(f"corp: {len(df)} actions, {df.ex_date.min()} -> {df.ex_date.max()}, {df.symbol.nunique()} symbols; failed months: {failed}")


def nifty_daily(start: date, end: date):
    """Nifty 50 daily OHLC from the NSE all-index close file, one request per weekday (holidays 404). Merged into
    index/NIFTY50_daily.csv (date, open, high, low, close); for warming up long averages before the 1m file starts."""
    out_f = INDEX / "NIFTY50_daily.csv"
    rows, d = [], start
    while d <= end:
        if d.weekday() < 5:
            for attempt in range(4):
                try:
                    r = requests.get(INDCLOSE_URL.format(d), headers=NSE_UA, timeout=30)
                except requests.RequestException:
                    time.sleep(3 * (attempt + 1))
                    continue
                if r.status_code == 200 and r.content.startswith(b"Index Name"):
                    x = pd.read_csv(io.BytesIO(r.content))
                    x = x[x["Index Name"].str.strip().str.upper() == "NIFTY 50"]
                    if len(x):
                        v = x.iloc[0]
                        rows.append([str(d)] + [float(v[c]) for c in ("Open Index Value", "High Index Value", "Low Index Value", "Closing Index Value")])
                    break
                if r.status_code == 404:
                    break
                time.sleep(3 * (attempt + 1))
            time.sleep(0.3)
        d += timedelta(days=1)
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close"])
    if out_f.exists():
        df = pd.concat([pd.read_csv(out_f), df]).drop_duplicates("date", keep="last")
    INDEX.mkdir(parents=True, exist_ok=True)
    df.sort_values("date").to_csv(out_f, index=False)
    print(f"niftyday: {len(rows)} days fetched, file {df.date.min()} -> {df.date.max()} ({len(df)} days)")


def symbols():
    r = requests.get(SYMCHG_URL, headers=NSE_UA, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content), encoding="latin1")
    df.columns = ["company", "old", "new", "date"]
    df = df.assign(old=df.old.str.strip(), new=df.new.str.strip(), date=pd.to_datetime(df.date.str.strip(), format="%d-%b-%Y", errors="coerce").dt.strftime("%Y-%m-%d"))
    SYMBOL_CHANGES.parent.mkdir(parents=True, exist_ok=True)
    df.sort_values("date").to_csv(SYMBOL_CHANGES, index=False)
    print(f"symbols: {len(df)} changes, {df.date.min()} -> {df.date.max()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["nifty", "index", "bands", "corp", "niftyday", "symbols"])
    ap.add_argument("args", nargs="*", help="index: Fyers symbol and file name, e.g. NSE:INDIAVIX-INDEX INDIAVIX")
    ap.add_argument("--start", default="", help="bands: only days from this date; corp: first date (YYYY-MM-DD)")
    ap.add_argument("--end", default=str(date.today()), help="corp: last date")
    a = ap.parse_args()
    if a.what == "index":
        index_1m(a.args[0], a.args[1], date.fromisoformat(a.start) if a.start else START)
    elif a.what == "corp":
        corp(date.fromisoformat(a.start), date.fromisoformat(a.end))
    elif a.what == "niftyday":
        nifty_daily(date.fromisoformat(a.start), date.fromisoformat(a.end))
    elif a.what == "symbols":
        symbols()
    else:
        nifty() if a.what == "nifty" else bands(a.start)

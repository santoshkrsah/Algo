"""BSE SENSEX index option bhavcopy (expired contracts too, one row per contract per day) -> fno/bhavcopy_bse/<YYYY-MM-DD>.csv

    python -m fyers_data.download_bse_bhav --start 2023-05-15 [--end 2026-11-20]      # resumable: days already saved are skipped

Why: expired index options have no 1-minute data anywhere, so the straddle simulation (Algo_Backtesting/src/backtest/straddle.py)
prices them from the daily bhavcopy (open / close of every strike). NSE files are in fno/bhavcopy_idx*; this is the BSE
equivalent for SENSEX (BSE lists SENSEX options; they are not in the NSE files).

Source (public BSE downloads, no login): https://www.bseindia.com/download/Bhavcopy/Derivative/
    BhavCopy_BSE_FO_0_0_0_<YYYYMMDD>_F_0000.CSV   UDiFF format, from 5 Jan 2024 (has lot size, trade count, last traded price)
    MS_<YYYYMMDD>-01.csv                          older "market summary" format (SENSEX = asset code BSX, from 15 May 2023;
                                                  before that the file only has SENSEX 50 options, a different index)
Both are written in ONE format (the NSE UDiFF column names the simulation already reads):
    TradDt, TckrSymb (SENSEX), XpryDt, StrkPric, OptnTp, OpnPric, HghPric, LwPric, ClsPric, LastPric, UndrlygPric,
    TtlTradgVol, TtlNbOfTxsExctd, NewBrdLotQty, Src (udiff / ms)
Known quirks of the BSE file, handled here:
  - On a contract's own expiry day BSE puts the INDEX CLOSE (settlement) into Close Price instead of the option's close. Those rows
    get ClsPric = LastPric (the last traded price) in UDiFF files; the MS format has no last price, so ClsPric is left empty there
    (the simulation then cannot build the closing smile of that contract and skips the day).
  - The MS format has no lot size: SENSEX lot was 10 since the weekly options started (15 May 2023) - UDiFF files carry the real lot.
Holidays return no file; they are remembered in _no_file.txt (only days older than 3 days, so a late file is asked again).
"""
import argparse
import io
import time
from datetime import date, timedelta

import pandas as pd
import requests

from fyers_data.paths import FNO

OUT = FNO / "bhavcopy_bse"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
      "Referer": "https://www.bseindia.com/"}
BASE = "https://www.bseindia.com/download/Bhavcopy/Derivative/"
UDIFF = BASE + "BhavCopy_BSE_FO_0_0_0_{:%Y%m%d}_F_0000.CSV"
MS = BASE + "MS_{:%Y%m%d}-01.csv"
UDIFF_FROM = date(2024, 1, 1)                  # UDiFF files exist from 5 Jan 2024; before that only the MS format
MS_LOT = 10                                    # SENSEX lot size from the weekly launch (15 May 2023) until the UDiFF files carry it
COLS = ["TradDt", "TckrSymb", "XpryDt", "StrkPric", "OptnTp", "OpnPric", "HghPric", "LwPric", "ClsPric", "LastPric", "UndrlygPric",
        "TtlTradgVol", "TtlNbOfTxsExctd", "NewBrdLotQty", "Src"]


def fetch(url: str, session: requests.Session) -> bytes | None:
    for attempt in range(4):
        try:
            r = session.get(url, headers=UA, timeout=40)
        except requests.RequestException:
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code == 200 and len(r.content) > 300 and not r.content[:15].lower().startswith(b"<!doctype"):
            return r.content
        if r.status_code in (200, 404):
            return None
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"giving up on {url}")


def from_udiff(raw: bytes, d: date) -> pd.DataFrame:
    x = pd.read_csv(io.BytesIO(raw))
    x = x[(x.TckrSymb == "SENSEX") & (x.FinInstrmTp == "IDO")].copy()
    quirk = x.ClsPric == x.UndrlygPric                           # expiry-day rows: Close Price holds the index close
    x["ClsPric"] = x.ClsPric.where(~quirk, x.LastPric)
    x["Src"] = "udiff"
    return x[COLS]


MS_COLS = ["Market Summary Date", "Session ID", "Series ID", "Series Code", "Product Type", "Product Code", "Asset Code", "Expiry Date", "Strike Price",
           "Option Type (Call/Put)", "Previous Close Price", "Open Price", "High Price", "Low Price", "Close Price", "Total Traded Quantity",
           "Total Traded Value (in Thousands)(absolute)", "Average Traded Price", "No. of Trades", "Filler1", "Filler2", "Open Interest",
           "Underlying Asset Close Price"]


def from_ms(raw: bytes, d: date) -> pd.DataFrame:
    # some days come without the header line (the first line is already a contract): same 23 columns
    x = pd.read_csv(io.BytesIO(raw)) if raw[:19] == b"Market Summary Date" else pd.read_csv(io.BytesIO(raw), header=None, names=MS_COLS)
    x = x[(x["Asset Code"] == "BSX") & (x["Product Type"] == "IO")].copy()
    und = x["Underlying Asset Close Price"]
    quirk = x["Close Price"] == und
    out = pd.DataFrame({"TradDt": d.isoformat(), "TckrSymb": "SENSEX",
                        "XpryDt": pd.to_datetime(x["Expiry Date"], format="mixed", dayfirst=True).dt.strftime("%Y-%m-%d"),
                        "StrkPric": x["Strike Price"], "OptnTp": x["Option Type (Call/Put)"], "OpnPric": x["Open Price"],
                        "HghPric": x["High Price"], "LwPric": x["Low Price"], "ClsPric": x["Close Price"].where(~quirk), "LastPric": float("nan"),
                        "UndrlygPric": und, "TtlTradgVol": x["Total Traded Quantity"], "TtlNbOfTxsExctd": x["No. of Trades"],
                        "NewBrdLotQty": MS_LOT, "Src": "ms"})
    return out[COLS]


def download(start: date, end: date) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    none_f = OUT / "_no_file.txt"
    none = set(none_f.read_text().split()) if none_f.exists() else set()
    s, saved, skipped, new_none, failed = requests.Session(), 0, 0, [], []
    d = start
    t0 = time.time()
    while d <= end:
        iso = d.isoformat()
        if d.weekday() < 5 and not (OUT / f"{iso}.csv").exists() and iso not in none:
            try:
                df = None
                for fmt, url, fn in ((("udiff", UDIFF, from_udiff),) if d >= UDIFF_FROM else ()) + (("ms", MS, from_ms),):
                    raw = fetch(url.format(d), s)
                    if raw is not None:
                        df = fn(raw, d)
                        break
                    time.sleep(0.2)
                if df is None:
                    if d < date.today() - timedelta(days=3):
                        new_none.append(iso)
                elif len(df):
                    df.to_csv(OUT / f"{iso}.csv", index=False)
                    saved += 1
                else:
                    skipped += 1                                # a file without SENSEX options (e.g. before the BSX contracts traded)
                    new_none.append(iso)
            except RuntimeError as e:
                failed.append(iso)
                print("FAILED", iso, e, flush=True)
            time.sleep(0.25)
            if saved and saved % 100 == 0:
                print(f"{saved} days saved, {(time.time() - t0) / 60:.1f} min, at {iso}", flush=True)
        d += timedelta(days=1)
    if new_none:
        none_f.write_text("\n".join(sorted(none | set(new_none))) + "\n")
    files = sorted(OUT.glob("2*.csv"))
    print(f"bse bhavcopy: {saved} new days saved ({len(files)} files, {files[0].stem if files else '-'} -> {files[-1].stem if files else '-'}); "
          f"no file / holidays: {len(new_none)}; without SENSEX rows: {skipped}; failed: {failed or 'none'}")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-05-15")
    ap.add_argument("--end", default=str(date.today()))
    a = ap.parse_args()
    raise SystemExit(download(date.fromisoformat(a.start), date.fromisoformat(a.end)))

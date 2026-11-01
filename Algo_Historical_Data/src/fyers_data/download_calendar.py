"""Data for calendar-spread studies (near month vs next month futures of the same stock).

    python -m fyers_data.download_calendar bhav --start 2026-08-26 --end 2026-10-30
    python -m fyers_data.download_calendar bhavopt --start 2024-11-01 --end 2026-10-30
    python -m fyers_data.download_calendar bhavidx --start 2024-11-01 --end 2026-10-30
    python -m fyers_data.download_calendar contract NSE:WIPRO26NOVFUT NSE:ASTRAL26NOVFUT --start 2026-08-26

bhav: NSE F&O UDiFF bhavcopy for every weekday in the range, futures rows only (STF/IDF). Expired contracts are
included, but it is one row per contract per day (OHLC, close = last 30 min VWAP, last traded price, volume).
One file per trading day in fno/bhavcopy/ (resumable; holidays return 404 and are skipped).
bhavopt: same bhavcopy, STOCK OPTION rows (STO) of the two nearest expiries that traded that day, fewer columns,
into fno/bhavcopy_opt/ (for option-premium studies: daily OHLC of expired option contracts).
bhavidx: same bhavcopy, NIFTY / BANKNIFTY INDEX OPTION rows (IDO) of the three nearest expiries, into fno/bhavcopy_idx/.
contract: Fyers 1m candles of ONE contract (cont_flag=0, not the continuous series) into fno/1m/contracts/.
Fyers has no expired contracts, so only contracts still listed today can be fetched.
"""
import argparse
import io
import time
import zipfile
from datetime import date, timedelta

import pandas as pd
import requests

from fyers_data.download_fno import CHUNK_DAYS, fetch_chunk
from fyers_data.download_reference import NSE_UA
from fyers_data.paths import FNO_BHAV, FNO_BHAV_IDX, FNO_BHAV_OPT, FNO_CONTRACTS

BHAV_URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{:%Y%m%d}_F_0000.csv.zip"
FUT_TYPES = ("STF", "IDF")
IDX_OPT_SYMBOLS = ("NIFTY", "BANKNIFTY")
OPT_COLS = ["TradDt", "TckrSymb", "XpryDt", "StrkPric", "OptnTp", "OpnPric", "HghPric", "LwPric", "ClsPric", "LastPric",
            "UndrlygPric", "SttlmPric", "OpnIntrst", "TtlTradgVol", "TtlNbOfTxsExctd", "NewBrdLotQty"]


def _futures(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["FinInstrmTp"].isin(FUT_TYPES)]


def _stock_options(df: pd.DataFrame) -> pd.DataFrame:
    o = df[(df["FinInstrmTp"] == "STO") & (df["TtlTradgVol"] > 0)]
    near2 = o.groupby("TckrSymb")["XpryDt"].transform(lambda x: x.isin(sorted(x.unique())[:2]))
    return o[near2][OPT_COLS]


def _index_options(df: pd.DataFrame) -> pd.DataFrame:
    o = df[(df["FinInstrmTp"] == "IDO") & df["TckrSymb"].isin(IDX_OPT_SYMBOLS) & (df["TtlTradgVol"] > 0)]
    near3 = o.groupby("TckrSymb")["XpryDt"].transform(lambda x: x.isin(sorted(x.unique())[:3]))
    return o[near3][OPT_COLS]


def bhav(start: date, end: date, folder=FNO_BHAV, keep=_futures):
    folder.mkdir(parents=True, exist_ok=True)
    saved, holidays, failed = 0, [], []
    d = start
    while d <= end:
        out = folder / f"{d}.csv"
        if d.weekday() >= 5 or out.exists():
            d += timedelta(days=1)
            continue
        for attempt in range(4):
            try:
                r = requests.get(BHAV_URL.format(d), headers=NSE_UA, timeout=60)
            except requests.RequestException:
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code == 404:
                holidays.append(str(d))
                break
            if r.status_code == 200 and r.content[:2] == b"PK":
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    df = pd.read_csv(z.open(z.namelist()[0]))
                keep(df).to_csv(out, index=False)
                saved += 1
                break
            time.sleep(3 * (attempt + 1))
        else:
            failed.append(str(d))
        time.sleep(0.3)
        d += timedelta(days=1)
    print(f"bhav: {saved} days saved, holidays/no file: {holidays}, failed: {failed}")
    return failed


def contract(symbols: list[str], start: date, end: date):
    FNO_CONTRACTS.mkdir(parents=True, exist_ok=True)
    for sym in symbols:
        rows, b = [], end
        while b >= start:
            a = max(start, b - timedelta(days=CHUNK_DAYS - 1))
            rows.extend(fetch_chunk(sym, a, b, cont="0"))
            b = a - timedelta(days=1)
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df["datetime"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        df = df.drop(columns="ts").drop_duplicates("datetime").sort_values("datetime")
        name = sym.split(":")[1]
        df[["datetime", "open", "high", "low", "close", "volume"]].to_csv(FNO_CONTRACTS / f"{name}.csv", index=False)
        print(f"contract {name}: {len(df)} candles, {df.datetime.min()} -> {df.datetime.max()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["bhav", "bhavopt", "bhavidx", "contract"])
    ap.add_argument("symbols", nargs="*", help="contract: Fyers symbols, e.g. NSE:WIPRO26NOVFUT")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", default=str(date.today()))
    a = ap.parse_args()
    s, e = date.fromisoformat(a.start), date.fromisoformat(a.end)
    if a.what == "contract":
        contract(a.symbols, s, e)
    else:
        keep = {"bhav": (FNO_BHAV, _futures), "bhavopt": (FNO_BHAV_OPT, _stock_options), "bhavidx": (FNO_BHAV_IDX, _index_options)}[a.what]
        bhav(s, e, *keep)

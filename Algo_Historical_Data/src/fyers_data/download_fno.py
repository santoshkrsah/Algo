"""Download Fyers F&O 1m candles (futures + ATM+-N options) and resample to 5/15/30/60m.

Usage:
    python download_fno.py --dry-run          # show the selected contracts only
    python download_fno.py                    # download everything (resumable)
    python download_fno.py --only RELIANCE NIFTY
"""
import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from fyers_data.auth import auth_header, login
from fyers_data.paths import FNO as DATA, SELECTED_CONTRACTS

START = date(2026, 1, 1)
LAST_EXPIRY = date(2026, 11, 30)
STRIKES_EACH_SIDE = 5
CHUNK_DAYS = 90
MIN_INTERVAL = 0.4          # Fyers allows 200 req/min -> keep ~150/min
TIMEFRAMES = {"5": "5min", "15": "15min", "30": "30min", "60": "60min"}

MASTERS = {
    "NSE": "https://public.fyers.in/sym_details/NSE_FO.csv",
    "BSE": "https://public.fyers.in/sym_details/BSE_FO.csv",
}
INDEX_SPOT = {
    "NIFTY": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
}
# instrument types in the Fyers symbol master
FUT_IDX, FUT_STK, OPT_IDX, OPT_STK = 11, 13, 14, 15
COLS = {"type": 2, "expiry": 8, "symbol": 9, "under": 13, "strike": 15, "opt": 16}


def load_master(exch: str) -> pd.DataFrame:
    df = pd.read_csv(MASTERS[exch], header=None)
    out = pd.DataFrame({k: df[v] for k, v in COLS.items()})
    out["expiry"] = (pd.to_datetime(out["expiry"], unit="s", utc=True)
                     .dt.tz_convert("Asia/Kolkata").dt.date)
    out["exch"] = exch
    return out


def spot_symbol(under: str, exch: str, is_index: bool) -> str:
    if is_index:
        return INDEX_SPOT[under]
    return f"{exch}:{under}-EQ"


def get_spots(symbols: list[str]) -> dict[str, float]:
    spots = {}
    for i in range(0, len(symbols), 50):
        batch = symbols[i:i + 50]
        r = requests.get("https://api-t1.fyers.in/data/quotes", headers=auth_header(),
                         params={"symbols": ",".join(batch)}, timeout=30).json()
        if r.get("s") != "ok":
            raise RuntimeError(f"quotes failed: {r}")
        for q in r["d"]:
            if q.get("s") == "ok":
                spots[q["n"]] = q["v"]["lp"]
    return spots


def select_contracts(only: set[str] | None) -> pd.DataFrame:
    m = pd.concat([load_master("NSE"), load_master("BSE")], ignore_index=True)
    today = date.today()
    m = m[(m["expiry"] >= today) & (m["expiry"] <= LAST_EXPIRY)]
    m = m[m["under"].isin(INDEX_SPOT) | m["type"].isin([FUT_STK, OPT_STK])]
    # BSE master also lists stock derivatives; only SENSEX is wanted from BSE
    m = m[(m["exch"] == "NSE") | (m["under"] == "SENSEX")]
    if only:
        m = m[m["under"].isin(only)]

    # underlying -> spot
    unders = m[["under", "exch"]].drop_duplicates()
    sym_for = {u: spot_symbol(u, e, u in INDEX_SPOT) for u, e in unders.itertuples(index=False)}
    spots = get_spots(list(sym_for.values()))
    missing = [u for u, s in sym_for.items() if s not in spots]
    if missing:
        print(f"WARNING: no spot for {len(missing)} underlyings, skipped: {missing[:15]}")

    fut = m[m["type"].isin([FUT_IDX, FUT_STK])]
    # one continuous series per underlying, keyed on the last listed expiry (Nov)
    fut = fut.sort_values("expiry").groupby("under", as_index=False).last()
    fut["kind"] = "FUT"

    opts = m[m["type"].isin([OPT_IDX, OPT_STK])].copy()
    picked = []
    for (under, expiry), g in opts.groupby(["under", "expiry"]):
        spot = spots.get(sym_for[under])
        if spot is None:
            continue
        strikes = sorted(g["strike"].unique())
        atm = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
        keep = strikes[max(0, atm - STRIKES_EACH_SIDE): atm + STRIKES_EACH_SIDE + 1]
        sel = g[g["strike"].isin(keep)].copy()
        sel["spot"], sel["atm"] = spot, strikes[atm]
        picked.append(sel)
    opts = pd.concat(picked, ignore_index=True) if picked else opts.iloc[0:0]
    opts["kind"] = "OPT"

    fut = fut[fut["under"].map(sym_for).isin(spots)]
    return pd.concat([fut, opts], ignore_index=True)


class Limiter:
    def __init__(self, interval):
        self.interval, self.last = interval, 0.0

    def wait(self):
        dt = time.time() - self.last
        if dt < self.interval:
            time.sleep(self.interval - dt)
        self.last = time.time()


limiter = Limiter(MIN_INTERVAL)


def fetch_chunk(symbol: str, a: date, b: date, retries: int = 5):
    for attempt in range(retries):
        limiter.wait()
        try:
            r = requests.get("https://api-t1.fyers.in/data/history", headers=auth_header(),
                             params=dict(symbol=symbol, resolution="1", date_format="1",
                                         range_from=a.isoformat(), range_to=b.isoformat(),
                                         cont_flag="1"), timeout=60)
            if r.status_code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            j = r.json()
        except (requests.RequestException, ValueError):
            time.sleep(2 * (attempt + 1))
            continue
        if j.get("s") == "ok":
            return j.get("candles", [])
        if j.get("s") == "no_data":
            return []
        msg = str(j.get("message", "")).lower()
        if "no data" in msg or j.get("code") == -300 or "invalid symbol" in msg:
            return []
        if j.get("code") in (-8, -16, -15) or "token" in msg:
            login()
            continue
        if "limit" in msg or "many" in msg:
            time.sleep(15 * (attempt + 1))
            continue
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"giving up on {symbol} {a}..{b}")


def download_symbol(symbol: str, is_fut: bool) -> pd.DataFrame:
    """Walk 90-day chunks newest -> oldest. Options stop at the first empty chunk."""
    rows, end = [], date.today()
    while end >= START:
        begin = max(START, end - timedelta(days=CHUNK_DAYS - 1))
        c = fetch_chunk(symbol, begin, end)
        rows.extend(c)
        if not c and not is_fut:
            break
        end = begin - timedelta(days=1)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["datetime"] = (pd.to_datetime(df["ts"], unit="s", utc=True)
                      .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
    df = df.drop(columns="ts").drop_duplicates("datetime").sort_values("datetime")
    return df[["datetime", "open", "high", "low", "close", "volume"]]


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    # anchor candles at the 09:15 session open, matching exchange-style bars
    r = df.set_index("datetime").resample(rule, origin="start_day", offset="9h15min")
    out = r.agg({"open": "first", "high": "max", "low": "min",
                 "close": "last", "volume": "sum"}).dropna(subset=["open"])
    return out.reset_index()


def save(df: pd.DataFrame, kind: str, name: str):
    folder = "futures" if kind == "FUT" else "options"
    p = DATA / "1m" / folder
    p.mkdir(parents=True, exist_ok=True)
    df.to_csv(p / f"{name}.csv", index=False)
    for res, rule in TIMEFRAMES.items():
        q = DATA / f"{res}m" / folder
        q.mkdir(parents=True, exist_ok=True)
        resample(df, rule).to_csv(q / f"{name}.csv", index=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", nargs="*", help="limit to these underlyings")
    args = ap.parse_args()

    sel = select_contracts(set(args.only) if args.only else None)
    SELECTED_CONTRACTS.parent.mkdir(parents=True, exist_ok=True)
    sel.to_csv(SELECTED_CONTRACTS, index=False)
    print(f"selected {len(sel)} contracts: "
          f"{(sel.kind == 'FUT').sum()} futures, {(sel.kind == 'OPT').sum()} options, "
          f"{sel.under.nunique()} underlyings")
    if args.dry_run:
        print(sel.groupby(["kind", "exch"]).size())
        return

    todo = list(sel.itertuples(index=False))
    t0 = time.time()
    for i, c in enumerate(todo, 1):
        name = c.symbol.split(":")[1]
        folder = "futures" if c.kind == "FUT" else "options"
        if (DATA / "1m" / folder / f"{name}.csv").exists():
            continue
        try:
            df = download_symbol(c.symbol, c.kind == "FUT")
        except Exception as e:  # keep going; failed ones are retried on the next run
            print(f"[{i}/{len(todo)}] FAIL {c.symbol}: {e}", flush=True)
            continue
        if df.empty:
            # write a marker-free skip so empty contracts are not retried forever
            (DATA / "1m" / folder).mkdir(parents=True, exist_ok=True)
            pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"]) \
                .to_csv(DATA / "1m" / folder / f"{name}.csv", index=False)
            continue
        save(df, c.kind, name)
        if i % 25 == 0:
            el = time.time() - t0
            print(f"[{i}/{len(todo)}] {c.symbol} rows={len(df)} elapsed={el/60:.1f}m", flush=True)
    print("done")


if __name__ == "__main__":
    sys.exit(main())

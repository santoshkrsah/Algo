"""Breadth + Sector + lowest-Volume candle (BVS) backtest - data part.

Builds, for every trading day, the 5-minute bars of the F&O stocks of that day (point-in-time list from the F&O bhavcopy),
the previous close, 20-day average traded value, lot size and sector. Cached as one pickle (see load_days).

    from backtest import bvs_data
    days = bvs_data.load_days()        # list[DayData]
"""
import glob
import os
import pickle
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import fast
from backtest.config import FNO

NB = 75                      # 5-minute bars 9:15 ... 15:25
START = "2021-10-01"
FIRST_TRADE_DAY = "2021-11-01"   # panel features (prev close, 20-day value) need ~20 earlier days

# Static sector map (NOT dated: a stock keeps its sector for the whole period). Stocks not listed = "OTHER" (not ranked as a sector).
SECTORS = {
    "PVTBANK": "HDFCBANK ICICIBANK AXISBANK KOTAKBANK INDUSINDBK FEDERALBNK IDFCFIRSTB BANDHANBNK RBLBANK AUBANK CUB KARURVYSYA UJJIVANSFB YESBANK",
    "PSUBANK": "SBIN BANKBARODA PNB CANBK UNIONBANK INDIANB BANKINDIA MAHABANK",
    "IT": "TCS INFY WIPRO HCLTECH TECHM LTIM LTI LTM MINDTREE MPHASIS COFORGE PERSISTENT OFSS KPITTECH TATAELXSI LTTS BSOFT CYIENT INTELLECT TATATECH",
    "AUTO": "MARUTI TATAMOTORS TMPV M&M BAJAJ-AUTO EICHERMOT HEROMOTOCO TVSMOTOR ASHOKLEY BALKRISIND MRF APOLLOTYRE BOSCHLTD MOTHERSON MOTHERSUMI "
            "EXIDEIND AMARAJABAT ESCORTS SONACOMS UNOMINDA FORCEMOT",
    "PHARMA": "SUNPHARMA DRREDDY CIPLA DIVISLAB LUPIN AUROPHARMA TORNTPHARM ZYDUSLIFE CADILAHC ALKEM BIOCON GLENMARK GRANULES IPCALAB LAURUSLABS "
              "ABBOTINDIA PFIZER SYNGENE MANKIND LALPATHLAB METROPOLIS APOLLOHOSP FORTIS MAXHEALTH PPLPHARMA",
    "METAL": "TATASTEEL JSWSTEEL HINDALCO VEDL JINDALSTEL SAIL NMDC NATIONALUM HINDCOPPER HINDZINC JSL APLAPOLLO COALINDIA",
    "FMCG": "HINDUNILVR ITC NESTLEIND BRITANNIA DABUR MARICO GODREJCP COLPAL TATACONSUM UBL MCDOWELL-N UNITDSPR RADICO VBL PATANJALI JUBLFOOD",
    "MEDIA": "ZEEL SUNTV PVR PVRINOX NYKAA NAUKRI INDIAMART",
    "ENERGY": "RELIANCE ONGC BPCL IOC HINDPETRO GAIL OIL PETRONET IGL MGL GUJGASLTD ATGL NTPC POWERGRID TATAPOWER ADANIGREEN ADANIPOWER ADANIENSOL "
              "NHPC SJVN TORNTPOWER JSWENERGY CESC IREDA INOXWIND SUZLON PREMIERENE WAAREEENER",
    "FINSERV": "BAJFINANCE BAJAJFINSV HDFC HDFCLIFE SBILIFE ICICIGI ICICIPRULI LICI SBICARD CHOLAFIN MUTHOOTFIN MANAPPURAM SHRIRAMFIN M&MFIN L&TFH LTF "
               "LICHSGFIN CANFINHOME PNBHOUSING PEL POONAWALLA IIFL ABCAPITAL RECLTD PFC IRFC HUDCO MFSL HDFCAMC NAM-INDIA CDSL BSE MCX ANGELONE "
               "360ONE JIOFIN SRTRANSFIN IBULHSGFIN KFINTECH CAMS MOTILALOFS NUVAMA ANANDRATHI SAMMAANCAP POLICYBZR PAYTM",
    "REALTY": "DLF GODREJPROP OBEROIRLTY PRESTIGE LODHA PHOENIXLTD",
    "INFRA": "LT ADANIENT ADANIPORTS ULTRACEMCO GRASIM AMBUJACEM ACC SHREECEM DALBHARAT JKCEMENT RAMCOCEM INDIACEM SIEMENS ABB HAVELLS POLYCAB KEI "
             "CUMMINSIND BHEL BEL HAL BDL MAZDOCK COCHINSHIP RVNL IRB IRCTC CONCOR NBCC NCC GMRINFRA GMRAIRPORT INDIGO CGPOWER GVT&D POWERINDIA "
             "TIINDIA TITAGARH SUPREMEIND ASTRAL DIXON KAYNES CROMPTON VOLTAS BLUESTARCO HFCL PGEL",
}
SECTOR_NAMES = list(SECTORS)
SYM2SEC = {s: SECTOR_NAMES.index(k) for k, v in SECTORS.items() for s in v.split()}


@dataclass
class DayData:
    day: str
    syms: list
    o: np.ndarray          # (n, 75) float32 5-minute bars; missing bars: price = last close, volume 0
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    prev_close: np.ndarray
    avg_value_cr: np.ndarray
    lot: np.ndarray
    sec: np.ndarray        # sector index, -1 = OTHER
    n_missing: int = 0     # F&O names without 1-minute data that day


def _bhav_files() -> dict:
    """day -> bhavcopy path (old folder first, new folder overrides)."""
    out = {}
    for sub in ("bhavcopy_hist", "bhavcopy"):
        for f in sorted(glob.glob(str(FNO / sub / "*.csv"))):
            out[Path(f).stem] = f
    return out


def fno_by_day(days: list) -> dict:
    """day -> {symbol: lot}. Lot sizes are only in the bhavcopy from Aug 2024; before that the first known lot is used (flagged in the report)."""
    files = _bhav_files()
    res, last = {}, None
    raw = {}
    for d in days:
        f = files.get(d)
        if f is None:
            raw[d] = None
            continue
        x = pd.read_csv(f, usecols=["FinInstrmTp", "TckrSymb", "XpryDt", "NewBrdLotQty"])
        x = x[x.FinInstrmTp == "STF"].sort_values("XpryDt").drop_duplicates("TckrSymb")
        raw[d] = dict(zip(x.TckrSymb, x.NewBrdLotQty))
    first_lot = {}
    for d in days:
        for s, q in (raw[d] or {}).items():
            if pd.notna(q) and s not in first_lot:
                first_lot[s] = float(q)
    for d in days:
        cur = raw[d] if raw[d] is not None else last
        res[d] = {s: (float(q) if pd.notna(q) else first_lot.get(s, np.nan)) for s, q in cur.items()}
        last = cur
    return res


def _build_day(args) -> DayData | None:
    day, fno, pc, av = args           # fno {sym: lot}; pc, av {sym: value}
    m = fast.day_1m(day, symbols=list(fno))
    if m.empty:
        return None
    m = m[(m.minute >= 555) & (m.minute < 930)]
    syms = sorted(s for s in m.symbol.unique() if s in fno and pd.notna(fno[s]) and s in pc)
    if not syms:
        return None
    m = m[m.symbol.isin(syms)]
    sidx = {s: i for i, s in enumerate(syms)}
    n = len(syms)
    b = ((m.minute.to_numpy() - 555) // 5).astype(np.int64)
    si = m.symbol.map(sidx).to_numpy()
    key = si * NB + b
    order = np.argsort(key, kind="stable")
    key, o_, h_, l_, c_, v_ = key[order], *(m[k].to_numpy(dtype=np.float64)[order] for k in ("open", "high", "low", "close", "volume"))
    start = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    end = np.r_[start[1:], len(key)] - 1
    cells = key[start]
    O = np.full(n * NB, np.nan); H = O.copy(); L = O.copy(); C = O.copy(); V = np.zeros(n * NB)
    O[cells] = o_[start]; C[cells] = c_[end]
    H[cells] = np.maximum.reduceat(h_, start); L[cells] = np.minimum.reduceat(l_, start); V[cells] = np.add.reduceat(v_, start)
    O, H, L, C, V = (x.reshape(n, NB) for x in (O, H, L, C, V))
    pcl = np.array([pc[s] for s in syms])
    for j in range(NB):                      # carry the last close into empty bars (volume stays 0)
        miss = np.isnan(C[:, j])
        if miss.any():
            prev = C[:, j - 1] if j else pcl
            for X in (O, H, L, C):
                X[miss, j] = prev[miss]
    return DayData(day, syms, O.astype(np.float32), H.astype(np.float32), L.astype(np.float32), C.astype(np.float32), V.astype(np.float32),
                   pcl, np.array([av.get(s, np.nan) for s in syms]), np.array([fno[s] for s in syms]),
                   np.array([SYM2SEC.get(s, -1) for s in syms]), n_missing=len(set(fno) - set(syms)))


def build(workers: int = 6, cache: Path | None = None, log=print) -> list:
    days = [d for d in fast.days(START) ]
    fno = fno_by_day(days)
    p = fast.add_features(fast.panel(START, None))
    p = p[["date", "symbol", "prev_close", "avg_value_cr"]]
    by = {d: g for d, g in p.groupby("date")}
    jobs = []
    for d in days:
        if d < FIRST_TRADE_DAY or d not in by:
            continue
        g = by[d].dropna(subset=["prev_close"])
        jobs.append((d, fno[d], dict(zip(g.symbol, g.prev_close)), dict(zip(g.symbol, g.avg_value_cr))))
    out = []
    with ProcessPoolExecutor(workers) as ex:
        for i, r in enumerate(ex.map(_build_day, jobs, chunksize=4)):
            if r is not None:
                out.append(r)
            if i % 100 == 0:
                log(f"data {i}/{len(jobs)}")
    if cache:
        with open(cache, "wb") as f:
            pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)
    return out


def load_days(cache: Path, rebuild: bool = False, workers: int = 6, log=print) -> list:
    if cache.exists() and not rebuild:
        class _U(pickle.Unpickler):          # cache built with `python -m` stores the class under __main__
            def find_class(self, module, name):
                return DayData if name == "DayData" else super().find_class(module, name)
        with open(cache, "rb") as f:
            return _U(f).load()
    return build(workers, cache, log)


if __name__ == "__main__":
    import sys
    c = Path(sys.argv[1])
    ds = load_days(c, rebuild=True)
    print(len(ds), ds[0].day, ds[-1].day, np.mean([len(d.syms) for d in ds]), sum(d.n_missing for d in ds) / len(ds))

"""Abhishek Kadam F2F strategies on index options (prices MODELLED, calibrated on real daily bhavcopy closes).

S1: Sensex 9:50 short strangle, closest premium Rs 60 CE + PE, SL 50% of premium per leg, 1 re-entry, 0-3 trading days to expiry, exit 15:15.
S2: Nifty / Sensex STBT range breakout (stbt.py) with 1 lot.
"""
import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from backtest import stbt
from backtest.config import DATA

FNO = DATA / "fno"
SENSEX_1M = DATA / "index" / "SENSEX_1m.csv"
NIFTY_1M = DATA / "index" / "NIFTY50_1m.csv"
NIFTY_OLD_1M = DATA / "index" / "NIFTY50_FROM2020_1m.csv"   # only the days before NIFTY_1M starts
START, END = "2021-01-01", "2026-11-23"                    # last complete trading day
WARMUP = "2020-11-01"                                       # for the 20-day volatility of Jan 2021
SENSEX_FROM = "2023-05-15"          # first day of Sensex weekly options in the BSE bhavcopy
# Sensex lot: 10 until 2024-12-31, 20 after (BSE bhavcopy). Nifty: stma config table.
NIFTY_LOTS = [("2015-10-29", 75), ("2021-07-30", 50), ("2024-04-26", 25), ("2025-01-31", 75), ("2025-12-31", 65)]
SENSEX_LOTS = [("2020-01-01", 10), ("2025-01-01", 20)]
_ncdf = np.frompyfunc(lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2))), 1, 1)


def lot_on(name: str, day: str) -> int:
    tab = NIFTY_LOTS if name == "NIFTY" else SENSEX_LOTS
    return [q for d, q in tab if d <= day][-1]


# ---------------------------------------------------------------- data
def load_under(name: str) -> pd.DataFrame:
    path = NIFTY_1M if name == "NIFTY" else SENSEX_1M
    stbt.SOURCES[name], stbt.SPOT[name] = path, True
    d = stbt.load_under(name)
    if name == "NIFTY":
        stbt.SOURCES[name] = NIFTY_OLD_1M
        old = stbt.load_under(name)
        stbt.SOURCES[name] = path
        old = old[(old.day >= WARMUP) & (old.day < d.day.min())]
        d = pd.concat([old, d], ignore_index=True)
    return d[d.day <= END].sort_values("datetime").reset_index(drop=True)


BHAV_COLS = ("TradDt", "TckrSymb", "XpryDt", "StrkPric", "OptnTp", "ClsPric", "UndrlygPric", "TtlTradgVol")
_BHAV: dict = {}


def bhav(name: str) -> pd.DataFrame:
    """Real daily option closes (index options): columns day, exp, K, cp, close, und, vol."""
    if name not in _BHAV:
        parts = []
        for sub in (("bhavcopy_bse",) if name == "SENSEX" else ("bhavcopy_idx_hist", "bhavcopy_idx")):
            for f in sorted((FNO / sub).glob("20*.csv")):
                if f.stem > END:
                    continue
                x = pd.read_csv(f, usecols=lambda c: c in BHAV_COLS)
                x = x[(x.TckrSymb == name) & x.OptnTp.isin(["CE", "PE"])]
                if len(x):
                    parts.append(x)
        d = pd.concat(parts).rename(columns={"TradDt": "day", "XpryDt": "exp", "StrkPric": "K", "OptnTp": "cp", "ClsPric": "close",
                                              "UndrlygPric": "und", "TtlTradgVol": "vol"})
        _BHAV[name] = d[["day", "exp", "K", "cp", "close", "und", "vol"]].reset_index(drop=True)
    return _BHAV[name]


def sensex_expiries() -> list[date]:
    return sorted({date.fromisoformat(x) for x in bhav("SENSEX").exp.unique()})


def trading_dte(days: list[str], day: str, exp: str) -> int:
    """Trading days after `day` up to and including `exp` (0 = expiry day). Past the data: weekdays."""
    n = sum(1 for d in days if day < d <= exp)
    x = date.fromisoformat(max(days[-1], day)) + timedelta(days=1)
    while x.isoformat() <= exp:
        n += x.weekday() < 5
        x += timedelta(days=1)
    return n


# ---------------------------------------------------------------- vector pricing
def price_v(F, K, cp: str, T, vol):
    """Black-76 on arrays. F forward, K strike, T years, vol (arrays or scalars)."""
    F, K, T, vol = (np.asarray(x, float) for x in np.broadcast_arrays(F, K, T, vol))
    out = np.empty(F.shape)
    zero = T <= 0
    sv = vol * np.sqrt(np.where(zero, 1.0, T))
    d1 = (np.log(F / K) + 0.5 * sv * sv) / sv
    d2 = d1 - sv
    df = np.exp(-stbt.R * T)
    if cp == "CE":
        v = df * (F * np.asarray(_ncdf(d1), float) - K * np.asarray(_ncdf(d2), float))
        out = np.where(zero, np.maximum(F - K, 0), v)
    else:
        v = df * (K * np.asarray(_ncdf(-d2), float) - F * np.asarray(_ncdf(-d1), float))
        out = np.where(zero, np.maximum(K - F, 0), v)
    return out


def fwd(S, T):
    return np.asarray(S, float) * np.exp((stbt.R - stbt.Q) * np.asarray(T, float))


# ---------------------------------------------------------------- calibration on real daily closes
def calibration_rows(name: str, under: pd.DataFrame) -> pd.DataFrame:
    b = bhav(name)
    rv = stbt.realised_vol(under)
    b = b[(b.close >= 5) & (b.vol > 0)].copy()
    b["rv"] = b.day.map(rv)
    b["und"] = b.und.fillna(b.day.map(under.groupby("day").close.last()))      # old NSE files have no underlying price
    b = b.dropna(subset=["rv", "und"])
    e = pd.to_datetime(b.exp) + pd.Timedelta(hours=15, minutes=30)
    t = pd.to_datetime(b.day) + pd.Timedelta(hours=15, minutes=30)
    b["dte"] = (pd.to_datetime(b.exp) - pd.to_datetime(b.day)).dt.days
    b["Ty"] = (e - t).dt.total_seconds() / (365 * 86400)
    b = b[b.Ty > 0.5 / 365]
    b["mny"] = np.where(b.cp == "CE", b.K / b.und - 1, 1 - b.K / b.und)      # >0 = OTM
    return b


def fit_k(b: pd.DataFrame, grid=np.arange(0.5, 3.01, 0.05)) -> tuple[float, float, float]:
    """k = IV multiplier (IV = k x 20-day realised vol) with the smallest median |error|; returns (k, med_abs_err, med_err)."""
    best = None
    for k in grid:
        iv = np.clip(b.rv.values * k, stbt.IV_MIN, stbt.IV_MAX)
        m = np.where(b.cp.values == "CE", price_v(fwd(b.und.values, b["Ty"].values), b.K.values, "CE", b["Ty"].values, iv),
                     price_v(fwd(b.und.values, b["Ty"].values), b.K.values, "PE", b["Ty"].values, iv))
        err = (m - b.close.values) / b.close.values
        sc = float(np.median(np.abs(err)))
        if best is None or sc < best[1]:
            best = (float(k), sc, float(np.median(err)))
    return best


# ---------------------------------------------------------------- implied vol from real closes (S1: IV of the previous close)
def implied_vol(b: pd.DataFrame) -> np.ndarray:
    """Black-76 implied vol of each row (close, und, K, cp, Ty), bisection 5%..250%."""
    F = fwd(b.und.values, b.Ty.values)
    lo, hi = np.full(len(b), 0.05), np.full(len(b), 2.5)
    ce = (b.cp.values == "CE")
    for _ in range(30):
        mid = (lo + hi) / 2
        p = np.where(ce, price_v(F, b.K.values, "CE", b.Ty.values, mid), price_v(F, b.K.values, "PE", b.Ty.values, mid))
        up = p < b.close.values
        lo, hi = np.where(up, mid, lo), np.where(up, hi, mid)
    return (lo + hi) / 2


def wing_iv(name: str, under: pd.DataFrame) -> pd.DataFrame:
    """Per (day, exp, cp): median implied vol (at the 15:30 close) of OTM options priced Rs 20-200 -> the wing where the Rs 60 strike sits."""
    b = calibration_rows(name, under)
    b = b[(b.mny > 0) & b.close.between(20, 200)].copy()
    b["iv"] = implied_vol(b)
    g = b.groupby(["day", "exp", "cp"]).iv.agg(["median", "size"]).reset_index()
    return g[g["size"] >= 2].rename(columns={"median": "iv"})


# ---------------------------------------------------------------- Sensex expiries for stbt (BSE bhavcopy: Fri -> Tue Jan 2025 -> Thu Sep 2025)
_orig_expiries = stbt.expiries


def _expiries(name, days):
    return sensex_expiries() if name == "SENSEX" else _orig_expiries(name, days)


stbt.expiries = _expiries


# ---------------------------------------------------------------- costs (BSE index options: exchange 0.0325%; NSE 0.03503%)
def costs(sells: float, buys: float, orders: int, exch: float = 0.000325) -> float:
    brk = 20.0 * orders
    ex, sebi = exch * (sells + buys), 1e-6 * (sells + buys)
    return brk + ex + sebi + 0.18 * (brk + ex + sebi) + 0.001 * sells + 0.00003 * buys


# ---------------------------------------------------------------- S1: 9:50 short strangle, closest premium Rs 60
@dataclass(frozen=True)
class S1:
    prem: float = 60.0
    hedge: bool = False
    hedge_prem: float = 10.0
    sl: float = 0.5                 # SL = sold premium x (1 + sl)
    reentry: bool = True
    slip: float = 0.0
    entry_m: int = 590              # 9:50
    exit_m: int = 915               # 15:15
    reentry_cut: int = 870          # no re-entry from 14:30
    max_dte: int = 3                # trading days to expiry
    dte_calendar: bool = False      # True: count calendar days instead
    weekdays: tuple = (0, 1, 2, 3, 4)
    iv_mult: float = 1.0            # x previous-close wing IV
    capital: float = 400_000.0


def _day_arrays(under: pd.DataFrame) -> dict:
    out = {}
    for day, g in under.groupby("day"):
        out[day] = (g.m.values.astype(int), g.open.values, g.high.values, g.low.values, g.close.values)
    return out


def wing_lookup(w: pd.DataFrame) -> dict:
    d = {}
    for (exp, cp), g in w.sort_values("day").groupby(["exp", "cp"]):
        d[(exp, cp)] = (g.day.values, g.iv.values)
    return d


def _prev_iv(lk: dict, exp: str, cp: str, day: str):
    if (exp, cp) not in lk:
        return None
    ds, iv = lk[(exp, cp)]
    i = np.searchsorted(ds, day) - 1
    return float(iv[i]) if i >= 0 else None


def simulate_s1(under: pd.DataFrame, lk: dict, exps: list[date], p: S1, start: str = SENSEX_FROM, end: str | None = None) -> pd.DataFrame:
    A = _day_arrays(under)
    all_days = sorted(A)
    rows, skipped = [], {"no_iv": 0, "dte": 0, "bars": 0}
    step = 100
    for day in sorted(A):
        wd = date.fromisoformat(day)
        if day < start or (end and day > end) or wd.weekday() not in p.weekdays:
            continue
        m, o, h, l, c = A[day]
        if 590 not in m or p.exit_m not in m or p.entry_m not in m:
            skipped["bars"] += 1
            continue
        exp = next((e for e in exps if e >= wd), None)
        dte = 99 if not exp else ((exp - wd).days if p.dte_calendar else trading_dte(all_days, day, exp.isoformat()))
        if dte > p.max_dte:
            skipped["dte"] += 1
            continue
        i0, ix = int(np.where(m == p.entry_m)[0][0]), int(np.where(m == p.exit_m)[0][0])
        Tm = ((exp - wd).days * 1440 + (930 - m)) / (365 * 1440.0)
        for cp in ("CE", "PE"):
            iv = _prev_iv(lk, exp.isoformat(), cp, day)
            if iv is None:
                skipped["no_iv"] += 1
                continue
            iv = float(np.clip(iv * p.iv_mult, 0.05, 1.5))
            sgn = 1 if cp == "CE" else -1

            def pr(K, x, j):
                return float(price_v(fwd(x, Tm[j]), K, cp, Tm[j], iv))

            S0 = o[i0]
            Ks = (np.floor(S0 / step) * step + sgn * step * np.arange(1, 90)) if cp == "CE" else (np.ceil(S0 / step) * step + sgn * step * np.arange(1, 90))
            prs = price_v(fwd(S0, Tm[i0]), Ks, cp, Tm[i0], iv)
            kk = int(np.argmin(np.abs(prs - p.prem)))
            K = float(Ks[kk])
            if not 25 <= prs[kk] <= 110:
                skipped["no_iv"] += 1
                continue
            Kh = None
            if p.hedge:
                hh = kk + 1 + int(np.argmin(np.abs(prs[kk + 1:] - p.hedge_prem)))
                Kh = float(Ks[hh])
            prh = (lambda x, j: float(price_v(fwd(x, Tm[j]), Kh, cp, Tm[j], iv))) if Kh else (lambda x, j: 0.0)
            worst = h if cp == "CE" else l
            i_start, spot_in, reent = i0, float(S0), False
            while True:
                e = pr(K, spot_in, i_start)                       # quote at entry
                eh = prh(spot_in, i_start)
                slp = e * (1 + p.sl)
                js = np.arange(i_start + (1 if reent else 0), ix)  # first entry is at the open, so its own candle counts
                pw = price_v(fwd(worst[js], Tm[js]), K, cp, Tm[js], iv) if len(js) else np.array([])
                hit = np.where(pw >= slp)[0]
                if len(hit):
                    k = int(js[hit[0]])
                    po = pr(K, o[k], k)
                    gap = po >= slp
                    xq = po if gap else slp
                    if gap:
                        sx = float(o[k])
                    else:                                              # index level where the premium reaches the SL
                        lo_x, hi_x = float(o[k]), float(worst[k])
                        for _ in range(40):
                            mid = (lo_x + hi_x) / 2
                            lo_x, hi_x = (lo_x, mid) if pr(K, mid, k) >= slp else (mid, hi_x)
                        sx = hi_x
                    xh = prh(sx, k)
                    why, xm = "SL", int(m[k])
                else:
                    k = ix
                    sx = float(o[ix])
                    xq, xh, why, xm = pr(K, sx, ix), prh(sx, ix), "15:15 exit", int(m[ix])
                lot = lot_on("SENSEX", day)
                s_ = p.slip
                sell, buy = e * (1 - s_), xq * (1 + s_)
                hb, hs = eh * (1 + s_), xh * (1 - s_)
                gross = lot * ((sell - buy) + ((hs - hb) if Kh else 0.0))
                cst = costs(lot * (sell + (hs if Kh else 0)), lot * (buy + (hb if Kh else 0)), 4 if Kh else 2)
                im = int(m[i_start])
                rows.append({"date": day, "symbol": "SENSEX", "side": f"SELL {cp}", "strike": K, "hedge": Kh or "-", "expiry": exp.isoformat(),
                             "dte": dte, "entry_time": f"{(im + (0 if not reent else 1)) // 60:02d}:{(im + (0 if not reent else 1)) % 60:02d}",
                             "und_entry": round(spot_in, 2), "und_sl": round(sx, 2) if why == "SL" else None, "exit_date": day,
                             "exit_time": f"{xm // 60:02d}:{xm % 60:02d}", "und_exit": round(sx, 2), "iv_pct": round(iv * 100, 1),
                             "opt_entry": round(e, 2), "opt_exit": round(xq, 2), "hedge_entry": round(eh, 2), "hedge_exit": round(xh, 2),
                             "qty": lot, "exit_reason": why, "reentry": "haan" if reent else "nahi", "gross_pnl": round(gross, 2),
                             "costs": round(cst, 2), "net_pnl": round(gross - cst, 2)})
                if not (why == "SL" and p.reentry and not reent and m[k] < p.reentry_cut):
                    break
                e0 = rows[-1]["opt_entry"]
                nxt = None
                for j in range(k + 1, ix - 1):
                    if m[j] >= p.reentry_cut:
                        break
                    if pr(K, c[j], j) <= e0:
                        nxt = j
                        break
                if nxt is None:
                    break
                i_start, spot_in, reent = nxt, float(c[nxt]), True
    out = pd.DataFrame(rows)
    out.attrs["skipped"] = skipped
    return out


def fit_wing_mult(name: str, under: pd.DataFrame, w: pd.DataFrame, max_dte: int = 3) -> tuple[float, dict]:
    """Scale m on the previous close's wing IV so the model is unbiased on the NEXT close (OTM options Rs 25-150, DTE <= max_dte)."""
    b = calibration_rows(name, under)
    w = w.sort_values(["exp", "cp", "day"]).copy()
    w["prev_iv"] = w.groupby(["exp", "cp"]).iv.shift(1)
    o = b[(b.mny > 0) & b.close.between(25, 150) & (b.dte <= max_dte)].merge(w[["day", "exp", "cp", "prev_iv"]], on=["day", "exp", "cp"], how="left").dropna(subset=["prev_iv"])
    F = fwd(o.und.values, o.Ty.values)
    best = None
    for mm in np.arange(0.8, 1.6, 0.025):
        iv = np.clip(o.prev_iv.values * mm, 0.05, 1.5)
        pm = np.where(o.cp.values == "CE", price_v(F, o.K.values, "CE", o.Ty.values, iv), price_v(F, o.K.values, "PE", o.Ty.values, iv))
        err = (pm - o.close.values) / o.close.values
        sc = abs(float(np.median(err)))
        if best is None or sc < best[0]:
            best = (sc, float(mm), float(np.median(np.abs(err))), float(np.median(err)))
    return best[1], {"n": int(len(o)), "m": round(best[1], 3), "med_abs_err": round(best[2] * 100, 1), "med_err": round(best[3] * 100, 1)}

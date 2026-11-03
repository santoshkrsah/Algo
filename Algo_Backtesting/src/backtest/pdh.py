"""NIFTY intraday Previous Day High / Low (PDH / PDL) trend strategy on 3-minute bars.

Price = NIFTY 50 spot 1-min candles (no expired futures minute data), resampled to 3-min; costs = NIFTY futures charges.
Trend state from daily closes (or 3-min closes): close > PDH -> UP, close < PDL -> DOWN, else keep. UP = only longs, DOWN = only shorts.
Entry A = a 3-min close beyond the level (breakout), B = touch the level and close back (rejection). Signal is known at the
3-min close, the order fills at the next bar's open + slippage. Inside a bar the stop comes before any target; a gap through
the stop fills at the open. Every setting is one dict (`variant`); `grid` runs all of them, `run` one of them.

    from backtest import pdh
    m = pdh.load_market()
    trades, counts = pdh.run(m, pdh.variant(entry="A", window="W2", stop="S3-0.1", exit="E1"))
"""
import itertools
import pickle
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.config import DATA, FNO

UP, DOWN = 1, -1
NB = 125                        # 3-min bars per day: 9:15, 9:18 ... 15:27
SQ = 120                        # bar 120 opens at 15:15 = square off price

CFG = {
    "index_1m": DATA / "index" / "NIFTY50_1m.csv",
    "daily": DATA / "daily" / "index" / "NIFTY50.csv",   # official daily candles (close for the trend, history for warm-up)
    "start": "2021-10-01", "end": "2026-10-30", "warmup_from": "2021-01-01",
    "min_bars": 370,            # days with fewer 1-min bars (muhurat, Saturday DR sessions, a partial download) are left out
    "is_frac": 0.60,            # first ~60% of days = in-sample (fit), rest = out-of-sample
    "capital": 500_000, "compounding": False, "max_lots": 10, "margin_pct": 0.12,   # lots also capped by capital / (12% x lot x price)
    "brokerage": 20.0, "sebi": 0.000001, "stamp_buy": 0.00002, "gst": 0.18,
    "exch": [("2024-10-01", 0.00002), ("9999-12-31", 0.0000173)],                   # NSE futures, rate valid BEFORE the date
    "stt": [("2023-04-01", 0.0001), ("2024-10-01", 0.000125), ("2026-04-01", 0.0002), ("9999-12-31", 0.0005)],  # futures, sell side
    # near-month NIFTY futures lot (valid up to and including the date): NSE circulars + bhavcopy NewBrdLotQty (Nov 2024 on)
    "lots": [("2024-04-25", 50), ("2025-01-30", 25), ("2025-12-30", 75), ("9999-12-31", 65)],
    "swing_n": 2,               # swing low / high = lower / higher than 2 bars on each side
    "scratch_r": 0.1,           # |net R| <= 0.1 = scratch
    "min_is_trades": 100,       # a setting needs this many in-sample trades to be picked
    "random_seeds": 20,
}
# baseline asked for first: Variant A, W2, S3, E1; the rest are the defaults of the brief
BASE = {"level": "pdh", "entry": "A", "window": "W2", "cutoff": "14:30", "chase": 0.0025, "stop": "S3-0.1", "exit": "E1",
        "max_trades": 2, "trend": "daily", "risk_pct": 0.01, "slip": 1.0, "max_stop_pct": 0.005, "seed": 0}
GRID = {"entry": ["A", "B"], "window": ["W1", "W2"], "cutoff": ["14:30", "15:00"], "chase": [0.0015, 0.0025, 0.004],
        "stop": ["S1", "S2-0.1", "S2-0.2", "S3-0.1", "S3-0.2"], "exit": ["E1", "E2", "E3", "E4", "E5", "F1", "F2"],
        "max_trades": [2, 1], "trend": ["daily", "intraday"]}
KEY = ["level", "entry", "window", "cutoff", "chase", "stop", "exit", "max_trades", "trend", "risk_pct", "slip", "max_stop_pct", "seed"]
WINDOW = {"W1": "09:30", "W2": "11:00"}
EXITS = {"E1": "Half 1R + BE, baaki trailing (swing)", "E2": "Half 1R + BE, baaki 2R", "E3": "Half 1R + BE, baaki 3R",
         "E4": "Half 1R + BE, baaki doosra level", "E5": "Half 1R + BE, baaki 3:15 tak", "F1": "Pura 1R (no partial)", "F2": "Pura 2R (no partial)"}
SPECIAL = {"2021-11-04": "Muhurat", "2022-10-24": "Muhurat", "2023-11-12": "Muhurat", "2024-11-01": "Muhurat", "2025-10-21": "Muhurat",
           "2024-03-02": "Saturday DR session", "2024-05-18": "Saturday DR session"}


def variant(**kw) -> dict:
    v = dict(BASE, **kw)
    unknown = set(v) - set(KEY)
    assert not unknown, unknown
    return v


def vkey(v: dict) -> str:
    return "|".join(str(v[k]) for k in KEY)


def bar_of(hhmm: str) -> int:
    h, mi = map(int, hhmm.split(":"))
    return (h * 60 + mi - 555) // 3


def hm(i: int) -> str:
    t = 555 + 3 * i
    return f"{t // 60:02d}:{t % 60:02d}"


def _rate(table, day: str) -> float:
    return next(r for d, r in table if day < d)


def lot_on(day: str, p=CFG) -> int:
    return next(n for d, n in p["lots"] if day <= d)


def order_cost(value: float, buy: bool, day: str, p=CFG) -> dict:
    """Charges of one futures order of Rs <value>."""
    exch = _rate(p["exch"], day) * value
    sebi = p["sebi"] * value
    stt = 0.0 if buy else _rate(p["stt"], day) * value
    stamp = p["stamp_buy"] * value if buy else 0.0
    gst = p["gst"] * (p["brokerage"] + exch + sebi)
    return {"brokerage": p["brokerage"], "stt": stt, "exch": exch + sebi, "stamp": stamp, "gst": gst,
            "total": p["brokerage"] + exch + sebi + stt + stamp + gst}


# ---------------------------------------------------------------- data
@dataclass
class Market:
    days: list
    m1: np.ndarray              # (days, 375, 4) 1-min OHLC 9:15 ... 15:29
    O: list                     # per day: 125 3-min opens (Python floats, fast in loops)
    H: list
    L: list
    C: list
    pdh: np.ndarray
    pdl: np.ndarray
    pclose: np.ndarray
    dopen: np.ndarray
    state: np.ndarray           # daily trend state known before the day opens (1 UP, -1 DOWN, 0 none yet)
    intra: list                 # per day: state after each 3-min close (flip on close vs PDH / PDL, carried across days)
    lot: np.ndarray
    expiry: np.ndarray          # NIFTY weekly expiry day
    gap: np.ndarray             # +1 open above PDH, -1 open below PDL, 0 inside
    cut: str                    # first out-of-sample day
    dropped: dict


def _expiries(days: list) -> set:
    """NIFTY weekly expiry days: NSE bhavcopy (Nov 2024 on); before that Thursday, or the trading day before it on a holiday."""
    files = sorted((FNO / "bhavcopy_idx").glob("*.csv"))
    exps = set()
    for f in files:
        c = pd.read_csv(f, usecols=["TckrSymb", "XpryDt"])
        exps |= set(c.loc[c.TckrSymb == "NIFTY", "XpryDt"].unique())
    first = files[0].stem if files else "9999-12-31"
    dset = set(days)
    out = {d for d in days if d >= first and d in exps}
    for thu in pd.date_range(days[0], first, freq="W-THU"):
        d = thu
        while d.strftime("%Y-%m-%d") not in dset and d > thu - pd.Timedelta(days=6):
            d -= pd.Timedelta(days=1)
        s = d.strftime("%Y-%m-%d")
        if s in dset and s < first:
            out.add(s)
    return out


def load_market(p=CFG, cache: Path | None = None) -> Market:
    if cache and Path(cache).exists():
        return pickle.loads(Path(cache).read_bytes())
    m = pd.read_csv(p["index_1m"])
    m = m[(m.datetime >= p["start"]) & (m.datetime < p["end"] + " 99")]
    dt = pd.to_datetime(m.datetime)
    m = m.assign(date=m.datetime.str[:10], k=(dt.dt.hour * 60 + dt.dt.minute - 555).values)
    m = m[(m.k >= 0) & (m.k < 375)]
    cnt = m.groupby("date").size()
    days = sorted(cnt[cnt >= p["min_bars"]].index)
    dropped = {d: SPECIAL.get(d, "adhoora din") for d in cnt[cnt < p["min_bars"]].index}
    di = {d: i for i, d in enumerate(days)}
    mm = m[m.date.isin(di)]
    a = np.full((len(days), 375, 4), np.nan)
    a[mm.date.map(di).values, mm.k.values] = mm[["open", "high", "low", "close"]].values
    for i in range(len(days)):                  # a missing minute = flat bar at the last close
        c = pd.Series(a[i, :, 3]).ffill().bfill().values
        for j in range(3):
            a[i, :, j] = np.where(np.isnan(a[i, :, j]), c, a[i, :, j])
        a[i, :, 3] = c
    b = a.reshape(len(days), NB, 3, 4)
    O, H, L, C = b[:, :, 0, 0], b[:, :, :, 1].max(2), b[:, :, :, 2].min(2), b[:, :, 2, 3]

    # daily rows: official candles for the warm-up, then the regular-session high / low from 1-min + official close
    dly = pd.read_csv(p["daily"])
    off_close = dict(zip(dly.date, dly.close))
    warm = dly[(dly.date >= p["warmup_from"]) & (dly.date < p["start"])]
    hi = np.r_[warm.high.values, a[:, :, 1].max(1)]
    lo = np.r_[warm.low.values, a[:, :, 2].min(1)]
    cl = np.r_[warm.close.values, [off_close.get(d, a[i, -1, 3]) for i, d in enumerate(days)]]
    st = np.zeros(len(hi), int)
    for t in range(1, len(hi)):
        st[t] = UP if cl[t] > hi[t - 1] else DOWN if cl[t] < lo[t - 1] else st[t - 1]
    w = len(warm)
    pdh, pdl, pclose, state = hi[w - 1:-1], lo[w - 1:-1], cl[w - 1:-1], st[w - 1:-1]
    dopen = a[:, 0, 0]

    intra, s = [], state[0]
    for t in range(len(days)):
        row = []
        for c in C[t]:
            s = UP if c > pdh[t] else DOWN if c < pdl[t] else s
            row.append(int(s))
        intra.append(row)
    ex = _expiries(days)
    n = len(days)
    cut_i = int(round(p["is_frac"] * n))
    cut = next(d for d in days if d >= days[cut_i][:7] + "-01")      # start of that month
    mk = Market(days=days, m1=a, O=O.tolist(), H=H.tolist(), L=L.tolist(), C=C.tolist(), pdh=pdh, pdl=pdl, pclose=pclose, dopen=dopen,
                state=state, intra=intra, lot=np.array([lot_on(d, p) for d in days]), expiry=np.array([d in ex for d in days]),
                gap=np.where(dopen > pdh, 1, np.where(dopen < pdl, -1, 0)), cut=cut, dropped=dropped)
    if cache:
        Path(cache).write_bytes(pickle.dumps(mk))
    return mk


# ---------------------------------------------------------------- signals
def levels(m: Market, level: str, seed: int = 0):
    """(upper level, lower level, names) per day for the level families compared in the report."""
    if level == "pdh":
        return m.pdh, m.pdl, ("PDH", "PDL")
    if level == "open":
        return m.dopen, m.dopen, ("Open", "Open")
    if level == "pclose":
        return m.pclose, m.pclose, ("Prev close", "Prev close")
    if level == "random":
        # same distance from the open as PDH / PDL on a random other day
        s = np.random.default_rng(seed).integers(0, len(m.days), len(m.days))
        return m.dopen * m.pdh[s] / m.dopen[s], m.dopen * m.pdl[s] / m.dopen[s], ("Random upar", "Random neeche")
    raise ValueError(level)


def signals(m: Market, level: str = "pdh", seed: int = 0) -> list:
    """Per day {(entry, side): [(signal bar, level name, level price)]}.
    A long : close > upper level after being at / below it (previous close <= level, or this bar touched it).
    A short: close < lower level after being at / above it.
    B long : bar dips to a level (low <= level), closes back above it, previous close above it. B short mirrored.
    The first bar's 'previous close' is the day open, so a gap beyond the level needs a retest (touch) first."""
    hi, lo, nm = levels(m, level, seed)
    two = nm[0] != nm[1]
    out = []
    for t in range(len(m.days)):
        O, H, L, C = (np.array(x) for x in (m.O[t], m.H[t], m.L[t], m.C[t]))
        pc = np.r_[m.dopen[t], C[:-1]]
        u, d = hi[t], lo[t]
        al = [(int(i), nm[0], u) for i in np.flatnonzero((C > u) & ((pc <= u) | (L <= u)))]
        as_ = [(int(i), nm[1], d) for i in np.flatnonzero((C < d) & ((pc >= d) | (H >= d)))]
        bl = [(int(i), nm[1], d) for i in np.flatnonzero((L <= d) & (C > d) & (pc > d))]
        bs = [(int(i), nm[0], u) for i in np.flatnonzero((H >= u) & (C < u) & (pc < u))]
        if two:
            bl += [(int(i), nm[0], u) for i in np.flatnonzero((L <= u) & (C > u) & (pc > u))]
            bs += [(int(i), nm[1], d) for i in np.flatnonzero((H >= d) & (C < d) & (pc < d))]
        out.append({("A", 1): al, ("A", -1): as_, ("B", 1): sorted(bl), ("B", -1): sorted(bs)})
    return out


# ---------------------------------------------------------------- one trade
def _simulate(m: Market, t: int, k: int, side: int, entry: float, stop: float, risk: float, lots: int, target, v: dict, p=CFG) -> dict:
    """Bars k .. 15:12 of day t. Order inside a bar: stop at the open (gap) -> stop -> 1R partial -> target. The breakeven /
    trailing stop is moved at the bar's close (active from the next bar)."""
    O, H, L = m.O[t], m.H[t], m.L[t]
    partial = v["exit"].startswith("E")
    n = p["swing_n"]
    t1 = entry + side * risk
    cur, kind = stop, "SL"
    part = None                                  # (bar, price) of the 1R booking
    be_next = False
    worst = entry
    px = reason = None
    for j in range(k, SQ):
        o, h, l = O[j], H[j], L[j]
        adv, fav = (l, h) if side > 0 else (h, l)
        if side * (o - cur) <= 0:
            px, reason, worst = o, kind, (min(worst, o) if side > 0 else max(worst, o))
            break
        if side * (adv - cur) <= 0:
            px, reason, worst = cur, kind, (min(worst, cur) if side > 0 else max(worst, cur))
            break
        worst = min(worst, adv) if side > 0 else max(worst, adv)
        if target is not None and side * (target - t1) < 0 and side * (fav - target) >= 0:
            px, reason = (o if side * (o - target) >= 0 else target), "Target"      # target nearer than 1R: all out there
            break
        if partial and part is None and side * (fav - t1) >= 0:
            part = (j, o if side * (o - t1) >= 0 else t1)
            be_next = True
        if target is not None and side * (fav - target) >= 0:
            px, reason = (o if side * (o - target) >= 0 else target), "Target"
            break
        if be_next:
            cur, kind, be_next = entry, "Breakeven SL", False
        if v["exit"] == "E1" and part is not None and j - n >= k:
            s = j - n                            # swing confirmed n bars later
            if side > 0 and all(L[s] < L[x] for x in range(s - n, s + n + 1) if x != s) and L[s] > cur:
                cur, kind = L[s], "Trailing SL"
            if side < 0 and all(H[s] > H[x] for x in range(s - n, s + n + 1) if x != s) and H[s] < cur:
                cur, kind = H[s], "Trailing SL"
    else:
        j, px, reason = SQ, O[SQ], "3:15 square off"
    day, lot, slip = m.days[t], int(m.lot[t]), v["slip"]
    p_lots = lots // 2 if part is not None else 0
    r_lots = lots - p_lots
    x_fill = px - side * slip
    p_fill = part[1] - side * slip if part is not None else None
    gross = side * lot * ((p_lots * (p_fill - entry) if p_lots else 0.0) + r_lots * (x_fill - entry))
    costs = [order_cost(entry * lots * lot, side > 0, day, p), order_cost(x_fill * r_lots * lot, side < 0, day, p)]
    if p_lots:
        costs.append(order_cost(p_fill * p_lots * lot, side < 0, day, p))
    cb = {c: sum(x[c] for x in costs) for c in ("brokerage", "stt", "exch", "stamp", "gst", "total")}
    net = gross - cb["total"]
    rr = risk * lots * lot
    o = O[k]                                     # raw R: same trade without slippage and charges (edge before friction), same R unit
    r0 = side * ((p_lots * (part[1] - o) if p_lots else 0.0) + r_lots * (px - o)) / (lots * risk)
    if reason == "Target":
        reason = {"E2": "Target 2R", "E3": "Target 3R", "E4": "Target doosra level", "F1": "Target 1R", "F2": "Target 2R"}[v["exit"]]
    return {"exit_bar": j, "exit_raw": px, "exit": x_fill, "reason": reason, "part_bar": part[0] if part else None,
            "part_raw": part[1] if part else None, "part": p_fill, "part_lots": p_lots, "rest_lots": r_lots,
            "gross": gross, "slip_rs": slip * lot * (lots + p_lots + r_lots), **{"c_" + c: cb[c] for c in ("brokerage", "stt", "exch", "stamp", "gst")},
            "costs": cb["total"], "net": net, "r": net / rr, "r_gross": gross / rr, "r0": r0, "risk_rs": rr,
            "mae_pts": max(0.0, side * (entry - worst)), "mae_r": max(0.0, side * (entry - worst)) / risk, "orders": len(costs)}


# ---------------------------------------------------------------- one setting
def run(m: Market, v: dict, sig=None, p=CFG, detail: bool = True):
    """All trades of setting <v> over every day + counts of skipped signals. detail=False keeps only what the grid needs."""
    sig = sig if sig is not None else signals(m, v["level"], v["seed"])
    up, dn, _ = levels(m, v["level"], v["seed"])
    ks, kc = bar_of(WINDOW[v["window"]]), bar_of(v["cutoff"])
    st, _, b = v["stop"].partition("-")
    buf = float(b) / 100 if b else 0.0
    risk_rs = p["capital"] * v["risk_pct"]
    cnt, trades = Counter(), []
    for t in range(len(m.days)):
        if v["trend"] == "daily":
            if m.state[t] == 0:
                continue
            cands = [c + (m.state[t],) for c in sig[t][(v["entry"], m.state[t])]]
        else:
            it = m.intra[t]
            cands = sorted([c + (1,) for c in sig[t][(v["entry"], 1)] if it[c[0]] == UP]
                           + [c + (-1,) for c in sig[t][(v["entry"], -1)] if it[c[0]] == DOWN])
        free, done, blocked = 0, 0, set()
        lot = int(m.lot[t])
        for i, name, lv, side in cands:
            k = i + 1
            if k < ks or i < free:
                continue
            if k > kc or done >= v["max_trades"]:
                break
            if (name, side) in blocked:
                cnt["same_level_after_loss"] += 1
                continue
            cnt["signals"] += 1
            o = m.O[t][k]
            if side * (o - lv) / lv > v["chase"]:
                cnt["chase"] += 1
                continue
            entry = o + side * v["slip"]
            s1 = m.L[t][i] if side > 0 else m.H[t][i]
            s2 = lv * (1 - side * buf)
            stop = s1 if st == "S1" else s2 if st == "S2" else (max(s1, s2) if side > 0 else min(s1, s2))
            risk = side * (entry - stop)
            if risk <= 0:
                cnt["stop_beyond_fill"] += 1
                continue
            if risk > v["max_stop_pct"] * o:
                cnt["stop_too_wide"] += 1
                continue
            lots = min(int(risk_rs // (risk * lot)), p["max_lots"], int(p["capital"] // (p["margin_pct"] * lot * o)))
            if lots < 1:
                cnt["size_below_1_lot"] += 1
                continue
            target = None
            if v["exit"] in ("E2", "F2"):
                target = entry + side * 2 * risk
            elif v["exit"] == "E3":
                target = entry + side * 3 * risk
            elif v["exit"] == "F1":
                target = entry + side * risk
            elif v["exit"] == "E4":
                other = [x for x in (up[t], dn[t]) if side * (x - entry) > 0] if up[t] != dn[t] else []
                target = (min(other) if side > 0 else max(other)) if other else None
            x = _simulate(m, t, k, side, entry, stop, risk, lots, target, v, p)
            cnt["taken"] += 1
            done += 1
            free = x["exit_bar"]
            if x["net"] < 0:
                blocked.add((name, side))
            if detail:
                x.update(date=m.days[t], side="LONG" if side > 0 else "SHORT", state="UP" if m.state[t] == UP else "DOWN" if m.state[t] == DOWN else "-",
                         intra_state="UP" if m.intra[t][i] == UP else "DOWN", level=name, level_px=lv, signal_bar=i, entry_bar=k,
                         entry_raw=o, entry=entry, stop=stop, risk_pts=risk, lots=lots, lot=lot, qty=lots * lot, target=target,
                         gap=int(m.gap[t]), expiry=bool(m.expiry[t]))
                trades.append(x)
            else:
                trades.append((m.days[t], x["net"], x["r"], x["costs"], x["r0"]))
    return trades, cnt


# ---------------------------------------------------------------- numbers
def stats(rows, n_days: int, p=CFG) -> dict:
    """rows = [(date, net, r, costs, raw r)] in time order."""
    if not rows:
        return {"n": 0, "net": 0.0, "win": None, "loss": None, "scr": None, "pf": None, "dd": 0.0, "exp_r": None, "aw_r": None, "al_r": None,
                "avg": None, "costs": 0.0, "per_day": 0.0, "ret": 0.0, "exp_r0": None}
    net = np.array([r[1] for r in rows])
    R = np.array([r[2] for r in rows])
    s = p["scratch_r"]
    w, lo = R > s, R < -s
    gp, gl = net[net > 0].sum(), -net[net < 0].sum()
    eq = np.cumsum(net)
    dd = float((eq - np.maximum.accumulate(np.r_[0.0, eq])[1:]).min())
    return {"n": len(rows), "net": float(net.sum()), "win": w.mean() * 100, "loss": lo.mean() * 100, "scr": (~w & ~lo).mean() * 100,
            "pf": float(gp / gl) if gl > 0 else None, "dd": min(0.0, dd), "exp_r": float(R.mean()),
            "aw_r": float(R[w].mean()) if w.any() else None, "al_r": float(R[lo].mean()) if lo.any() else None,
            "avg": float(net.mean()), "costs": float(sum(r[3] for r in rows)), "per_day": len(rows) / max(1, n_days),
            "ret": float(net.sum()) / p["capital"] * 100, "exp_r0": float(np.mean([r[4] for r in rows]))}


def split_stats(m: Market, rows, p=CFG) -> dict:
    n_is = sum(d < m.cut for d in m.days)
    a = [r for r in rows if r[0] < m.cut]
    b = [r for r in rows if r[0] >= m.cut]
    return {"is": stats(a, n_is, p), "oos": stats(b, len(m.days) - n_is, p), "all": stats(rows, len(m.days), p)}


def as_rows(trades: list) -> list:
    return [(x["date"], x["net"], x["r"], x["costs"], x["r0"]) for x in trades]


# ---------------------------------------------------------------- grid (parallel)
_M = _SIG = None


def _init(cache):
    global _M, _SIG
    _M = load_market(cache=cache)
    _SIG = {}


def _work(vs: list) -> list:
    out = []
    for v in vs:
        key = (v["level"], v["seed"])
        if key not in _SIG:
            _SIG[key] = signals(_M, *key)
        rows, cnt = run(_M, v, _SIG[key], detail=False)
        out.append((v, split_stats(_M, rows), dict(cnt)))
    return out


def grid_variants(**fixed) -> list:
    keys = list(GRID)
    return [variant(**dict(zip(keys, combo)), **fixed) for combo in itertools.product(*(GRID[k] for k in keys))]


def run_many(variants: list, cache, workers: int = 7, progress=None, chunk: int = 40) -> list:
    """[(variant, split stats, counts)] for every variant, in the order given."""
    from concurrent.futures import ProcessPoolExecutor
    parts = [variants[i:i + chunk] for i in range(0, len(variants), chunk)]
    out = [None] * len(parts)
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(str(cache),)) as ex:
        futs = {ex.submit(_work, part): i for i, part in enumerate(parts)}
        from concurrent.futures import as_completed
        for n, f in enumerate(as_completed(futs), 1):
            out[futs[f]] = f.result()
            if progress:
                progress(n * chunk, len(variants))
    return [x for part in out for x in part]


def flat(v: dict, s: dict, cnt: dict) -> dict:
    row = {k: v[k] for k in KEY}
    for part in ("is", "oos", "all"):
        for k, val in s[part].items():
            row[f"{part}_{k}"] = val
    row.update({f"n_{k}": cnt.get(k, 0) for k in ("signals", "chase", "stop_beyond_fill", "stop_too_wide", "size_below_1_lot", "same_level_after_loss", "taken")})
    return row

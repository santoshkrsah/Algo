"""MCX commodity version of the layered-straddle engine (backtest.straddle.Sim is reused unchanged): Crude oil, Gold, Silver, Natural gas.

There is no MCX option data (not even for expired strikes), so every option is MODELLED: Black-76 on the real 1-minute MCX futures, one
IV per day = realized vol of the last 20 sessions x a premium factor (no smile, no skew). The rules of report 10 (9:20 ATM straddle SELL,
leg SL 75%, day loss limit 1.5%, trailing 50%, square-off) run on those prices. MCX session: t = 0 is 09:00, t = 870 is 23:30.

    mk = Market("CRUDEOIL", "2021-10-01", "2026-11-03", iv_mult=1.1)
    df = run(mk, params, "2021-11-01", "2026-11-03")
"""
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from backtest import straddle as S
from backtest.config import DATA

BARS = DATA / "bars"
N = 871                                   # t = 0 ... 870 (09:00 ... 23:30)
SESSION = 870                             # minutes in the session used (09:00 - 23:30)
ENTRY_T = 20                              # 09:20
EXIT_T = 855                              # 23:15
MIN_BARS = 700

SPEC = {   # qty = price units per lot (Fyers quotes gold per 10 g, silver per kg, crude per bbl, natural gas per mmBtu); pct = typical strike step as share of price
    "CRUDEOIL": dict(name="Crude oil", qty=100, pct=0.006, fut_months=tuple(range(1, 13)), lot_txt="100 bbl"),
    "GOLD": dict(name="Gold", qty=100, pct=0.003, fut_months=(2, 4, 6, 8, 10, 12), lot_txt="1 kg"),
    "SILVER": dict(name="Silver", qty=30, pct=0.0035, fut_months=(3, 5, 7, 9, 12), lot_txt="30 kg"),
    "NATURALGAS": dict(name="Natural gas", qty=1250, pct=0.013, fut_months=tuple(range(1, 13)), lot_txt="1250 mmBtu"),
}
MINI = {"GOLD": (10, "Gold Mini (100 g)"), "SILVER": (5, "Silver Mini (5 kg)"), "CRUDEOIL": (10, "Crude Oil Mini (10 bbl)"), "NATURALGAS": (250, "Natural Gas Mini (250 mmBtu)")}


# ---------------------------------------------------------------- calendars (approximate, from the Fyers symbol master pattern)
def _wk(d: date, sun_forward: bool = False) -> date:
    """Saturday -> Friday; Sunday -> Monday (sun_forward) or Friday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1) if sun_forward else d - timedelta(days=2)
    return d


def _bd_back(d: date, n: int) -> date:
    while n > 0:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _last_bd(y: int, m: int) -> date:
    first_next = date(y + (m == 12), m % 12 + 1, 1)
    return _bd_back(first_next, 1)


def fut_expiry(root: str, y: int, m: int) -> date:
    if root in ("GOLD", "SILVER"):
        return _wk(date(y, m, 5))
    if root == "CRUDEOIL":
        return _wk(date(y, m, 19), True)
    return _wk(date(y, m, 25), True)                                   # natural gas


def opt_expiry(root: str, y: int, m: int) -> date:
    """Monthly option expiry of month (y, m)."""
    if root == "GOLD":
        return _last_bd(y, m)
    if root == "SILVER":
        return _bd_back(_last_bd(y, m), 2)
    return _bd_back(fut_expiry(root, y, m), 2)                         # crude, natural gas: 2 business days before the futures expiry


def calendars(root: str, y0: int = 2021, y1: int = 2027):
    futs = sorted(fut_expiry(root, y, m) for y in range(y0, y1 + 1) for m in SPEC[root]["fut_months"])
    opts = sorted(opt_expiry(root, y, m) for y in range(y0, y1 + 1) for m in range(1, 13))
    return futs, opts


def nice_step(price: float, pct: float) -> float:
    raw = price * pct
    e = 10 ** math.floor(math.log10(raw))
    return float(min((1, 2, 2.5, 5, 10), key=lambda f: abs(f * e - raw)) * e)


# ---------------------------------------------------------------- costs (MCX commodity options, Fyers)
def fill_cost(value: float, buy: bool, fut: bool, day: str, index: str = "") -> dict:
    """Rs 20 per order, CTT 0.05% on the premium sold, MCX exchange charge 0.0418% of premium, SEBI 0.0001%, stamp 0.003% on the buy, GST 18%."""
    brokerage = 20.0
    exch = 0.000026 * value if fut else 0.000418 * value
    sebi = 0.000001 * value
    ctt = 0.0 if buy else (0.0001 if fut else 0.0005) * value
    stamp = (0.00002 if fut else 0.00003) * value if buy else 0.0
    gst = 0.18 * (brokerage + exch + sebi)
    return {"brokerage": brokerage, "stt": ctt, "exch": exch + sebi, "stamp": stamp, "gst": gst, "total": brokerage + exch + sebi + ctt + stamp + gst}


# ---------------------------------------------------------------- data
def load_bars(name: str, start: str, end: str) -> dict:
    """{date: array (870, 4) open, high, low, close per 1-minute bar 09:00 ... 23:29}; short / half days are left out."""
    m = pd.read_parquet(BARS / f"{name}_1m.parquet")
    m = m[(m.datetime >= start) & (m.datetime < pd.Timestamp(end) + pd.Timedelta(days=1))]
    k = (m.datetime.dt.hour * 60 + m.datetime.dt.minute - 540).values
    m = m.assign(k=k, date=m.datetime.dt.strftime("%Y-%m-%d"))
    m = m[(m.k >= 0) & (m.k < SESSION)]
    out = {}
    for d, g in m.groupby("date"):
        if len(g) < MIN_BARS or g.k.max() < 840:
            continue
        a = np.full((SESSION, 4), np.nan)
        a[g.k.values] = g[["open", "high", "low", "close"]].values
        out[d] = pd.DataFrame(a).ffill().bfill().values
    return out


class MSmile:
    def __init__(self, expiry: date, day: str, lot: int, iv: float):
        self.expiry, self.lot, self.iv = expiry.isoformat(), lot, iv
        d0 = date.fromisoformat(day)
        self.days = (expiry - d0).days                                     # calendar days (shown in the report)
        self.tdays = int(np.busday_count(d0 + timedelta(days=1), expiry + timedelta(days=1)))   # trading sessions after today up to the expiry day
        self.ok = True


class MDay:
    """One commodity on one day, same interface as straddle.Day for the Sim. picks = [(expiry date, bars of its underlying futures)], nearest first."""

    def __init__(self, root: str, dstr: str, prev_close: float, picks: list, lot: int, iv: float):
        self.index, self.date = root, dstr
        b0 = picks[0][1]
        self.bars, self.prev_close = b0, prev_close
        self.S, self.H, self.L = self._series(b0)
        self.step = nice_step(float(b0[ENTRY_T, 0]), SPEC[root]["pct"])
        self.vix, self.vix_prev, self.macd, self.last_lot = None, None, {}, lot
        self.smiles, self.u = {}, {}
        for e, ub in picks:
            sm = MSmile(e, dstr, lot, iv)
            self.smiles[sm.expiry] = sm
            self.u[sm.expiry] = self._series(ub)
        self.expiries = sorted(self.smiles)
        self._cache = {}

    @staticmethod
    def _series(b):
        return (np.concatenate([[b[0, 0]], b[:, 3]]), np.concatenate([[b[0, 0]], b[:, 1]]), np.concatenate([[b[0, 0]], b[:, 2]]))

    @staticmethod
    def _T(sm, t):
        """Trading-time years (252 sessions a year): whole sessions left + share of today's session left. IV is on the same 252-session basis,
        so a session's decay matches a session's realized variance (a calendar-time T would give only ~0.58 day of decay for 1 full session of risk)."""
        return (sm.tdays + (SESSION - np.asarray(t, float)) / SESSION) / 252

    def contract(self, expiry: str, K: float, cp: str):
        """(price, delta, price at the minute's worst underlying level) over t = 0 ... 870."""
        key = (expiry, K, cp)
        if key not in self._cache:
            sm, (s, h, l) = self.smiles[expiry], self.u[expiry]
            T = self._T(sm, np.arange(N))
            px, dl = S.b76(s, K, T, sm.iv, cp)
            worst = S.b76(h if cp == "CE" else l, K, T, sm.iv, cp)[0]
            self._cache[key] = (px, dl, worst)
        return self._cache[key]

    def price_at(self, expiry: str, Ks, cp: str, t: int):
        sm, (s, _, _) = self.smiles[expiry], self.u[expiry]
        return S.b76(s[t], np.asarray(Ks, float), self._T(sm, t), sm.iv, cp)[0]


class Market:
    """One commodity: front / next futures 1-minute bars, calendars, daily IV (realized vol of the last rv_days sessions x iv_mult)."""

    def __init__(self, root: str, start: str, end: str, iv_mult: float = 1.1, lot: int | None = None, rv_days: int = 20, base: "Market | None" = None):
        self.root, self.iv_mult, self.rv_days = root, iv_mult, rv_days
        self.lot = lot or SPEC[root]["qty"]
        if base is not None:                                           # same data, other IV factor / lot
            self.f1, self.f2, self.futs, self.opts, self.all_days, self._rv = base.f1, base.f2, base.futs, base.opts, base.all_days, base._rv
            return
        s0 = (pd.Timestamp(start) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
        self.f1, self.f2 = load_bars(f"{root}_F1", s0, end), load_bars(f"{root}_F2", s0, end)
        self.futs, self.opts = calendars(root)
        self.all_days = sorted(self.f1)
        self._rv = self._realized()

    def _front(self, d: date) -> int:
        return next(i for i, e in enumerate(self.futs) if e >= d)

    def _realized(self) -> dict:
        """Variance of one session: sum of squared 5-minute log returns inside the session + the overnight gap (skipped on a roll day)."""
        out, prev = {}, None
        for dstr in self.all_days:
            b = self.f1[dstr]
            v = float(np.sum(np.diff(np.log(b[4::5, 3])) ** 2))
            fi = self._front(date.fromisoformat(dstr))
            if prev is not None and prev[1] == fi:
                v += math.log(b[0, 0] / prev[0]) ** 2
            out[dstr] = v
            prev = (float(b[-1, 3]), fi)
        return out

    def iv_on(self, dstr: str) -> float | None:
        past = [d for d in self.all_days if d < dstr][-self.rv_days:]
        if len(past) < self.rv_days:
            return None
        return float(math.sqrt(np.mean([self._rv[d] for d in past]) * 252)) * self.iv_mult

    def days(self, start: str, end: str = "9999") -> list[str]:
        return [d for d in self.all_days if start <= d <= end]

    def day(self, dstr: str) -> "MDay | None":
        d = date.fromisoformat(dstr)
        iv = self.iv_on(dstr)
        fi = self._front(d)
        picks = []
        for e in [x for x in self.opts if x >= d][:2]:
            ui = next(i for i, x in enumerate(self.futs) if x > e)            # underlying: the first futures expiring after the option
            b = self.f1.get(dstr) if ui == fi else self.f2.get(dstr) if ui == fi + 1 else None
            if b is not None:
                picks.append((e, b))
        if iv is None or not picks:
            return None
        prevd = [x for x in self.all_days if x < dstr]
        pc = float(self.f1[prevd[-1]][-1, 3]) if prevd else float("nan")
        return MDay(self.root, dstr, pc, picks, self.lot, iv)


def patch_engine():
    """Point the shared engine (straddle.Sim) at MCX times and costs."""
    S.fill_cost = fill_cost
    S.EXIT_T = EXIT_T
    S.CLOSE_T = EXIT_T


def run(mk: Market, params: S.Params, start: str, end: str, keep: dict | None = None) -> pd.DataFrame:
    """One parameter set through the market. keep (optional dict) collects {date: MDay} of traded days for the viewer."""
    patch_engine()
    sim = S.Sim(params)
    for dstr in mk.days(start, end):
        d = mk.day(dstr)
        if d is None:
            continue
        sim.step(d)
        if keep is not None and sim.log[-1]["traded"]:
            keep[dstr] = d
    return S.frame(sim)

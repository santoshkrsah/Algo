"""Layered short-straddle algo on NIFTY / BANKNIFTY index options, simulated minute by minute.

Layers: core (sell the ATM straddle), strike adjustment, averaging, tactical buying, delta scalping (futures hedge timed by
a MACD histogram turn), risk (leg stop, daily loss limit = kill switch, trailing profit lock, confirmed stops), drawdown
scaling and a regime pause (gap / VIX jump / budget day). Every layer can be switched off through Params.

Prices: expired index options have no 1-minute data, so every contract is priced minute by minute with Black-76 on the
real 1-minute index: forward = index + basis (basis from put-call parity at the open and at the close), IV per strike from
that day's real NSE bhavcopy OPEN and CLOSE (the smile at 9:15 and at ~15:15), moving in a straight line in between.
Option prices are therefore real at 9:15 and at the close and follow the real index in between.

    ctx = Market("NIFTY", "2024-11-25", "2026-10-30")
    sims = [Sim(Params()), Sim(Params(scalp=False))]
    for day in ctx.days("2024-12-02"):
        d = ctx.day(day)
        for s in sims:
            s.step(d)
    sims[0].log                                       # one row per day: entry, adjustments, layer P&L, exit reason, equity
"""
import math
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from backtest.config import DATA, FNO

R = 0.06                                   # interest rate in Black-76
SPOT_FILE = {"NIFTY": "NIFTY50_1m.csv", "BANKNIFTY": "BANKNIFTY_1m.csv"}
STEP = {"NIFTY": 50, "BANKNIFTY": 100}
N = 376                                    # t = minutes after 9:15: t = 0 is the 9:15 open, t = 375 is 15:30
CLOSE_T = 360                              # NSE close = 15:00-15:30 average, taken as the 15:15 price
EXIT_T = 360                               # square off at 15:15
EVENTS = {"2025-02-01": "Union Budget", "2026-02-01": "Union Budget"}
MIN_TRADES = 100                           # a strike's open / close counts for the smile only with this many trades that day


@dataclass(frozen=True)
class Params:
    index: str = "NIFTY"
    capital: float = 3_000_000.0
    deploy: float = 1 / 3                  # share of capital used as margin (rest is buffer)
    margin_pct: float = 0.10               # margin per straddle = 10% of contract value (lot x index)
    entry_t: int = 5                       # 9:20 (0 = 9:15 open)
    expiry: str = "cur"                    # "cur" = nearest expiry, "next" = the one after
    hold: str = "day"                      # "day" = square off 15:15, "expiry" = hold till the expiry day 15:15
    adj_pts: float | None = 200.0          # strike adjustment trigger (premium points), None = layer off
    adj_mode: str = "leg"                  # "leg" = one leg up adj_pts from its entry, "combined" = straddle up adj_pts
    adj_max: int = 2                       # adjustments per day
    avg: bool = False                      # averaging layer
    avg_trigger: float = 0.20              # add when the straddle premium is 20% above entry ...
    avg_frac: float = 0.5                  # ... half the lots more, once a day, only while the day loss < half the limit
    tactical: bool = True                  # buy layer
    tac_frac: float = 0.25                 # lots = 25% of the core lots (min 1)
    tac_sl: float = 0.30                   # stop 30% of the premium paid
    or_t: int = 15                         # opening range = 9:15-9:30
    tac_last: int = 315                    # no new tactical buy after 14:30
    scalp: bool = True                     # delta layer
    band: float = 0.5                      # net delta per core lot that switches the hedge on
    macd_tf: int = 3                       # MACD (12, 26, 9) histogram on 3-minute bars of the index
    leg_sl: float | None = 0.75            # leg stop: premium up 75% from its entry
    day_sl: float | None = 0.015           # daily loss limit (kill switch), share of capital
    trail: float | None = 0.5              # trailing lock once profit reaches 50% of the premium collected
    confirm: int = 2                       # stops need this many 1-minute closes beyond the level (1 = touch inside the minute)
    dd_steps: tuple = (0.05, 0.10, 0.15)   # drawdown from peak equity that halves the size each time
    pause: bool = True                     # regime pause
    gap_max: float = 0.01                  # |open / previous close - 1| above this -> no trade
    vix_jump: float = 0.10                 # India VIX at entry above previous close by this -> no trade
    slip_opt: float = 0.005                # slippage, share of the option premium, every fill
    slip_fut: float = 0.0002               # slippage, share of the futures price, every fill


def with_params(base: Params, **kw) -> Params:
    return replace(base, **kw)


# ---------------------------------------------------------------- Black-76
def ncdf(x):
    """Normal CDF (Abramowitz-Stegun 7.1.26, error < 1.5e-7)."""
    x = np.asarray(x, float)
    z = np.abs(x) / math.sqrt(2)
    k = 1 / (1 + 0.3275911 * z)
    erf = 1 - (((((1.061405429 * k - 1.453152027) * k) + 1.421413741) * k - 0.284496736) * k + 0.254829592) * k * np.exp(-z * z)
    return 0.5 * (1 + np.sign(x) * erf)


def b76(F, K, T, vol, cp):
    """(price, delta). cp = 'CE' / 'PE'. T in years (floored at one second)."""
    F, K, T, vol = np.broadcast_arrays(*(np.asarray(x, float) for x in (F, K, T, vol)))
    T = np.maximum(T, 1 / (365 * 86400))
    sv = vol * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sv * sv) / sv
    df = np.exp(-R * T)
    if cp == "CE":
        return df * (F * ncdf(d1) - K * ncdf(d1 - sv)), df * ncdf(d1)
    return df * (K * ncdf(sv - d1) - F * ncdf(-d1)), -df * ncdf(-d1)


def implied_vol(price, F, K, T, cp):
    price, F, K, T = np.broadcast_arrays(*(np.asarray(x, float) for x in (price, F, K, T)))
    lo, hi = np.full(price.shape, 0.005), np.full(price.shape, 3.0)
    for _ in range(50):
        mid = (lo + hi) / 2
        up = b76(F, K, T, mid, cp)[0] < price
        lo, hi = np.where(up, mid, lo), np.where(up, hi, mid)
    iv = (lo + hi) / 2
    return np.where((iv > 0.006) & (iv < 2.99), iv, np.nan)


def years(day: str, expiry: str, t):
    """Years from <day> 9:15 + t minutes to <expiry> 15:30."""
    return ((pd.Timestamp(expiry) - pd.Timestamp(day)).days * 1440 + (375 - np.asarray(t, float))) / (365 * 1440)


# ---------------------------------------------------------------- costs
def fill_cost(value: float, buy: bool, fut: bool, day: str) -> dict:
    """Fyers / NSE charges for one order of Rs <value>. STT: Budget 2026 rates from 1 Apr 2026 (options 0.15%, futures 0.05% on
    the sell side), before that 0.1% / 0.02%."""
    new = day >= "2026-04-01"
    brokerage = 20.0
    exch = (0.0000173 if fut else 0.0003503) * value
    sebi = 0.000001 * value
    stt = 0.0 if buy else ((0.0005 if new else 0.0002) if fut else (0.0015 if new else 0.001)) * value
    stamp = ((0.00002 if fut else 0.00003) * value) if buy else 0.0
    gst = 0.18 * (brokerage + exch + sebi)
    return {"brokerage": brokerage, "stt": stt, "exch": exch + sebi, "stamp": stamp, "gst": gst, "total": brokerage + exch + sebi + stt + stamp + gst}


# ---------------------------------------------------------------- data
def _minutes(path, start: str, end: str) -> dict:
    """{date: array (375, 4) open, high, low, close per 1-minute bar 9:15 ... 15:29}; days with < 300 bars are left out."""
    m = pd.read_csv(path)
    m = m[(m.datetime >= start) & (m.datetime <= end + " 99")]
    dt = pd.to_datetime(m.datetime)
    m = m.assign(date=m.datetime.str[:10], k=(dt.dt.hour * 60 + dt.dt.minute - 555).values)
    m = m[(m.k >= 0) & (m.k < 375)]
    out = {}
    for d, g in m.groupby("date"):
        if len(g) < 300:
            continue
        a = np.full((375, 4), np.nan)
        a[g.k.values] = g[["open", "high", "low", "close"]].values
        out[d] = pd.DataFrame(a).ffill().bfill().values
    return out


def load_chain(index: str, start: str, end: str) -> pd.DataFrame:
    cols = {"TradDt": "date", "XpryDt": "expiry", "StrkPric": "strike", "OptnTp": "type", "OpnPric": "open", "ClsPric": "close",
            "TtlNbOfTxsExctd": "trades", "NewBrdLotQty": "lot", "TckrSymb": "symbol"}
    files = [p for p in sorted((FNO / "bhavcopy_idx").glob("*.csv")) if start <= p.stem <= end]
    c = pd.concat([pd.read_csv(p, usecols=list(cols)) for p in files], ignore_index=True).rename(columns=cols)
    return c[c.symbol == index].drop(columns="symbol")


class Smile:
    """One expiry on one day: basis and IV per strike at the open (9:15) and at the close (~15:15)."""

    def __init__(self, day: str, expiry: str, g: pd.DataFrame, s_open: float, s_close: float, step: int):
        self.expiry, self.days = expiry, (pd.Timestamp(expiry) - pd.Timestamp(day)).days
        self.lot = int(g.lot.mode().iloc[0])
        To, Tc = years(day, expiry, 0), years(day, expiry, CLOSE_T)
        p = g.pivot_table(index="strike", columns="type", values=["open", "close", "trades"], aggfunc="first")
        ok = pd.Series(True, index=p.index)
        for side in ("CE", "PE"):
            ok &= p.get(("trades", side), pd.Series(0, index=p.index)).fillna(0) >= MIN_TRADES
        both = p[ok]
        self.basis_o = self.basis_c = 0.0
        if len(both):
            near = both.index[np.argsort(np.abs(both.index.values - s_open))[:5]]
            fo = near.values + (both.loc[near, ("open", "CE")] - both.loc[near, ("open", "PE")]).values * math.exp(R * To)
            fc = near.values + (both.loc[near, ("close", "CE")] - both.loc[near, ("close", "PE")]).values * math.exp(R * Tc)
            bo, bc = np.nanmedian(fo - s_open), np.nanmedian(fc - s_close)
            lim = 0.02 * s_open                      # a broken parity (stale price) gives nonsense; keep the basis sane
            self.basis_o = float(bo) if abs(bo) < lim else 0.0
            self.basis_c = float(bc) if abs(bc) < lim else 0.0
        Fo, Fc = s_open + self.basis_o, s_close + self.basis_c
        ks, ivo, ivc = [], [], []
        for k, r in p.iterrows():
            row = []
            for px, F, T in (("open", Fo, To), ("close", Fc, Tc)):
                side = "CE" if k >= F else "PE"           # OTM side gives the cleaner IV
                if r.get(("trades", side), 0) >= MIN_TRADES and r.get((px, side), 0) > 0.05:
                    row.append((r[(px, side)], F, T, side))
                else:
                    row.append(None)
            ks.append(k)
            ivo.append(row[0])
            ivc.append(row[1])
        ks = np.array(ks, float)
        self.k_o, self.iv_o = self._fit(ks, ivo)
        self.k_c, self.iv_c = self._fit(ks, ivc)
        self.ok = len(self.k_o) >= 3 and len(self.k_c) >= 3
        self.step = step

    @staticmethod
    def _fit(ks, rows):
        out_k, out_v = [], []
        for side in ("CE", "PE"):
            idx = [i for i, r in enumerate(rows) if r is not None and r[3] == side]
            if not idx:
                continue
            iv = implied_vol([rows[i][0] for i in idx], [rows[i][1] for i in idx], ks[idx], [rows[i][2] for i in idx], side)
            out_k.extend(ks[idx])
            out_v.extend(iv)
        k, v = np.array(out_k), np.array(out_v)
        keep = ~np.isnan(v)
        k, v = k[keep], v[keep]
        o = np.argsort(k)
        k, v = k[o], v[o]
        if len(v) >= 5:                                   # drop a strike whose IV is far from its neighbours (stale print)
            med = pd.Series(v).rolling(5, center=True, min_periods=3).median().values
            good = np.abs(v / med - 1) <= 0.35
            k, v = k[good], v[good]
        return k, v

    def iv(self, K):
        return np.interp(K, self.k_o, self.iv_o), np.interp(K, self.k_c, self.iv_c)


class Day:
    """One index on one day: 1-minute index (t = 0 ... 375), VIX, smiles of the nearest expiries, contract prices on demand."""

    def __init__(self, index: str, date: str, bars: np.ndarray, prev_close: float, vix: np.ndarray | None, vix_prev: float | None,
                 chain: pd.DataFrame, macd: dict):
        self.index, self.date, self.step = index, date, STEP[index]
        self.S = np.concatenate([[bars[0, 0]], bars[:, 3]])          # t = 0 open, t >= 1 close of bar t - 1
        self.H = np.concatenate([[bars[0, 0]], bars[:, 1]])
        self.L = np.concatenate([[bars[0, 0]], bars[:, 2]])
        self.bars, self.prev_close = bars, prev_close
        self.vix = None if vix is None else np.concatenate([[vix[0, 0]], vix[:, 3]])
        self.vix_prev = vix_prev
        self.macd = macd                                              # {tf: histogram per completed bar of the day}
        s_close = float(bars[345:375, 3].mean())
        self.smiles = {}
        for e, g in chain.groupby("expiry"):
            if e < date:
                continue
            sm = Smile(date, e, g, float(bars[0, 0]), s_close, self.step)
            if sm.ok:
                self.smiles[e] = sm
        self.expiries = sorted(self.smiles)
        self._cache = {}
        t = np.arange(N)
        self.w = np.minimum(t, CLOSE_T) / CLOSE_T

    def contract(self, expiry: str, K: float, cp: str):
        """(price, delta, price at the minute's worst index level) arrays over t = 0 ... 375."""
        key = (expiry, K, cp)
        if key not in self._cache:
            sm = self.smiles[expiry]
            ivo, ivc = sm.iv(K)
            iv = ivo + (ivc - ivo) * self.w
            basis = sm.basis_o + (sm.basis_c - sm.basis_o) * self.w
            T = (sm.days * 1440 + (375 - np.arange(N))) / (365 * 1440)
            px, dl = b76(self.S + basis, K, T, iv, cp)
            worst = b76((self.H if cp == "CE" else self.L) + basis, K, T, iv, cp)[0]
            self._cache[key] = (px, dl, worst)
        return self._cache[key]

    def price_at(self, expiry: str, Ks: np.ndarray, cp: str, t: int) -> np.ndarray:
        sm = self.smiles[expiry]
        ivo, ivc = sm.iv(Ks)
        w = self.w[t]
        F = self.S[t] + sm.basis_o + (sm.basis_c - sm.basis_o) * w
        T = (sm.days * 1440 + (375 - t)) / (365 * 1440)
        return b76(F, Ks, T, ivo + (ivc - ivo) * w, cp)[0]


class Market:
    """All data of one index for a period: 1-minute index and VIX, option chain, MACD of the index."""

    def __init__(self, index: str, start: str, end: str):
        self.index = index
        self.bars = _minutes(DATA / "index" / SPOT_FILE[index], start, end)
        vf = DATA / "index" / "INDIAVIX_1m.csv"
        self.vix = _minutes(vf, start, end) if vf.exists() else {}
        self.chain = {d: g for d, g in load_chain(index, start, end).groupby("date")}
        self.all_days = sorted(set(self.bars) & set(self.chain))
        self.macd = {tf: self._macd(tf) for tf in (2, 3)}

    def _macd(self, tf: int) -> dict:
        """MACD (12, 26, 9) histogram of tf-minute bars of the index, EMAs running across days; {date: hist per bar}."""
        closes, owner = [], []
        for d in sorted(self.bars):
            c = self.bars[d][:, 3]
            n = 375 // tf
            closes.extend(c[tf - 1:n * tf:tf])
            owner.extend([d] * n)
        s = pd.Series(closes)
        macd = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
        hist = (macd - macd.ewm(span=9, adjust=False).mean()).values
        out = {}
        for d, h in zip(owner, hist):
            out.setdefault(d, []).append(h)
        return {d: np.array(v) for d, v in out.items()}

    def days(self, start: str, end: str = "9999") -> list[str]:
        return [d for d in self.all_days if start <= d <= end]

    def day(self, date: str) -> Day:
        prev = [d for d in sorted(self.bars) if d < date]
        pc = float(self.bars[prev[-1]][-1, 3]) if prev else float("nan")
        vprev = [d for d in sorted(self.vix) if d < date]
        return Day(self.index, date, self.bars[date], pc, self.vix.get(date), float(self.vix[vprev[-1]][-1, 3]) if vprev else None,
                   self.chain[date], {tf: m.get(date) for tf, m in self.macd.items()})


# ---------------------------------------------------------------- simulation
class Sim:
    """One parameter set walked through the days; keeps equity, drawdown stage and (hold mode) open positions."""

    def __init__(self, p: Params, name: str = ""):
        self.p, self.name = p, name
        self.equity, self.peak, self.stage = 0.0, 0.0, 0
        self.pos = []                                  # open positions (hold mode carries them overnight)
        self.cycle = None                              # current straddle cycle (hold mode spans days)
        self.log, self.events = [], {}

    # -------------------------------------------------- helpers
    def _lots(self, d: Day, lot: int) -> int:
        p = self.p
        base = int((p.capital * p.deploy) // (p.margin_pct * lot * d.S[p.entry_t]))
        return max(1, int(base * 0.5 ** self.stage)) if base else 1

    def _dd_update(self):
        p = self.p
        val, pk = p.capital + self.equity, p.capital + self.peak
        dd = (pk - val) / pk
        steps = p.dd_steps
        while self.stage < len(steps) and dd >= steps[self.stage]:
            self.stage += 1
        while self.stage > 0 and dd < steps[self.stage - 1] / 2:
            self.stage -= 1
        return dd

    def _fill(self, ps, t, px, buy, why, rec, d):
        """Book a fill for position ps (dict) at model price px; returns the fill price after slippage."""
        p = self.p
        fut = ps["cp"] == "FUT"
        slip = p.slip_fut if fut else p.slip_opt
        f = px * (1 + slip) if buy else px * (1 - slip)
        c = fill_cost(f * ps["qty"], buy, fut, d.date)
        rec["costs"] += c["total"]
        rec["slip"] += abs(f - px) * ps["qty"]
        rec["cost_" + ps["layer"]] += c["total"]
        for k in ("brokerage", "stt", "exch", "stamp", "gst"):
            rec["c_" + k] += c[k]
        rec["orders"] += 1
        self.ev.append([t, why, ps["layer"]])
        return f

    def _open(self, d, t, layer, expiry, K, cp, side, lots, lot, rec, why):
        if cp == "FUT":
            px = d.S[t]
        else:
            px = d.contract(expiry, K, cp)[0][t]
        ps = {"layer": layer, "exp": expiry, "K": K, "cp": cp, "side": side, "qty": lots * lot, "lots": lots, "lot": lot,
              "ref": px, "t0": t, "cnt": 0}
        ps["px0"] = self._fill(ps, t, px, side > 0, why.format(px=px), rec, d)
        ps["base"] = ps["px0"]                         # price the position is measured from today (yesterday's mark when carried)
        self.pos.append(ps)
        return ps

    def _close(self, ps, t, px, rec, why, d):
        f = self._fill(ps, t, px, ps["side"] < 0, why.format(px=px), rec, d)
        pnl = ps["side"] * (f - ps["px0"]) * ps["qty"]                    # since the position was opened (cycle total)
        rec["pnl_" + ps["layer"]] += ps["side"] * (f - ps["base"]) * ps["qty"]   # today's share
        self.pos.remove(ps)
        return pnl

    def _px(self, d, ps, t, worst=False):
        if ps["cp"] != "FUT" and ps["exp"] not in d.smiles:      # no data for this expiry today: last known price
            return ps.get("mark", ps["px0"])
        if ps["cp"] == "FUT":
            return d.S[t] if not worst else (d.L[t] if ps["side"] > 0 else d.H[t])
        a = d.contract(ps["exp"], ps["K"], ps["cp"])
        if not worst:
            return a[0][t]
        return a[2][t] if ps["side"] < 0 else a[0][t]   # short option: worst = price at the minute's adverse extreme

    def _mtm(self, d, t, worst=False):
        """Unrealised P&L of open positions vs their fills (exit slippage left out)."""
        if not worst:
            return sum(ps["side"] * (self._px(d, ps, t) - ps["px0"]) * ps["qty"] for ps in self.pos)
        up = dn = 0.0                                    # index at the minute's high and at its low
        for ps in self.pos:
            if ps["cp"] == "FUT":
                hi, lo = d.H[t], d.L[t]
            else:
                a = d.contract(ps["exp"], ps["K"], ps["cp"])
                hi, lo = (a[2][t], a[0][t]) if ps["cp"] == "CE" else (a[0][t], a[2][t])
            up += ps["side"] * (hi - ps["px0"]) * ps["qty"]
            dn += ps["side"] * (lo - ps["px0"]) * ps["qty"]
        return min(up, dn)

    def _hit(self, ps, cond: bool) -> bool:
        """Stop confirmation: <confirm> consecutive 1-minute closes beyond the level."""
        ps["cnt"] = ps["cnt"] + 1 if cond else 0
        return ps["cnt"] >= self.p.confirm

    # -------------------------------------------------- one day
    def step(self, d: Day):
        p = self.p
        self.ev = []
        rec = {"date": d.date, "index": d.index, "traded": 0, "expiry": None, "dte": None, "strike": None, "lots": 0, "lot": None,
               "entry_t": None, "ce0": None, "pe0": None, "prem": None, "vix": None, "gap": None, "pause": "", "adj": 0, "adj_txt": "",
               "sl": 0, "tac": 0, "hedges": 0, "avg_done": 0, "exit": "", "exit_t": None, "orders": 0, "costs": 0.0, "slip": 0.0,
               "deployed": 0.0, "max_target": 0.0, "net_delta_max": 0.0, "dd_stage": self.stage, "carry": 0}
        for k in ("core", "avg", "tac", "hedge"):
            rec["pnl_" + k] = 0.0
            rec["cost_" + k] = 0.0
        for k in ("brokerage", "stt", "exch", "stamp", "gst"):
            rec["c_" + k] = 0.0
        gap = d.S[0] / d.prev_close - 1 if d.prev_close == d.prev_close else 0.0
        rec["gap"] = gap
        te = p.entry_t
        vix_now = float(d.vix[te]) if d.vix is not None else None
        rec["vix"] = vix_now
        exp_list = d.expiries
        # ------------------------------------------------ carried positions (hold mode): marks from yesterday's 15:15 price
        carried = bool(self.pos)
        if carried:
            rec["carry"] = 1
            for ps in self.pos:
                ps["base"] = ps["mark"]
            start_mtm = sum(ps["side"] * (ps["mark"] - ps["px0"]) * ps["qty"] for ps in self.pos)
            cyc = self.cycle
            rec.update(traded=1, expiry=cyc["exp"], strike=cyc["K"], lots=cyc["lots"], lot=cyc["lot"], dte=(pd.Timestamp(cyc["exp"]) - pd.Timestamp(d.date)).days,
                       prem=cyc["prem"], max_target=cyc["max_target"], deployed=cyc["deployed"])
            t_from = 0
        else:
            start_mtm = 0.0
            dd = self._dd_update()
            rec["dd_stage"] = self.stage
            if p.pause:
                why = []
                if abs(gap) > p.gap_max:
                    why.append(f"gap {gap * 100:+.2f}%")
                if vix_now and d.vix_prev and vix_now / d.vix_prev - 1 > p.vix_jump:
                    why.append(f"VIX {(vix_now / d.vix_prev - 1) * 100:+.1f}%")
                if d.date in EVENTS:
                    why.append(EVENTS[d.date])
                if why:
                    rec["pause"] = ", ".join(why)
            want = 0 if p.expiry == "cur" else 1
            if rec["pause"] or len(exp_list) <= want:
                rec["exit"] = "Pause (trade nahi)" if rec["pause"] else "Contract nahi mila"
                self._finish(rec, d)
                return
            exp = exp_list[want]
            sm = d.smiles[exp]
            K = round(d.S[te] / d.step) * d.step
            lots = self._lots(d, sm.lot)
            ce = self._open(d, te, "core", exp, K, "CE", -1, lots, sm.lot, rec, f"SELL {K:g} CE {{px:.2f}} ({lots} lot)")
            pe = self._open(d, te, "core", exp, K, "PE", -1, lots, sm.lot, rec, f"SELL {K:g} PE {{px:.2f}} ({lots} lot)")
            self.cycle = {"exp": exp, "K": K, "lots": lots, "lot": sm.lot, "prem": ce["ref"] + pe["ref"], "max_target": (ce["px0"] + pe["px0"]) * lots * sm.lot,
                          "deployed": lots * p.margin_pct * sm.lot * d.S[te], "peak": 0.0, "armed": False, "trail_cnt": 0, "start": d.date, "realised": 0.0}
            rec.update(traded=1, expiry=exp, dte=sm.days, strike=K, lots=lots, lot=sm.lot, entry_t=te, ce0=ce["ref"], pe0=pe["ref"], prem=ce["ref"] + pe["ref"],
                       max_target=self.cycle["max_target"], deployed=self.cycle["deployed"])
            t_from = te + 1
        cyc = self.cycle
        last_day = (not p.hold == "expiry") or d.date >= cyc["exp"]
        t_end = EXIT_T
        # ------------------------------------------------ minute loop
        realised_today = 0.0
        day_cnt, tac_armed, tac_done, avg_done, adj_n = 0, 0, False, False, 0
        or_hi = float(d.bars[:p.or_t, 1].max())
        or_lo = float(d.bars[:p.or_t, 2].min())
        hist = d.macd.get(p.macd_tf)
        stop_all = ""
        for t in range(t_from, t_end + 1):
            if not self.pos:
                break
            for ps in self.pos:
                if ps["cp"] != "FUT" and ps["exp"] not in d.smiles:
                    stop_all = "Contract data nahi"
            if stop_all:
                break
            day_pnl = realised_today + self._mtm(d, t) - start_mtm
            # kill switch (daily loss limit)
            if p.day_sl is not None:
                v = realised_today + self._mtm(d, t, worst=True) - start_mtm if p.confirm <= 1 else day_pnl
                day_cnt = day_cnt + 1 if v <= -p.day_sl * p.capital else 0
                if day_cnt >= max(1, p.confirm):
                    stop_all = "Din ka loss limit (kill switch)"
                    break
            # trailing profit lock (on the whole cycle's profit)
            if p.trail is not None:
                cyc_pnl = cyc["realised"] + realised_today + self._mtm(d, t)
                if cyc_pnl >= p.trail * cyc["max_target"]:
                    cyc["armed"] = True
                if cyc["armed"]:
                    cyc["peak"] = max(cyc["peak"], cyc_pnl)
                    cyc["trail_cnt"] = cyc["trail_cnt"] + 1 if cyc_pnl < 0.5 * cyc["peak"] else 0
                    if cyc["trail_cnt"] >= max(1, p.confirm):
                        stop_all = "Trailing profit lock"
                        break
            # core / averaging legs: adjustment, then leg stop
            shorts = [ps for ps in self.pos if ps["side"] < 0 and ps["layer"] in ("core", "avg")]
            core = [ps for ps in shorts if ps["layer"] == "core"]
            if p.adj_pts is not None and adj_n < p.adj_max and core:
                thr = None
                if p.adj_mode == "leg":
                    for ps in core:
                        rise = self._px(d, ps, t, worst=p.confirm <= 1) - ps["ref"]
                        ps["acnt"] = ps.get("acnt", 0) + 1 if rise >= p.adj_pts else 0
                        if ps["acnt"] >= max(1, p.confirm):
                            thr = ps
                            break
                elif len(core) == 2:
                    rise = sum(self._px(d, ps, t) - ps["ref"] for ps in core)
                    cyc["acnt"] = cyc.get("acnt", 0) + 1 if rise >= p.adj_pts else 0
                    if cyc["acnt"] >= max(1, p.confirm):
                        thr = max(core, key=lambda ps: self._px(d, ps, t) - ps["ref"])
                        cyc["acnt"] = 0
                if thr is not None:
                    other = [ps for ps in core if ps is not thr]
                    px_thr = self._px(d, thr, t)
                    realised_today += self._close(thr, t, px_thr, rec, f"ADJ: {thr['K']:g} {thr['cp']} +{px_thr - thr['ref']:.0f} pt, buy back {{px:.2f}}", d)
                    adj_n += 1
                    rec["adj"] += 1
                    if other:
                        target = self._px(d, other[0], t)
                        Ks = np.arange(-40, 41) * d.step + round(d.S[t] / d.step) * d.step
                        prices = d.price_at(thr["exp"], Ks, thr["cp"], t)
                        K2 = float(Ks[int(np.argmin(np.abs(prices - target)))])
                        self._open(d, t, "core", thr["exp"], K2, thr["cp"], -1, thr["lots"], thr["lot"], rec, f"ADJ: SELL {K2:g} {thr['cp']} {{px:.2f}}")
                        rec["adj_txt"] += f"{_hm(t)} {thr['cp']} {thr['K']:g}->{K2:g}; "
                    else:
                        rec["adj_txt"] += f"{_hm(t)} {thr['cp']} {thr['K']:g} band; "
            if p.leg_sl is not None:
                for ps in [x for x in self.pos if x["side"] < 0 and x["layer"] in ("core", "avg")]:
                    lvl = ps["ref"] * (1 + p.leg_sl)
                    if p.confirm <= 1:
                        if self._px(d, ps, t, worst=True) >= lvl:
                            fill_px = max(lvl, self._px(d, ps, t - 1) if t > 0 else lvl)
                            realised_today += self._close(ps, t, fill_px, rec, f"SL {ps['K']:g} {ps['cp']} ({int(p.leg_sl * 100)}%) {{px:.2f}}", d)
                            rec["sl"] += 1
                            tac_armed = tac_armed or (1 if ps["cp"] == "CE" else -1)
                    elif self._hit(ps, self._px(d, ps, t) >= lvl):
                        realised_today += self._close(ps, t, self._px(d, ps, t), rec, f"SL {ps['K']:g} {ps['cp']} ({int(p.leg_sl * 100)}%) {{px:.2f}}", d)
                        rec["sl"] += 1
                        tac_armed = tac_armed or (1 if ps["cp"] == "CE" else -1)
            # averaging
            core = [ps for ps in self.pos if ps["layer"] == "core"]
            if p.avg and not avg_done and len(core) == 2 and day_pnl > -0.5 * (p.day_sl or 1) * p.capital:
                if sum(self._px(d, ps, t) for ps in core) >= (1 + p.avg_trigger) * sum(ps["ref"] for ps in core):
                    n = max(1, int(cyc["lots"] * p.avg_frac))
                    for ps in core:
                        self._open(d, t, "avg", ps["exp"], ps["K"], ps["cp"], -1, n, ps["lot"], rec, f"AVG: SELL {ps['K']:g} {ps['cp']} {{px:.2f}} ({n} lot)")
                    avg_done = True
                    rec["avg_done"] = 1
            # tactical buy
            if p.tactical and tac_armed and not tac_done and p.or_t <= t <= p.tac_last:
                up = tac_armed > 0
                if (up and d.S[t] > or_hi) or (not up and d.S[t] < or_lo):
                    exp = cyc["exp"] if cyc["exp"] in d.smiles else d.expiries[0]
                    K = round(d.S[t] / d.step) * d.step
                    n = max(1, round(cyc["lots"] * p.tac_frac))
                    self._open(d, t, "tac", exp, K, "CE" if up else "PE", +1, n, cyc["lot"], rec, f"TACTICAL BUY {K:g} {'CE' if up else 'PE'} {{px:.2f}} ({n} lot)")
                    tac_done = True
                    rec["tac"] = 1
            for ps in [x for x in self.pos if x["layer"] == "tac"]:
                if self._hit(ps, self._px(d, ps, t) <= ps["ref"] * (1 - p.tac_sl)):
                    realised_today += self._close(ps, t, self._px(d, ps, t), rec, f"TACTICAL SL {ps['K']:g} {ps['cp']} {{px:.2f}}", d)
            # delta scalping (futures hedge)
            if p.scalp:
                opts = [ps for ps in self.pos if ps["cp"] != "FUT"]
                lot = cyc["lot"]
                tot = sum(ps["side"] * d.contract(ps["exp"], ps["K"], ps["cp"])[1][t] * ps["qty"] for ps in opts) / lot
                per = tot / max(1, cyc["lots"])
                rec["net_delta_max"] = max(rec["net_delta_max"], abs(per))
                hedge = [ps for ps in self.pos if ps["layer"] == "hedge"]
                if hedge and abs(per) <= p.band:
                    realised_today += self._close(hedge[0], t, d.S[t], rec, f"HEDGE BAND: delta {per:+.2f}/lot, FUT {{px:.2f}}", d)
                elif not hedge and abs(per) > p.band and hist is not None and t >= 3 * p.macd_tf and t % p.macd_tf == 0:
                    k = t // p.macd_tf - 1
                    if k >= 2:
                        h0, h1, h2 = hist[k], hist[k - 1], hist[k - 2]
                        want_up = per < 0
                        turned = (h0 > h1 and h1 <= h2) if want_up else (h0 < h1 and h1 >= h2)
                        if turned:
                            n = max(1, int(round(abs(tot))))
                            self._open(d, t, "hedge", None, None, "FUT", +1 if want_up else -1, n, lot, rec,
                                       f"HEDGE {'BUY' if want_up else 'SELL'} {n} lot FUT {{px:.2f}} (delta {per:+.2f}/lot, MACD)")
                            rec["hedges"] += 1
        # ------------------------------------------------ end of day
        t_close = min(t, t_end) if self.pos else t_end
        if stop_all:
            for ps in list(self.pos):
                realised_today += self._close(ps, t, self._px(d, ps, t), rec, stop_all + " {px:.2f}", d)
            rec["exit"], rec["exit_t"] = stop_all, t
        else:
            for ps in [x for x in self.pos if x["layer"] in ("tac", "hedge")]:
                realised_today += self._close(ps, t_end, self._px(d, ps, t_end), rec, "3:15 square off {px:.2f}", d)
            if self.pos and last_day:
                for ps in list(self.pos):
                    realised_today += self._close(ps, t_end, self._px(d, ps, t_end), rec, "3:15 square off {px:.2f}", d)
                rec["exit"], rec["exit_t"] = ("Expiry din 3:15" if p.hold == "expiry" else "3:15 square off"), t_end
            elif self.pos:
                for ps in self.pos:                       # carried overnight: mark at 15:15
                    ps["mark"] = self._px(d, ps, t_end)
                    ps["cnt"] = 0
                    rec["pnl_" + ps["layer"]] += ps["side"] * (ps["mark"] - ps["base"]) * ps["qty"]
                rec["exit"], rec["exit_t"] = "Agle din tak hold", t_end
            else:
                rec["exit"] = "Saare leg SL / adjust se band"
                rec["exit_t"] = t_close
        mtm_end = sum(ps["side"] * (ps["mark"] - ps["px0"]) * ps["qty"] for ps in self.pos)
        rec["gross"] = realised_today + mtm_end - start_mtm
        cyc["realised"] += realised_today
        self._finish(rec, d)

    def _finish(self, rec, d):
        rec.setdefault("gross", 0.0)
        rec["net"] = rec["gross"] - rec["costs"]
        self.equity += rec["net"]
        self.peak = max(self.peak, self.equity)
        rec["equity"] = self.equity
        rec["dd"] = (self.peak - self.equity) / (self.p.capital + self.peak)
        rec["events"] = self.ev if hasattr(self, "ev") else []
        if not self.pos:
            self.cycle = None if rec["exit"] not in ("Agle din tak hold",) else self.cycle
        self.log.append(rec)


def _hm(t: int) -> str:
    m = 555 + int(t)
    return f"{m // 60}:{m % 60:02d}"


def frame(sim: Sim) -> pd.DataFrame:
    return pd.DataFrame(sim.log)


def stats(log: pd.DataFrame, capital: float) -> dict:
    x = log[log.traded == 1]
    net = log.net
    cum = net.cumsum()
    dd = float((cum - cum.cummax().clip(lower=0)).min()) if len(cum) else 0.0
    w, l = x.net[x.net > 0], x.net[x.net <= 0]
    return {"days": int(len(x)), "net": float(net.sum()), "ret_pct": float(net.sum() / capital * 100), "win_pct": float((x.net > 0).mean() * 100) if len(x) else None,
            "profit_factor": float(w.sum() / -l.sum()) if l.sum() < 0 else None, "max_dd": dd, "worst": float(x.net.min()) if len(x) else 0.0,
            "best": float(x.net.max()) if len(x) else 0.0, "avg_win": float(w.mean()) if len(w) else None, "avg_loss": float(l.mean()) if len(l) else None}

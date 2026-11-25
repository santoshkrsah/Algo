"""Manish Chandra directional "wait & trade" strangle (video YvmsB1KIcyg), NIFTY next-week options, STBT.

At 9:20 pick the CE and the PE priced about Rs 100. Per side: sell lot 1 when the premium falls 5%, lot 2 (same strike)
when it falls 15%. SL 30% per lot, trailing (stop = lowest premium + 30% of entry). When lot 2 enters, buy a cheaper
OTM hedge for every open lot of that side. Whatever is still open exits next trading day.

Option prices are MODELLED (Black-76, expired option minutes do not exist): IV of a day = IV implied by the PREVIOUS close
of the same expiry's OTM options (Rs 50-180) x m, m fitted on real daily closes; the next day uses the entry day's close IV.
One simulation gives every slippage and hedge on / off variant: those only change the P&L, not the triggers (apply()).
"""
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from backtest import f2f, stbt

STEP = 50
EXCH = 0.0003503            # NSE option exchange charge
M_CLOSE = 930               # 15:30


@dataclass(frozen=True)
class P:
    entry_m: int = 560          # 9:20
    prem: float = 100.0
    wait1: float = 0.05
    wait2: float | None = 0.15  # None: no second lot
    sl: float = 0.30
    trail: bool = True
    hedge_prem: float = 30.0
    last_entry_m: int = 915     # no new lot from 15:15
    exit_next: bool = True
    exit_m: int = 915           # exit minute (next day, or same day when exit_next is False)
    iv_mult: float = 1.0        # x previous-close IV (already includes the fitted m)
    trading_theta: bool = False # True: premium loses time value only while the market is open
    expiry_n: int = 1           # 0 = nearest weekly, 1 = next week


def _b(name_under: pd.DataFrame) -> pd.DataFrame:
    return f2f.calibration_rows("NIFTY", name_under)


def iv_table(under: pd.DataFrame) -> tuple[dict, float, dict]:
    """({(expiry, cp): (days, iv)}, m, model-check info) from real daily closes."""
    b = _b(under)
    w = b[(b.mny > 0) & b.close.between(50, 180)].copy()
    w["iv"] = f2f.implied_vol(w)
    g = w.groupby(["day", "exp", "cp"]).iv.agg(["median", "size"]).reset_index()
    g = g[g["size"] >= 2].rename(columns={"median": "iv"}).sort_values(["exp", "cp", "day"])
    g["prev_iv"] = g.groupby(["exp", "cp"]).iv.shift(1)
    o = b[(b.mny > 0) & b.close.between(60, 150) & b.dte.between(6, 15)].merge(
        g[["day", "exp", "cp", "prev_iv"]], on=["day", "exp", "cp"], how="left").dropna(subset=["prev_iv"])
    F = f2f.fwd(o.und.values, o.Ty.values)
    best = None
    for mm in np.arange(0.9, 1.151, 0.01):
        iv = np.clip(o.prev_iv.values * mm, 0.05, 1.5)
        pm = np.where(o.cp.values == "CE", f2f.price_v(F, o.K.values, "CE", o.Ty.values, iv),
                      f2f.price_v(F, o.K.values, "PE", o.Ty.values, iv))
        err = (pm - o.close.values) / o.close.values
        sc = abs(float(np.median(err)))
        if best is None or sc < best[0]:
            best = (sc, float(mm), float(np.median(np.abs(err))), float(np.median(err)), float(np.percentile(np.abs(err), 80)))
    m = best[1]
    info = {"n": int(len(o)), "m": round(m, 2), "med_abs_err": round(best[2] * 100, 1), "med_err": round(best[3] * 100, 1),
            "p80": round(best[4] * 100, 1)}
    lk = {(e, cp): (x.day.values, x.iv.values) for (e, cp), x in g.groupby(["exp", "cp"])}
    return lk, m, info


def expiries(under: pd.DataFrame) -> list[date]:
    return stbt.expiries("NIFTY", sorted(under.day.unique()))


def _iv_before(lk: dict, exp: str, cp: str, day: str):
    if (exp, cp) not in lk:
        return None
    ds, iv = lk[(exp, cp)]
    i = np.searchsorted(ds, day) - 1
    return float(iv[i]) if i >= 0 else None


def arrays(under: pd.DataFrame) -> dict:
    return {d: (g.m.values.astype(int), g.open.values, g.high.values, g.low.values, g.close.values)
            for d, g in under.groupby("day")}


def simulate(A: dict, lk: dict, m_fit: float, exps: list[date], p: P, start: str, vix: dict | None = None) -> pd.DataFrame:
    days = sorted(A)
    rows, skip = [], {"no_iv": 0, "no_bar": 0, "no_strike": 0, "days": 0}
    exp_s = [e.isoformat() for e in exps]
    for i, day in enumerate(days[:-1]):
        if day < start:
            continue
        nxt = days[i + 1]
        m, o, h, l, c = A[day]
        m2, o2, h2, l2, _ = A[nxt]
        hit = np.where(m == p.entry_m)[0]
        if not len(hit) or (p.exit_next and p.exit_m not in m2) or (not p.exit_next and p.exit_m not in m):
            skip["no_bar"] += 1
            continue
        i0 = int(hit[0])
        later = [e for e in exp_s if e >= day]
        if len(later) <= p.expiry_n:
            continue
        exp = later[p.expiry_n]
        if p.exit_next and nxt > exp:
            continue
        # bars from the entry candle to the exit candle (last element = exit bar)
        if p.exit_next:
            n1, n2 = len(m) - i0, int(np.where(m2 == p.exit_m)[0][0]) + 1
            mm = np.concatenate([m[i0:], m2[:n2]])
            oo, hh, ll = (np.concatenate([a[i0:], b[:n2]]) for a, b in ((o, o2), (h, h2), (l, l2)))
            dd = np.array([day] * n1 + [nxt] * n2)
        else:
            j = int(np.where(m == p.exit_m)[0][0]) + 1
            mm, oo, hh, ll, dd = m[i0:j], o[i0:j], h[i0:j], l[i0:j], np.array([day] * (j - i0))
            n1 = len(mm)
        ed = date.fromisoformat(exp)
        cal = np.array([(ed - date.fromisoformat(d)).days for d in dd]) * 1440 + (M_CLOSE - mm)
        T = np.maximum(cal, 1) / (365 * 1440.0)
        if p.trading_theta:   # same T as calendar at the entry day's open, then it shrinks with market minutes only
            full = {d: f2f.trading_dte(days, str(d), exp) for d in set(dd)}
            left = np.array([full[d] for d in dd]) * 375 + (M_CLOSE - mm)
            T = T[0] * np.maximum(left, 1) / left[0]
        seg = np.arange(len(mm)) >= n1                                    # True = next day
        took = False
        for cp in ("CE", "PE"):
            iv1 = _iv_before(lk, exp, cp, day)
            iv2 = _iv_before(lk, exp, cp, nxt)                            # = the entry day's close (known next morning)
            if iv1 is None:
                skip["no_iv"] += 1
                continue
            iv2 = iv1 if iv2 is None else iv2
            iv = np.clip(np.where(seg, iv2, iv1) * m_fit * p.iv_mult, 0.05, 1.5)
            S0 = oo[0]
            sgn = 1 if cp == "CE" else -1
            base = np.floor(S0 / STEP) * STEP if cp == "CE" else np.ceil(S0 / STEP) * STEP
            Ks = base + sgn * STEP * np.arange(0, 60)
            prs = f2f.price_v(f2f.fwd(S0, T[0]), Ks, cp, T[0], iv[0])
            kk = int(np.argmin(np.abs(prs - p.prem)))
            K, P0 = float(Ks[kk]), float(prs[kk])
            if not 0.75 * p.prem <= P0 <= 1.3 * p.prem:
                skip["no_strike"] += 1
                continue

            def val(strike, spot, j):
                return float(f2f.price_v(f2f.fwd(spot, T[j]), strike, cp, T[j], iv[j]))

            F = f2f.fwd
            po = f2f.price_v(F(oo, T), K, cp, T, iv)
            p_hi = f2f.price_v(F(hh if cp == "CE" else ll, T), K, cp, T, iv)     # dearest premium in the candle
            p_lo = f2f.price_v(F(ll if cp == "CE" else hh, T), K, cp, T, iv)     # cheapest
            best_und = ll if cp == "CE" else hh
            worst_und = hh if cp == "CE" else ll
            day_ok = (~seg) & (mm < p.last_entry_m)
            if not p.exit_next:
                day_ok &= mm < p.exit_m
            legs = []
            start_j = 0
            for leg, wait in ((1, p.wait1), (2, p.wait2)):
                if wait is None:
                    break
                level = P0 * (1 - wait)
                if wait == 0:
                    j, price, spot = 0, float(po[0]), float(oo[0])
                else:
                    c_ = np.where(day_ok[start_j:] & (p_lo[start_j:] <= level + 1e-9))[0]
                    if not len(c_):
                        break
                    j = start_j + int(c_[0])
                    if po[j] <= level:                                       # gap below the trigger: filled at the open
                        price, spot = float(po[j]), float(oo[j])
                    else:
                        a, b = float(oo[j]), float(best_und[j])
                        for _ in range(30):
                            mid = (a + b) / 2
                            a, b = (a, mid) if val(K, mid, j) <= level else (mid, b)
                        price, spot = level, b
                legs.append({"leg": leg, "j": j, "e": price, "spot": spot})
                start_j = j
            for lg in legs:
                j, e = lg["j"], lg["e"]
                init = e * (1 + p.sl)
                rng = np.arange(j + 1, len(mm) - 1)                          # the last bar is the time exit (at its open)
                if len(rng):
                    low_before = np.concatenate([[e], np.minimum.accumulate(p_lo[rng])[:-1]])
                    low_before = np.minimum(low_before, e)
                    steps = np.floor((e - low_before) / (0.01 * e) + 1e-9)        # whole 1% moves in favour so far
                    stop = init - steps * 0.01 * e if p.trail else np.full(len(rng), init)
                    h_ = np.where(p_hi[rng] >= stop)[0]
                else:
                    h_ = []
                if len(h_):
                    k = int(rng[h_[0]])
                    lvl = float(stop[h_[0]])
                    if po[k] >= lvl:
                        x, xs = float(po[k]), float(oo[k])
                    else:
                        a, b = float(oo[k]), float(worst_und[k])
                        for _ in range(30):
                            mid = (a + b) / 2
                            a, b = (a, mid) if val(K, mid, k) >= lvl else (mid, b)
                        x, xs = lvl, b
                    why = "SL 30%" if lvl >= init - 1e-9 else "Trailing SL"
                    if p.sl != 0.30 and why == "SL 30%":
                        why = "SL"
                else:
                    k = len(mm) - 1
                    x, xs = float(po[k]), float(oo[k])
                    why = "Agle din exit" if p.exit_next else "Usi din exit"
                lg.update(k=k, x=x, xs=xs, why=why)
            # hedge: bought when lot 2 enters, one per lot still open then; closed with its lot
            if len(legs) == 2:
                jh, spot = legs[1]["j"], legs[1]["spot"]
                hk = Ks[kk + 1:] if kk + 1 < len(Ks) else []
                if len(hk):
                    hp = f2f.price_v(f2f.fwd(spot, T[jh]), hk, cp, T[jh], iv[jh])
                    H = float(hk[int(np.argmin(np.abs(hp - p.hedge_prem)))])
                    he = val(H, spot, jh)
                    for lg in legs:
                        if lg["k"] > jh or lg["leg"] == 2:
                            lg.update(H=H, he=he, hx=val(H, lg["xs"], lg["k"]), jh=jh)
            for lg in legs:
                took = True
                j, k = lg["j"], lg["k"]
                hm = lambda x: f"{x // 60:02d}:{x % 60:02d}"  # noqa: E731
                rows.append({"date": day, "symbol": "NIFTY", "side": f"SELL {cp}", "leg": lg["leg"], "strike": K, "hedge": lg.get("H", "-"),
                             "expiry": exp, "dte": (ed - date.fromisoformat(day)).days, "ref_prem": round(P0, 2),
                             "entry_time": hm(int(mm[j])), "und_entry": round(lg["spot"], 2),
                             "und_sl": round(lg["xs"], 2) if "SL" in lg["why"] else None, "exit_date": str(dd[k]),
                             "exit_time": hm(int(mm[k])), "und_exit": round(lg["xs"], 2), "iv_pct": round(float(iv[j]) * 100, 1),
                             "opt_entry": round(lg["e"], 2), "opt_exit": round(lg["x"], 2), "hedge_entry": round(lg.get("he", 0.0), 2),
                             "hedge_exit": round(lg.get("hx", 0.0), 2), "hedge_time": hm(int(mm[lg["jh"]])) if "jh" in lg else "-",
                             "qty": f2f.lot_on("NIFTY", day), "exit_reason": lg["why"],
                             "vix": round(vix.get(day, np.nan), 2) if vix else None})
        skip["days"] += int(took)
    out = pd.DataFrame(rows)
    out.attrs["skipped"] = skip
    return out


def apply(t: pd.DataFrame, slip: float, hedge: bool) -> pd.DataFrame:
    """P&L of simulated lots for one slippage (share of premium, every fill) with or without the hedges."""
    t = t.copy()
    has = (t.hedge.astype(str) != "-") if hedge else pd.Series(False, index=t.index)
    if not hedge:
        t["hedge"], t["hedge_entry"], t["hedge_exit"], t["hedge_time"] = "-", 0.0, 0.0, "-"
    sell, buy = t.opt_entry * (1 - slip), t.opt_exit * (1 + slip)
    hb = np.where(has, t.hedge_entry * (1 + slip), 0.0)
    hs = np.where(has, t.hedge_exit * (1 - slip), 0.0)
    t["gross_pnl"] = (t.qty * ((sell - buy) + (hs - hb))).round(2)
    s_val, b_val = t.qty * (sell + hs), t.qty * (buy + hb)
    orders = np.where(has, 4, 2)
    brk = 20.0 * orders
    ex, sebi = EXCH * (s_val + b_val), 1e-6 * (s_val + b_val)
    t["costs"] = (brk + ex + sebi + 0.18 * (brk + ex + sebi) + 0.001 * s_val + 0.00003 * b_val).round(2)
    t["net_pnl"] = (t.gross_pnl - t.costs).round(2)
    return t
